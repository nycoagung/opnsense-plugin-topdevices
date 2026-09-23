# Exact NetFlow Ranges from the Raw Flow Log Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ranges that start within the last 24 hours (Last hour, Today, Last 24 hours,
and recent custom ranges) show each device's exact download and upload for exactly the
window asked for, in both scopes. The figures are read on demand from NetFlow's raw
flow log.

**Architecture:** A new root script, `scripts/topdevices/flows.py`, is run by configd
like `live.py`.

- It decodes `/var/log/flowd.log*` with one precompiled `struct` per record layout.
- It spreads every record over time with core's own arithmetic.
- It fills the part of All traffic that falls before the log from core's hourly
  records.
- It answers with JSON: every device's totals, or one device's peers and ports.

`Api/FlowsController.php` serves it at `/api/topdevices/flows/…` behind the existing
TopDevices privilege and core's Network Insight privilege. The widget reads recent
ranges through it, getting both scopes in one answer. It keeps 0.1.2's export path for
older ranges and as a fallback.

**Tech Stack:**
- Python 3.13 standard library, plus core's NetFlow library on the firewall;
- PHP (OPNsense MVC);
- an ES-module dashboard widget;
- `unittest`, `node:test` and `sh` tests.

**Spec:** `docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md`

## Global Constraints

- Firewall: OPNsense 26.7.4, FreeBSD 15.1, Python 3.13. `flows.py` uses the standard
  library, plus core's NetFlow library at `/usr/local/opnsense/scripts/netflow` for
  interface names and hourly records.
- No cron job, no database, and nothing written on the firewall.
- **Raw-log ranges** are those with `from >= now − 86400 − 300` in the script. The
  widget uses `from >= now − 86400 and from < now`.
- **Spreading** follows core's `add()` exactly:
  - `flow_end = recv − (uptime − finish) / 1000`, and
    `flow_start = flow_end − (finish − start) / 1000`;
  - share = `(min(flow_end, hi) − max(flow_start, lo)) / (duration_ms / 1000) · octets`;
  - zero duration counts whole if `lo ≤ flow_start < hi`;
  - negative duration counts `(flow_end − flow_start) / (duration_ms / 1000) · octets`
    if `lo ≤ flow_start < hi`.
- **Workers:** `max(1, (os.cpu_count() or 2) // 2)`, forked, one file per task,
  largest first.
- **Device:** an IPv4 address in a local subnet (`live.Topology.local_nets`) that is not
  the subnet's broadcast address. The firewall's own addresses are kept.
- **Upstream:** `live.Topology.upstream_devs`, mapped to interface numbers with core's
  `lib.parse.Interfaces`.
- **Output:** one JSON line on stdout, exit 0 always. Failures are
  `{"error": "<reason>"}`.
- **Device lists:** the top 100 per scope, each sorted by its own scope's bytes.
- **Endpoints:** `GET /api/topdevices/flows/totals/{from}/{to}` and
  `GET /api/topdevices/flows/device/{ip}/{from}/{to}`.
  - They require the caller to pass
    `isPageAccessible(user, '/api/diagnostics/networkinsight/export')`.
  - They sit behind the ACL key `page-dashboard-topdevices-live`, renamed
    *Dashboard: Top Devices*, with the new pattern `api/topdevices/flows/*`.
- **Notes,** verbatim:
  - `Internet only covers the last 23 h 0 min: older flows are no longer in the log`
    (the duration is `to − L`, rounded down to the minute);
  - `Internet only: no flows in the log for this range`;
  - `Peers and ports cover the last 23 h 0 min`;
  - `The raw flow log could not be read: showing NetFlow's records`.
- **Widget requests** use `$.ajax` with a 60 s timeout, never `ajaxCall`.
- **Version 0.2.0** in `Makefile` (`PLUGIN_VERSION`), `pkg-descr`, and `VERSION` in
  `live.py` and `flows.py`.
- **Test data:** tests contain no private data. Every flow log is generated in core's
  binary format during the test.
- **Merging:** nothing reaches `main` without the user's approval. The weekly job
  installs `main`: merge before Sun 27 Sep 05:00, or ask the user to pause it.

## Review Focus

1. **A log rotation during a read** renames every file and deletes the oldest. The
   answer must be unchanged, with no file read twice or missed. Pinned by Task 3's
   `test_a_rotation_while_reading_mixes_nothing_up`.
2. **A log file still being written:** the last record is cut short. Reading must stop
   cleanly before it, with no error. Pinned by Task 1's
   `test_a_record_still_being_written_ends_the_read`.
3. **The browser's clock a few minutes off the firewall's.** A Last 24 hours request
   up to 5 minutes older than `now − 86400` is accepted, and a `to` up to 5 minutes
   ahead is capped at now. Pinned by Task 5's `test_the_clocks_may_differ_by_minutes`.
4. **NetFlow capture off, or its library missing:** there is no log, or core's code
   can't be imported. The script answers `{"error"}` and the widget falls back, with
   its note. Pinned by Task 5's `test_no_flow_log_is_an_error_answer` and Task 7's
   `test('a failed or refused raw read falls back …')`.
5. **A panel for an address that is no longer a device,** such as a stale selection:
   an error answer, never a traceback or a wrong list. Pinned by Task 4's
   `test_only_a_device_has_a_panel`.

## File structure

| File | Responsibility |
|---|---|
| Create `src/opnsense/scripts/topdevices/flows.py` | Decode records, apply the coverage rules, read the log (workers), device mode, command line |
| Create `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php` | Validate input, check the least privilege, call configd |
| Modify `src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml` | New pattern and name |
| Modify `src/opnsense/www/js/widgets/Metadata/TopDevices.xml` | Declare `/api/topdevices/flows/*` |
| Modify `install.sh` | 8 files; the two new configd actions |
| Modify `src/opnsense/www/js/widgets/TopDevices.js` | The raw path: `rawRange`/`rawRows`/`rawSpan`/`fmtSpan`, load, caption, device panel, fallback |
| Create `tests/test_flows.py` | Script tests, including core parity |
| Create `tests/parity_flows.py` | On-firewall check against core, and timings |
| Modify `tests/mutate.py` | Mutants for `flows.py` |
| Modify `tests/netflow_ranges.test.mjs`, `tests/mutate_widget.mjs` | Widget tests and mutants |
| Modify `tests/test_install.sh` | 8 files, the actions, the ACL |
| Modify `README.md`, `pkg-descr`, `Makefile`, `live.py` (`VERSION`) | Docs, 0.2.0 |

Run everything from the repository root:

```bash
python3 -m unittest discover -s tests -v
node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs
sh tests/test_install.sh
python3 tests/mutate.py
node tests/mutate_widget.mjs
```

---

### Task 1: Decoding a record and spreading it over time

**Files:**
- Create: `src/opnsense/scripts/topdevices/flows.py`
- Create: `tests/test_flows.py`

**Interfaces:**
- Produces:
  - `flows.FIELDS`, a list of `(name, size)`;
  - `flows.HEAD`, `struct.Struct('>BBHI')`;
  - `flows.REQUIRED`, a tuple;
  - `flows.layout(mask:int, ports:bool=False) -> (struct.Struct, has_ports:bool, has_if:bool, has_ft:bool) | None`;
  - `flows.records(buf:bytes, ports:bool=False)`, yielding
    `(recv:int, src:int, dst:int, src_port:int, dst_port:int, octets:int, if_in:int, if_out:int, flow_start:float, flow_end:float, dur_ms:int)`;
  - `flows.share(flow_start, flow_end, dur_ms, octets, lo, hi) -> float`.
  - Test helpers in `tests/test_flows.py`: `record(**fields)`,
    `flow(src, dst, octets, recv, start, end, if_in=2, if_out=1, ports=(40000, 443), omit=(), v6=False)`,
    `IP(s)`, and the constants `T0` and `UPTIME0`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_flows.py`:

```python
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


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest tests.test_flows -v`
Expected: an error at import, `FileNotFoundError: ... flows.py`, because the script does
not exist yet.

- [ ] **Step 3: Write the decoding and spreading**

Create `src/opnsense/scripts/topdevices/flows.py`:

```python
#!/usr/local/bin/python3
"""TopDevices: exact per-device totals for a recent window, read on demand from
NetFlow's raw flow log (/var/log/flowd.log and its rotations). Run by configd:

    flows.py totals FROM TO           every device's bytes, both scopes
    flows.py device IP FROM TO        one device's peers and ports, both scopes

FROM and TO are epoch seconds. It prints one JSON object and exits 0, errors
included ({"error": "..."}): configd returns stdout as the answer, and turns a
failing exit into "Execute error". Design:
docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md
"""
import calendar
import datetime
import glob
import ipaddress
import json
import math
import multiprocessing
import os
import re
import socket
import struct
import sys
import time

VERSION = '0.2.0'

LOG = '/var/log/flowd.log'
NETFLOW_LIB = '/usr/local/opnsense/scripts/netflow'  # core's parser, interface map, aggregates
HOUR = 3600
DAY = 86400
SLACK = 300              # the browser's clock may be a few minutes off the firewall's
TOP = 100                # the device panel lists at most 100 rows
DIGITS = re.compile(r'[0-9]+')

# core's lib/flowparser.py: the fields a record may carry, in order of appearance
FIELDS = [('tag', 4), ('recv_time', 8), ('proto_flags_tos', 4),
          ('agent_addr4', 4), ('agent_addr6', 16), ('src_addr4', 4), ('src_addr6', 16),
          ('dst_addr4', 4), ('dst_addr6', 16), ('gateway_addr4', 4), ('gateway_addr6', 16),
          ('srcdst_port', 4), ('packets', 8), ('octets', 8), ('if_indices', 8),
          ('agent_info', 16), ('flow_times', 8), ('as_info', 12), ('flow_engine_info', 12)]
HEAD = struct.Struct('>BBHI')        # version, length in 32-bit words, reserved, field mask
# a record core's parser skips lacks one of these; an IPv6 one lacks the IPv4 addresses
REQUIRED = ('recv_time', 'agent_info', 'packets', 'octets', 'src_addr4', 'dst_addr4')


# ---------------------------------------------------------------- one record


def layout(mask, ports=False):
    """One Struct for the fields needed from records with this field mask, or None
    for a record core skips and for an IPv6 one. It unpacks, in this order:
    recv_sec, src, dst, [src_port, dst_port,] octets, [if_in, if_out,] uptime_ms,
    [flow_start, flow_finish] - the bracketed ones only when the mask has them."""
    at, off = {}, 0
    for i, (name, size) in enumerate(FIELDS):
        if mask & (1 << i):
            at[name] = off
            off += size
    if not all(k in at for k in REQUIRED):
        return None
    parts = [(at['recv_time'], 'I'), (at['src_addr4'], 'I'), (at['dst_addr4'], 'I')]
    has_ports = ports and 'srcdst_port' in at
    if has_ports:
        parts.append((at['srcdst_port'], 'HH'))
    parts.append((at['octets'], 'Q'))
    has_if = 'if_indices' in at
    if has_if:
        parts.append((at['if_indices'], 'II'))
    parts.append((at['agent_info'], 'I'))
    has_ft = 'flow_times' in at
    if has_ft:
        parts.append((at['flow_times'], 'II'))
    fmt, pos = '>', 0
    for o, code in parts:                    # offsets increase in this order
        if o > pos:
            fmt += '%dx' % (o - pos)
        fmt += code
        pos = o + struct.calcsize('>' + code)
    return struct.Struct(fmt), has_ports, has_if, has_ft


def records(buf, ports=False):
    """Every IPv4 record core's parser would yield, as (recv, src, dst, src_port,
    dst_port, octets, if_in, if_out, flow_start, flow_end, dur_ms), the times from
    core's own expressions (lib/flowparser.py). Ports are 0 and interfaces -1 when
    a record lacks them, as core leaves them; ports are read only when asked for."""
    layouts, off, n = {}, 0, len(buf)
    head = HEAD.unpack_from
    while off + 8 <= n:
        _, words, _, mask = head(buf, off)
        body = off + 8
        off = body + words * 4
        if off > n:
            break                            # the last record is still being written
        lay = layouts.get(mask)
        if lay is None:
            lay = layouts[mask] = layout(mask, ports) or False
        if not lay:
            continue
        st, has_ports, has_if, has_ft = lay
        v = st.unpack_from(buf, body)
        if has_ports:
            src_port, dst_port, i = v[3], v[4], 5
        else:
            src_port = dst_port = 0
            i = 3
        octets = v[i]
        if has_if:
            if_in, if_out = v[i + 1], v[i + 2]
            i += 3
        else:
            if_in = if_out = -1
            i += 1
        uptime = v[i]
        start, finish = (v[i + 1], v[i + 2]) if has_ft else (uptime, uptime)
        if finish > uptime:
            continue                         # core: impossible data
        flow_end = v[0] - (uptime - finish) / 1000.0
        flow_start = flow_end - (finish - start) / 1000.0
        yield v[0], v[1], v[2], src_port, dst_port, octets, if_in, if_out, flow_start, flow_end, finish - start


def share(flow_start, flow_end, dur_ms, octets, lo, hi):
    """A record's bytes inside [lo, hi): spread evenly over its duration with core's
    own expressions in core's order (lib/aggregates/__init__.py add()), so sums match
    core's aggregates to the byte. A record of zero duration counts whole where it
    starts, and one of negative duration (bogus) as core's arithmetic has it."""
    if dur_ms > 0:
        ov = min(flow_end, hi) - max(flow_start, lo)
        return ov / (dur_ms / 1000.0) * octets if ov > 0 else 0.0
    if not lo <= flow_start < hi:
        return 0.0
    if dur_ms < 0:
        return (flow_end - flow_start) / (dur_ms / 1000.0) * octets
    return octets
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python3 -m unittest tests.test_flows -v`
Expected: `Ran 9 tests … OK`.

Run: `python3 -m unittest discover -s tests`
Expected: `OK (skipped=3)`, with no other suite broken.

- [ ] **Step 5: Commit**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py
git commit -m "flows.py: decode the raw flow log and spread records as core does"
```

---

### Task 2: Coverage rules, devices and the hourly fill-in

**Files:**
- Modify: `src/opnsense/scripts/topdevices/flows.py` (append after `share`)
- Modify: `tests/test_flows.py` (add classes before `if __name__`)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `flows.plan(frm:int, to:int, L:float) -> {'raw_all': (lo, hi), 'hourly': (lo, hi) | None, 'inet': (lo, hi)}`;
  - `flows.hourly_weight(bucket:int, lo, hi, now) -> float`;
  - `flows.Net(local_nets:[ipaddress.IPv4Network], upstream_ifindex:[int], upstream_names:[str])`,
    with `.is_device(ip:int) -> bool`, `.upstream` (a `frozenset` of int) and
    `.upstream_names` (a list of str);
  - `flows.hourly_fill(rows:[dict], lo, hi, now, net) -> {ip:int: [down, up]}`;
  - the helpers `flows._ip4(text) -> int | None`, `flows._ip_str(ip:int) -> str` and
    `flows._epoch(value) -> int`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_flows.py`, before `if __name__ == '__main__':`:

```python
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
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest tests.test_flows -v`
Expected: an error at import, `AttributeError: module 'flows' has no attribute 'Net'`,
raised by the module-level `NET = flows.Net(...)`.

- [ ] **Step 3: Write the coverage rules, `Net` and the fill-in**

Append to `flows.py`:

```python
# ---------------------------------------------------------------- coverage


def plan(frm, to, L):
    """What covers each part of a totals answer for [frm, to), the log being
    complete from L (spec §4): all traffic from the raw log over raw_all and from
    core's hourly records over hourly (or None); internet only from the raw log
    over inet. A span whose start is not before its end is empty."""
    if frm >= L:
        return {'raw_all': (frm, to), 'hourly': None, 'inet': (frm, to)}
    seam = min(math.ceil(L / HOUR) * HOUR, to)
    return {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (L, to)}


def hourly_weight(bucket, lo, hi, now):
    """How much of an hourly bucket falls in [lo, hi): its overlap over the time it
    spans, which ends at now while the bucket is still in progress (spec §4)."""
    end = min(bucket + HOUR, now)
    if end <= bucket:
        return 0.0
    ov = min(end, hi) - max(bucket, lo)
    return ov / (end - bucket) if ov > 0 else 0.0


class Net:
    """Which addresses are devices and which interfaces lead upstream, as integers
    for speed. A device is an IPv4 address in a local subnet, except each subnet's
    broadcast address; the firewall's own addresses are kept (the table lists them
    as '<network> gateway')."""

    def __init__(self, local_nets, upstream_ifindex, upstream_names):
        self.nets = [(int(n.network_address), int(n.netmask)) for n in local_nets]
        self.bcast = {int(n.broadcast_address) for n in local_nets if n.prefixlen < 31}
        self.upstream = frozenset(upstream_ifindex)
        self.upstream_names = list(upstream_names)
        self._dev = {}

    def is_device(self, ip):
        d = self._dev.get(ip)
        if d is None:
            d = self._dev[ip] = ip not in self.bcast and any(ip & m == n for n, m in self.nets)
        return d


def _ip4(text):
    """An IPv4 address as an integer, or None for anything else (IPv6)."""
    try:
        return int(ipaddress.IPv4Address(text))
    except ValueError:
        return None


def _ip_str(ip):
    return socket.inet_ntoa(struct.pack('>I', ip))


def _epoch(value):
    """A bucket's start as core's aggregates return it (a naive UTC datetime), or seconds."""
    if isinstance(value, datetime.datetime):
        return calendar.timegm(value.timetuple())
    return int(value)


def hourly_fill(rows, lo, hi, now, net):
    """All-traffic [down, up] per device from core's hourly totals over [lo, hi).
    A totals row holds one address: 'out' rows are bytes delivered to it, 'in' rows
    bytes it sent (core's FlowSourceAddrTotals.add())."""
    out = {}
    for row in rows:
        ip = _ip4(row['src_addr'])
        if ip is None or not net.is_device(ip):
            continue
        w = hourly_weight(_epoch(row['start_time']), lo, hi, now)
        if w:
            t = out.setdefault(ip, [0.0, 0.0])
            t[0 if row['direction'] == 'out' else 1] += w * float(row['octets'] or 0)
    return out
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python3 -m unittest tests.test_flows -v`
Expected: `Ran 17 tests … OK`.

- [ ] **Step 5: Commit**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py
git commit -m "flows.py: coverage rules, devices, and the hourly fill-in"
```

---

### Task 3: Reading the log, attribution, workers and the totals answer

**Files:**
- Modify: `src/opnsense/scripts/topdevices/flows.py` (append)
- Modify: `tests/test_flows.py` (add classes, including `CoreParity`)

**Interfaces:**
- Consumes: `records`, `share`, `plan`, `hourly_fill`, `Net`, `_ip_str` (Tasks 1–2).
- Produces:
  - `flows.open_log(log=LOG) -> [(fd:int, mtime:float, size:int)]`, oldest first;
  - `flows.close_log(opened)`;
  - `flows._read(fd) -> bytes`;
  - `flows.log_from(opened) -> int | None`;
  - `flows.files_for(opened, lo) -> [(fd, mtime, size)]`, biggest first;
  - `flows.scan_totals(job) -> {ip:int: [all_down, all_up, inet_down, inet_up]}`, where
    `job = (fd, a_lo, a_hi, i_lo, i_hi, net)`;
  - `flows.merge_totals(parts) -> dict`;
  - `flows.run_parallel(fn, jobs, workers) -> list`;
  - `flows.answer_totals(frm, to, now, opened, net, hourly_rows, workers) -> dict`, where
    `hourly_rows(lo, hi) -> [dict]`.
  - Test helpers: `write_log(dirpath, files) -> log path`, `synthetic_log(n, t0, rnd) -> bytes`,
    and the constant `IFNAME`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_flows.py`, before `if __name__ == '__main__':`:

```python
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


CORE_NETFLOW = next((p for p in (os.environ.get('CORE_NETFLOW'),
                                 os.path.join(os.environ.get('OPNSENSE_CORE', '/nonexistent'), 'src/opnsense/scripts/netflow'),
                                 flows.NETFLOW_LIB)
                     if p and os.path.isfile(os.path.join(p, 'lib', 'flowparser.py'))), None)


@unittest.skipUnless(CORE_NETFLOW, 'set CORE_NETFLOW or OPNSENSE_CORE (see the module docstring), or run on the firewall')
class CoreParity(unittest.TestCase):
    """Against core's own parser and aggregators (lib/flowparser.py, lib/aggregates)."""

    @classmethod
    def setUpClass(cls):
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
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest tests.test_flows -v`
Expected: the new tests error with `AttributeError: module 'flows' has no attribute 'open_log'`
(and `scan_totals`, `log_from` and so on). `CoreParity` is skipped. The Task 1–2 tests
still pass.

- [ ] **Step 3: Write the reading, attribution, workers and totals answer**

Append to `flows.py`:

```python
# ---------------------------------------------------------------- the log


def open_log(log=LOG):
    """Every flow log file, opened now, oldest first: [(fd, last write, size)].
    Reading through descriptors opened at once keeps a rotation meanwhile - which
    renames every file and deletes the oldest - from mixing up which is which."""
    opened = []
    for path in glob.glob(glob.escape(log) + '*'):
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            continue                              # rotated away since the listing
        st = os.fstat(fd)
        opened.append((fd, st.st_mtime, st.st_size))
    opened.sort(key=lambda f: f[1])
    return opened


def close_log(opened):
    for fd, _, _ in opened:
        try:
            os.close(fd)
        except OSError:
            pass


def _read(fd):
    """The whole file behind fd, as far as it is written now."""
    size = os.fstat(fd).st_size
    chunks, off = [], 0
    while off < size:
        chunk = os.pread(fd, size - off, off)
        if not chunk:
            break
        chunks.append(chunk)
        off += len(chunk)
    return b''.join(chunks)


def log_from(opened):
    """When the log becomes complete (spec §4): the receive time of the oldest IPv4
    record still in it. None when it holds none."""
    for fd, _, _ in opened:
        for r in records(os.pread(fd, 65536, 0)):
            return r[0]
    return None


def files_for(opened, lo):
    """The files that can hold records overlapping a span from lo - those written to
    at or after lo, since a record received before lo ended before it - biggest
    first, so the last file a worker starts is a small one."""
    return sorted((f for f in opened if f[1] >= lo), key=lambda f: -f[2])


def scan_totals(job):
    """One file's bytes per device: {ip: [all_down, all_up, inet_down, inet_up]}, all
    traffic over [a_lo, a_hi) and internet only over [i_lo, i_hi) (spec §5.3). The
    destination device downloads, the source device uploads; a download is internet
    when it came in upstream, an upload when it left upstream."""
    fd, a_lo, a_hi, i_lo, i_hi, net = job
    lo = min(a_lo, i_lo)
    acc = {}
    for recv, src, dst, _, _, octets, if_in, if_out, fs, fe, dur in records(_read(fd)):
        if recv < lo:
            continue                              # ended before either span
        a = share(fs, fe, dur, octets, a_lo, a_hi) if a_hi > a_lo else 0.0
        i = share(fs, fe, dur, octets, i_lo, i_hi) if i_hi > i_lo else 0.0
        if not (a or i):
            continue
        if net.is_device(dst):
            t = acc.get(dst) or acc.setdefault(dst, [0.0, 0.0, 0.0, 0.0])
            t[0] += a
            if if_in in net.upstream:
                t[2] += i
        if net.is_device(src):
            t = acc.get(src) or acc.setdefault(src, [0.0, 0.0, 0.0, 0.0])
            t[1] += a
            if if_out in net.upstream:
                t[3] += i
    return acc


def merge_totals(parts):
    out = {}
    for part in parts:
        for ip, v in part.items():
            t = out.setdefault(ip, [0.0, 0.0, 0.0, 0.0])
            for k in range(4):
                t[k] += v[k]
    return out


def run_parallel(fn, jobs, workers):
    """fn over every job, in up to `workers` forked processes. The descriptors in
    the jobs are inherited: the log files are opened before the fork."""
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    with multiprocessing.get_context('fork').Pool(min(workers, len(jobs))) as pool:
        return pool.map(fn, jobs, chunksize=1)


def answer_totals(frm, to, now, opened, net, hourly_rows, workers):
    """The totals answer (spec §5.1) for [frm, to) at now. hourly_rows(lo, hi) gives
    core's hourly FlowSourceAddrTotals rows for buckets starting in [lo, hi)."""
    L = log_from(opened)
    if L is None:
        raise ValueError('the NetFlow flow log holds no flows yet')
    p = plan(frm, to, L)
    (a_lo, a_hi), (i_lo, i_hi) = p['raw_all'], p['inet']
    jobs = [(fd, a_lo, a_hi, i_lo, i_hi, net) for fd, _, _ in files_for(opened, min(a_lo, i_lo))]
    acc = merge_totals(run_parallel(scan_totals, jobs, workers))
    if p['hourly']:
        h_lo, h_hi = p['hourly']
        for ip, (down, up) in hourly_fill(hourly_rows(h_lo, h_hi), h_lo, h_hi, now, net).items():
            t = acc.setdefault(ip, [0.0, 0.0, 0.0, 0.0])
            t[0] += down
            t[1] += up
    return {'now': now, 'log_from': L,
            'all': {'from': frm, 'to': to, 'hourly_until': p['hourly'][1] if p['hourly'] else None},
            'inet': {'from': i_lo, 'to': to},
            'wan': net.upstream_names,
            'devices': {_ip_str(ip): [int(round(x)) for x in v] for ip, v in sorted(acc.items())},
            'files': len(jobs), 'workers': max(1, min(workers, len(jobs)))}
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python3 -m unittest tests.test_flows -v`
Expected: `Ran 31 tests … OK (skipped=3)`, the three being `CoreParity`.

- [ ] **Step 5: Run the core parity tests against core's own code**

These need the network.

Run:
```bash
git clone -q --depth 1 --branch 26.7.4 --filter=blob:none --sparse https://github.com/opnsense/core.git "$TMPDIR/core"
git -C "$TMPDIR/core" sparse-checkout set src/opnsense/scripts/netflow
OPNSENSE_CORE="$TMPDIR/core" python3 -m unittest tests.test_flows -v
```
Expected: `Ran 31 tests … OK`, with nothing skipped. If `test_totals_match_cores_aggregator`
fails, stop and find the cause with superpowers:systematic-debugging. Never loosen the
`delta`.

- [ ] **Step 6: Commit**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py
git commit -m "flows.py: read the log through descriptors, attribute, workers, totals answer"
```

---

### Task 4: Device mode (peers and ports)

**Files:**
- Modify: `src/opnsense/scripts/topdevices/flows.py` (append)
- Modify: `tests/test_flows.py` (add a `Device` class and a `CoreParity` method)

**Interfaces:**
- Consumes: `records(buf, ports=True)`, `share`, `open_log`, `log_from`, `files_for`,
  `run_parallel`, `Net`, `_read` and `_ip_str` (Tasks 1–3).
- Produces:
  - `flows.scan_device(job) -> ({peer:int: [all, inet]}, {port:int: [all, inet]})`, where
    `job = (fd, lo, hi, ip, net)`;
  - `flows.top(table, k, name, n=TOP) -> [[str, int]]`;
  - `flows.answer_device(ip:int, frm, to, now, opened, net, workers) -> dict`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_flows.py`, before `CORE_NETFLOW = …`:

```python
class Device(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def answer(self, data, frm, to, ip='192.168.1.10', early=True):
        opened = flows.open_log(write_log(self.tmp.name, [('flowd.log', (EARLY if early else b'') + data, T0 + 100)]))
        self.addCleanup(flows.close_log, opened)
        return flows.answer_device(IP(ip), frm, to, T0 + 200, opened, NET, 1)

    def test_peers_and_ports_in_both_directions_and_both_scopes(self):
        data = b''.join(KINDS) + flow('8.8.8.8', '192.168.1.10', 500, recv=T0 + 100, start=T0 + 10, end=T0 + 90,
                                      if_in=1, if_out=2, ports=(53, 5353))
        a = self.answer(data, T0, T0 + 100)
        self.assertEqual((a['ip'], a['from'], a['to']), ('192.168.1.10', T0, T0 + 100))
        self.assertEqual(a['peers'], {'all': [['8.8.8.8', 1500], ['1.1.1.1', 300], ['192.168.20.5', 50],
                                              ['192.168.1.255', 7]],
                                      'inet': [['8.8.8.8', 1500], ['1.1.1.1', 300]]})
        self.assertEqual(a['ports'], {'all': [['443', 1357], ['53', 500]], 'inet': [['443', 1300], ['53', 500]]})

    def test_port_zero_is_other(self):
        a = self.answer(flow('8.8.8.8', '192.168.1.10', 5, recv=T0 + 100, start=T0, end=T0 + 90, ports=(0, 0)),
                        T0, T0 + 100)
        self.assertEqual(a['ports']['all'], [['other', 5]])

    def test_lists_stop_at_the_panels_hundred_rows(self):
        data = b''.join(flow('8.8.%d.%d' % (i // 250, i % 250), '192.168.1.10', 1000 + i, recv=T0 + 100,
                             start=T0, end=T0 + 90, if_in=1) for i in range(105))
        a = self.answer(data, T0, T0 + 100)
        self.assertEqual(len(a['peers']['all']), 100)
        self.assertEqual(a['peers']['all'][0], ['8.8.0.104', 1104])

    def test_the_lists_cover_the_log_part_of_the_range(self):
        a = self.answer(b''.join(KINDS), T0 - 500, T0 + 100, early=False)
        self.assertEqual((a['from'], a['log_from']), (T0 + 100, T0 + 100))   # KINDS arrive at T0 + 100
        self.assertEqual(a['peers'], {'all': [], 'inet': []})

    def test_only_a_device_has_a_panel(self):
        for ip in ('192.168.1.255', '8.8.8.8'):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                self.answer(b''.join(KINDS), T0, T0 + 100, ip=ip)
```

Add this method to `class CoreParity`:

```python
    def test_device_lists_match_cores_details(self):
        a0 = -(-T0 // 300) * 300
        lo, hi = a0 + 300, a0 + 1800
        for dev in ('192.168.1.12', '192.168.20.103'):
            core = {}
            for row in self.details.get_data(lo, hi):
                if row['dst_addr'] != dev:        # dst-keyed: both directions of the device's flows
                    continue
                t = core.setdefault(row['src_addr'], [0.0, 0.0])
                t[0] += row['octets']
                if row['if'] == 'em0':
                    t[1] += row['octets']
            peers, _ = flows.scan_device((self.opened[0][0], lo, hi, IP(dev), NET))
            self.assertEqual({flows._ip_str(p) for p in peers}, set(core), dev)
            for p, v in peers.items():
                for k in (0, 1):
                    self.assertAlmostEqual(v[k], core[flows._ip_str(p)][k], delta=1e-3)
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest tests.test_flows -v`
Expected: the `Device` tests error with `AttributeError: … has no attribute 'answer_device'`.

- [ ] **Step 3: Write device mode**

Append to `flows.py`:

```python
# ---------------------------------------------------------------- one device


def scan_device(job):
    """One file's peers and ports for one device over [lo, hi), in both scopes:
    ({peer: [all, inet]}, {port: [all, inet]}), both directions summed. A port is the
    service port as core's details define it: the lower of the flow's two."""
    fd, lo, hi, ip, net = job
    peers, ports = {}, {}
    for recv, src, dst, sp, dp, octets, if_in, if_out, fs, fe, dur in records(_read(fd), ports=True):
        if recv < lo or (src != ip and dst != ip):
            continue
        b = share(fs, fe, dur, octets, lo, hi)
        if not b:
            continue
        if dst == ip:                     # its download: internet when it came in upstream
            peer, inet = src, if_in in net.upstream
        else:                             # its upload: internet when it left upstream
            peer, inet = dst, if_out in net.upstream
        port = min(sp, dp)
        for key, table in ((peer, peers), (port, ports)):
            t = table.get(key) or table.setdefault(key, [0.0, 0.0])
            t[0] += b
            if inet:
                t[1] += b
    return peers, ports


def _merge2(into, part):
    for key, v in part.items():
        t = into.setdefault(key, [0.0, 0.0])
        t[0] += v[0]
        t[1] += v[1]


def top(table, k, name, n=TOP):
    """The n biggest entries of table by scope k (0 all, 1 internet), biggest first,
    as [[name(key), bytes]]."""
    items = sorted(((key, v[k]) for key, v in table.items() if v[k] > 0), key=lambda kv: (-kv[1], kv[0]))
    return [[name(key), int(round(b))] for key, b in items[:n]]


def _port_str(port):
    return str(port) if port else 'other'


def answer_device(ip, frm, to, now, opened, net, workers):
    """One device's peers and ports over [max(frm, L), to), both scopes (spec §5.5)."""
    if not net.is_device(ip):
        raise ValueError('%s is not a local device' % _ip_str(ip))
    L = log_from(opened)
    if L is None:
        raise ValueError('the NetFlow flow log holds no flows yet')
    lo = max(frm, L)
    peers, ports, jobs = {}, {}, []
    if lo < to:
        jobs = [(fd, lo, to, ip, net) for fd, _, _ in files_for(opened, lo)]
        for p, q in run_parallel(scan_device, jobs, workers):
            _merge2(peers, p)
            _merge2(ports, q)
    return {'now': now, 'log_from': L, 'ip': _ip_str(ip), 'from': lo, 'to': to,
            'peers': {'all': top(peers, 0, _ip_str), 'inet': top(peers, 1, _ip_str)},
            'ports': {'all': top(ports, 0, _port_str), 'inet': top(ports, 1, _port_str)},
            'files': len(jobs), 'workers': max(1, min(workers, len(jobs)))}
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python3 -m unittest tests.test_flows -v` and
`OPNSENSE_CORE="$TMPDIR/core" python3 -m unittest tests.test_flows -v`
Expected: `Ran 37 tests … OK (skipped=4)`, then `Ran 37 tests … OK` with core present.

- [ ] **Step 5: Commit**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py
git commit -m "flows.py: one device's peers and ports"
```

---

### Task 5: Command line, firewall wiring, and the script's mutants

**Files:**
- Modify: `src/opnsense/scripts/topdevices/flows.py` (append)
- Modify: `tests/test_flows.py` (add a `CommandLine` class)
- Modify: `tests/mutate.py`

**Interfaces:**
- Consumes: `answer_totals`, `answer_device`, `open_log`, `close_log`, `Net`,
  `live.Topology`, `live.parse_ifconfig`, `live.parse_default_devs`, `live._run`,
  `live.IFCONFIG` and `live.ROUTES`.
- Produces:
  - `flows.parse_args(args:[str], now:int) -> {'mode', 'from', 'to'[, 'ip':int]}`, raising
    `ValueError` with a reason;
  - `flows.system_net() -> Net`;
  - `flows.core_interfaces() -> {int: str}`;
  - `flows.core_hourly(lo, hi) -> [dict]`;
  - `flows.main(argv, now=None, log=LOG, system=None, hourly=None) -> 0`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_flows.py`, before `CORE_NETFLOW = …`:

```python
class CommandLine(unittest.TestCase):
    NOW = T0 + 86400

    def reject(self, args):
        with self.assertRaises(ValueError) as caught:
            flows.parse_args(args, self.NOW)
        return str(caught.exception)

    def test_both_modes_parse(self):
        self.assertEqual(flows.parse_args(['totals', str(self.NOW - 3600), str(self.NOW)], self.NOW),
                         {'mode': 'totals', 'from': self.NOW - 3600, 'to': self.NOW})
        self.assertEqual(flows.parse_args(['device', '192.168.1.10', str(self.NOW - 60), str(self.NOW)], self.NOW),
                         {'mode': 'device', 'from': self.NOW - 60, 'to': self.NOW, 'ip': IP('192.168.1.10')})

    def test_bad_input_is_refused_with_a_reason(self):
        now = str(self.NOW)
        cases = [(['totals', '1'], 'usage'), (['bogus', '1', '2'], 'usage'),
                 (['totals', 'abc', now], 'whole epoch seconds'), (['totals', '\u0661', now], 'whole epoch seconds'),
                 (['totals', '', now], 'whole epoch seconds'), (['totals', now, now], 'before TO'),
                 (['totals', str(self.NOW - 86400 - 301), now], 'more than a day ago'),
                 (['totals', str(self.NOW - 60), str(self.NOW + 301)], 'in the future'),
                 (['device', 'fd00::1', str(self.NOW - 60), now], 'IPv4'),
                 (['device', '192.168.01.10', str(self.NOW - 60), now], 'IPv4')]
        for args, reason in cases:
            with self.subTest(args=args):
                self.assertIn(reason, self.reject(args))

    def test_the_clocks_may_differ_by_minutes(self):
        req = flows.parse_args(['totals', str(self.NOW - 86400 - 299), str(self.NOW + 299)], self.NOW)
        self.assertEqual((req['from'], req['to']), (self.NOW - 86400 - 299, self.NOW))

    def test_a_range_that_has_not_begun_is_refused(self):
        self.assertIn('not begun', self.reject(['totals', str(self.NOW + 10), str(self.NOW + 200)]))

    def run_main(self, argv, log, now=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = flows.main(argv, now=now, log=log, system=lambda: NET, hourly=lambda lo, hi: [])
        self.assertEqual(rc, 0)
        line = out.getvalue()
        self.assertTrue(line.endswith('\n') and line.count('\n') == 1, line)
        return json.loads(line)

    def test_an_answer_is_one_json_line(self):
        with tempfile.TemporaryDirectory() as d:
            log = write_log(d, [('flowd.log', b''.join(KINDS), T0 + 100)])
            a = self.run_main(['flows.py', 'totals', str(T0 + 100), str(T0 + 200)], log, now=T0 + 300)
        self.assertEqual(a['v'], flows.VERSION)
        self.assertIn('cost_ms', a)
        self.assertNotIn('error', a)

    def test_no_flow_log_is_an_error_answer(self):
        with tempfile.TemporaryDirectory() as d:
            a = self.run_main(['flows.py', 'totals', str(T0 + 100), str(T0 + 200)], os.path.join(d, 'flowd.log'),
                              now=T0 + 300)
        self.assertIn('NetFlow', a['error'])

    def test_the_script_never_prints_a_traceback(self):
        proc = subprocess.run([sys.executable, FLOWS_PY, 'totals', 'x', 'y'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0)
        self.assertIn('whole epoch seconds', json.loads(proc.stdout)['error'])
        self.assertEqual(proc.stderr, '')
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest tests.test_flows -v`
Expected: the `CommandLine` tests error with `AttributeError: … 'parse_args'` or `'main'`.
The subprocess test fails because the script prints nothing.

- [ ] **Step 3: Write the command line and wiring**

Append to `flows.py`:

```python
# ---------------------------------------------------------------- command line


def parse_args(args, now):
    """The request {'mode', 'from', 'to'[, 'ip']} from the command line, checked as
    spec §5.1 says. Raises ValueError with the reason."""
    if len(args) == 3 and args[0] == 'totals':
        mode, ip, frm, to = 'totals', None, args[1], args[2]
    elif len(args) == 4 and args[0] == 'device':
        mode, ip, frm, to = 'device', args[1], args[2], args[3]
    else:
        raise ValueError('usage: flows.py totals FROM TO | flows.py device IP FROM TO')
    if not (DIGITS.fullmatch(frm) and DIGITS.fullmatch(to)):
        raise ValueError('FROM and TO must be whole epoch seconds')
    frm, to = int(frm), int(to)
    if frm >= to:
        raise ValueError('FROM must be before TO')
    if frm < now - DAY - SLACK:
        raise ValueError("FROM is more than a day ago: that range is read from NetFlow's records")
    if to > now + SLACK:
        raise ValueError('TO is in the future')
    to = min(to, now)
    if frm >= to:
        raise ValueError('the range has not begun yet')
    req = {'mode': mode, 'from': frm, 'to': to}
    if mode == 'device':
        req['ip'] = _ip4(ip)
        if req['ip'] is None:
            raise ValueError('IP must be an IPv4 address')
    return req


def _core():
    """Core's NetFlow library (lib.flowparser, lib.parse, lib.aggregates) on the path."""
    if NETFLOW_LIB not in sys.path:
        sys.path.insert(0, NETFLOW_LIB)


def core_interfaces():
    """{ifindex: device}: core's own map (lib/parse.py Interfaces, from ifinfo)."""
    _core()
    from lib.parse import Interfaces
    index = getattr(Interfaces(), '_if_index', None)
    if not isinstance(index, dict):
        raise RuntimeError("core's interface map has changed (lib/parse.py)")
    return {int(k): v for k, v in index.items()}


def core_hourly(lo, hi):
    """Core's hourly FlowSourceAddrTotals rows for buckets starting in [lo, hi)."""
    _core()
    from lib.aggregates.source import FlowSourceAddrTotals
    return list(FlowSourceAddrTotals(HOUR).get_data(lo, hi))


def system_net():
    """This firewall's devices and upstream interfaces, by the Live sampler's rules
    (live.py Topology), with upstream numbered as core's aggregator numbers it."""
    import live                                   # the sampler, beside this script
    topo = live.Topology(live.parse_ifconfig(live._run(live.IFCONFIG)),
                         live.parse_default_devs(live._run(live.ROUTES)))
    index = core_interfaces()
    upstream = sorted(i for i, name in index.items() if name in topo.upstream_devs)
    return Net(topo.local_nets, upstream, list(topo.upstream_devs))


def main(argv, now=None, log=LOG, system=None, hourly=None):
    """Answer the command line with one JSON line on stdout. Always returns 0."""
    t0 = time.monotonic()
    opened = []
    try:
        now = int(time.time()) if now is None else int(now)
        req = parse_args(argv[1:], now)
        net = (system or system_net)()
        opened = open_log(log)
        if not opened:
            raise ValueError('no NetFlow flow log: is NetFlow capture enabled?')
        workers = max(1, (os.cpu_count() or 2) // 2)
        if req['mode'] == 'totals':
            out = answer_totals(req['from'], req['to'], now, opened, net, hourly or core_hourly, workers)
        else:
            out = answer_device(req['ip'], req['from'], req['to'], now, opened, net, workers)
        out['cost_ms'] = int(round((time.monotonic() - t0) * 1000))
    except Exception as exc:          # never a traceback: configd returns stdout as the answer
        out = {'error': str(exc) or exc.__class__.__name__}
    finally:
        close_log(opened)
    out['v'] = VERSION
    sys.stdout.write(json.dumps(out, separators=(',', ':')) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the tests and watch them pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: every test passes, with `test_flows`' `CoreParity` skipped when core isn't
present.

- [ ] **Step 5: Add the script's mutants to `tests/mutate.py`**

In `tests/mutate.py`, after `LIVE = ROOT / 'src/opnsense/scripts/topdevices/live.py'`, add:

```python
FLOWS = ROOT / 'src/opnsense/scripts/topdevices/flows.py'
```

After the closing `]` of `MUTANTS`, add:

```python
FLOW_MUTANTS = [
    ("core's order of operations not kept",
     '        return ov / (dur_ms / 1000.0) * octets if ov > 0 else 0.0',
     '        return octets * ov / (dur_ms / 1000.0) if ov > 0 else 0.0'),
    ('negative duration counted like a zero one',
     '    if dur_ms < 0:\n        return (flow_end - flow_start) / (dur_ms / 1000.0) * octets\n', ''),
    ('a record finished after its export kept',
     '        if finish > uptime:\n            continue', '        if False:\n            continue'),
    ('records without packets kept',
     "REQUIRED = ('recv_time', 'agent_info', 'packets', 'octets', 'src_addr4', 'dst_addr4')",
     "REQUIRED = ('recv_time', 'agent_info', 'octets', 'src_addr4', 'dst_addr4')"),
    ('a record still being written read anyway', '        if off > n:\n            break', '        pass'),
    ('ingress and egress confused for internet downloads',
     '            if if_in in net.upstream:\n                t[2] += i', '            if if_out in net.upstream:\n                t[2] += i'),
    ('broadcasts counted as devices',
     '            d = self._dev[ip] = ip not in self.bcast and any(', '            d = self._dev[ip] = any('),
    ('the seam on the hour before the log began',
     '    seam = min(math.ceil(L / HOUR) * HOUR, to)', '    seam = min(math.floor(L / HOUR) * HOUR, to)'),
    ('internet only from the range start, not the log start',
     "    return {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (L, to)}",
     "    return {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (frm, to)}"),
    ('a bucket in progress weighed as a full hour', '    end = min(bucket + HOUR, now)', '    end = bucket + HOUR'),
    ('hourly rows read with the details convention',
     "            t[0 if row['direction'] == 'out' else 1] += w * float(row['octets'] or 0)",
     "            t[1 if row['direction'] == 'out' else 0] += w * float(row['octets'] or 0)"),
    ('files taken in the wrong order', '    opened.sort(key=lambda f: f[1])', '    opened.sort(key=lambda f: f[2])'),
    ('a file written since the span began left unread',
     '    return sorted((f for f in opened if f[1] >= lo), key=lambda f: -f[2])',
     '    return sorted((f for f in opened if f[1] >= lo + 60), key=lambda f: -f[2])'),
    ('a record received just after the span began skipped', '        if recv < lo:\n', '        if recv < lo + 20:\n'),
    ("the device's port taken as the higher one", '        port = min(sp, dp)', '        port = max(sp, dp)'),
    ("a device's upload judged by where it came in",
     '            peer, inet = dst, if_out in net.upstream', '            peer, inet = dst, if_in in net.upstream'),
    ("lists not capped at the panel's 100 rows", 'TOP = 100 ', 'TOP = 1000 '),
    ('the clock slack dropped', '    if frm < now - DAY - SLACK:', '    if frm < now - DAY:'),
    ('non-ASCII digits accepted', '    if not (DIGITS.fullmatch(frm) and DIGITS.fullmatch(to)):',
     '    if not (frm.isdigit() and to.isdigit()):'),
    ('a non-device given a panel', '    if not net.is_device(ip):\n        raise', '    if False:\n        raise'),
]
```

Replace `def main():` through its `return 1 if survivors else 0` with:

```python
def run(target, env_name, pattern, mutants, survivors):
    source = target.read_text()
    for name, old, new in mutants:
        count = source.count(old)
        if count != 1:
            print('BROKEN MUTANT %r: anchor found %d times' % (name, count))
            return False
        with tempfile.TemporaryDirectory() as tmp:
            mutant = pathlib.Path(tmp) / target.name
            mutant.write_text(source.replace(old, new))
            env = dict(os.environ, **{env_name: str(mutant)})
            proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'),
                                   '-p', pattern], env=env, capture_output=True, text=True)
        killed = proc.returncode != 0
        print('%-8s %s' % ('killed' if killed else 'SURVIVED', name))
        if not killed:
            survivors.append(name)
    return True


def main():
    survivors = []
    for args in ((LIVE, 'LIVE_PY', 'test_live.py', MUTANTS), (FLOWS, 'FLOWS_PY', 'test_flows.py', FLOW_MUTANTS)):
        if not run(*args, survivors):
            return 2
    return 1 if survivors else 0
```

- [ ] **Step 6: Run the mutants and watch every one die**

Run: `python3 tests/mutate.py; echo "exit=$?"`
Expected: 34 lines of `killed`, no `SURVIVED` or `BROKEN MUTANT`, and `exit=0`.

A mutant that survives is a missing test. Add one that fails on that mutant, then
rerun. Never delete the mutant.

- [ ] **Step 7: Commit**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py tests/mutate.py
git commit -m "flows.py: command line, firewall wiring, and mutants"
```

---

### Task 6: configd actions, installer, controller, ACL and metadata

**Files:**
- Create: `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php`
- Modify: `install.sh`, `tests/test_install.sh`
- Modify: `src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml`
- Modify: `src/opnsense/www/js/widgets/Metadata/TopDevices.xml`

**Interfaces:**
- Consumes: `flows.py`'s command line (Task 5).
- Produces:
  - the configd actions `topdevices flows totals FROM TO` and
    `topdevices flows device IP FROM TO`;
  - the API endpoints `GET /api/topdevices/flows/totals/{from}/{to}` and
    `GET /api/topdevices/flows/device/{ip}/{from}/{to}`, which return `flows.py`'s JSON or
    `{"error"}`.

- [ ] **Step 1: Extend the installer test so it fails**

In `tests/test_install.sh`, add these two lines to the `<<LIST` block, before `LIST`:

```
src/opnsense/scripts/topdevices/flows.py|/usr/local/opnsense/scripts/topdevices/flows.py|755
src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php|/usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php|644
```

Add after the line `grep -qx 'type:stream_output' "$A" || fail "live action is not a stream"`:

```sh
for a in 'flows.totals|totals|%s %s' 'flows.device|device|%s %s %s'; do
    name=${a%%|*}; rest=${a#*|}; mode=${rest%%|*}; params=${rest#*|}
    block=$(awk -v h="[$name]" '$0 == h {on = 1; next} /^\[/ {on = 0} on' "$A")
    echo "$block" | grep -qx "command:/usr/local/opnsense/scripts/topdevices/flows.py $mode" || fail "[$name] command wrong"
    echo "$block" | grep -qx "parameters:$params" || fail "[$name] parameters wrong"
    echo "$block" | grep -qx 'type:script_output' || fail "[$name] is not script_output"
done
grep -q '<pattern>api/topdevices/flows/\*</pattern>' "$R/usr/local/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml" \
    || fail "the ACL does not cover the flows endpoints"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `sh tests/test_install.sh`
Expected: `FAIL: not installed: /usr/local/opnsense/scripts/topdevices/flows.py`.

- [ ] **Step 3: Install the two files and actions**

In `install.sh`, in the `FILES="…"` list, add after the `LiveController.php` line:

```
$P/scripts/topdevices/flows.py|$SCRIPTS/flows.py
$P/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php|$MVC/controllers/OPNsense/TopDevices/Api/FlowsController.php
```

In the actions heredoc, add after `message:TopDevices live stream (%s s)` and before
`ACT`:

```

[flows.totals]
command:$SCRIPTS/flows.py totals
parameters:%s %s
type:script_output
message:TopDevices flows totals %s %s

[flows.device]
command:$SCRIPTS/flows.py device
parameters:%s %s %s
type:script_output
message:TopDevices flows device %s
```

In the header comment, replace `Six files is survivable, but` with
`Eight files is survivable, but`.

Replace the contents of `src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml` with:

```xml
<acl>
    <page-dashboard-topdevices-live>
        <name>Dashboard: Top Devices</name>
        <patterns>
            <pattern>api/topdevices/live/*</pattern>
            <pattern>api/topdevices/flows/*</pattern>
        </patterns>
    </page-dashboard-topdevices-live>
</acl>
```

In `src/opnsense/www/js/widgets/Metadata/TopDevices.xml`, add after
`<endpoint>/api/topdevices/live/*</endpoint>`:

```xml
            <endpoint>/api/topdevices/flows/*</endpoint>
```

Create `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php`:

```php
<?php

/*
 * TopDevices exact recent ranges: every device's totals, or one device's peers
 * and ports, for a window from NetFlow's raw flow log, through
 * scripts/topdevices/flows.py (configd). See
 * docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md
 */

namespace OPNsense\TopDevices\Api;

use OPNsense\Base\ApiControllerBase;
use OPNsense\Core\ACL;
use OPNsense\Core\Backend;

class FlowsController extends ApiControllerBase
{
    /* The same data reaches the dashboard through core's NetFlow exports; this
       endpoint must never show it to someone core itself would not. */
    private function mayReadNetflow()
    {
        return (new ACL())->isPageAccessible($this->getUserName(), '/api/diagnostics/networkinsight/export');
    }

    private function answer($action, $params)
    {
        $data = json_decode((string)(new Backend())->configdpRun($action, $params), true);
        return is_array($data) ? $data : ['error' => 'no answer from the flows script'];
    }

    /**
     * GET /api/topdevices/flows/totals/{from}/{to}
     * @param string $from epoch seconds
     * @param string $to epoch seconds
     */
    public function totalsAction($from = '', $to = '')
    {
        if (!$this->mayReadNetflow()) {
            return ['error' => 'this needs the Diagnostics: Network Insight privilege too'];
        }
        if (!ctype_digit((string)$from) || !ctype_digit((string)$to)) {
            return ['error' => 'from and to must be whole epoch seconds'];
        }
        return $this->answer('topdevices flows totals', [(string)$from, (string)$to]);
    }

    /**
     * GET /api/topdevices/flows/device/{ip}/{from}/{to}
     * @param string $ip the device's IPv4 address
     * @param string $from epoch seconds
     * @param string $to epoch seconds
     */
    public function deviceAction($ip = '', $from = '', $to = '')
    {
        if (!$this->mayReadNetflow()) {
            return ['error' => 'this needs the Diagnostics: Network Insight privilege too'];
        }
        if (filter_var($ip, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4) === false
            || !ctype_digit((string)$from) || !ctype_digit((string)$to)) {
            return ['error' => 'ip must be IPv4, and from and to whole epoch seconds'];
        }
        return $this->answer('topdevices flows device', [(string)$ip, (string)$from, (string)$to]);
    }
}
```

- [ ] **Step 4: Run the installer test and a PHP syntax check**

Run: `sh tests/test_install.sh && php -l src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php`
Expected: `install dry run: OK`, then
`No syntax errors detected in src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php`.

- [ ] **Step 5: Commit**

```bash
git add install.sh tests/test_install.sh src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php \
        src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml src/opnsense/www/js/widgets/Metadata/TopDevices.xml
git commit -m "flows endpoints: configd actions, controller with least privilege, ACL, installer"
```

---

### Task 7: The widget's raw path: load, both scopes, caption, fallback

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js`
- Modify: `tests/netflow_ranges.test.mjs`
- Modify: `tests/mutate_widget.mjs`

**Interfaces:**
- Consumes: the endpoint `/api/topdevices/flows/totals/{from}/{to}` and its JSON (Task 6).
- Produces:
  - the exports `rawRange(fromS, nowS) -> bool`,
    `rawRows(resp, scope) -> [{ip, down, up}]`, `rawSpan(resp, scope) -> [from, to]` and
    `fmtSpan(seconds) -> string`;
  - the methods `_flows(path) -> Promise<object>`, `_applyRaw(scope)`, `_rawCaption()` and
    `_exportCaption()`;
  - `state.raw` (the totals answer), `state.fallback` (bool), and `state.request.raw`
    (bool).
  - Spec §7 lists `rawCaption` among the pure exports. It is the method
    `_rawCaption()` here, because a caption uses the widget's `_dateStr`. Its pure
    part is `rawSpan`, and the tests reach it through `_windowCaption()`.

- [ ] **Step 1: Update the fallback-path tests, and write the new failing tests**

In `tests/netflow_ranges.test.mjs`, 0.1.2's export tests now run through the fallback.
Their stand-in answers a flows URL with CSV text, which `_flows` refuses because it is
not a JSON object, so the export path runs. Add after `const EXPORT = …`:

```js
const FLOWS = '/api/topdevices/flows';
const FALLBACK = "The raw flow log could not be read: showing NetFlow's records";
```

Make these exact changes to existing tests:

- `'the caption gives the span the figures cover'`: rename it
  `'on the fallback path, the caption gives the span the figures cover'`, and change
  `assert.equal(w._windowCaption().note, null);` to
  `assert.equal(w._windowCaption().note, FALLBACK);`.
- `'Internet only on a sub-day range says it is kept per day, and from when'`: replace
  its `assert.match(...)` with
  ``assert.equal(w._windowCaption().note, `${FALLBACK} · Internet only is kept per day (days start at 10:00): these cover 10:00 → 19:08`);``.
- `'Internet only · Last hour right after 10:00 still says it is kept per day'`:
  - change the expected string to
    ``` `${FALLBACK} · Internet only is kept per day (days start at 10:00): these cover 10:00 → ${shown}` ```;
  - change the final `assert.equal(w._windowCaption().note, null);` to
    `assert.equal(w._windowCaption().note, FALLBACK);`.
- `'no note when the buckets fit the range'`: rename it
  `'on the fallback path, the only note is the fallback\'s when the buckets fit'`, and
  change `assert.equal(w._windowCaption().note, null, range);` to
  `assert.equal(w._windowCaption().note, FALLBACK, range);`.
- `'Today in a half-hour time zone starts at the nearest UTC hour'`: change
  `assert.equal(w._windowCaption().note, null);` to
  `assert.equal(w._windowCaption().note, FALLBACK);`.
- `'in UTC, Internet only · Last hour just after midnight still says it is kept per day'`:
  change the expected string to
  ``` `${FALLBACK} · Internet only is kept per day (days start at 00:00): these cover 00:00 → 00:20` ```.
- `'switching scope and back reads each export once, for the window on screen'`:
  - rename it `'on the fallback path, switching scope and back reads each export once'`;
  - change its expected requests to
    ``[`${FLOWS}/totals/${NOW - 3600}/${NOW}`, `${EXPORT}/FlowSourceAddrDetails/${S(0, 0, 23)}/${NOW}/86400`, `${FLOWS}/totals/${NOW - 3600}/${NOW}`]``.

Append the new tests:

```js
// --- recent ranges from the raw flow log -----------------------------------------

// a flows/totals answer as flows.py gives it (spec §5.1)
function totalsAnswer(from, to, { L = from - 3600, hourlyUntil = null,
                                  devices = { '192.168.1.10': [1000, 357, 1000, 300], '192.168.20.5': [50, 0, 0, 0] } } = {}) {
    return { v: '0.2.0', now: to, log_from: L,
             all: { from, to, hourly_until: hourlyUntil }, inet: { from: Math.max(from, L), to },
             wan: ['em0'], devices };
}
const flowsOnly = (answer) => (url) => (url.startsWith(`${FLOWS}/`) ? answer(url) : null);

async function loadRaw(range, answer, now = NOW, scope = 'all') {
    const w = widget(range, scope);
    reply = flowsOnly(answer);
    const before = requests.length;
    await w._load(now * 1000);
    return { w, made: requests.slice(before) };
}

test('a range starting within the last day, and not in the future, is read from the raw log', () => {
    assert.equal(m.rawRange(NOW - 86400, NOW), true);
    assert.equal(m.rawRange(NOW - 86401, NOW), false);
    assert.equal(m.rawRange(NOW, NOW), false);
});

test('spans are told rounded down to the minute', () => {
    assert.equal(m.fmtSpan(22 * 3600 + 40 * 60 + 59), '22 h 40 min');
    assert.equal(m.fmtSpan(3600), '1 h 0 min');
    assert.equal(m.fmtSpan(59), '0 min');
});

test('Last hour reads the raw log for exactly the last hour', async () => {
    const { w, made } = await loadRaw('1h', () => totalsAnswer(NOW - 3600, NOW));
    assert.deepEqual(made, [`${FLOWS}/totals/${NOW - 3600}/${NOW}`]);
    assert.deepEqual(w.state.window, [NOW - 3600, NOW]);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
    assert.deepEqual(w._windowCaption(), {
        text: 'Wed Sep 23 18:08:54 AEST 2026  →  Wed Sep 23 19:08:54 AEST 2026 · all traffic', note: null });
});

test('Today and Last 24 hours read the raw log from local midnight and from a day back', async () => {
    let r = await loadRaw('today', () => totalsAnswer(S(14, 0, 22), NOW));
    assert.deepEqual(r.made, [`${FLOWS}/totals/${S(14, 0, 22)}/${NOW}`]);
    assert.match(r.w._windowCaption().text, /^Wed Sep 23 00:00:00 AEST 2026 {2}→ {2}Wed Sep 23 19:08:54 AEST 2026/);
    r = await loadRaw('24h', () => totalsAnswer(NOW - 86400, NOW));
    assert.deepEqual(r.made, [`${FLOWS}/totals/${NOW - 86400}/${NOW}`]);
});

test('Yesterday and Last 7 days still read NetFlow\'s records', async () => {
    for (const range of ['yesterday', '7d']) {
        const before = requests.length;
        await load(range);
        assert.ok(!requests.slice(before).some(u => u.startsWith(`${FLOWS}/`)), range);
    }
});

test('both scopes come from one answer: switching scope sends no request', async () => {
    const { w } = await loadRaw('1h', () => totalsAnswer(NOW - 3600, NOW));
    const before = requests.length;
    w.state.scope = 'wan';
    await w.render();
    assert.equal(requests.length, before);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 300] });            // devices with internet traffic only
    assert.match(dom['.td-window small'], / · internet only \(via em0\)$/);
});

test('internet only reaching past the log says how much it covers', async () => {
    const L = NOW - 3000;
    const { w } = await loadRaw('1h', () => totalsAnswer(NOW - 3600, NOW, { L }), NOW, 'wan');
    const c = w._windowCaption();
    assert.match(c.text, /^Wed Sep 23 18:18:54 AEST 2026 {2}→/);
    assert.equal(c.note, 'Internet only covers the last 50 min: older flows are no longer in the log');
});

test('internet only for a range wholly before the log says so', async () => {
    const w = widget('custom', 'wan');
    w.state.customFrom = '2026-09-23T17:00';
    w.state.customTo = '2026-09-23T17:30';
    // the log starts after the range: no internet-only span, so no internet bytes
    reply = flowsOnly(() => totalsAnswer(S(7, 0), S(7, 30), { L: S(8, 0), devices: { '192.168.1.10': [1000, 357, 0, 0] } }));
    await w._load(NOW * 1000);
    assert.deepEqual(byIp(w), {});
    assert.equal(w._windowCaption().note, 'Internet only: no flows in the log for this range');
});

test('all traffic filled from hourly records shows the whole range with no note', async () => {
    const { w } = await loadRaw('24h', () => totalsAnswer(NOW - 86400, NOW, { L: NOW - 80000, hourlyUntil: NOW - 79200 }));
    assert.deepEqual(w._windowCaption(), {
        text: 'Tue Sep 22 19:08:54 AEST 2026  →  Wed Sep 23 19:08:54 AEST 2026 · all traffic', note: null });
});

test('a failed or refused raw read falls back to NetFlow\'s records, and says so', async () => {
    for (const refused of [null, { error: 'the NetFlow flow log holds no flows yet' }]) {
        const w = widget('1h');
        reply = (url) => (url.startsWith(`${FLOWS}/`) ? refused : serve(url));
        await w._load(NOW * 1000);
        assert.equal(requests[requests.length - 1], `${EXPORT}/FlowSourceAddrTotals/${S(8, 10)}/${NOW}/300`);
        assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
        assert.equal(w._windowCaption().note, FALLBACK);
    }
});

test('a raw answer that lands after a newer load is dropped', async () => {
    const w = widget('1h');
    let release;
    reply = flowsOnly((url) => (url.includes(`/${NOW - 3600}/`)
        ? new Promise(r => { release = () => r(totalsAnswer(NOW - 3600, NOW)); })
        : totalsAnswer(S(14, 0, 22), NOW)));
    const first = w._load(NOW * 1000);
    await tick();
    w.state.range = 'today';
    await w._load(NOW * 1000);
    release();
    assert.equal(await first, false);
    assert.deepEqual(w.state.window, [S(14, 0, 22), NOW]);
});
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `node --test tests/netflow_ranges.test.mjs`
Expected:
- the new tests fail, with `m.rawRange is not a function` and the like;
- the updated fallback tests fail on their `FALLBACK` expectations, because no fallback
  note exists yet;
- everything else passes.

- [ ] **Step 3: Write the raw path**

In `TopDevices.js`, add after `deviceTotals` (before `export function fmtRate`):

```js
/* ---------- recent ranges from the raw flow log (tests/netflow_ranges.test.mjs) ---------- */

// A range that starts within the last day - and not in the future, where nothing
// is recorded yet - is read from NetFlow's raw flow log through the plugin's flows
// endpoints: exact to the second, both scopes in one answer (spec
// 2026-09-23-raw-flow-ranges §4). Older ranges read NetFlow's records (nfPlan).
export function rawRange(fromS, nowS) {
    return fromS >= nowS - DAY && fromS < nowS;
}

// One scope's rows from a flows/totals answer, whose devices hold
// [down, up, internet down, internet up].
export function rawRows(resp, scope) {
    const k = scope === 'wan' ? 2 : 0;
    return Object.entries(resp.devices || {})
        .map(([ip, v]) => ({ ip: ip, down: v[k], up: v[k + 1] }))
        .filter(r => r.down + r.up > 0);
}

// The span a scope's figures cover in a flows/totals answer.
export function rawSpan(resp, scope) {
    const s = scope === 'wan' ? resp.inet : resp.all;
    return [s.from, s.to];
}

// "23 h 0 min", "45 min": a span, rounded down to the minute
export function fmtSpan(seconds) {
    const m = Math.floor(Math.max(0, seconds) / 60);
    return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`;
}

const FALLBACK_NOTE = "The raw flow log could not be read: showing NetFlow's records";
```

Replace `async _load(nowMs = Date.now(), scope = this.state.scope) {` through the line
`        const plan = nfPlan(from, to, now, scope);` with:

```js
    async _load(nowMs = Date.now(), scope = this.state.scope) {
        const token = this._loadToken = (this._loadToken || 0) + 1;
        const now = Math.floor(nowMs / 1000);
        const [from, to] = this._window(this.state.range, nowMs);
        let fallback = false;
        if (rawRange(from, now)) {
            const end = Math.min(to, now);
            let resp = null;
            try { resp = await this._flows(`totals/${from}/${end}`); } catch (e) { resp = null; }
            if (token !== this._loadToken) return false;
            if (resp) {
                this.state.raw = resp;
                this.state.plan = null;
                this.state.fallback = false;
                this.state.request = { from, to: end, now, scope, raw: true };
                this._applyRaw(scope);
                return true;
            }
            fallback = true;                     // say so, and read NetFlow's records instead
        }
        const plan = nfPlan(from, to, now, scope);
```

In the same method, replace
`        this.state.request = { from, to, now, scope };        // what the range asked for` with:

```js
        this.state.request = { from, to, now, scope };        // what the range asked for
        this.state.raw = null;
        this.state.fallback = fallback;
```

Add after the end of `_load` (before `// A load that failed leaves no rows it cannot vouch for`):

```js
    // A flows/ answer from the plugin's own endpoint. $.ajax, not ajaxCall: that
    // gives up after 5 s and retries, and every retry reads the whole log again.
    // Only a JSON object without an error is an answer.
    _flows(path) {
        return new Promise((resolve, reject) => {
            $.ajax({ url: `/api/topdevices/flows/${path}`, dataType: 'json', timeout: 60000 })
                .done((r) => (r && typeof r === 'object' && !r.error ? resolve(r)
                    : reject(new Error((r && r.error) || 'no answer'))))
                .fail(() => reject(new Error('flows request failed')));
        });
    }

    // Rows, span and scope from the raw answer in hand: a scope switch needs no request.
    _applyRaw(scope) {
        const r = this.state.raw;
        this.state.rows = rawRows(r, scope).map(d => ({
            ip: d.ip, name: this.names[d.ip] || (this.ifaceNames || {})[d.ip] || '', net: this._netOf(d.ip),
            down: d.down, up: d.up, total: d.down + d.up
        }));
        this.state.window = rawSpan(r, scope);
        this.state.request = Object.assign({}, this.state.request, { scope });
    }
```

Rename `    _windowCaption() {` to `    _exportCaption() {` (keeping its comment and body), and
insert before it:

```js
    // What the caption says: the raw answer's span, or the export's (spec §4).
    _windowCaption() {
        if (this.state.request.raw) return this._rawCaption();
        const c = this._exportCaption();
        if (this.state.fallback) c.note = c.note ? `${FALLBACK_NOTE} · ${c.note}` : FALLBACK_NOTE;
        return c;
    }

    // The raw answer's caption: the span the scope covers, and a note only when
    // internet only reaches past the log.
    _rawCaption() {
        const r = this.state.raw, q = this.state.request;
        const wan = q.scope === 'wan';
        const tail = wan ? ` · internet only (via ${(r.wan || []).join(', ') || 'WAN'})` : ' · all traffic';
        const [a, b] = rawSpan(r, q.scope);
        if (wan && a >= b) {
            return { text: `${this._dateStr(q.from)}  →  ${this._dateStr(q.to)}${tail}`,
                     note: 'Internet only: no flows in the log for this range' };
        }
        const note = wan && a > q.from
            ? `Internet only covers the last ${fmtSpan(b - a)}: older flows are no longer in the log` : null;
        return { text: `${this._dateStr(a)}  →  ${this._dateStr(b)}${tail}`, note };
    }
```

In `_render()`, replace:

```js
        let q = this.state.request;
        while (this.state.window && q && q.scope !== this.state.scope) {
```

with:

```js
        let q = this.state.request;
        if (q && q.raw && q.scope !== this.state.scope) this._applyRaw(this.state.scope);   // both scopes in hand
        q = this.state.request;
        while (this.state.window && q && !q.raw && q.scope !== this.state.scope) {
```

and replace
`        const caption = this.state.window && this.state.plan ? this._windowCaption() : null;` with
`        const caption = this.state.window && this.state.request ? this._windowCaption() : null;`.

- [ ] **Step 4: Run all widget tests and watch them pass**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: every test passes, with `ℹ fail 0`.

- [ ] **Step 5: Update the widget mutants**

In `tests/mutate_widget.mjs`, update two existing mutants whose anchors the raw path
changes or duplicates:

- Replace the anchor of `'a scope change reloads nothing'` with
  `'        while (this.state.window && q && !q.raw && q.scope !== this.state.scope) {'`.
- Replace `'a superseded load still lands'` with
  `['a superseded load still lands', '        const flows = await this._export(plan);\n        if (token !== this._loadToken) return false;\n', '        const flows = await this._export(plan);\n'],`.
  The raw path adds a second, deeper-indented token check, which contains the old
  anchor.

Append to the `mutants` list:

```js
    // recent ranges from the raw flow log
    ['the raw log used for ranges older than a day', '    return fromS >= nowS - DAY && fromS < nowS;', '    return fromS < nowS;'],
    ['the raw log asked for a range in the future', '    return fromS >= nowS - DAY && fromS < nowS;', '    return fromS >= nowS - DAY;'],
    ['internet only read from the all-traffic columns', "    const k = scope === 'wan' ? 2 : 0;", '    const k = 0;'],
    ['devices without traffic in the scope listed', '        .filter(r => r.down + r.up > 0);', ';'],
    ['spans rounded to the nearest minute', '    const m = Math.floor(Math.max(0, seconds) / 60);', '    const m = Math.round(Math.max(0, seconds) / 60);'],
    ['a string taken for a raw answer', "                .done((r) => (r && typeof r === 'object' && !r.error ? resolve(r)",
     '                .done((r) => (r ? resolve(r)'],
    ['a stale raw answer lands', '            if (token !== this._loadToken) return false;\n            if (resp) {', '            if (resp) {'],
    ['a failed raw read shows nothing', '            fallback = true;                     // say so', '            return false;                        // say so'],
    ["no fallback note", '        if (this.state.fallback) c.note = c.note ? `${FALLBACK_NOTE} · ${c.note}` : FALLBACK_NOTE;\n', ''],
    ['a scope switch leaves the other scope\'s rows', '        if (q && q.raw && q.scope !== this.state.scope) this._applyRaw(this.state.scope);', ''],
    ['the raw caption shows the range asked for', '        return { text: `${this._dateStr(a)}  →  ${this._dateStr(b)}${tail}`, note };',
     '        return { text: `${this._dateStr(q.from)}  →  ${this._dateStr(q.to)}${tail}`, note };'],
    ['no note when internet only reaches past the log', '        const note = wan && a > q.from', '        const note = false && a > q.from'],
    ['no word when internet only has nothing in the log', '        if (wan && a >= b) {', '        if (false) {'],
```

- [ ] **Step 6: Run the mutants and watch every one die**

Run: `node tests/mutate_widget.mjs > "$TMPDIR/mut.txt" 2>&1; echo "exit=$?"; grep -vc '^killed' "$TMPDIR/mut.txt"`
Expected: `exit=0` and `0`, meaning no survivors and no broken anchors. That is 89 mutants
killed: 76 earlier plus 13 new.

- [ ] **Step 7: Commit**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/netflow_ranges.test.mjs tests/mutate_widget.mjs
git commit -m "widget: recent ranges from the raw flow log, both scopes in one answer, fallback"
```

---

### Task 8: The widget's device panel from the raw log

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js`
- Modify: `tests/netflow_ranges.test.mjs`
- Modify: `tests/mutate_widget.mjs`

**Interfaces:**
- Consumes: `_flows`, `state.request.raw`, `fmtSpan` (Task 7), and the endpoint
  `/api/topdevices/flows/device/{ip}/{from}/{to}` (Task 6).
- Produces: `_rawDetails(ip) -> {peers, ports, down, up, note}`, the same shape as
  `_detailsData`; and `this._device = {key, resp}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/netflow_ranges.test.mjs`:

```js
// a flows/device answer as flows.py gives it (spec §5.1)
function deviceAnswer(from, to) {
    return { v: '0.2.0', ip: '192.168.1.10', from, to, log_from: from,
             peers: { all: [['8.8.8.8', 1000], ['1.1.1.1', 300], ['192.168.20.5', 50]], inet: [['8.8.8.8', 1000], ['1.1.1.1', 300]] },
             ports: { all: [['443', 1300], ['445', 50]], inet: [['443', 1300]] } };
}

test('the device panel reads the raw log for the table\'s window, and a scope switch reuses it', async () => {
    const { w } = await loadRaw('1h', (url) => (url.includes('/device/') ? deviceAnswer(NOW - 3600, NOW)
        : totalsAnswer(NOW - 3600, NOW)));
    const before = requests.length;
    let d = await w._detailsData('192.168.1.10');
    assert.deepEqual(requests.slice(before), [`${FLOWS}/device/192.168.1.10/${NOW - 3600}/${NOW}`]);
    assert.deepEqual(d, { peers: { '8.8.8.8': 1000, '1.1.1.1': 300, '192.168.20.5': 50 }, ports: { 443: 1300, 445: 50 },
                          down: 1000, up: 357, note: null });
    w.state.scope = 'wan';
    await w.render();
    d = await w._detailsData('192.168.1.10');
    assert.equal(requests.length, before + 1);
    assert.deepEqual([d.peers, d.down, d.up], [{ '8.8.8.8': 1000, '1.1.1.1': 300 }, 1000, 300]);
});

test('the device panel says when its lists cover less than the range', async () => {
    const { w } = await loadRaw('1h', (url) => (url.includes('/device/') ? deviceAnswer(NOW - 3000, NOW)
        : totalsAnswer(NOW - 3600, NOW)));
    assert.equal((await w._detailsData('192.168.1.10')).note, 'Peers and ports cover the last 50 min');
});

test('a refused panel read is an error, which the panel shows as unavailable', async () => {
    const { w } = await loadRaw('1h', (url) => (url.includes('/device/') ? { error: '192.168.1.99 is not a local device' }
        : totalsAnswer(NOW - 3600, NOW)));
    await assert.rejects(w._detailsData('192.168.1.99'));
});
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `node --test tests/netflow_ranges.test.mjs`
Expected: the three new tests fail. The panel still reads the daily details
(`requests` shows an `FlowSourceAddrDetails` URL).

- [ ] **Step 3: Write the raw panel**

In `TopDevices.js`, replace the first line of `_detailsData`'s body,
`        const q = this.state.request, table = this.state.plan;`, with:

```js
        if (this.state.request && this.state.request.raw) return this._rawDetails(ip);
        const q = this.state.request, table = this.state.plan;
```

Add before `    async _detailsData(ip) {`:

```js
    // The device panel from the raw log, for exactly the table's window. The answer
    // holds both scopes, so a scope switch redraws the panel without a request.
    async _rawDetails(ip) {
        const q = this.state.request;
        const key = `device/${ip}/${q.from}/${q.to}`;
        if (!this._device || this._device.key !== key) this._device = { key, resp: await this._flows(key) };
        const d = this._device.resp;
        const k = q.scope === 'wan' ? 'inet' : 'all';
        const row = this.state.rows.find(x => x.ip === ip);
        return {
            peers: Object.fromEntries(d.peers[k]), ports: Object.fromEntries(d.ports[k]),
            down: row ? row.down : 0, up: row ? row.up : 0,
            note: d.from > q.from ? `Peers and ports cover the last ${fmtSpan(d.to - d.from)}` : null
        };
    }
```

In `refresh(force)`, replace
`        if (force) this.cache = {};          // drop the cached export on an explicit refresh` with:

```js
        if (force) { this.cache = {}; this._device = null; }   // an explicit refresh reads everything again
```

- [ ] **Step 4: Run all widget tests and watch them pass**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ fail 0`.

- [ ] **Step 5: Add the panel's mutants, and watch them die**

Append to `mutants` in `tests/mutate_widget.mjs`:

```js
    ['the raw panel re-read on a scope switch', '        if (!this._device || this._device.key !== key)', '        if (true)'],
    ["the raw panel's internet lists taken from all traffic", "        const k = q.scope === 'wan' ? 'inet' : 'all';", "        const k = 'all';"],
    ['no note when the raw panel covers less', '            note: d.from > q.from ? ', '            note: false ? '],
    ['the panel reads the daily details on a raw range', '        if (this.state.request && this.state.request.raw) return this._rawDetails(ip);\n', ''],
```

Run: `node tests/mutate_widget.mjs > "$TMPDIR/mut.txt" 2>&1; echo "exit=$?"; grep -vc '^killed' "$TMPDIR/mut.txt"`
Expected: `exit=0` and `0`, with 93 mutants killed.

- [ ] **Step 6: Commit**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/netflow_ranges.test.mjs tests/mutate_widget.mjs
git commit -m "widget: the device panel from the raw flow log"
```

---

### Task 9: The firewall check script, docs and version 0.2.0

**Files:**
- Create: `tests/parity_flows.py`
- Modify: `README.md`, `pkg-descr`, `Makefile`
- Modify: `src/opnsense/scripts/topdevices/live.py` (`VERSION`)

**Interfaces:**
- Consumes:
  - `flows.open_log`, `log_from`, `answer_totals`, `answer_device`, `system_net`,
    `core_hourly` and `records`;
  - core's `lib.parse.parse_flow`;
  - core's `lib.aggregates.source.FlowSourceAddrTotals` and `FlowSourceAddrDetails`.
- Produces: `python3 tests/parity_flows.py` on the firewall. It prints agreement and
  timings, and exits 0 when both comparisons agree to under 1 byte per device.

- [ ] **Step 1: Write the firewall check**

Create `tests/parity_flows.py`:

```python
"""On the firewall: flows.py against core's own parser and aggregators, over the
live raw flow log, and how long flows.py takes as configd runs it.

    python3 tests/parity_flows.py        (as root, from the extracted branch tarball)

Prints counts, timings and agreement only - no addresses. Exit status 0 when both
comparisons agree to under 1 byte per device.
"""
import copy
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / 'src/opnsense/scripts/topdevices'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gap(ours, core):
    ips = set(ours) | set(core)
    worst = max((abs(ours.get(i, [0, 0])[k] - core.get(i, [0, 0])[k]) for i in ips for k in (0, 1)), default=0.0)
    return len(ips), worst


def main():
    load('live', SRC / 'live.py')
    flows = load('flows', SRC / 'flows.py')
    flows._core()
    from lib.parse import parse_flow
    from lib.aggregates.source import FlowSourceAddrDetails, FlowSourceAddrTotals
    now = int(time.time())
    net = flows.system_net()
    opened = flows.open_log()
    L = flows.log_from(opened)
    print('log: %d files, complete from %s (%.1f h); upstream %s' % (len(opened), time.ctime(L), (now - L) / 3600,
                                                                   net.upstream_names))

    # windows inside the log, ended 45 min ago: no record still to come can touch them
    hour = -(-L // 3600) * 3600
    if hour + 3600 > now - 2700:
        print('the log is too short for the comparison (needs an hour ending 45 min ago)')
        return 1
    five = hour + 900, hour + 2700

    tmp = tempfile.mkdtemp(prefix='parity_flows.')
    try:
        totals, details = FlowSourceAddrTotals(3600, tmp), FlowSourceAddrDetails(300, tmp)
        n = 0
        for r in parse_flow(hour - 3600, flows.LOG):       # every record received since an hour before
            if r is None:
                continue
            totals.add(copy.copy(r))
            details.add(copy.copy(r))
            n += 1
        totals.commit()
        details.commit()
        core_all, core_inet = {}, {}
        for row in totals.get_data(hour, hour + 3600):
            ip = flows._ip4(row['src_addr'])
            if ip is not None and net.is_device(ip):
                t = core_all.setdefault(ip, [0.0, 0.0])
                t[0 if row['direction'] == 'out' else 1] += float(row['octets'] or 0)
        for row in details.get_data(*five):
            ip = flows._ip4(row['dst_addr'])
            if ip is not None and net.is_device(ip) and row['if'] in net.upstream_names:
                t = core_inet.setdefault(ip, [0.0, 0.0])
                t[1 if row['direction'] == 'out' else 0] += float(row['octets'] or 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    workers = max(1, (os.cpu_count() or 2) // 2)
    a = flows.answer_totals(hour, hour + 3600, now, opened, net, flows.core_hourly, workers)
    i = flows.answer_totals(five[0], five[1], now, opened, net, flows.core_hourly, workers)
    ours_all = {flows._ip4(k): v[:2] for k, v in a['devices'].items()}
    ours_inet = {flows._ip4(k): v[2:] for k, v in i['devices'].items() if v[2] or v[3]}
    dev_a, gap_a = gap(ours_all, core_all)
    dev_i, gap_i = gap(ours_inet, core_inet)
    print('core read %d records once, into scratch aggregates' % n)
    print('all traffic, %s-%s:   %3d devices, largest per-device gap %.3f B'
          % (time.strftime('%H:%M', time.localtime(hour)), time.strftime('%H:%M', time.localtime(hour + 3600)), dev_a, gap_a))
    print('internet only, %s-%s: %3d devices, largest per-device gap %.3f B'
          % (time.strftime('%H:%M', time.localtime(five[0])), time.strftime('%H:%M', time.localtime(five[1])), dev_i, gap_i))

    # as configd runs it, through the installed action when there is one
    lt = time.localtime(now)
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    for label, frm in (('Last hour', now - 3600), ('Today', midnight), ('Last 24 hours', now - 86400)):
        t = time.monotonic()
        out = subprocess.run(['/usr/local/sbin/configctl', 'topdevices', 'flows', 'totals', str(frm), str(now)],
                             capture_output=True, text=True).stdout
        wall = time.monotonic() - t
        try:
            ans = json.loads(out)
        except ValueError:
            print('%-14s configctl gave no JSON: %r' % (label, out[:80]))
            continue
        if 'error' in ans:
            print('%-14s error: %s' % (label, ans['error']))
            continue
        print('%-14s via configd %.2f s (script %d ms, %d files, %d workers)%s'
              % (label, wall, ans['cost_ms'], ans['files'], ans['workers'],
                 '' if ans['inet']['from'] <= frm else ', internet only from the log start'))

    # spec §14: do this firewall's records carry ports (the device panel's Top ports)?
    buf, off, total, with_ports = flows._read(opened[-1][0]), 0, 0, 0
    bit = 1 << [name for name, _ in flows.FIELDS].index('srcdst_port')
    while off + 8 <= len(buf):
        _, words, _, mask = flows.HEAD.unpack_from(buf, off)
        off += 8 + words * 4
        total += 1
        with_ports += bool(mask & bit)
    print('records in the newest file: %d, %d of them with ports' % (total, with_ports))
    flows.close_log(opened)
    return 0 if gap_a < 1 and gap_i < 1 else 1


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 2: Syntax-check it locally**

Run: `python3 -m py_compile tests/parity_flows.py && echo compiled`
Expected: `compiled`. It can only run on the firewall (Task 10).

- [ ] **Step 3: Set the version to 0.2.0**

- `Makefile`: `PLUGIN_VERSION=		0.1.2` becomes `PLUGIN_VERSION=		0.2.0`.
- `live.py`: `VERSION = '0.1.2'` becomes `VERSION = '0.2.0'`. `flows.py` already says
  0.2.0.
- `pkg-descr`: insert above the `0.1.2` entry:

```
0.2.0

* Last hour, Today, Last 24 hours and custom ranges starting within the last
  day are exact to the second in both scopes: read on demand from NetFlow's
  raw flow log (about a day of flows), with no cron job and no database
* Where a range reaches past the log, all traffic is filled in from NetFlow's
  hourly records, and internet only says how much it covers
* The device panel's peers and ports cover exactly the table's window
* The privilege is now called "Dashboard: Top Devices"; it also needs
  "Diagnostics: Network Insight" to read the new endpoints
```

- [ ] **Step 4: Update the README**

Make these edits in `README.md`:

1. **`## Upstream API limitations`,** item 2. After the paragraph ending `and switching
   back is served from the cache.`, add:

```
3. **Since 0.2.0, recent ranges read the raw flow log instead.** A range that starts
   within the last day - Last hour, Today, Last 24 hours, or a custom one - is read by
   the plugin's `flows.py` from `/var/log/flowd.log` and its rotations, which hold
   every flow with its interfaces. It is exact to the second in both scopes, and
   agrees to the byte with core's own parser and aggregators run over the same log.
   Core keeps that log by size, not time: about 110 MB, which is roughly a day on the
   reference install and less on a busy network. Where a range reaches further back,
   all traffic is filled in from core's hourly records (the oldest hour counted in
   proportion), and internet only says how much it covers. The table above still
   applies to older ranges, and to recent ones when the raw log cannot be read (the
   caption says so). NetFlow reports a long connection every 30 minutes
   (`activeTimeout`), so the most recent half hour can under-count a long download.
   One known edge: while core holds rows stamped in the future (after the firewall's
   clock jumped back), its cleanup can drop the oldest hourly record early, and Last
   24 hours can then be short by up to that hour's part of the range, with no note.
```

2. **`**Access.**` under Live traffic:** replace `The stream needs the *Dashboard: Top
   Devices live traffic*` with `The stream and the recent ranges need the *Dashboard:
   Top Devices*`. At the end of that bullet, add
   ` The recent ranges also need *Diagnostics: Network Insight*, so they never show more than core's own NetFlow pages would.`
3. **Upgrade note:** replace `0.1.0 on, non-root users need the *Dashboard: Top Devices
   live traffic* privilege,` with `0.1.0 on, non-root users need the *Dashboard: Top
   Devices* privilege (called *… live traffic* before 0.2.0),`.
4. **Codeload paragraph:** replace `installs all six files byte-identically` with
   `installs all eight files byte-identically`.
5. **`## Tests`:** add after the `node --test tests/netflow_ranges.test.mjs` line:

```
    OPNSENSE_CORE=<core checkout> python3 -m unittest tests.test_flows -v   # flows.py against core's parser and aggregators
```

   and add after the paragraph mentioning `tests/parity_live.py`:
   `On the firewall, python3 tests/parity_flows.py compares flows.py with core's own code over the live log and times it through configd.`
6. **`## What has been verified`:** add a `**Recent ranges (0.2.0)**` paragraph with the
   figures Task 10 measures. Write it after Task 10, never before.

- [ ] **Step 5: Run everything**

Run: `python3 -m unittest discover -s tests && node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs && sh tests/test_install.sh && python3 tests/mutate.py > "$TMPDIR/m1.txt"; echo "py=$?"; node tests/mutate_widget.mjs > "$TMPDIR/m2.txt"; echo "js=$?"`
Expected:
- `OK (skipped=…)` (only `CoreParity`, when core is absent);
- `ℹ fail 0`;
- `install dry run: OK`;
- `py=0` and `js=0`.

- [ ] **Step 6: Commit**

```bash
git add tests/parity_flows.py README.md pkg-descr Makefile src/opnsense/scripts/topdevices/live.py
git commit -m "0.2.0: firewall check script, docs, version"
```

---

### Task 10: Verification on the firewall, review, and release gates

**Files:**
- Modify: `README.md` (the verification paragraph, with measured figures)

**Interfaces:**
- Consumes: everything above.
- Produces: a verified branch. With the user's approval, `main` is merged, `0.2.0` is
  tagged, and the release is published.

- [ ] **Step 1: Push the branch and have the user install it**

Run: `git push -u origin raw-flows`

Ask the user to run, as root (csh):

```
env GH_REF=raw-flows sh /usr/local/opnsense/scripts/topdevices/install.sh
```

Expected output:
- `flows.py` and `FlowsController.php` among 8 files;
- `configd actions changed - configd restarts in 1 s`;
- `ACL cache cleared`.

- [ ] **Step 2: Check the deployed files against the dry run**

Run: `R=$(mktemp -d "$TMPDIR/tdroot.XXXXXX") && mkdir -p "$R/usr/local/opnsense/www/js/widgets/Metadata" "$R/usr/local/opnsense/service/conf/actions.d" && ROOT="$R" sh install.sh >/dev/null && (cd "$R/usr/local/opnsense" && find . -type f | sort | while read f; do printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -c1-12)" "${f#./}"; done); rm -rf "$R"`

Ask the user for the same `sha256 -r … | cut -c1-12,65-` over the seven files that
aren't web-served. Fetch the two served widget files with the API key, and compare
every hash.

- [ ] **Step 3: Run the core parity tests and the firewall check on the firewall**

Ask the user to run:
```
fetch -qo /tmp/rf.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/raw-flows && rm -rf /tmp/rf && mkdir /tmp/rf && tar -xzf /tmp/rf.tgz -C /tmp/rf && cd /tmp/rf/opnsense-plugin-topdevices-raw-flows && python3 -m unittest tests.test_flows && python3 tests/parity_flows.py; php -l /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php
```

Expected:
- the unit tests `OK`, with nothing skipped, since core's library is present;
- both parity lines with a largest gap under 1 B;
- timings through configd within §1's targets: Last hour ≤ 1 s, Today and Last 24 hours
  ≤ 2 s;
- `No syntax errors`.

If a comparison disagrees, stop and use superpowers:systematic-debugging.

Then: `rm -rf /tmp/rf /tmp/rf.tgz`.

- [ ] **Step 4: Call the endpoints from the Mac**

These need the sandbox off for the LAN. With the scratchpad's `api.sh`:
- `totals/<now-3600>/<now>`: JSON with `devices`.
- `device/<a device>/<now-3600>/<now>`: `peers` and `ports`.
- `totals/abc/1`, `device/fd00::1/1/2`, and a `from` two days back: each an `{"error"}`.

The least-privilege refusal can't be exercised, because only root exists there. Record
that.

- [ ] **Step 5: The widget end to end**

1. Rerun the scratchpad's `e2e_netflow.mjs` against the firewall. For raw ranges, the
   widget's figures must equal `flows.py`'s answer.
2. Extend the scratchpad's `harness/build_nf.py`. Its `$.ajax` stand-in answers
   `/api/topdevices/flows/totals/…` and `/device/…` URLs with answers built from its
   synthetic flows, in the shapes of `totalsAnswer`/`deviceAnswer` in
   `tests/netflow_ranges.test.mjs`, and answers `null` for one run to force the
   fallback. Run it in headless Chrome, and check:
   - the exact captions;
   - the Internet-only notes;
   - the device panel and its note;
   - the fallback note.
3. Ask the user to hard-refresh the dashboard and read the three captions from spec
   §1:
   - Last hour `… 19:28:32 → … 20:28:32 · internet only (via em0)`;
   - Last 24 hours `Tue … → Wed …`, to the second;
   - Today `… 00:00:00 → …`.

- [ ] **Step 6: The whole-branch review**

Dispatch a fresh reviewer on the most capable model, using
superpowers:requesting-code-review over `fix-0.1.2..raw-flows`, with this plan's Review
Focus. Fix every Critical and Important finding test-first. Minor findings go to the
user.

- [ ] **Step 7: Record the verification**

Write the README's `**Recent ranges (0.2.0)**` paragraph from the measured figures:
parity gaps, timings, reach, and what was not verified (non-root users, IPv6,
multi-WAN). Commit it, push the branch, and ask the user to reinstall and check the
hashes again.

- [ ] **Step 8: Release, only with the user's go-ahead**

Only after the user approves:
- fast-forward `main` to `raw-flows`, rerun every suite on `main`, and push `main`;
- tag `0.2.0` and push the tag;
- publish the GitHub release, whose notes come from `pkg-descr` plus the measured
  figures.

This must happen before Sun 27 Sep 05:00, or the user pauses the weekly job first.
