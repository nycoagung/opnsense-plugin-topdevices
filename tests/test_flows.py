"""Unit tests for the raw flow log reader (scripts/topdevices/flows.py).
Standard library only; no OPNsense needed.
Run:  python3 -m unittest discover -s tests -v
FLOWS_PY=<path> points the suite at another copy of flows.py (used by mutate.py).

Class CoreParity compares with core's own parser and aggregators. It runs when
core's NetFlow library is found: CORE_NETFLOW=<core>/src/opnsense/scripts/netflow,
or OPNSENSE_CORE=<checkout>, or on the firewall. To fetch it:

  git clone -q --depth 1 --branch 26.7.4 --filter=blob:none --sparse \\
      https://github.com/opnsense/core.git /tmp/core
  git -C /tmp/core sparse-checkout set src/opnsense/scripts/netflow
  OPNSENSE_CORE=/tmp/core python3 -m unittest discover -s tests -v
"""
import contextlib
import datetime
import importlib.util
import io
import ipaddress
import json
import os
import pathlib
import random
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
import warnings

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / 'src/opnsense/scripts/topdevices'
FLOWS_PY = os.environ.get('FLOWS_PY') or str(SRC / 'flows.py')
LIVE_PY = os.environ.get('LIVE_PY') or str(SRC / 'live.py')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module          # flows imports live; forked workers unpickle flows.*
    spec.loader.exec_module(module)
    return module


live = _load('live', LIVE_PY)
flows = _load('flows', FLOWS_PY)

# core's lib/flowparser.py formats, to write records exactly as flowd does
FMT = {'tag': 'I', 'recv_time': '>II', 'proto_flags_tos': 'BBBB', 'srcdst_port': '>HH', 'packets': '>Q',
       'octets': '>Q', 'if_indices': '>II', 'agent_info': '>IIIHH', 'flow_times': '>II',
       'as_info': 'IIBBH', 'flow_engine_info': 'HHII'}
T0 = 1_790_000_000               # Mon 21 Sep 2026 12:53:20 UTC
UPTIME0 = 500_000_000            # the exporter's uptime (ms) at T0


def IP(text):
    return int(ipaddress.IPv4Address(text))


def record(**fields):
    """One flowd record: the header, then the given fields in core's order."""
    mask, body = 0, b''
    for i, (name, _) in enumerate(flows.FIELDS):
        if name in fields:
            mask |= 1 << i
            v = fields[name]
            body += v if isinstance(v, bytes) else struct.pack(FMT[name], *(v if isinstance(v, tuple) else (v,)))
    return struct.pack('BBHI', 2, len(body) // 4, 0, socket.htonl(mask)) + body


def flow(src, dst, octets, recv, start, end, if_in=2, if_out=1, ports=(40000, 443), omit=(), v6=False):
    """A record of `octets` for a flow from `start` to `end` (epoch seconds),
    received at `recv` (whole seconds). The exporter's clock is UPTIME0 at T0."""
    up = UPTIME0 + int(round((recv - T0) * 1000))
    fin = up - int(round((recv - end) * 1000))
    beg = fin - int(round((end - start) * 1000))
    f = {'tag': 0, 'recv_time': (int(recv), 0), 'proto_flags_tos': (0, 6, 0, 0),
         'agent_addr4': socket.inet_aton('127.0.0.1'), 'src_addr4': socket.inet_aton(src),
         'dst_addr4': socket.inet_aton(dst), 'gateway_addr4': bytes(4), 'srcdst_port': ports,
         'packets': 10, 'octets': octets, 'if_indices': (if_in, if_out),
         'agent_info': (up, int(recv), 0, 5, 0), 'flow_times': (beg, fin),
         'as_info': (0, 0, 0, 0, 0), 'flow_engine_info': (0, 0, 0, 0)}
    if v6:
        for k in ('agent_addr4', 'src_addr4', 'dst_addr4', 'gateway_addr4'):
            del f[k]
        f['src_addr6'], f['dst_addr6'] = b'\x20\x01' + bytes(14), b'\xfd\x00' + bytes(14)
    for k in omit:
        f.pop(k, None)
    return record(**f)


class Decode(unittest.TestCase):
    def test_an_ipv4_record_as_core_reads_it(self):
        buf = flow('8.8.8.8', '192.168.1.10', 5000, recv=T0 + 60, start=T0, end=T0 + 50, if_in=1, if_out=2)
        self.assertEqual(list(flows.records(buf, ports=True)),
                         [(T0 + 60, IP('8.8.8.8'), IP('192.168.1.10'), 40000, 443, 5000, 1, 2,
                           float(T0), float(T0 + 50), 50000)])

    def test_ports_are_read_only_when_asked(self):
        buf = flow('8.8.8.8', '192.168.1.10', 5000, recv=T0 + 60, start=T0, end=T0 + 50)
        self.assertEqual(list(flows.records(buf))[0][3:5], (0, 0))

    def test_records_core_skips_are_skipped(self):
        buf = b''.join([
            flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0, omit=('packets',)),
            flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0, omit=('agent_info',)),
            flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0 + 5),      # finished after its export
            flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0, v6=True),
        ])
        self.assertEqual(list(flows.records(buf)), [])

    def test_missing_interfaces_and_times_read_as_core_defaults_them(self):
        buf = flow('8.8.8.8', '192.168.1.10', 7, recv=T0 + 9, start=T0, end=T0 + 9,
                   omit=('if_indices', 'flow_times', 'srcdst_port'))
        self.assertEqual(list(flows.records(buf, ports=True)),
                         [(T0 + 9, IP('8.8.8.8'), IP('192.168.1.10'), 0, 0, 7, -1, -1,
                           float(T0 + 9), float(T0 + 9), 0)])

    def test_a_record_still_being_written_ends_the_read(self):
        buf = flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0) * 2
        self.assertEqual(len(list(flows.records(buf[:-5]))), 1)


class Spread(unittest.TestCase):
    """A record's bytes inside a span: core's add() arithmetic, to the byte."""

    def test_a_record_is_spread_evenly_over_its_duration(self):
        self.assertEqual(flows.share(100.0, 200.0, 100000, 1000, 150, 400), 500.0)
        self.assertEqual(flows.share(100.0, 200.0, 100000, 1000, 0, 400), 1000.0)
        self.assertEqual(flows.share(100.0, 200.0, 100000, 1000, 200, 400), 0.0)

    def test_the_expression_and_its_order_are_cores(self):
        # (part of the span) / duration * octets, as lib/aggregates/__init__.py add();
        # octets * part / duration gives ...65 here
        fs, fe, dur, octets = 1790000140.891, 1790001334.599, 1193708, 820096793
        self.assertEqual(flows.share(fs, fe, dur, octets, 1790001007, 1790004353), 225065836.26082462)

    def test_zero_duration_counts_whole_where_it_starts(self):
        self.assertEqual(flows.share(150.0, 150.0, 0, 42, 100, 200), 42)
        self.assertEqual(flows.share(200.0, 200.0, 0, 42, 100, 200), 0.0)

    def test_negative_duration_counts_as_cores_arithmetic_has_it(self):
        self.assertEqual(flows.share(150.003, 150.0, -3, 42, 100, 200), (150.0 - 150.003) / (-3 / 1000.0) * 42)
        self.assertEqual(flows.share(250.003, 250.0, -3, 42, 100, 200), 0.0)


def utc(t):
    """A bucket's start as core's aggregates return it: a naive UTC datetime."""
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).replace(tzinfo=None)


LAN = ipaddress.ip_network('192.168.1.0/24')
IOT = ipaddress.ip_network('192.168.20.0/24')
NET = flows.Net([LAN, IOT], [1], ['em0'])       # upstream: interface 1, em0


class Coverage(unittest.TestCase):
    """What covers which part of a range (spec §4)."""

    def test_a_range_inside_the_log_is_the_log_alone(self):
        self.assertEqual(flows.plan(T0 + 100, T0 + 200, T0 + 50),
                         {'raw_all': (T0 + 100, T0 + 200), 'hourly': None, 'inet': (T0 + 100, T0 + 200)})

    def test_before_the_log_all_traffic_is_filled_from_hourly_records_up_to_the_next_hour(self):
        # the log complete from 22:06:16 AEST; the next whole hour is 23:00 AEST
        L, seam, frm, to = 1790082376, 1790085600, 1790068194, 1790154594
        self.assertEqual(flows.plan(frm, to, L),
                         {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (L, to)})

    def test_a_log_shorter_than_its_first_hour_leaves_all_traffic_to_the_hourly_records(self):
        frm, to, L = 1790000000, 1790007200, 1790007000     # the next hour, 1790010000, is after to
        self.assertEqual(flows.plan(frm, to, L), {'raw_all': (to, to), 'hourly': (frm, to), 'inet': (L, to)})

    def test_a_range_wholly_before_the_log_has_no_internet_only_span(self):
        frm, to, L = 1790000000, 1790000600, 1790000900
        p = flows.plan(frm, to, L)
        self.assertEqual(p['inet'], (L, to))
        self.assertGreaterEqual(p['inet'][0], p['inet'][1])

    def test_hourly_buckets_are_weighted_by_their_overlap(self):
        b = 1790085600
        now = b + 5 * 3600
        self.assertEqual(flows.hourly_weight(b, b - 100, b + 3600, now), 1.0)
        self.assertEqual(flows.hourly_weight(b, b + 900, b + 3600, now), 0.75)
        self.assertEqual(flows.hourly_weight(b, b + 3600, b + 7200, now), 0.0)

    def test_a_bucket_in_progress_counts_for_the_part_recorded(self):
        b = 1790085600
        self.assertEqual(flows.hourly_weight(b, b, b + 3600, now=b + 1800), 1.0)
        self.assertEqual(flows.hourly_weight(b, b + 900, b + 3600, now=b + 1800), 0.5)


class Devices(unittest.TestCase):
    def test_a_device_is_in_a_local_subnet_and_not_its_broadcast(self):
        self.assertTrue(NET.is_device(IP('192.168.1.10')))
        self.assertTrue(NET.is_device(IP('192.168.1.254')))    # the firewall's own address: a table row
        self.assertTrue(NET.is_device(IP('192.168.20.5')))
        self.assertFalse(NET.is_device(IP('192.168.1.255')))
        self.assertFalse(NET.is_device(IP('192.168.2.10')))
        self.assertFalse(NET.is_device(IP('8.8.8.8')))
        self.assertEqual((NET.upstream, NET.upstream_names), (frozenset({1}), ['em0']))


class Hourly(unittest.TestCase):
    def test_hourly_rows_become_device_download_and_upload(self):
        b = 1790085600
        rows = [{'start_time': utc(b), 'src_addr': '192.168.1.10', 'direction': 'out', 'octets': 4000.0},
                {'start_time': utc(b), 'src_addr': '192.168.1.10', 'direction': 'in', 'octets': 1000.0},
                {'start_time': utc(b + 3600), 'src_addr': '192.168.1.10', 'direction': 'out', 'octets': 800.0},
                {'start_time': utc(b), 'src_addr': '8.8.8.8', 'direction': 'in', 'octets': 9.0},
                {'start_time': utc(b), 'src_addr': '192.168.1.255', 'direction': 'out', 'octets': 9.0},
                {'start_time': utc(b), 'src_addr': 'fd00::1', 'direction': 'out', 'octets': 9.0}]
        got = flows.hourly_fill(rows, b + 900, b + 3600 + 1800, now=b + 7 * 3600, net=NET)
        # a totals row holds one address: 'out' is bytes delivered to it, 'in' bytes it sent
        self.assertEqual(got, {IP('192.168.1.10'): [4000 * 0.75 + 800 * 0.5, 1000 * 0.75]})


IFNAME = {1: 'em0', 2: 'ue0', 3: 'vlan01'}      # the interface numbers the synthetic logs use


def write_log(dirpath, files):
    """Write flow log files [(name, bytes, mtime)] into dirpath; the log's path."""
    for name, data, mtime in files:
        p = os.path.join(dirpath, name)
        with open(p, 'wb') as f:
            f.write(data)
        os.utime(p, (mtime, mtime))
    return os.path.join(dirpath, 'flowd.log')


def synthetic_log(n, t0, rnd):
    """n records of the kinds core meets: downloads, uploads, local traffic across
    networks, broadcasts, IPv6, missing fields, zero, negative and impossible durations."""
    lan = ['192.168.1.%d' % i for i in range(10, 30)] + ['192.168.1.255', '192.168.1.254']
    iot = ['192.168.20.%d' % i for i in range(100, 110)]
    out = []
    for i in range(n):
        recv = t0 + i // 25                               # 25 records a second
        kind = rnd.random()
        if kind < 0.45:
            src, dst, ifs = '8.%d.1.1' % rnd.randrange(256), rnd.choice(lan + iot), (1, 2)
        elif kind < 0.8:
            src, dst, ifs = rnd.choice(lan + iot), '1.%d.1.1' % rnd.randrange(256), (2, 1)
        else:
            src, dst, ifs = rnd.choice(lan), rnd.choice(iot), (2, 3)
        end = recv - rnd.randrange(0, 5)
        r = rnd.random()
        dur = 0 if r < 0.1 else (-0.003 if r < 0.11 else rnd.randrange(1, 1800))
        v, kw = rnd.random(), {}
        if v < 0.03:
            kw['v6'] = True
        elif v < 0.08:
            kw['omit'] = ('if_indices',)
        elif v < 0.12:
            kw['omit'] = ('flow_times',)
        elif v < 0.13:
            kw['omit'] = ('packets',)
        elif v < 0.14:
            end = recv + 3                                # finished after its export: core skips
        out.append(flow(src, dst, rnd.randrange(40, 10_000_000), recv=recv, start=end - dur, end=end,
                        if_in=ifs[0], if_out=ifs[1], ports=(rnd.randrange(1024, 65535), rnd.choice((443, 53, 0))), **kw))
    return b''.join(out)


# one of each kind of flow, all inside [T0, T0 + 100)
KINDS = [flow('8.8.8.8', '192.168.1.10', 1000, recv=T0 + 100, start=T0 + 10, end=T0 + 90, if_in=1, if_out=2),   # download
         flow('192.168.1.10', '1.1.1.1', 300, recv=T0 + 100, start=T0 + 10, end=T0 + 90, if_in=2, if_out=1),    # upload
         flow('192.168.1.10', '192.168.20.5', 50, recv=T0 + 100, start=T0 + 10, end=T0 + 90, if_in=2, if_out=3),  # local
         flow('192.168.1.10', '192.168.1.255', 7, recv=T0 + 100, start=T0 + 10, end=T0 + 90, if_in=2, if_out=2)]  # broadcast
# a first record received at T0, so a log holding it is complete from T0 (flows.log_from)
EARLY = flow('8.8.8.8', '192.168.1.11', 1, recv=T0, start=T0, end=T0, if_in=1, if_out=2)


class Reading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def opened(self, files):
        opened = flows.open_log(write_log(self.tmp.name, files))
        self.addCleanup(flows.close_log, opened)
        return opened

    def test_files_are_taken_in_the_order_they_were_written(self):
        opened = self.opened([('flowd.log', b'c', T0 + 300), ('flowd.log.000002', b'a', T0 + 100),
                              ('flowd.log.000001', b'bb', T0 + 200)])
        self.assertEqual([flows._read(fd) for fd, _, _ in opened], [b'a', b'bb', b'c'])

    def test_the_log_is_complete_from_its_oldest_ipv4_record(self):
        oldest = flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0, v6=True) + \
                 flow('8.8.8.8', '192.168.1.10', 1, recv=T0 + 5, start=T0, end=T0 + 5)
        opened = self.opened([('flowd.log.000001', oldest, T0 + 60),
                              ('flowd.log', flow('8.8.8.8', '192.168.1.10', 1, recv=T0 + 70, start=T0, end=T0), T0 + 90)])
        self.assertEqual(flows.log_from(opened), T0 + 5)

    def test_an_empty_log_has_no_start(self):
        self.assertIsNone(flows.log_from(self.opened([('flowd.log', b'', T0)])))

    def test_only_files_written_since_the_span_began_are_read_biggest_first(self):
        opened = self.opened([('flowd.log.000002', b'aa', T0 + 100), ('flowd.log.000001', b'b', T0 + 200),
                              ('flowd.log', b'ccc', T0 + 300)])
        self.assertEqual([size for _, _, size in flows.files_for(opened, T0 + 150)], [3, 1])

    def test_a_rotation_while_reading_mixes_nothing_up(self):
        log = write_log(self.tmp.name, [('flowd.log.000001', EARLY + KINDS[0] + KINDS[1], T0 + 100),
                                        ('flowd.log', KINDS[2], T0 + 200)])
        before = flows.open_log(log)
        self.addCleanup(flows.close_log, before)
        expected = flows.answer_totals(T0, T0 + 100, T0 + 300, before, NET, lambda lo, hi: [], 1)['devices']
        self.assertEqual(set(expected), {'192.168.1.10', '192.168.1.11', '192.168.20.5'})
        # core rotates: every file moves one number up, a new flowd.log begins
        os.rename(log + '.000001', log + '.000002')
        os.rename(log, log + '.000001')
        write_log(self.tmp.name, [('flowd.log', KINDS[3], T0 + 250)])
        self.assertEqual(flows.answer_totals(T0, T0 + 100, T0 + 300, before, NET, lambda lo, hi: [], 1)['devices'],
                         expected)

    def test_a_rotation_while_the_files_are_opened_reads_each_once(self):
        log = write_log(self.tmp.name, [('flowd.log.000001', EARLY + KINDS[0], T0 + 100),
                                        ('flowd.log', KINDS[1], T0 + 200)])
        real_open, rotated = os.open, []

        def open_after_a_rotation(path, *args, **kwargs):
            if not rotated:                       # core rotates between the listing and the first open
                rotated.append(path)
                os.rename(log + '.000001', log + '.000002')
                os.rename(log, log + '.000001')
                write_log(self.tmp.name, [('flowd.log', KINDS[2], T0 + 300)])
            return real_open(path, *args, **kwargs)

        with unittest.mock.patch('os.open', open_after_a_rotation):
            opened = flows.open_log(log)
        self.addCleanup(flows.close_log, opened)
        self.assertEqual([flows._read(fd) for fd, _, _ in opened], [EARLY + KINDS[0], KINDS[1], KINDS[2]])

    def test_a_log_that_never_settles_is_an_error(self):
        log = write_log(self.tmp.name, [('flowd.log', KINDS[0], T0 + 100)])
        with unittest.mock.patch.object(flows, '_files_now', lambda log: {}), \
                self.assertRaisesRegex(RuntimeError, 'kept rotating'):
            flows.open_log(log)

    def test_a_file_that_cannot_be_opened_is_an_error_not_a_gap(self):
        log = write_log(self.tmp.name, [('flowd.log.000001', EARLY, T0 + 100), ('flowd.log', KINDS[0], T0 + 200)])
        real_open = os.open

        def refuse_one(path, *args, **kwargs):
            if path.endswith('.000001'):
                raise PermissionError(13, 'Permission denied', path)
            return real_open(path, *args, **kwargs)

        with unittest.mock.patch('os.open', refuse_one), self.assertRaises(PermissionError):
            flows.open_log(log)


class Attribution(unittest.TestCase):
    def scan(self, data, a, i):
        with tempfile.TemporaryDirectory() as d:
            opened = flows.open_log(write_log(d, [('flowd.log', data, T0 + 100)]))
            try:
                return flows.scan_totals((opened[0][0], a[0], a[1], i[0], i[1], NET))
            finally:
                flows.close_log(opened)

    def test_downloads_uploads_local_traffic_and_broadcasts(self):
        got = self.scan(b''.join(KINDS), (T0, T0 + 100), (T0, T0 + 100))
        self.assertEqual(got, {IP('192.168.1.10'): [1000.0, 357.0, 1000.0, 300.0],
                               IP('192.168.20.5'): [50.0, 0.0, 0.0, 0.0]})

    def test_the_two_scopes_count_over_their_own_spans(self):
        got = self.scan(KINDS[0], (T0, T0 + 50), (T0 + 50, T0 + 100))   # the flow runs T0+10 .. T0+90
        self.assertEqual(got, {IP('192.168.1.10'): [500.0, 0.0, 500.0, 0.0]})

    def test_a_record_received_before_the_span_is_skipped_and_one_straddling_it_is_split(self):
        early = flow('8.8.8.8', '192.168.1.10', 9999, recv=T0 + 40, start=T0, end=T0 + 40, if_in=1)
        across = flow('8.8.8.8', '192.168.1.10', 1000, recv=T0 + 60, start=T0 + 40, end=T0 + 60, if_in=1)
        self.assertEqual(self.scan(early + across, (T0 + 50, T0 + 100), (T0 + 50, T0 + 100)),
                         {IP('192.168.1.10'): [500.0, 0.0, 500.0, 0.0]})


class Totals(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        rnd = random.Random(3)
        files = [('flowd.log.%06d' % (4 - i) if i < 4 else 'flowd.log',
                  synthetic_log(4000, T0 + i * 200, rnd), T0 + i * 200 + 200) for i in range(5)]
        self.opened = flows.open_log(write_log(self.tmp.name, files))
        self.addCleanup(flows.close_log, self.opened)

    def test_three_workers_give_exactly_what_one_gives(self):
        jobs = [(fd, T0, T0 + 1000, T0, T0 + 1000, NET) for fd, _, _ in flows.files_for(self.opened, T0)]
        one = flows.merge_totals(flows.run_parallel(flows.scan_totals, jobs, 1))
        three = flows.merge_totals(flows.run_parallel(flows.scan_totals, jobs, 3))
        self.assertEqual(set(one), set(three))
        for ip in one:
            for k in range(4):
                self.assertAlmostEqual(one[ip][k], three[ip][k], delta=1e-6 * max(1.0, one[ip][k]))

    def test_the_answer_inside_the_log(self):
        L = flows.log_from(self.opened)
        a = flows.answer_totals(L + 100, L + 700, L + 900, self.opened, NET, lambda lo, hi: self.fail('no fill-in'), 2)
        self.assertEqual((a['all'], a['inet'], a['wan'], a['log_from']),
                         ({'from': L + 100, 'to': L + 700, 'hourly_until': None}, {'from': L + 100, 'to': L + 700},
                          ['em0'], L))
        self.assertTrue(a['devices'])
        for ip, v in a['devices'].items():
            ipaddress.IPv4Address(ip)
            self.assertTrue(all(isinstance(x, int) and x >= 0 for x in v), v)

    def test_before_the_log_all_traffic_adds_the_hourly_records_and_internet_only_starts_at_the_log(self):
        L = flows.log_from(self.opened)
        seam = -(-L // 3600) * 3600
        frm, to, now = L - 7200, seam + 600, seam + 900
        asked = []
        rows = [{'start_time': utc(seam - 3600), 'src_addr': '192.168.1.200', 'direction': 'out', 'octets': 3600.0}]
        a = flows.answer_totals(frm, to, now, self.opened, NET, lambda lo, hi: asked.append((lo, hi)) or rows, 1)
        self.assertEqual(asked, [(frm, seam)])
        self.assertEqual(a['all']['hourly_until'], seam)
        self.assertEqual(a['inet'], {'from': L, 'to': to})
        self.assertEqual(a['devices']['192.168.1.200'], [3600, 0, 0, 0])   # a device only the hourly records have

    def test_all_traffic_is_the_hourly_records_before_the_seam_and_the_log_after_it(self):
        # a log complete from T0 (EARLY) that reaches past its first whole hour, the seam
        seam = -(-T0 // 3600) * 3600
        before = flow('8.8.8.8', '192.168.1.10', 500, recv=T0 + 200, start=T0 + 100, end=T0 + 200, if_in=1)
        after = flow('8.8.8.8', '192.168.1.10', 1000, recv=seam + 200, start=seam + 100, end=seam + 200, if_in=1)
        with tempfile.TemporaryDirectory() as d:
            opened = flows.open_log(write_log(d, [('flowd.log', EARLY + before + after, seam + 300)]))
            self.addCleanup(flows.close_log, opened)
            rows = [{'start_time': utc(seam - 3600), 'src_addr': '192.168.1.10', 'direction': 'out', 'octets': 3600.0}]
            a = flows.answer_totals(T0 - 7200, seam + 600, seam + 900, opened, NET, lambda lo, hi: rows, 1)
        # all traffic: the hourly record before the seam, plus the log after it;
        # internet only: the log from where it is complete (T0)
        self.assertEqual(a['devices'], {'192.168.1.10': [3600 + 1000, 0, 500 + 1000, 0],
                                        '192.168.1.11': [0, 0, 1, 0]})


CORE_NETFLOW = next((p for p in (os.environ.get('CORE_NETFLOW'),
                                 os.path.join(os.environ.get('OPNSENSE_CORE', '/nonexistent'), 'src/opnsense/scripts/netflow'),
                                 flows.NETFLOW_LIB)
                     if p and os.path.isfile(os.path.join(p, 'lib', 'flowparser.py'))), None)


@unittest.skipUnless(CORE_NETFLOW, 'set CORE_NETFLOW or OPNSENSE_CORE (see the module docstring), or run on the firewall')
class CoreParity(unittest.TestCase):
    """Against core's own parser and aggregators (lib/flowparser.py, lib/aggregates)."""

    @classmethod
    def setUpClass(cls):
        cls.enterClassContext(warnings.catch_warnings())
        warnings.filterwarnings('ignore', category=DeprecationWarning, module=r'lib\.')   # core's own code, Python >= 3.12
        sys.path.insert(0, CORE_NETFLOW)
        from lib.flowparser import FlowParser
        from lib.aggregates.source import FlowSourceAddrDetails, FlowSourceAddrTotals
        cls.tmp = tempfile.TemporaryDirectory()
        cls.log = write_log(cls.tmp.name, [('flowd.log', synthetic_log(60_000, T0, random.Random(7)), T0 + 2500)])
        cls.core = [r for r in FlowParser(cls.log)]
        details = FlowSourceAddrDetails(300, cls.tmp.name)
        totals = FlowSourceAddrTotals(3600, cls.tmp.name)
        for r in cls.core:
            if 'src_addr4' in r and 'dst_addr4' in r:
                r['if_in'] = IFNAME.get(r['if_ndx_in'], str(r['if_ndx_in']))
                r['if_out'] = IFNAME.get(r['if_ndx_out'], str(r['if_ndx_out']))
                details.add(dict(r))
                totals.add(dict(r))
        details.commit()
        totals.commit()
        cls.details, cls.totals = details, totals
        cls.opened = flows.open_log(cls.log)

    @classmethod
    def tearDownClass(cls):
        flows.close_log(cls.opened)
        cls.tmp.cleanup()

    def test_records_match_cores_parser(self):
        core = [(r['recv_sec'], IP(r['src_addr']), IP(r['dst_addr']), r['octets'], r['if_ndx_in'],
                 r['if_ndx_out'], r['flow_start'], r['flow_end']) for r in self.core if 'src_addr4' in r]
        ours = [r[:3] + r[5:10] for r in flows.records(flows._read(self.opened[0][0]))]
        self.assertEqual(ours, core)

    def test_totals_match_cores_aggregator(self):
        a0 = -(-T0 // 300) * 300                  # core's buckets start on 5-minute boundaries
        for lo, hi in ((a0 + 600, a0 + 1800), (a0 + 300, a0 + 2100), (a0 - 3600, a0 + 7200)):
            core = {}
            for row in self.details.get_data(lo, hi):
                ip = IP(row['dst_addr'])
                if not NET.is_device(ip):
                    continue
                t = core.setdefault(ip, [0.0, 0.0, 0.0, 0.0])
                k = 1 if row['direction'] == 'out' else 0
                t[k] += row['octets']
                if row['if'] == 'em0':
                    t[2 + k] += row['octets']
            ours = flows.scan_totals((self.opened[0][0], lo, hi, lo, hi, NET))
            self.assertEqual(set(ours), set(core))
            for ip in core:
                for k in range(4):
                    self.assertAlmostEqual(ours[ip][k], core[ip][k], delta=1e-3, msg=(lo, hi, ip, k))

    def test_hourly_fill_reads_cores_totals_the_right_way_round(self):
        h = (T0 // 3600) * 3600
        fill = flows.hourly_fill(list(self.totals.get_data(h, h + 3600)), h, h + 3600, h + 7200, NET)
        raw = flows.scan_totals((self.opened[0][0], h, h + 3600, h, h + 3600, NET))
        self.assertEqual(set(fill), {ip for ip, v in raw.items() if v[0] or v[1]})
        for ip, (down, up) in fill.items():
            self.assertAlmostEqual(down, raw[ip][0], delta=1e-3)
            self.assertAlmostEqual(up, raw[ip][1], delta=1e-3)


if __name__ == '__main__':
    unittest.main()
