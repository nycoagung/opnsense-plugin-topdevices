#!/usr/local/bin/python3
"""TopDevices: the devices connected now, and the ones seen this week (the Connected
devices widget).

`list` prints the widget's answer as JSON and changes nothing; configd runs it for
api/topdevices/connected/list. `track` runs every minute (/usr/local/etc/cron.d/topdevices)
and records when each connected device's current visit began, in a root-only state file.

A device is a MAC address on an internal network. It is connected when the firewall's
ARP table has it now, or core's Host discovery saw it within the last 20 minutes; it is
listed as not connected when Host discovery saw it within the last 7 days. A WireGuard
peer is connected when its latest handshake is at most 5 minutes old (core's own rule),
and listed when it is within 7 days. Names come from Dnsmasq: the reservation, else the
name the device gave DHCP.

Standard library only, and no import of the plugin's other scripts. WireGuard is read
without any key material (`wg show all latest-handshakes` and `allowed-ips`).
Design: docs/superpowers/specs/2026-10-27-connected-devices-widget-design.md
"""
import calendar
import ipaddress
import re
import time
import xml.etree.ElementTree as ET

VERSION = '0.5.0'
PRESENT_WINDOW = 1200            # seen within this many seconds: connected (inclusive)
VISIT_GAP = 1800                 # seen again within this: the same visit (inclusive)
RECENT_WINDOW = 604800           # 7 days: a device not connected is still listed (inclusive)
WG_ONLINE = 300                  # a peer's handshake this recent: online, core's own rule
TRACKING_STALE = 180             # the per-minute run's last run this recent: tracking runs
STATE = '/var/db/topdevices/connected.json'
LEASES = '/var/db/dnsmasq.leases'
CONFIG = '/conf/config.xml'
LIST_ARP = '/usr/local/opnsense/scripts/interfaces/list_arp.py'      # core's `interface list arp`
LIST_HOSTS = '/usr/local/opnsense/scripts/interfaces/list_hosts.py'  # core's `hostwatch dump_full`
WG = '/usr/bin/wg'
WG_TIMEOUT = 10


def _is_ip(text):
    try:
        ipaddress.ip_address(text)
    except ValueError:
        return False
    return True


def _epoch(text):
    """Host discovery's "YYYY-MM-DDTHH:MM:SSZ" (UTC) in epoch seconds; None if unreadable."""
    try:
        return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))
    except (TypeError, ValueError):
        return None


def dnsmasq_leases(text, now):
    """The unexpired leases in a Dnsmasq lease file: [{mac, ip, hostname, expires}].

    An expired lease can point a MAC at an address another device now holds, so it is
    left out. A lease that never expires is written with 0. The DUID line and IPv6
    leases (an IAID where the MAC goes) are skipped.
    """
    leases = []
    for line in text.splitlines():
        parts = line.split()        # <expiry> <mac> <ip> <hostname> <client id>
        if (len(parts) < 3 or not parts[0].isdigit() or not re.fullmatch(r'[0-9a-fA-F:]{17}', parts[1])
                or not _is_ip(parts[2])):
            continue
        expires = int(parts[0])
        if 0 < expires < now:
            continue
        name = parts[3] if len(parts) > 3 else ''
        leases.append({'mac': parts[1].lower(), 'ip': parts[2], 'hostname': '' if name == '*' else name,
                       'expires': expires})
    return leases


def config_inputs(xml_text):
    """From config.xml's text: the interfaces, the Dnsmasq reservations and the WireGuard
    peers' names. Read only. Raises ValueError when the text is not XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError('config.xml: %s' % exc) from None

    def text(node, tag):
        child = node.find(tag)
        return (child.text or '').strip() if child is not None else ''

    def items(value):
        return [item.strip() for item in value.split(',') if item.strip()]

    interfaces = []
    section = root.find('interfaces')
    for node in list(section) if section is not None else []:
        if text(node, 'if'):
            interfaces.append({'name': node.tag, 'device': text(node, 'if'), 'descr': text(node, 'descr'),
                               'ipaddr': text(node, 'ipaddr'), 'subnet': text(node, 'subnet'),
                               'gateway': text(node, 'gateway'), 'enabled': node.find('enable') is not None})
    reservations = []
    for host in root.findall('dnsmasq/hosts'):
        if text(host, 'host'):
            reservations.append({'host': text(host, 'host'),
                                 'macs': [mac.lower() for mac in items(text(host, 'hwaddr'))],
                                 'ips': items(text(host, 'ip'))})
    peer_names = {}
    for client in root.findall('OPNsense/wireguard/client/clients/client'):
        if text(client, 'pubkey'):
            peer_names[text(client, 'pubkey')] = text(client, 'name')
    return {'interfaces': interfaces, 'reservations': reservations, 'peer_names': peer_names}


def internal_networks(interfaces):
    """The internal networks, in config.xml's order: [{device, descr, address, net, mask}],
    addresses as numbers.

    Enabled interfaces with a static IPv4 address and no upstream gateway in their
    settings ("none" is none): the interfaces core's NAT reflection treats as internal. A
    gateway entry alone, such as a router for a static route, does not make one upstream.
    WireGuard interfaces are not networks here: their peers are the VPN clients.
    """
    networks = []
    for iface in interfaces:
        try:
            address = int(ipaddress.IPv4Address(iface['ipaddr']))
        except ValueError:
            continue
        bits = int(iface['subnet']) if iface['subnet'].isdigit() else 0
        if (not iface['enabled'] or not 1 <= bits <= 32 or iface['gateway'] not in ('', 'none')
                or re.fullmatch(r'wg\d+', iface['device'])):
            continue
        mask = (0xffffffff << (32 - bits)) & 0xffffffff
        networks.append({'device': iface['device'], 'descr': iface['descr'] or iface['name'].upper(),
                         'address': address, 'net': address & mask, 'mask': mask})
    return networks


def parse_wg(handshakes, allowed):
    """`wg show all latest-handshakes` and `wg show all allowed-ips` -> [{key, handshake,
    ip}], a peer per handshake line, in that order. A handshake of 0 means never. The
    address is the first IPv4 /32 among the peer's allowed IPs, else ''. Lines of
    another shape are skipped. Only public keys are ever read."""
    ips = {}
    for line in allowed.splitlines():
        parts = line.split('\t')
        if len(parts) != 3:
            continue
        for item in parts[2].split():
            try:
                net = ipaddress.ip_network(item, strict=False)
            except ValueError:
                continue
            if net.version == 4 and net.prefixlen == 32:
                ips.setdefault(parts[1], str(net.network_address))
                break
    peers = []
    for line in handshakes.splitlines():
        parts = line.split('\t')
        if len(parts) == 3 and parts[1] and parts[2].isdigit():
            peers.append({'key': parts[1], 'handshake': int(parts[2]), 'ip': ips.get(parts[1], '')})
    return peers


def sightings(networks, arp, hostwatch, now):
    """Each device seen on an internal network, by lower-case MAC: {last, ip, ip_at,
    vendor, first, heard, device}.

    The ARP table (core's records, or None) counts as seen now. Host discovery
    ({source, rows}, or None) counts only when its source is 'discovery' (otherwise core
    gave the ARP table again, without times), by the time of each sighting, IPv6 ones
    included; the newest of those is when the device was last heard from (`heard`; None
    when only the ARP table has it, which keeps no times). A time after `now` counts as
    `now`. A device's address is its newest IPv4 sighting inside its network; a sighting
    that cannot be its address (IPv6, or IPv4 outside the interface's subnet) still keeps
    it present. A device with no address at all is left out, and so is the firewall
    itself: its permanent ARP entries and its interface addresses.
    """
    by_device = {net['device']: net for net in networks}
    own_ips = {net['address'] for net in networks}
    own_macs = {row['mac'].lower() for row in arp or []
                if row.get('permanent') is True and isinstance(row.get('mac'), str)}

    def address_on(ip, device):
        """The address as a number when the device can hold it there: IPv4, inside the
        interface's network, not the firewall's own."""
        net = by_device.get(device)
        try:
            value = int(ipaddress.IPv4Address(ip))
        except ValueError:
            return None
        if net is None or value in own_ips or value & net['mask'] != net['net']:
            return None
        return value

    seen = {}

    def see(mac, ip, device, at, timed, vendor, first):
        mac = mac.lower()
        if mac in own_macs:
            return
        s = seen.setdefault(mac, {'last': 0, 'ip': '', 'ip_at': 0, 'vendor': '', 'first': None,
                                  'heard': None, 'device': ''})
        s['last'] = max(s['last'], at)
        if timed and (s['heard'] is None or at > s['heard']):
            s['heard'] = at
        if address_on(ip, device) is not None and (s['ip'] == '' or at > s['ip_at']):
            s['ip'], s['ip_at'], s['device'] = ip, at, device
        if not s['vendor'] and vendor:
            s['vendor'] = vendor
        if first is not None and (s['first'] is None or first < s['first']):
            s['first'] = first

    for row in arp or []:
        mac, ip, intf = row.get('mac'), row.get('ip'), row.get('intf')
        if (not all(isinstance(v, str) for v in (mac, ip, intf)) or row.get('permanent') is True
                or row.get('expired') is True or address_on(ip, intf) is None):
            continue
        vendor = row.get('manufacturer')
        see(mac, ip, intf, now, False, vendor if isinstance(vendor, str) else '', None)
    if hostwatch is not None and hostwatch.get('source') == 'discovery':
        for row in hostwatch.get('rows', []):
            if len(row) < 6 or row[0] not in by_device:
                continue
            last = _epoch(row[5])
            if last is None:
                continue
            first = _epoch(row[4])
            see(row[1], row[2], row[0], min(last, now), True, row[3], None if first is None else min(first, now))
    return {mac: s for mac, s in seen.items() if s['ip'] != ''}


def present_keys(sightings, wg, now):
    """Who is present now, as visit key => when last seen: each device seen within
    PRESENT_WINDOW, by MAC, and each WireGuard peer (parse_wg) whose latest handshake is
    within WG_ONLINE, as 'wg:' and its public key."""
    present = {mac: s['last'] for mac, s in sightings.items() if s['last'] >= now - PRESENT_WINDOW}
    for peer in wg:
        if 0 < peer['handshake'] and now - peer['handshake'] <= WG_ONLINE:
            present['wg:' + peer['key']] = min(peer['handshake'], now)
    return present


def discovery_state(hostwatch, networks, now):
    """Host discovery on, idle or off, and its newest sighting on the internal networks:
    {state, last}. Off: core handed back the ARP table, or nothing could be read. Idle:
    it is on but has heard nothing on these networks within PRESENT_WINDOW."""
    if hostwatch is None or hostwatch.get('source') != 'discovery':
        return {'state': 'off', 'last': None}
    devices = {net['device'] for net in networks}
    times = [_epoch(row[5]) for row in hostwatch.get('rows', []) if len(row) >= 6 and row[0] in devices]
    times = [min(t, now) for t in times if t is not None]
    last = max(times) if times else None
    return {'state': 'idle' if last is None or now - last > PRESENT_WINDOW else 'on', 'last': last}


def duration_text(seconds):
    """How long, in words: "under 1 min", "6 min", "2 h 14 min", "2 h", "3 d 4 h", "3 d".
    A negative span (a clock gone back) reads as under a minute."""
    if seconds < 60:
        return 'under 1 min'
    minutes = seconds // 60
    if minutes < 60:
        return '%d min' % minutes
    hours, rest = divmod(minutes, 60)
    if hours < 24:
        return '%d h' % hours + (' %d min' % rest if rest else '')
    days, rest = divmod(hours, 24)
    return '%d d' % days + (' %d h' % rest if rest else '')
