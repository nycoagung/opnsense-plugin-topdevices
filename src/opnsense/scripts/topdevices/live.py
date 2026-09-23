#!/usr/local/bin/python3
"""
TopDevices live sampler.

Reads the pf state table once per interval and writes per-device traffic rates
to stdout as server-sent events. configd runs it as a stream_output action and
relays the stream to the dashboard widget:

    GET /api/topdevices/live/stream/{interval}
      -> LiveController -> configd 'topdevices live <interval>' -> live.py

Design and the measurements behind it:
docs/superpowers/specs/2026-09-23-live-traffic-design.md

All logic lives in the pure functions below (parse_*, Topology, deltas,
credits, aggregate, wan_rates, next_interval, Drift, format_event) so it is
unit-tested without OPNsense. run_loop() wires them to the real commands, and
main() is the only place that touches the process's stdout.
"""
import ipaddress
import json
import os
import re
import resource
import subprocess
import sys
import time

VERSION = '0.1.0'

PFCTL = ('/sbin/pfctl', '-vvs', 'state')
IFCONFIG = ('/sbin/ifconfig',)
IFINFO = '/usr/local/sbin/ifinfo'
ROUTES = ('/usr/bin/netstat', '-rWn', '-f', 'inet')

# Local means "inside RFC 1918 and configured on a firewall interface". Never use
# ipaddress.is_private: it also answers True for the documentation ranges and
# others, and its meaning has changed between Python releases.
RFC1918 = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
LOOPBACK = ipaddress.ip_network('127.0.0.0/8')
LINK_LOCAL = ipaddress.ip_network('169.254.0.0/16')

MIN_INTERVAL, MAX_INTERVAL = 1, 10
ETHER_HEADER = 14            # bytes per frame the interface counts and pf does not
CPU_BUDGET = 0.10            # a sampler may use at most this share of one core
COST_EMA_SAMPLES = 5
LIFETIME = 3600              # seconds; then a clean exit and the browser reconnects
TOPOLOGY_REFRESH = 60        # seconds between re-reading interface addresses
DRIFT_WINDOW = 60            # seconds of history the drift check judges
DRIFT_MIN_BPS = 1000000      # a direction is judged only above this average rate
DRIFT_BAND = (0.90, 1.10)
DRIFT_MAX_V6_SHARE = 0.01    # IPv6 above this share of WAN bytes suspends the check
TOP_PEERS = 10
TOP_PORTS = 5

INET, ALL, FW = 'inet', 'all', 'fw'
PORT_PROTOCOLS = ('tcp', 'udp', 'sctp')

_AGE = re.compile(r'^age (\d+):(\d+):(\d+)')
_BYTES = re.compile(r', (\d+):(\d+) bytes(?:,|$)')
_ID = re.compile(r'^id: ([0-9a-f]+) creatorid: ([0-9a-f]+)')
_IFINFO_HEAD = re.compile(r'^Interface (\S+) \(([^)]+)\):$')
_UNSAFE = re.compile(r'[<>&]')


# ---------------------------------------------------------------- parsing


def _host(token):
    """'192.0.2.1:443' -> ('192.0.2.1', 443, 4); '2001:db8::1[443]' -> ('2001:db8::1', 443, 6)."""
    token = token.strip('()')
    if token.count(':') > 1 or '[' in token:
        addr, _, rest = token.partition('[')
        port = rest.rstrip(']')
        return addr, int(port) if port.isdigit() else 0, 6
    addr, _, port = token.partition(':')
    return addr, int(port) if port.isdigit() else 0, 4


def parse_header(line):
    """First line of a state -> dict, or None when the line is not a state header.

    Layout, from pfctl's pf_print_state.c:
        ifname proto host1 [(nat1)] ->|<- host2 [(nat2)] STATE
    With '->' (out) host1 is the source and nat1 its address before NAT. With
    '<-' (in) host1 is the destination after any redirect and nat1 the original
    destination. The arrow is found by value, not by position: either host may
    carry a translation, and a fixed index misreads states where both do.
    """
    parts = line.split()
    arrow = next((i for i, p in enumerate(parts) if p in ('->', '<-')), None)
    if arrow is None or arrow < 3 or arrow + 2 >= len(parts):
        return None
    left, right = parts[2:arrow], parts[arrow + 1:-1]
    if not left or not right or left[0].startswith('(') or right[0].startswith('('):
        return None
    h1, h2 = _host(left[0]), _host(right[0])
    nat = _host(left[1]) if len(left) > 1 and left[1].startswith('(') else None
    if parts[arrow] == '->':
        direction, src, dst = 'out', h1, h2
    else:
        direction, src, dst = 'in', h2, h1
    # only TCP, UDP and SCTP have ports; for ICMP pfctl prints the query id there
    port = dst[1] if parts[1] in PORT_PROTOCOLS else 0
    return {'dir': direction, 'af': h1[2], 'src': src[0], 'dst': dst[0], 'dst_port': port,
            'nat': nat[0] if nat else None}


def parse_states(text):
    """`pfctl -vvs state` output -> ({'id/creatorid': state}, unparsed).

    A state is a header line at column 0 followed by indented lines: a TCP
    sequence-window line, the counters line (age, pkts, bytes, ...), the id line
    and possibly an origif line. `unparsed` counts header lines that are not
    states, indented lines of an unknown shape and states that never got a
    usable counters or id line, so a format change shows up as a number instead
    of as silently wrong rates.
    """
    states = {}
    unparsed = 0
    cur = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            if cur is not None:
                unparsed += 1                    # the previous state never reached its id line
            cur = parse_header(line)
            if cur is None:
                unparsed += 1
            continue
        body = line.strip()
        if body.startswith('age '):
            age, byts = _AGE.match(body), _BYTES.search(body)
            if cur is None:
                continue
            if age and byts:
                cur['age'] = int(age.group(1)) * 3600 + int(age.group(2)) * 60 + int(age.group(3))
                cur['b0'], cur['b1'] = int(byts.group(1)), int(byts.group(2))
            else:
                unparsed += 1
                cur = None
        elif body.startswith('id: '):
            m = _ID.match(body)
            if cur is not None:
                if m and 'b0' in cur:
                    states['%s/%s' % (m.group(1), m.group(2))] = cur
                else:
                    unparsed += 1
            cur = None
        elif body.startswith('[') or body.startswith('origif: '):
            continue                             # TCP sequence window / original interface
        else:
            unparsed += 1
    if cur is not None:
        unparsed += 1
    return states, unparsed


def parse_ifconfig(text):
    """`ifconfig` output -> [(device, IPv4Interface)] for every inet address.

    Same reading as core's legacy_interfaces_details(): a device line carries
    'flags=' at column 0; an address line starts with a tab and 'inet ', and
    its netmask is the hex word after 'netmask'.
    """
    out = []
    dev = None
    for line in text.splitlines():
        if line and not line[0].isspace() and 'flags=' in line:
            dev = line.split(':', 1)[0]
        elif dev and line.startswith('\tinet '):
            parts = line.split()
            if 'netmask' not in parts:
                continue
            try:
                bits = bin(int(parts[parts.index('netmask') + 1], 16)).count('1')
                out.append((dev, ipaddress.ip_interface('%s/%d' % (parts[1], bits))))
            except (IndexError, ValueError):
                continue
    return out


def parse_ifinfo(text):
    """`ifinfo <dev>` output -> {device: {'rx', 'tx', 'rxp', 'txp', 'ether'}}.

    Same reading as core's legacy_interface_stats(): a heading per interface,
    then 'key: value' lines.
    """
    raw = {}
    cur = None
    for line in text.splitlines():
        m = _IFINFO_HEAD.match(line.strip())
        if m:
            cur = raw.setdefault(m.group(1), {})
        elif cur is not None and ':' in line:
            key, _, value = line.partition(':')
            cur[key.strip()] = value.strip()
    out = {}
    for dev, kv in raw.items():
        try:
            out[dev] = {
                'rx': int(kv['bytes received']), 'tx': int(kv['bytes transmitted']),
                'rxp': int(kv['packets received']), 'txp': int(kv['packets transmitted']),
                'ether': kv.get('type', '') == 'Ethernet',
            }
        except (KeyError, ValueError):
            continue
    return out


def parse_default_devs(text):
    """`netstat -rWn -f inet` output -> interfaces carrying an IPv4 default route.

    Same reading as core's show_routes.py: the columns are named by the
    'Destination Gateway ...' header line.
    """
    names, devs = None, []
    for line in text.splitlines():
        f = line.split()
        if len(f) > 2 and f[0] == 'Destination' and f[1] == 'Gateway':
            names = [x.lower() for x in f]
        elif names and 'netif' in names and len(f) > names.index('netif') and f[0] == 'default':
            dev = f[names.index('netif')]
            if dev not in devs:
                devs.append(dev)
    return devs


class Topology:
    """What the firewall's own interfaces and routes say about the network.

    Upstream is every interface carrying a default route, plus any interface
    with a public address. The route matters behind an ISP router (double
    NAT): there the WAN has an RFC 1918 address, and an address-only rule
    would call it local and credit no internet traffic at all. Local is then
    the RFC 1918 subnets of every other interface.
    """

    def __init__(self, ifaddrs, default_devs=()):
        self.local_nets = []
        self.fw_addrs = set()
        self.upstream_devs = []
        self.upstream_addrs = set()
        public = [dev for dev, iface in ifaddrs
                  if not any(iface.ip in net for net in RFC1918)
                  and iface.ip not in LOOPBACK and iface.ip not in LINK_LOCAL]
        for dev in list(default_devs) + public:
            if dev not in self.upstream_devs:
                self.upstream_devs.append(dev)
        for dev, iface in ifaddrs:
            ip = iface.ip
            self.fw_addrs.add(str(ip))
            if dev in self.upstream_devs:
                self.upstream_addrs.add(str(ip))
            elif any(ip in net for net in RFC1918) and iface.network not in self.local_nets:
                self.local_nets.append(iface.network)
        self._local = {}

    def is_local(self, addr):
        hit = self._local.get(addr)
        if hit is None:
            try:
                ip = ipaddress.ip_address(addr)
                hit = ip.version == 4 and any(ip in net for net in self.local_nets)
            except ValueError:
                hit = False
            self._local[addr] = hit
        return hit


