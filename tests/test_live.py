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


