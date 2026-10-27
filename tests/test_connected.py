"""Unit tests for the Connected devices script (scripts/topdevices/connected.py).
Standard library only; no OPNsense needed.
Run:  python3 -m unittest tests.test_connected -v
CONNECTED_PY=<path> points the suite at another copy (used by mutate.py).
"""
import calendar
import importlib.util
import json
import os
import pathlib
import stat
import sys
import tempfile
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
CONNECTED_PY = os.environ.get('CONNECTED_PY') or str(HERE.parent / 'src/opnsense/scripts/topdevices/connected.py')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


connected = _load('connected', CONNECTED_PY)

T0 = 1_793_000_000               # Mon 26 Oct 2026 07:33:20 UTC


def ago(seconds):
    """Host discovery's time format, `seconds` before T0."""
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(T0 - seconds))


def utc(text):
    return calendar.timegm(time.strptime(text, '%Y-%m-%dT%H:%M:%SZ'))


# The interfaces as config_inputs() reads them: opt5 has a gateway entry (a router for
# a static route) and stays internal; opt6's gateway "none" is none.
IFACES = [
    {'name': 'lan',  'device': 'igb1',   'descr': 'LAN',    'ipaddr': '192.0.2.1',    'subnet': '24', 'gateway': '',      'enabled': True},
    {'name': 'wan',  'device': 'igb0',   'descr': 'WAN',    'ipaddr': 'dhcp',         'subnet': '',   'gateway': '',      'enabled': True},
    {'name': 'opt1', 'device': 'vlan01', 'descr': '',       'ipaddr': '198.51.100.1', 'subnet': '24', 'gateway': '',      'enabled': True},
    {'name': 'opt2', 'device': 'igb2',   'descr': 'UPLINK', 'ipaddr': '203.0.113.33', 'subnet': '28', 'gateway': 'GW_UP', 'enabled': True},
    {'name': 'opt3', 'device': 'wg0',    'descr': 'VPN',    'ipaddr': '203.0.113.65', 'subnet': '27', 'gateway': '',      'enabled': True},
    {'name': 'opt4', 'device': 'igb3',   'descr': 'SPARE',  'ipaddr': '203.0.113.1',  'subnet': '24', 'gateway': '',      'enabled': False},
    {'name': 'opt5', 'device': 'igb4',   'descr': 'LAN2',   'ipaddr': '203.0.113.9',  'subnet': '29', 'gateway': '',      'enabled': True},
    {'name': 'opt6', 'device': 'igb5',   'descr': 'LAB',    'ipaddr': '203.0.113.97', 'subnet': '28', 'gateway': 'none',  'enabled': True},
]
NETS = connected.internal_networks(IFACES)
# core's `interface list arp json` records
ARP = [
    {'mac': '00:00:5E:00:53:01', 'ip': '192.0.2.10',   'intf': 'igb1', 'permanent': False, 'expired': False, 'manufacturer': 'Example Inc'},
    {'mac': '00:00:5e:00:53:ff', 'ip': '192.0.2.1',    'intf': 'igb1', 'permanent': True,  'expired': False, 'manufacturer': ''},
    {'mac': '00:00:5e:00:53:09', 'ip': '192.0.2.99',   'intf': 'igb1', 'permanent': False, 'expired': True,  'manufacturer': ''},
    {'mac': '00:00:5e:00:53:0a', 'ip': '203.0.113.40', 'intf': 'igb2', 'permanent': False, 'expired': False, 'manufacturer': ''},
]
# core's `hostwatch dump_full`: interface, MAC, address, vendor, first and last seen (UTC)
HW = {'source': 'discovery', 'rows': [
    ['vlan01', '00:00:5e:00:53:02', '198.51.100.20', 'Example IoT', '2026-09-20T01:00:00Z', ago(600)],
    ['vlan01', '00:00:5e:00:53:02', '198.51.100.7',  'Example IoT', '2026-09-19T01:00:00Z', ago(86400)],
    ['igb1',   '00:00:5e:00:53:03', 'fe80::1',       '',            '2026-09-20T01:00:00Z', ago(60)],
    ['igb1',   '00:00:5e:00:53:03', '192.0.2.30',    '',            '2026-09-20T01:00:00Z', ago(3000)],
    ['igb1',   '00:00:5e:00:53:04', '192.0.2.40',    '',            '2026-09-20T01:00:00Z', ago(1201)],
    ['igb1',   '00:00:5e:00:53:05', '192.0.2.50',    '',            '2026-09-20T01:00:00Z', ago(1200)],
    ['igb1',   '00:00:5e:00:53:06', 'fe80::6',       '',            '2026-09-20T01:00:00Z', ago(10)],
    ['igb1',   '00:00:5e:00:53:ff', '192.0.2.1',     '',            '2026-09-19T01:00:00Z', ago(5)],
    ['igb1',   '00:00:5e:00:53:ff', '192.0.2.2',     '',            '2026-09-19T01:00:00Z', ago(5)],    # the firewall's alias address
    ['igb0',   '00:00:5e:00:53:0b', '203.0.113.77',  '',            '2026-09-19T01:00:00Z', ago(5)],
]}
# parse_wg()'s peers: online at 300 s, offline at 301 s, never connected
WG = [
    {'key': 'KEYA', 'handshake': T0 - 300, 'ip': '198.51.100.3'},
    {'key': 'KEYB', 'handshake': T0 - 301, 'ip': '198.51.100.4'},
    {'key': 'KEYC', 'handshake': 0,        'ip': '198.51.100.5'},
]
S = connected.sightings(NETS, ARP, HW, T0)


class Leases(unittest.TestCase):
    def test_unexpired_leases_mac_lower_case_star_is_no_name_zero_never_expires(self):
        text = ('1793000000 00:00:5e:00:53:01 192.0.2.10 laptop 01:00:00:5e:00:53:01\n'
                '1793000000 00:00:5E:00:53:02 192.0.2.11 * *\n'
                '1700000000 00:00:5e:00:53:03 192.0.2.12 old *\n'
                '0 00:00:5e:00:53:04 192.0.2.13 forever *\n'
                'not a lease\n'
                '1793000000 not-a-mac 192.0.2.14 x *\n')
        self.assertEqual(connected.dnsmasq_leases(text, 1_792_990_000), [
            {'mac': '00:00:5e:00:53:01', 'ip': '192.0.2.10', 'hostname': 'laptop', 'expires': 1_793_000_000},
            {'mac': '00:00:5e:00:53:02', 'ip': '192.0.2.11', 'hostname': '', 'expires': 1_793_000_000},
            {'mac': '00:00:5e:00:53:04', 'ip': '192.0.2.13', 'hostname': 'forever', 'expires': 0}])

    def test_an_empty_file_is_no_leases(self):
        self.assertEqual(connected.dnsmasq_leases('', 1_792_990_000), [])

    def test_leases_skip_duid_and_ipv6_lines(self):
        # Dnsmasq writes its DUID and IPv6 leases (an IAID where the MAC goes) in the same file
        text = ('duid 00:01:00:01:2a:2b:2c:2d:00:00:5e:00:53:01\n'
                '1793000000 00:00:5e:00:53:01 192.0.2.10 laptop 01:00:00:5e:00:53:01\n'
                '1793000000 5678 2001:db8::10 phone 00:01:00:01:2a:2b:2c:2d:00:00:5e:00:53:02\n')
        self.assertEqual(connected.dnsmasq_leases(text, 1_792_990_000), [
            {'mac': '00:00:5e:00:53:01', 'ip': '192.0.2.10', 'hostname': 'laptop', 'expires': 1_793_000_000}])


class Duration(unittest.TestCase):
    def test_under_a_minute_minutes_hours_days_and_a_clock_gone_back(self):
        self.assertEqual([connected.duration_text(s) for s in (30, 360, 8040, 7200, 273600, 259200, -5)],
                         ['under 1 min', '6 min', '2 h 14 min', '2 h', '3 d 4 h', '3 d', 'under 1 min'])


class Networks(unittest.TestCase):
    def test_static_enabled_no_upstream_gateway_not_wireguard_in_config_order(self):
        self.assertEqual([n['descr'] for n in NETS], ['LAN', 'OPT1', 'LAN2', 'LAB'])

    def test_a_network_holds_its_device_address_and_mask_as_numbers(self):
        self.assertEqual(NETS[0], {'device': 'igb1', 'descr': 'LAN', 'address': 3_221_225_985,
                                   'net': 3_221_225_984, 'mask': 4_294_967_040})


class Config(unittest.TestCase):
    XML = '''<?xml version="1.0"?>
<opnsense>
  <interfaces>
    <lan><enable>1</enable><if>igb1</if><descr>LAN</descr><ipaddr>192.0.2.1</ipaddr><subnet>24</subnet></lan>
    <wan><enable/><if>igb0</if><ipaddr>dhcp</ipaddr><gateway>WAN_GW</gateway></wan>
    <opt1><if>vlan01</if><descr>SPARE</descr><ipaddr>198.51.100.1</ipaddr><subnet>24</subnet></opt1>
    <lo0><descr>Loopback</descr><ipaddr>127.0.0.1</ipaddr></lo0>
  </interfaces>
  <dnsmasq>
    <hosts><host>Laptop-Example</host><hwaddr>00:00:5E:00:53:01,00:00:5e:00:53:02</hwaddr><ip>192.0.2.10</ip></hosts>
    <hosts><host></host><ip>192.0.2.11</ip></hosts>
  </dnsmasq>
  <OPNsense><wireguard><client><clients>
    <client><pubkey>KEYA</pubkey><name>Phone-Example</name></client>
    <client><pubkey></pubkey><name>No-Key</name></client>
  </clients></client></wireguard></OPNsense>
</opnsense>'''

    def test_config_inputs_reads_interfaces_reservations_and_peers(self):
        self.assertEqual(connected.config_inputs(self.XML), {
            'interfaces': [
                {'name': 'lan', 'device': 'igb1', 'descr': 'LAN', 'ipaddr': '192.0.2.1', 'subnet': '24',
                 'gateway': '', 'enabled': True},
                {'name': 'wan', 'device': 'igb0', 'descr': '', 'ipaddr': 'dhcp', 'subnet': '',
                 'gateway': 'WAN_GW', 'enabled': True},
                {'name': 'opt1', 'device': 'vlan01', 'descr': 'SPARE', 'ipaddr': '198.51.100.1', 'subnet': '24',
                 'gateway': '', 'enabled': False}],
            'reservations': [{'host': 'Laptop-Example', 'macs': ['00:00:5e:00:53:01', '00:00:5e:00:53:02'],
                              'ips': ['192.0.2.10']}],
            'peer_names': {'KEYA': 'Phone-Example'}})

    def test_config_without_the_optional_sections_has_none_of_them(self):
        self.assertEqual(connected.config_inputs('<opnsense/>'),
                         {'interfaces': [], 'reservations': [], 'peer_names': {}})

    def test_config_inputs_rejects_junk(self):
        with self.assertRaises(ValueError):
            connected.config_inputs('not xml <')


class WireGuard(unittest.TestCase):
    def test_parse_wg_online_never_and_no_slash32(self):
        handshakes = ('wg0\tKEYA\t%d\n' % (T0 - 300) + 'wg0\tKEYB\t0\n'
                      + 'wg0\tKEYC\t%d\n' % (T0 - 50) + 'wg1\tKEYD\t%d\n' % (T0 - 60))
        allowed = ('wg0\tKEYA\t198.51.100.3/32 198.51.100.0/24\n' 'wg0\tKEYB\tfd00::4/128 198.51.100.4/32\n'
                   'wg0\tKEYC\t203.0.113.0/24\n' 'wg1\tKEYD\t(none)\n')
        self.assertEqual(connected.parse_wg(handshakes, allowed), [
            {'key': 'KEYA', 'handshake': T0 - 300, 'ip': '198.51.100.3'},
            {'key': 'KEYB', 'handshake': 0, 'ip': '198.51.100.4'},
            {'key': 'KEYC', 'handshake': T0 - 50, 'ip': ''},
            {'key': 'KEYD', 'handshake': T0 - 60, 'ip': ''}])

    def test_parse_wg_skips_other_shapes(self):
        handshakes = 'garbage\nwg0\tKEYX\tsoon\nwg0\tKEYA\t%d\n\n' % (T0 - 10)
        self.assertEqual(connected.parse_wg(handshakes, 'wg0\tKEYA\t198.51.100.3/32\nnoise\n'),
                         [{'key': 'KEYA', 'handshake': T0 - 10, 'ip': '198.51.100.3'}])


class Sightings(unittest.TestCase):
    def test_arp_now_host_discovery_by_its_time_own_expired_upstream_ipv6_only_left_out(self):
        self.assertEqual(list(S), ['00:00:5e:00:53:01', '00:00:5e:00:53:02', '00:00:5e:00:53:03',
                                   '00:00:5e:00:53:04', '00:00:5e:00:53:05'])

    def test_the_newest_ipv4_is_the_address_an_ipv6_sighting_only_keeps_it_present(self):
        self.assertEqual([S['00:00:5e:00:53:02']['ip'], S['00:00:5e:00:53:03']['ip'],
                          S['00:00:5e:00:53:03']['last'], S['00:00:5e:00:53:01']['last']],
                         ['198.51.100.20', '192.0.2.30', T0 - 60, T0])

    def test_first_seen_vendor_device(self):
        self.assertEqual([S['00:00:5e:00:53:02']['first'], S['00:00:5e:00:53:01']['vendor'],
                          S['00:00:5e:00:53:02']['device']],
                         [utc('2026-09-19T01:00:00Z'), 'Example Inc', 'vlan01'])

    def test_heard_is_the_newest_host_discovery_time_ipv6_included_none_from_the_arp_table_alone(self):
        self.assertEqual([S['00:00:5e:00:53:01']['heard'], S['00:00:5e:00:53:02']['heard'],
                          S['00:00:5e:00:53:03']['heard']], [None, T0 - 600, T0 - 60])

    def test_in_the_arp_table_it_counts_as_seen_now_and_heard_keeps_host_discoverys_older_time(self):
        arp = [{'mac': '00:00:5e:00:53:04', 'ip': '192.0.2.40', 'intf': 'igb1', 'permanent': False,
                'expired': False, 'manufacturer': ''}]
        both = connected.sightings(NETS, arp, {'source': 'discovery', 'rows': [HW['rows'][4]]}, T0)['00:00:5e:00:53:04']
        self.assertEqual([both['last'], both['heard']], [T0, T0 - 1201])

    def test_without_host_discovery_the_arp_table_alone(self):
        self.assertEqual(list(connected.sightings(NETS, ARP, {'source': 'arp-ndp', 'rows': HW['rows']}, T0)),
                         ['00:00:5e:00:53:01'])

    def test_unreadable_host_discovery_is_no_rows(self):
        self.assertEqual(list(connected.sightings(NETS, ARP, None, T0)), ['00:00:5e:00:53:01'])

    def test_future_sightings_count_as_now(self):
        row = ['igb1', '00:00:5e:00:53:21', '192.0.2.21', '', ago(1000), ago(-60)]
        seen = connected.sightings(NETS, [], {'source': 'discovery', 'rows': [row]}, T0)['00:00:5e:00:53:21']
        self.assertEqual([seen['last'], seen['heard']], [T0, T0])


class Presence(unittest.TestCase):
    def test_20_minute_window_inclusive_wireguard_within_300_s_never_connected_peer_out(self):
        self.assertEqual(connected.present_keys(S, WG, T0), {
            '00:00:5e:00:53:01': T0, '00:00:5e:00:53:02': T0 - 600, '00:00:5e:00:53:03': T0 - 60,
            '00:00:5e:00:53:05': T0 - 1200, 'wg:KEYA': T0 - 300})


class Discovery(unittest.TestCase):
    def rows(self, *ages, device='igb1'):
        return {'source': 'discovery',
                'rows': [[device, '00:00:5e:00:53:%02x' % i, '192.0.2.%d' % (10 + i), '', ago(9000), ago(a)]
                         for i, a in enumerate(ages)]}

    def test_discovery_state_off_idle_on(self):
        self.assertEqual([connected.discovery_state(None, NETS, T0),
                          connected.discovery_state({'source': 'arp-ndp', 'rows': HW['rows']}, NETS, T0),
                          connected.discovery_state(self.rows(1201, 5000), NETS, T0),
                          connected.discovery_state(self.rows(), NETS, T0),
                          connected.discovery_state(self.rows(1200, 5000), NETS, T0)],
                         [{'state': 'off', 'last': None}, {'state': 'off', 'last': None},
                          {'state': 'idle', 'last': T0 - 1201}, {'state': 'idle', 'last': None},
                          {'state': 'on', 'last': T0 - 1200}])

    def test_only_sightings_on_internal_networks_count(self):
        self.assertEqual(connected.discovery_state(self.rows(5, device='igb0'), NETS, T0),
                         {'state': 'idle', 'last': None})


LEASES = [{'mac': '00:00:5e:00:53:02', 'ip': '198.51.100.21', 'hostname': 'sensor', 'expires': T0 + 3600},
          {'mac': '00:00:5e:00:53:05', 'ip': '192.0.2.50', 'hostname': '', 'expires': T0 + 3600}]
RESERVATIONS = [{'host': 'Laptop-Example', 'macs': ['00:00:5e:00:53:01'], 'ips': []},
                {'host': 'Tv-Example', 'macs': [], 'ips': ['192.0.2.30']},
                {'host': 'Two-Macs', 'macs': ['00:00:5e:00:53:07', '00:00:5e:00:53:08'], 'ips': ['192.0.2.70']},
                {'host': 'Tablet-Example', 'macs': ['00:00:5e:00:53:04'], 'ips': []}]
PEER_NAMES = {'KEYA': 'Phone-Example'}
LIST_STATE = {'run': T0 - 60, 'started': T0 - 7200, 'error': '', 'visits': {
    '00:00:5e:00:53:01': {'start': T0 - 8040, 'last': T0},
    '00:00:5e:00:53:02': {'start': None, 'last': T0 - 600},
    'wg:KEYA': {'start': T0 - 2700, 'last': T0 - 300}}}


def lists(sightings=S, leases=LEASES, wg=WG, peer_names=PEER_NAMES, tracking=True):
    return connected.connected_list(NETS, sightings, leases, RESERVATIONS, wg, peer_names, LIST_STATE, tracking, T0)


def seen(ip, age=0, mac='00:00:5e:00:53:99'):
    """A sighting as sightings() makes it: an address on the LAN, seen `age` seconds ago."""
    return {mac: {'last': T0 - age, 'ip': ip, 'ip_at': T0 - age, 'vendor': '', 'first': None, 'heard': None,
                  'device': 'igb1'}}


def rows(groups):
    return [[g['name'], g['vpn'], [[d['name'], d['ip']] for d in g['devices']]] for g in groups]


def times(d):
    return [d['since'], d['before'], d['for']]


class Lists(unittest.TestCase):
    def test_grouped_by_network_vpn_last_named_before_unnamed(self):
        self.assertEqual(rows(lists()['groups']), [
            ['LAN', False, [['Laptop-Example', '192.0.2.10'], ['Tv-Example', '192.0.2.30'], ['', '192.0.2.50']]],
            ['OPT1', False, [['sensor', '198.51.100.21']]],
            ['VPN', True, [['Phone-Example', '198.51.100.3']]]])

    def test_the_leases_address_beats_the_sightings(self):
        self.assertEqual(lists()['groups'][1]['devices'][0]['ip'], '198.51.100.21')

    def test_since_before_for(self):
        g = lists()['groups']
        self.assertEqual([times(g[0]['devices'][0]), times(g[1]['devices'][0]), times(g[2]['devices'][0])],
                         [[T0 - 8040, None, '2 h 14 min'], [None, T0 - 7200, ''], [T0 - 2700, None, '45 min']])

    def test_last_seen_is_host_discoverys_time_none_from_the_arp_table_alone_a_vpn_clients_handshake(self):
        self.assertEqual([d['last_seen'] for g in lists()['groups'] for d in g['devices']],
                         [None, T0 - 60, T0 - 1200, T0 - 600, T0 - 300])

    def test_not_connected_newest_first_a_device_by_reservation_and_last_address_a_vpn_client_by_handshake(self):
        self.assertEqual([[d['name'], d['ip'], d['mac'], d['vpn'], d['last_seen']] for d in lists()['recent']],
                         [['', '198.51.100.4', '', True, T0 - 301],
                          ['Tablet-Example', '192.0.2.40', '00:00:5e:00:53:04', False, T0 - 1201]])

    def test_a_device_not_connected_keeps_its_vendor_and_first_sighting(self):
        recent = lists()['recent'][1]
        self.assertEqual([recent['vendor'], recent['first_seen']], ['', utc('2026-09-20T01:00:00Z')])

    def test_7_days_is_the_limit_inclusive_for_devices_and_vpn_clients_a_peer_that_never_connected_left_out(self):
        week = {**seen('192.0.2.81', 604800, '00:00:5e:00:53:61'), **seen('192.0.2.82', 604801, '00:00:5e:00:53:62')}
        for s in week.values():
            s['heard'] = s['last']
        peers = [{'key': 'KEYE', 'handshake': T0 - 604800, 'ip': '198.51.100.6'},
                 {'key': 'KEYF', 'handshake': T0 - 604801, 'ip': '198.51.100.7'},
                 {'key': 'KEYG', 'handshake': 0, 'ip': '198.51.100.8'}]
        self.assertEqual([d['ip'] for d in lists(week, [], peers, {})['recent']], ['192.0.2.81', '198.51.100.6'])

    def test_without_host_discovery_no_device_is_listed_as_not_connected_only_vpn_clients(self):
        arp_only = connected.sightings(NETS, ARP, {'source': 'arp-ndp', 'rows': HW['rows']}, T0)
        self.assertEqual([d['ip'] for d in lists(arp_only)['recent']], ['198.51.100.4'])

    def test_tracking_off_leaves_the_times_empty(self):
        self.assertEqual({tuple(times(d)) for g in lists(tracking=False)['groups'] for d in g['devices']},
                         {(None, None, '')})

    def test_a_reservation_with_two_macs_matches_either(self):
        two = {**S, **seen('192.0.2.71', 0, '00:00:5e:00:53:08')}
        self.assertEqual({d['ip']: d['name'] for d in lists(two)['groups'][0]['devices']}['192.0.2.71'], 'Two-Macs')

    def test_unnamed_sorted_by_number_not_text(self):
        more = {**S, **seen('192.0.2.100', 0, '00:00:5e:00:53:21'), **seen('192.0.2.9', 0, '00:00:5e:00:53:22')}
        self.assertEqual([d['ip'] for d in lists(more)['groups'][0]['devices'] if d['name'] == ''],
                         ['192.0.2.9', '192.0.2.50', '192.0.2.100'])

    def test_a_vpn_row_has_no_mac_vendor_or_first_seen(self):
        phone = lists()['groups'][2]['devices'][0]
        self.assertEqual([phone['mac'], phone['vendor'], phone['first_seen']], ['', '', None])

    def test_names_in_natural_order_9_before_10(self):
        more = {**S, **seen('192.0.2.61', 0, '00:00:5e:00:53:31'), **seen('192.0.2.62', 0, '00:00:5e:00:53:32')}
        leases = LEASES + [{'mac': '00:00:5e:00:53:31', 'ip': '192.0.2.61', 'hostname': 'Device-10', 'expires': T0 + 3600},
                           {'mac': '00:00:5e:00:53:32', 'ip': '192.0.2.62', 'hostname': 'Device-9', 'expires': T0 + 3600}]
        self.assertEqual([d['name'] for d in lists(more, leases)['groups'][0]['devices'] if d['name']],
                         ['Device-9', 'Device-10', 'Laptop-Example', 'Tv-Example'])

    def test_recent_never_after_now(self):
        row = ['igb1', '00:00:5e:00:53:23', '192.0.2.23', '', ago(1000), ago(-60)]
        future = connected.sightings(NETS, [], {'source': 'discovery', 'rows': [row]}, T0)
        got = lists(future, [], [{'key': 'KEYZ', 'handshake': T0 + 60, 'ip': '198.51.100.9'}], {})
        self.assertEqual([[d['ip'], d['last_seen']] for g in got['groups'] for d in g['devices']] + got['recent'],
                         [['192.0.2.23', T0], ['198.51.100.9', T0]])

    def test_peer_without_slash32_listed_without_address(self):
        sites = [{'key': 'KEYS', 'handshake': T0 - 10, 'ip': ''}, {'key': 'KEYT', 'handshake': T0 - 4000, 'ip': ''}]
        got = lists({}, [], sites, {'KEYS': 'Site-Example', 'KEYT': 'Site-Example-2'})
        self.assertEqual([rows(got['groups']), [[d['name'], d['ip'], d['vpn']] for d in got['recent']]],
                         [[['VPN', True, [['Site-Example', '']]]], [['Site-Example-2', '', True]]])


class Natural(unittest.TestCase):
    def test_natural_key_orders_9_before_10_and_ignores_case(self):
        self.assertEqual(sorted(['device-10', 'Device-9', 'device-1b', 'Device-1a'], key=connected.natural_key),
                         ['Device-1a', 'device-1b', 'Device-9', 'device-10'])


STATE = {'run': T0 - 60, 'started': T0 - 9000, 'error': '', 'visits': {
    'a': {'start': T0 - 8000, 'last': T0 - 120},
    'b': {'start': T0 - 5000, 'last': T0 - 2000},
    'c': {'start': T0 - 4000, 'last': T0 - 1830},
    'd': {'start': T0 - 3000, 'last': T0 - 1000},
    'e': {'start': T0 - 3000, 'last': T0 - 1801}}}
PRESENT = {'a': T0 - 30, 'b': T0 - 10, 'c': T0 - 30}
EMPTY = {'run': None, 'started': None, 'error': '', 'visits': {}}


class Visits(unittest.TestCase):
    def test_first_run(self):
        self.assertEqual(connected.track_visits(connected.read_state('/nonexistent'), {'m1': T0 - 30}, T0),
                         {'run': T0, 'started': T0, 'error': '', 'visits': {'m1': {'start': None, 'last': T0 - 30}}})

    def test_continue_new_after_gap_gap_is_inclusive_absent_kept_then_dropped(self):
        self.assertEqual(connected.track_visits(STATE, PRESENT, T0)['visits'], {
            'a': {'start': T0 - 8000, 'last': T0 - 30},
            'b': {'start': T0 - 10, 'last': T0 - 10},
            'c': {'start': T0 - 4000, 'last': T0 - 30},
            'd': {'start': T0 - 3000, 'last': T0 - 1000}})

    def test_tracking_paused_longer_than_the_gap_starts_afresh(self):
        paused = connected.track_visits({**STATE, 'run': T0 - 1801}, PRESENT, T0)
        self.assertEqual([paused['started'], paused['visits']],
                         [T0, {'a': {'start': None, 'last': T0 - 30}, 'b': {'start': None, 'last': T0 - 10},
                               'c': {'start': None, 'last': T0 - 30}}])

    def test_a_clock_gone_back_starts_afresh(self):
        back = connected.track_visits({**STATE, 'run': T0 + 500}, PRESENT, T0)
        self.assertEqual([back['started'], sorted(back['visits'])], [T0, ['a', 'b', 'c']])
        self.assertEqual({v['start'] for v in back['visits'].values()}, {None})


class StateFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, 'topdevices')
        self.path = os.path.join(self.dir, 'connected.json')

    def tearDown(self):
        self.tmp.cleanup()

    def test_root_only_through_its_directory_nothing_left_behind_read_back_the_same(self):
        os.mkdir(self.dir, 0o755)                     # an older directory: tightened too
        os.chmod(self.dir, 0o755)
        written = connected.track_visits(STATE, PRESENT, T0)
        connected.write_state(self.path, written)
        self.assertEqual([oct(stat.S_IMODE(os.stat(self.path).st_mode)), oct(stat.S_IMODE(os.stat(self.dir).st_mode)),
                          os.path.exists(self.path + '.tmp'), connected.read_state(self.path)],
                         ['0o600', '0o700', False, written])

    def test_the_directory_is_made_when_missing(self):
        connected.write_state(self.path, EMPTY)
        self.assertEqual([oct(stat.S_IMODE(os.stat(self.dir).st_mode)), connected.read_state(self.path)],
                         ['0o700', EMPTY])

    def test_junk_reads_as_empty(self):
        os.mkdir(self.dir)
        with open(self.path, 'w') as f:
            f.write('{oops')
        self.assertEqual(connected.read_state(self.path), EMPTY)

    def test_a_visit_of_the_wrong_shape_is_dropped(self):
        os.mkdir(self.dir)
        with open(self.path, 'w') as f:
            json.dump({'run': T0, 'started': 'yesterday', 'error': 5, 'visits': {
                'ok': {'start': None, 'last': T0}, 'bad': {'start': 'x', 'last': T0}, 'worse': [1, 2]}}, f)
        self.assertEqual(connected.read_state(self.path),
                         {'run': T0, 'started': None, 'error': '', 'visits': {'ok': {'start': None, 'last': T0}}})


if __name__ == '__main__':
    unittest.main()
