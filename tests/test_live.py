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

    def test_subnet_broadcast_is_never_a_device(self):
        # NetBIOS or SSDP to x.x.x.255 makes an ordinary pass state. The NetFlow
        # view already drops such addresses; Live must not list 192.168.1.255
        # as a device either - only the sender is credited.
        s = live.parse_header('all udp 192.168.1.255:137 <- 192.168.1.10:137       SINGLE:NO_TRAFFIC')
        self.assertEqual(live.credits(s, 50, 0, self.topo), [('all', '192.168.1.10', '192.168.1.255', 137, 0, 50)])

    def test_wireguard_from_the_lan_is_not_upstream_traffic(self):
        # A phone on home Wi-Fi reaches the WireGuard listener on the WAN address
        # across the LAN, so the tunnel never touches the WAN. Counted as the
        # firewall's upstream traffic, it made the drift check read upload at
        # 38-55x the WAN counter on the reference install. The phone's LAN
        # address still carries it, in 'all'.
        s = live.parse_header('all udp 198.51.100.2:51820 <- 192.168.1.41:58864       MULTIPLE:MULTIPLE')
        self.assertEqual(live.credits(s, 1000, 42000, self.topo),
                         [('all', '192.168.1.41', '198.51.100.2', 51820, 42000, 1000)])
        # the same tunnel when the firewall sends first (a keepalive to the peer's endpoint)
        s = live.parse_header('all udp 198.51.100.2:51820 -> 192.168.1.41:58864       MULTIPLE:MULTIPLE')
        self.assertEqual(live.credits(s, 900, 30000, self.topo), [])

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


class Throttle(unittest.TestCase):
    def test_ema(self):
        self.assertEqual(live.ema(None, 0.2), 0.2)
        self.assertAlmostEqual(live.ema(0.2, 0.8), 0.4)

    def test_next_interval(self):
        self.assertEqual(live.next_interval(1, None), 1.0)
        self.assertEqual(live.next_interval(1, 0.05), 1.0)
        self.assertAlmostEqual(live.next_interval(1, 0.3), 3.0)
        self.assertEqual(live.next_interval(5, 0.3), 5.0)


class DriftCheck(unittest.TestCase):
    @staticmethod
    def sample(rx=1000000, tx=50000, rxp=800, txp=400, down=988800, up=44400, v6=0):
        wan = {'rx': rx, 'tx': tx, 'hdr_rx': rxp * 14, 'hdr_tx': txp * 14}
        return (1.0, wan, down, up, v6)

    def fill(self, n=60, **kw):
        d = live.Drift()
        for _ in range(n):
            d.add(*self.sample(**kw))
        return d

    def test_needs_a_full_window(self):
        self.assertIsNone(self.fill(59).coverage())

    def test_reconciled(self):
        # 8 Mb/s down is judged; 0.4 Mb/s up is below the minimum and is not
        self.assertEqual(self.fill().coverage(), {'down': 1.0, 'up': None, 'ok': True})

    def test_drift_is_flagged(self):
        self.assertEqual(self.fill(down=494400).coverage(), {'down': 0.5, 'up': None, 'ok': False})

    def test_quiet_link_is_not_judged(self):
        self.assertEqual(self.fill(rx=1000, tx=1000, rxp=1, txp=1, down=0, up=0).coverage(),
                         {'down': None, 'up': None, 'ok': None})

    def test_ipv6_suspends_the_check(self):
        self.assertIsNone(self.fill(v6=20000).coverage())

    def test_window_slides(self):
        d = self.fill(down=494400)
        for _ in range(60):
            d.add(*self.sample())
        self.assertEqual(d.coverage()['ok'], True)
        self.assertEqual(len(d.samples), 60)


class Events(unittest.TestCase):
    def test_output_is_relay_safe(self):
        line = live.format_event({'error': live.safe_text('bad <tag> & more'), 'x': '<&>'})
        self.assertTrue(line.startswith('data: ') and line.endswith('\n\n'))
        self.assertFalse(set('<>&') & set(line))
        self.assertIn('bad', json.loads(line[6:])['error'])


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.cpu = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def pf_text(b0, b1):
    return ('all tcp 203.0.113.7:443 <- 192.168.1.10:50000       ESTABLISHED:ESTABLISHED\n'
            '   age 00:05:00, expires in 23:59:59, 1:1 pkts, %d:%d bytes, rule 12\n'
            '   id: 0000000000000001 creatorid: 0a0a0a0a\n'
            'all tcp 198.51.100.2:60000 (192.168.1.10:50000) -> 203.0.113.7:443       ESTABLISHED:ESTABLISHED\n'
            '   age 00:05:00, expires in 23:59:59, 1:1 pkts, %d:%d bytes, rule 3\n'
            '   id: 0000000000000002 creatorid: 0a0a0a0a\n') % (b0, b1, b0, b1)


def ifinfo_text(rx, tx, rxp, txp):
    return ('Interface em0 (em0):\n\ttype: Ethernet\n\tpackets received: %d\n\tpackets transmitted: %d\n'
            '\tbytes received: %d\n\tbytes transmitted: %d\n') % (rxp, txp, rx, tx)


class Loop(unittest.TestCase):
    def run_loop(self, interval=1, cost=0.001, lifetime=10 ** 6, max_samples=2, fail=None):
        clock = FakeClock()
        chunks = []
        pf = [pf_text(1000, 100000), pf_text(2000, 225000)]
        counters = [ifinfo_text(0, 0, 0, 0), ifinfo_text(126000, 1200, 90, 60)]
        calls = {'ifconfig': 0, 'pf': 0}

        def read_states():
            clock.cpu += cost
            calls['pf'] += 1
            if fail:
                raise fail
            return pf[min(calls['pf'], len(pf)) - 1]

        def read_ifaddrs():
            calls['ifconfig'] += 1
            return IFCONFIG_TEXT

        def read_counters(devs):
            self.assertEqual(devs, ['em0'])
            return counters[min(calls['pf'], len(counters)) - 1]

        rc = live.run_loop(interval, read_states, read_ifaddrs, lambda: ROUTES_TEXT, read_counters, chunks.append,
                           clock=clock, wall=lambda: 1790000000.0, cpu=lambda: clock.cpu,
                           sleep=clock.sleep, lifetime=lifetime, max_samples=max_samples)
        events = [json.loads(c[6:]) for c in chunks if c.startswith('data: ')]
        return rc, chunks, events, calls

    def test_baseline_then_rates(self):
        rc, chunks, events, _ = self.run_loop()
        self.assertEqual(rc, 0)
        self.assertEqual(chunks[0], 'retry: 1000\n\n')
        self.assertNotIn(': keepalive\n\n', chunks)
        self.assertEqual(len(events), 2)
        self.assertEqual((events[0]['dt'], events[0]['devices'], events[0]['error']), (0, {}, None))
        e = events[1]
        self.assertEqual(e['dt'], 1.0)
        self.assertEqual(e['devices']['192.168.1.10']['inet'], [1000000, 8000])
        self.assertEqual(e['devices']['192.168.1.10']['all'], [1000000, 8000])
        self.assertEqual(e['wan'], {'devs': ['em0'], 'down': 1008000, 'up': 9600})
        self.assertEqual((e['states'], e['unparsed'], e['v6_skipped']), (2, 0, 0))
        self.assertEqual((e['interval'], e['effective'], e['throttled']), (1, 1.0, False))
        self.assertEqual(e['v'], live.VERSION)

    def test_throttle_stretches_and_keeps_the_relay_alive(self):
        rc, chunks, events, _ = self.run_loop(cost=0.2)
        self.assertEqual((events[0]['effective'], events[0]['throttled']), (2.0, True))
        first, second = [i for i, c in enumerate(chunks) if c.startswith('data: ')]
        self.assertEqual(chunks[first + 1:second], [': keepalive\n\n'])
        self.assertEqual(events[1]['dt'], 2.0)

    def test_lifetime_ends_the_stream(self):
        rc, _, events, _ = self.run_loop(lifetime=2.5, max_samples=None)
        self.assertEqual((rc, len(events)), (0, 3))

    def test_reader_failure_is_reported_not_raised(self):
        rc, _, events, _ = self.run_loop(fail=RuntimeError('pfctl exited with 1 <boom> & more'))
        self.assertEqual(rc, 0)
        self.assertIn('boom', events[0]['error'])
        self.assertFalse(set('<>&') & set(events[0]['error']))
        self.assertEqual(events[1]['devices'], {})

    def test_failed_interface_read_is_reported(self):
        clock, chunks = FakeClock(), []

        def broken():
            raise OSError('ifconfig <gone>')

        live.run_loop(1, lambda: pf_text(1, 1), broken, lambda: ROUTES_TEXT, lambda devs: '', chunks.append,
                      clock=clock,
                      wall=lambda: 0.0, cpu=lambda: 0.0, sleep=clock.sleep, max_samples=1)
        event = json.loads([c for c in chunks if c.startswith('data: ')][0][6:])
        self.assertIn('interface addresses', event['error'])
        self.assertEqual(event['wan']['devs'], [])

    def test_missing_upstream_is_said_out_loud(self):
        clock, chunks = FakeClock(), []
        live.run_loop(1, lambda: pf_text(1, 1), lambda: DOUBLE_NAT_IFCONFIG, lambda: '', lambda devs: '',
                      chunks.append, clock=clock, wall=lambda: 0.0, cpu=lambda: 0.0, sleep=clock.sleep,
                      max_samples=1)
        event = json.loads([c for c in chunks if c.startswith('data: ')][0][6:])
        self.assertEqual(event['error'], 'no upstream interface found')

    def test_never_silent_longer_than_the_interval_between_samples(self):
        # The web relay drops a stream silent for longer than its timeout; while
        # throttled, keepalives must keep every gap at the requested interval.
        clock, stamps = FakeClock(), []
        cost = {'cpu': 0.0}

        def read_states():
            cost['cpu'] += 0.5                      # 5 s effective interval at 1 s requested
            return pf_text(1000, 1000)

        live.run_loop(1, read_states, lambda: IFCONFIG_TEXT, lambda: ROUTES_TEXT,
                      lambda devs: ifinfo_text(0, 0, 0, 0), lambda chunk: stamps.append(clock.t), clock=clock,
                      wall=lambda: 0.0, cpu=lambda: cost['cpu'], sleep=clock.sleep, max_samples=3)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertTrue(gaps and max(gaps) <= 1.0 + 1e-9, gaps)

    def test_topology_is_refreshed(self):
        _, _, _, calls = self.run_loop(interval=10, lifetime=125, max_samples=None)
        self.assertEqual(calls['ifconfig'], 3)    # start, t+60, t+120


class Main(unittest.TestCase):
    def test_rejects_bad_intervals(self):
        for arg in ('0', '11', '1.5', '', '١'):
            with self.subTest(arg=arg):
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    rc = live.main(['live.py', arg])
                self.assertEqual(rc, 1)
                self.assertIn('interval must be', json.loads(out.getvalue()[6:])['error'])

    def test_exits_quietly_when_the_reader_goes_away(self):
        proc = subprocess.Popen([sys.executable, LIVE_PY, '1'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            first = proc.stdout.readline()
            self.assertEqual(first, b'retry: 1000\n')
            proc.stdout.close()                   # the browser tab closes
            rc = proc.wait(timeout=10)
            err = proc.stderr.read()
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.stderr.close()
        self.assertEqual(rc, 0)
        self.assertEqual(err, b'')


if __name__ == '__main__':
    unittest.main()
