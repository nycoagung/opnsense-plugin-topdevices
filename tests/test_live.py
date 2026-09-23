"""Unit tests for the live sampler. Standard library only; no OPNsense needed.

Run:  python3 -m unittest discover -s tests -v
LIVE_PY=<path> points the suite at another copy of live.py (used by mutate.py).
"""
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import subprocess
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
LIVE_PY = os.environ.get('LIVE_PY') or str(HERE.parent / 'src/opnsense/scripts/topdevices/live.py')
_spec = importlib.util.spec_from_file_location('live', LIVE_PY)
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)

FIX = HERE / 'fixtures'
STATES_TEXT = (FIX / 'pfctl_states.txt').read_text()
IFCONFIG_TEXT = (FIX / 'ifconfig.txt').read_text()
IFINFO_TEXT = (FIX / 'ifinfo_em0.txt').read_text()
ROUTES_TEXT = (FIX / 'netstat_routes.txt').read_text()
# the same firewall behind an ISP router: the WAN has an RFC 1918 address
DOUBLE_NAT_IFCONFIG = IFCONFIG_TEXT.replace('198.51.100.2 netmask 0xffffff00 broadcast 198.51.100.255',
                                            '192.168.0.2 netmask 0xffffff00 broadcast 192.168.0.255')
CREATOR = '0a0a0a0a'


def sid(n):
    return '%016x/%s' % (n, CREATOR)


def topology(ifconfig=IFCONFIG_TEXT, routes=ROUTES_TEXT):
    return live.Topology(live.parse_ifconfig(ifconfig), live.parse_default_devs(routes))


class ParseStates(unittest.TestCase):
    def setUp(self):
        self.states, self.unparsed = live.parse_states(STATES_TEXT)

    def test_counts(self):
        # 0x01-0x12 and 0x14 are well formed; five entries are not (see fixture tail)
        self.assertEqual(sorted(self.states), sorted([sid(n) for n in range(1, 0x13)] + [sid(0x14)]))
        self.assertEqual(self.unparsed, 5)

    def test_fields(self):
        expect = {
            0x01: ('in', 4, '192.168.1.10', '203.0.113.7', 443, None, 10000, 500000, 300),
            0x02: ('out', 4, '198.51.100.2', '203.0.113.7', 443, '192.168.1.10', 10000, 500000, 300),
            0x03: ('in', 4, '192.0.2.50', '192.168.1.80', 443, '198.51.100.2', 3000, 90000, 60),
            0x05: ('in', 4, '192.168.20.5', '192.168.1.53', 53, '192.0.2.53', 120, 360, 10),
            0x0a: ('in', 4, '192.0.2.99', '198.51.100.2', 51820, None, 60000, 70000, 600),
            0x0c: ('out', 4, '198.51.100.2', '203.0.113.9', 443, '10.0.0.2', 2000, 80000, 180),
            0x0f: ('in', 6, '2001:db8:1::5', '2001:db8::10', 443, None, 1500, 2500, 120),
            0x10: ('in', 4, '192.168.1.10', '203.0.113.7', 0, None, 84, 84, 1),     # ICMP: no port
            0x11: ('in', 4, '192.168.1.10', '192.168.1.80', 443, '198.51.100.2', 700, 900, 30),
            0x12: ('in', 4, '192.168.1.10', '192.0.2.77', 3478, None, 900, 900, 123 * 3600 + 4 * 60 + 5),
        }
        for n, (d, af, src, dst, port, nat, b0, b1, age) in expect.items():
            with self.subTest(state=hex(n)):
                s = self.states[sid(n)]
                self.assertEqual((s['dir'], s['af'], s['src'], s['dst'], s['dst_port'], s['nat'],
                                  s['b0'], s['b1'], s['age']),
                                 (d, af, src, dst, port, nat, b0, b1, age))

    def test_header_rejects_non_states(self):
        for line in ('', 'this line is not a state header',
                     'all tcp 203.0.113.7:443 192.168.1.10:50600       ESTABLISHED:ESTABLISHED',
                     'all tcp -> 203.0.113.7:443       ESTABLISHED:ESTABLISHED'):
            with self.subTest(line=line):
                self.assertIsNone(live.parse_header(line))


class ParseInterfaces(unittest.TestCase):
    def test_topology(self):
        t = topology()
        self.assertEqual(t.upstream_devs, ['em0'])
        self.assertEqual(t.upstream_addrs, {'198.51.100.2'})
        self.assertEqual([str(n) for n in t.local_nets], ['192.168.1.0/24', '192.168.20.0/24', '10.0.0.0/24'])
        self.assertEqual(t.fw_addrs, {'198.51.100.2', '192.168.1.1', '192.168.20.1', '169.254.3.1',
                                      '10.0.0.1', '127.0.0.1'})

    def test_is_local_uses_rfc1918_on_interfaces_only(self):
        t = topology()
        for addr, want in (('192.168.1.10', True), ('10.0.0.2', True), ('192.168.20.5', True),
                           ('172.16.0.1', False),   # RFC 1918, but on no interface
                           ('203.0.113.7', False), ('192.0.2.50', False),   # documentation ranges
                           ('198.51.100.2', False), ('100.64.0.1', False), ('2001:db8::1', False),
                           ('not-an-address', False)):
            with self.subTest(addr=addr):
                self.assertIs(t.is_local(addr), want)

    def test_default_route(self):
        self.assertEqual(live.parse_default_devs(ROUTES_TEXT), ['em0'])
        self.assertEqual(live.parse_default_devs('no header here\ndefault 192.0.2.1 UGS 1 1500 em0\n'), [])

    def test_wan_behind_an_isp_router(self):
        # Double NAT: the address rule alone would call 192.168.0.0/24 local
        t = topology(DOUBLE_NAT_IFCONFIG)
        self.assertEqual((t.upstream_devs, t.upstream_addrs), (['em0'], {'192.168.0.2'}))
        self.assertFalse(t.is_local('192.168.0.2'))
        self.assertTrue(t.is_local('192.168.1.10'))
        # without the route, only the address rule is left - and it finds nothing
        self.assertEqual(topology(DOUBLE_NAT_IFCONFIG, routes='').upstream_devs, [])

    def test_ifinfo(self):
        self.assertEqual(live.parse_ifinfo(IFINFO_TEXT),
                         {'em0': {'rx': 1500000, 'tx': 40000, 'rxp': 1000, 'txp': 500, 'ether': True}})

    def test_ifinfo_skips_incomplete_interfaces(self):
        self.assertEqual(live.parse_ifinfo('Interface em1 (em1):\n\ttype: Ethernet\n'), {})


class Deltas(unittest.TestCase):
    @staticmethod
    def st(b0, b1, age):
        return {'b0': b0, 'b1': b1, 'age': age}

    def test_cases(self):
        prev = {'seen': self.st(100, 1000, 10), 'gone': self.st(5, 5, 10), 'idle': self.st(3, 3, 10),
                'reused': self.st(500, 500, 100), 'reused_old': self.st(500, 500, 100)}
        cur = {'seen': self.st(150, 1600, 11), 'idle': self.st(3, 3, 11),
               'new_young': self.st(10, 20, 1), 'new_old': self.st(999, 999, 50),
               'reused': self.st(5, 7, 1), 'reused_old': self.st(5, 7, 100)}
        names = {id(v): k for k, v in cur.items()}
        got = {names[id(s)]: (d0, d1) for s, d0, d1 in live.deltas(prev, cur, 1.0)}
        self.assertEqual(got, {'seen': (50, 600), 'new_young': (10, 20), 'reused': (5, 7)})


class Credits(unittest.TestCase):
    def setUp(self):
        self.states, _ = live.parse_states(STATES_TEXT)
        self.topo = topology()

    def credit(self, n):
        s = self.states[sid(n)]
        return live.credits(s, s['b0'], s['b1'], self.topo)

    def test_rules(self):
        cases = {
            # outbound NAT: in state credits 'all', out state credits 'inet'; b1 is download
            0x01: [('all', '192.168.1.10', '203.0.113.7', 443, 500000, 10000)],
            0x02: [('inet', '192.168.1.10', '203.0.113.7', 443, 500000, 10000)],
            # port-forward: remote initiator, so down/up swap (b0 is what reaches the server)
            0x03: [('inet', '192.168.1.80', '192.0.2.50', 443, 3000, 90000),
                   ('all', '192.168.1.80', '192.0.2.50', 443, 3000, 90000)],
            0x04: [],
            # DNS redirect to a local resolver: local source, so never internet
            0x05: [('all', '192.168.20.5', '192.168.1.53', 53, 360, 120),
                   ('all', '192.168.1.53', '192.168.20.5', 53, 120, 360)],
            0x06: [],
            # cross-VLAN: both ends credited once, from the ingress state only
            0x07: [('all', '192.168.1.20', '192.168.20.5', 554, 4000000, 50000),
                   ('all', '192.168.20.5', '192.168.1.20', 554, 50000, 4000000)],
            0x08: [],
            # the firewall's own upstream traffic: drift check only
            0x09: [('fw', None, None, 53, 200, 60)],
            0x0a: [('fw', None, None, 51820, 60000, 70000)],
            0x0d: [('fw', None, None, 123, 76, 76)],
            # a WireGuard client is NAT'd like any device
            0x0b: [('all', '10.0.0.2', '203.0.113.9', 443, 80000, 2000)],
            0x0c: [('inet', '10.0.0.2', '203.0.113.9', 443, 80000, 2000)],
            # traffic to the firewall's own LAN address credits the client only
            0x0e: [('all', '192.168.1.10', '192.168.1.1', 53, 300, 70)],
            0x0f: [],                             # IPv6: never attributed
            0x10: [('all', '192.168.1.10', '203.0.113.7', 0, 84, 84)],
            # reflection: local client to a forwarded port is local traffic
            0x11: [('all', '192.168.1.10', '192.168.1.80', 443, 900, 700),
                   ('all', '192.168.1.80', '192.168.1.10', 443, 700, 900)],
        }
        for n, want in cases.items():
            with self.subTest(state=hex(n)):
                self.assertEqual(self.credit(n), want)

    def test_double_nat_still_credits_internet(self):
        topo = topology(DOUBLE_NAT_IFCONFIG)
        s = live.parse_header('all tcp 192.168.0.2:60000 (192.168.1.10:50000) -> 203.0.113.7:443       '
                              'ESTABLISHED:ESTABLISHED')
        self.assertEqual(live.credits(s, 10, 20, topo), [('inet', '192.168.1.10', '203.0.113.7', 443, 20, 10)])

    def test_internet_rules_need_no_wan_address(self):
        # A DHCP change on the WAN must not break attribution: drop the upstream
        # address and the device credits stay the same (only 'fw' rows need it).
        self.topo.upstream_addrs = set()
        self.assertEqual(self.credit(0x02), [('inet', '192.168.1.10', '203.0.113.7', 443, 500000, 10000)])
        self.assertEqual(self.credit(0x03)[0], ('inet', '192.168.1.80', '192.0.2.50', 443, 3000, 90000))


class Aggregate(unittest.TestCase):
    def test_everything_new_over_one_second(self):
        states, _ = live.parse_states(STATES_TEXT)
        topo = topology()
        rows = []
        for s in states.values():
            rows.extend(live.credits(s, s['b0'], s['b1'], topo))
        devices, fw = live.aggregate(rows, 1.0, topo)
        self.assertEqual(fw, [60276, 70136])
        self.assertEqual(sorted(devices), ['10.0.0.2', '192.168.1.10', '192.168.1.20', '192.168.1.53',
                                           '192.168.1.80', '192.168.20.5'])
        d = devices['192.168.1.10']
        self.assertEqual(d['all'], [4017552, 94112])
        self.assertEqual(d['inet'], [4000000, 80000])
        self.assertEqual(d['peers'], [['203.0.113.7', 4000672, 80672, 1], ['192.0.2.77', 7200, 7200, 1],
                                      ['192.168.1.80', 7200, 5600, 0], ['192.168.1.1', 2400, 560, 0],
                                      ['203.0.113.8', 80, 80, 1]])
        self.assertEqual(d['ports'], {'all': [[443, 4007200, 85600], [3478, 7200, 7200], [53, 2480, 640],
                                              [0, 672, 672]],
                                      'inet': [[443, 4000000, 80000]]})
        self.assertEqual(devices['192.168.1.80']['inet'], [24000, 720000])
        self.assertEqual(devices['192.168.20.5'], {
            'all': [402880, 32000960], 'inet': [0, 0],
            'peers': [['192.168.1.20', 400000, 32000000, 0], ['192.168.1.53', 2880, 960, 0]],
            'ports': {'all': [[554, 400000, 32000000], [53, 2880, 960]], 'inet': []}})
        self.assertEqual(devices['10.0.0.2']['inet'], [640000, 16000])

    def test_peer_union_keeps_internet_peers_of_a_local_heavy_device(self):
        topo = topology()
        rows = [('all', '192.168.1.20', '192.168.20.%d' % i, 554, 1000 * (i + 1), 0) for i in range(12)]
        rows.append(('all', '192.168.1.20', '203.0.113.1', 443, 1, 0))
        devices, _ = live.aggregate(rows, 1.0, topo)
        peers = [p[0] for p in devices['192.168.1.20']['peers']]
        self.assertEqual(len(peers), live.TOP_PEERS + 1)
        self.assertIn('203.0.113.1', peers)
        self.assertNotIn('192.168.20.0', peers)   # the smallest local peer falls off


class WanRates(unittest.TestCase):
    def test_rates_and_headers(self):
        prev = {'em0': {'rx': 1000000, 'tx': 20000, 'rxp': 600, 'txp': 300, 'ether': True}}
        cur = live.parse_ifinfo(IFINFO_TEXT)
        down, up, sample = live.wan_rates(prev, cur, ['em0'], 2.0)
        self.assertEqual((down, up), (2000000, 80000))
        self.assertEqual(sample, {'rx': 500000, 'tx': 20000, 'hdr_rx': 5600, 'hdr_tx': 2800})

    def test_counter_reset_counts_as_zero(self):
        prev = {'em0': {'rx': 9000000, 'tx': 9000000, 'rxp': 9000, 'txp': 9000, 'ether': True}}
        down, up, sample = live.wan_rates(prev, live.parse_ifinfo(IFINFO_TEXT), ['em0'], 1.0)
        self.assertEqual((down, up), (0, 0))
        self.assertEqual(sample, {'rx': 0, 'tx': 0, 'hdr_rx': 0, 'hdr_tx': 0})


