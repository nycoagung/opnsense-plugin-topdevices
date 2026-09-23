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


if __name__ == '__main__':
    unittest.main()
