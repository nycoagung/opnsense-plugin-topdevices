# TopDevices Live Traffic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Live view to the TopDevices widget. It shows every device's download
and upload rate, updated every second, streamed from the pf state table by a small
sampler on the firewall.

**Architecture:**
- **Sampler.** A Python sampler (`live.py`) reads `pfctl -vvs state` once per
  interval, credits each connection's byte deltas to devices, and writes one
  server-sent event per interval.
- **Relay.** configd runs the sampler as a `stream_output` action, and
  `LiveController` relays it at `/api/topdevices/live/stream/{interval}`.
- **Widget.** A new *Live* range consumes the stream through
  `BaseWidget.openEventSource`. All the NetFlow views stay as they are.

**Tech Stack:**
- Python 3.13, standard library only, on OPNsense 26.7 / FreeBSD 15.
- A PHP (Phalcon MVC) API controller.
- The ES-module dashboard widget, with jQuery and Chart.js.
- A POSIX `sh` installer.
- Tests: `unittest`, `node:test` and `shellcheck`.

**Spec:** `docs/superpowers/specs/2026-09-23-live-traffic-design.md`. It was approved
on 2026-09-23 and refined while prototyping; refinements are marked *(refined)*.

**How this plan was checked:** every code block below was run before the plan was
written, and the plan is generated from those exact files. Specifically:
- the Python suite passes, including the parity test against core 26.7.4's own
  parser;
- the Node suite passes;
- all 10 sampler mutants and all 6 widget mutants are killed;
- the installer dry run passes, and `shellcheck`, `php -l` and `xmllint` are clean;
- each task's "run and see it fail" step was replayed against only the code that
  exists at that point, and fails with exactly the message quoted.

## Global Constraints

- **Firewall runtime.** Python 3.13 with the standard library only, so no syntax or
  API newer than 3.13.
- **Firewall paths:**
  - widgets: `/usr/local/opnsense/www/js/widgets`
  - scripts: `/usr/local/opnsense/scripts/topdevices`
  - MVC: `/usr/local/opnsense/mvc/app`
  - configd actions: `/usr/local/opnsense/service/conf/actions.d`
- **Interval.** Whole seconds from 1 to 10, validated in PHP and in Python. The widget
  offers 1, 2 and 5 s, defaulting to 1 s.
- **What counts as local.** RFC 1918 subnets (`10/8`, `172.16/12`, `192.168/16`) on
  non-upstream interfaces. **Never `ipaddress.is_private`.**
- **What counts as upstream.** Interfaces carrying an IPv4 default route, plus
  interfaces with a public IPv4 address.
- **Stream content.** Only numbers and IPv4 addresses, and no `<`, `>` or `&` in any
  line, because the web relay HTML-escapes lines for browser clients.
- **Sampler conduct.** No stderr writes in the loop, and nothing written to disk per
  sample.
- **Sampler timing:**
  - CPU budget of 10% of one core, as an EMA over 5 samples;
  - lifetime 3600 s, and the first line is `retry: 1000`;
  - keepalive every requested interval;
  - relay poll timeout of interval + 10 s.
- **Drift check.** A 60 s window and a band of 0.90–1.10, judged per direction only
  above 1 Mb/s. It uses 14 bytes of Ethernet header per frame, and is suspended while
  IPv6 exceeds 1% of WAN bytes.
- **Per-device lists.** Peers are the union of the top 10 by all traffic and the top 10
  internet peers. Ports are the top 5 per scope.
- **Widget:**
  - Live is the first range;
  - 3 s time-weighted average; a quiet device lingers 10 s; row order is held while
    the pointer is over the table;
  - watchdog thresholds `max(6 s, 3 × effective)` and `max(20 s, 6 × effective)`;
  - automatic retry every 30 s while unavailable.
- **Repository hygiene:**
  - test fixtures use RFC 5737 documentation addresses for the internet side;
  - nothing from the reference network is committed: no public address, host names,
    key paths or firewall address.
- **Git:**
  - work on branch `live-traffic`, version `0.1.0`;
  - nothing reaches `main` before Task 12;
  - commits follow the repository's style: an imperative subject and a body
    explaining why.

## Review Focus

1. **OPNsense behind an ISP router, with a WAN on a private address (double NAT).**
   Internet traffic should be credited as it is with a public WAN. Pinned by
   `test_wan_behind_an_isp_router` (Task 1), `test_double_nat_still_credits_internet`
   (Task 2) and the mutant *upstream found by address only* (Task 3).
2. **A laptop sleeps with Live open, or the firewall reboots.** Live should resume by
   itself rather than wait for a click. Pinned by *an unavailable stream retries by
   itself* and the mutant *no automatic retry once unavailable* (Task 6).
3. **The hourly recycle.** There should be no blank flash: rows stay on screen through
   the new stream's baseline. Pinned by *a recycled stream keeps its rows on screen
   through the new baseline* (Task 6).
4. **A warning arriving together with good data**, for example unreadable routes
   alongside readable addresses. Both the rates and the warning should be shown.
   Pinned by *an event carrying a warning still shows its rates, and the warning*
   (Task 6), and by `test_failed_interface_read_is_reported` and
   `test_missing_upstream_is_said_out_loud` (Task 3).
5. **A very large state table** (P2P, a scan). Updates should slow down, never drop the
   stream or start a reconnect storm. Pinned by
   `test_throttle_stretches_and_keeps_the_relay_alive`,
   `test_never_silent_longer_than_the_interval_between_samples` and the mutants *CPU
   budget ignored* and *no keepalive while throttled* (Task 3). Also by the
   controller's interval + 10 s relay margin (Task 7).

## Files

| Path | Responsibility | Task |
|---|---|---|
| `src/opnsense/scripts/topdevices/live.py` | The sampler: parse, credit, aggregate, throttle, drift check, stream | 1–3 |
| `tests/fixtures/{pfctl_states,ifconfig,netstat_routes,ifinfo_em0}.txt` | Synthetic command output in the real formats | 1 |
| `tests/test_live.py` | Sampler unit tests | 1–3 |
| `tests/mutate.py` | Proves the sampler tests catch the mistakes that matter | 3 |
| `tests/test_core_parity.py` | Our parser against core's own, on the fixture | 4 |
| `tests/parity_live.py` | The same, on the firewall's live state table | 4 |
| `src/opnsense/www/js/widgets/TopDevices.js` | Live helpers (pure) and the Live mode | 5–6 |
| `tests/live_view.test.mjs` | Widget tests: helpers, lifecycle, watchdog | 5–6 |
| `tests/mutate_widget.mjs` | Proves the widget tests catch the lifecycle mistakes | 6 |
| `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php` | The stream endpoint | 7 |
| `src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml` | The privilege for the endpoint | 7 |
| `src/opnsense/www/js/widgets/Metadata/TopDevices.xml` | Grants the widget the endpoint | 7 |
| `install.sh` | Installs the six files, registers the actions, restarts configd, clears the ACL cache | 7 |
| `tests/test_install.sh` | Installer dry run | 7 |
| `README.md`, `pkg-descr`, `Makefile` | Documentation and version 0.1.0 | 8 |

Run every command from the repository root, on the `live-traffic` branch.

---

### Task 1: Sampler: parsing

**Files:**
- Create: `tests/fixtures/pfctl_states.txt`, `tests/fixtures/ifconfig.txt`, `tests/fixtures/netstat_routes.txt`, `tests/fixtures/ifinfo_em0.txt`
- Create: `tests/test_live.py`
- Create: `src/opnsense/scripts/topdevices/live.py` (mode 0755)

**Interfaces:**
- Consumes: nothing.
- Produces, in `live.py`:
  - constants: `VERSION = '0.1.0'`, `PFCTL`, `IFCONFIG`, `IFINFO`, `ROUTES`, `RFC1918`,
    `MIN_INTERVAL`, `MAX_INTERVAL`, `ETHER_HEADER`, `CPU_BUDGET`, `COST_EMA_SAMPLES`,
    `LIFETIME`, `TOPOLOGY_REFRESH`, `DRIFT_*`, `TOP_PEERS`, `TOP_PORTS`,
    `INET/ALL/FW = 'inet'/'all'/'fw'`, `PORT_PROTOCOLS`;
  - `parse_header(line) -> dict | None`, with keys `dir` (`'in'|'out'`), `af` (4|6),
    `src`, `dst`, `dst_port` (int, 0 when not TCP, UDP or SCTP) and `nat`
    (str or None);
  - `parse_states(text) -> (dict[id, state], unparsed: int)`. A state is the header
    dict plus `age` (seconds), `b0` and `b1`, and `id` is `'<16 hex>/<8 hex>'`;
  - `parse_ifconfig(text) -> [(device, ipaddress.IPv4Interface)]`;
  - `parse_default_devs(text) -> [device]`;
  - `parse_ifinfo(text) -> {device: {'rx', 'tx', 'rxp', 'txp', 'ether': bool}}`;
  - `Topology(ifaddrs, default_devs=())`, with `.local_nets`, `.fw_addrs`,
    `.upstream_devs`, `.upstream_addrs` and `.is_local(addr) -> bool`.

The fixtures follow the exact layout of opnsense/src `stable/26.7`
`sbin/pfctl/pf_print_state.c`:
- the header line has `->` for out and `<-` for in, and a translation in parentheses
  after either host;
- a TCP sequence-window line;
- the counters line;
- the `id:` line;
- an optional `origif:` line.

Core's parsers define the `ifconfig`, `netstat` and `ifinfo` formats
(`legacy_interfaces_details`, `show_routes.py`, `legacy_interface_stats`). The last
lines of `pfctl_states.txt` are deliberately malformed.

- [ ] **Step 1: Create the fixtures**

`tests/fixtures/pfctl_states.txt`:

```text
all tcp 203.0.113.7:443 <- 192.168.1.10:50000       ESTABLISHED:ESTABLISHED
   [1000 + 65535] wscale 7  [2000 + 65535] wscale 7
   age 00:05:00, expires in 23:59:59, 100:200 pkts, 10000:500000 bytes, rule 12
   id: 0000000000000001 creatorid: 0a0a0a0a
all tcp 198.51.100.2:60000 (192.168.1.10:50000) -> 203.0.113.7:443       ESTABLISHED:ESTABLISHED
   [1000 + 65535] wscale 7  [2000 + 65535] wscale 7
   age 00:05:00, expires in 23:59:59, 100:200 pkts, 10000:500000 bytes, rule 3, allow-opts
   id: 0000000000000002 creatorid: 0a0a0a0a
all tcp 192.168.1.80:443 (198.51.100.2:443) <- 192.0.2.50:41000       ESTABLISHED:ESTABLISHED
   [5000 + 65535] wscale 7  [6000 + 65535] wscale 7
   age 00:01:00, expires in 23:59:00, 50:40 pkts, 3000:90000 bytes, rule 20
   id: 0000000000000003 creatorid: 0a0a0a0a
all tcp 192.0.2.50:41000 -> 192.168.1.80:443       ESTABLISHED:ESTABLISHED
   [5000 + 65535] wscale 7  [6000 + 65535] wscale 7
   age 00:01:00, expires in 23:59:00, 50:40 pkts, 3000:90000 bytes, rule 4, allow-opts
   id: 0000000000000004 creatorid: 0a0a0a0a
all udp 192.168.1.53:53 (192.0.2.53:53) <- 192.168.20.5:33333       SINGLE:MULTIPLE
   age 00:00:10, expires in 00:00:20, 2:2 pkts, 120:360 bytes, rule 30
   id: 0000000000000005 creatorid: 0a0a0a0a
all udp 192.168.20.5:33333 -> 192.168.1.53:53       MULTIPLE:SINGLE
   age 00:00:10, expires in 00:00:20, 2:2 pkts, 120:360 bytes, rule 5, allow-opts
   id: 0000000000000006 creatorid: 0a0a0a0a
all tcp 192.168.20.5:554 <- 192.168.1.20:40000       ESTABLISHED:ESTABLISHED
   [7000 + 65535] wscale 7  [8000 + 65535] wscale 7
   age 01:00:00, expires in 23:59:59, 1000:3000 pkts, 50000:4000000 bytes, rule 12
   id: 0000000000000007 creatorid: 0a0a0a0a
all tcp 192.168.1.20:40000 -> 192.168.20.5:554       ESTABLISHED:ESTABLISHED
   [7000 + 65535] wscale 7  [8000 + 65535] wscale 7
   age 01:00:00, expires in 23:59:59, 1000:3000 pkts, 50000:4000000 bytes, rule 6, allow-opts
   id: 0000000000000008 creatorid: 0a0a0a0a
all udp 198.51.100.2:12345 -> 203.0.113.53:53       MULTIPLE:SINGLE
   age 00:00:02, expires in 00:00:58, 1:1 pkts, 60:200 bytes, rule 2, allow-opts
   id: 0000000000000009 creatorid: 0a0a0a0a
all udp 198.51.100.2:51820 <- 192.0.2.99:51000       MULTIPLE:MULTIPLE
   age 00:10:00, expires in 00:02:00, 400:500 pkts, 60000:70000 bytes, rule 40
   id: 000000000000000a creatorid: 0a0a0a0a
all tcp 203.0.113.9:443 <- 10.0.0.2:52000       ESTABLISHED:ESTABLISHED
   [9000 + 65535] wscale 7  [9100 + 65535] wscale 7
   age 00:03:00, expires in 23:59:59, 30:40 pkts, 2000:80000 bytes, rule 50
   id: 000000000000000b creatorid: 0a0a0a0a
all tcp 198.51.100.2:61000 (10.0.0.2:52000) -> 203.0.113.9:443       ESTABLISHED:ESTABLISHED
   [9000 + 65535] wscale 7  [9100 + 65535] wscale 7
   age 00:03:00, expires in 23:59:59, 30:40 pkts, 2000:80000 bytes, rule 3, allow-opts
   id: 000000000000000c creatorid: 0a0a0a0a
all udp 198.51.100.2:5353 (192.168.1.1:5353) -> 203.0.113.123:123       MULTIPLE:SINGLE
   age 00:00:05, expires in 00:00:55, 1:1 pkts, 76:76 bytes, rule 3, allow-opts
   id: 000000000000000d creatorid: 0a0a0a0a
all udp 192.168.1.1:53 <- 192.168.1.10:40001       SINGLE:MULTIPLE
   age 00:00:03, expires in 00:00:27, 1:1 pkts, 70:300 bytes, rule 12
   id: 000000000000000e creatorid: 0a0a0a0a
all tcp 2001:db8::10[443] <- 2001:db8:1::5[50001]       ESTABLISHED:ESTABLISHED
   [100 + 65535] wscale 7  [200 + 65535] wscale 7
   age 00:02:00, expires in 23:59:59, 10:10 pkts, 1500:2500 bytes, rule 60
   id: 000000000000000f creatorid: 0a0a0a0a
all icmp 203.0.113.7:4242 <- 192.168.1.10:4242       0:0
   age 00:00:01, expires in 00:00:10, 1:1 pkts, 84:84 bytes, rule 12
   id: 0000000000000010 creatorid: 0a0a0a0a
all tcp 192.168.1.80:443 (198.51.100.2:443) <- 192.168.1.10:50500 (192.168.1.1:62000)       ESTABLISHED:ESTABLISHED
   [300 + 65535] wscale 7  [400 + 65535] wscale 7
   age 00:00:30, expires in 23:59:59, 5:5 pkts, 700:900 bytes, rule 21
   id: 0000000000000011 creatorid: 0a0a0a0a
all udp 192.0.2.77:3478 <- 192.168.1.10:3478       MULTIPLE:MULTIPLE
   age 123:04:05, expires in 00:01:00, 9:9 pkts, 900:900 bytes, rule 12
   id: 0000000000000012 creatorid: 0a0a0a0a
   origif: igb1
this line is not a state header
all tcp 203.0.113.7:443 192.168.1.10:50600       ESTABLISHED:ESTABLISHED
   age 00:00:10, expires in 23:59:59, 1:1 pkts, 10:10 bytes, rule 12
   id: 0000000000000013 creatorid: 0a0a0a0a
all udp 203.0.113.8:53 <- 192.168.1.10:40002       SINGLE:MULTIPLE
   frobnicate: yes
   age 00:00:10, expires in 00:00:20, 1:1 pkts, 10:10 bytes, rule 12
   id: 0000000000000014 creatorid: 0a0a0a0a
all udp 203.0.113.9:53 <- 192.168.1.10:40003       SINGLE:MULTIPLE
   age 00:00:10, expires in 00:00:20
   id: 0000000000000015 creatorid: 0a0a0a0a
all udp 203.0.113.10:53 <- 192.168.1.10:40004       SINGLE:MULTIPLE
   age 00:00:10, expires in 00:00:20, 1:1 pkts, 10:10 bytes, rule 12
```

`tests/fixtures/ifconfig.txt`:

```text
em0: flags=1008843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
	description: WAN (wan)
	options=4800028<VLAN_MTU,JUMBO_MTU,NOMAP>
	ether 00:00:5e:00:53:01
	inet 198.51.100.2 netmask 0xffffff00 broadcast 198.51.100.255
	inet6 fe80::200:5eff:fe00:5301%em0 prefixlen 64 scopeid 0x1
	media: Ethernet autoselect (1000baseT <full-duplex>)
	status: active
igb1: flags=1008843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
	description: LAN (lan)
	ether 00:00:5e:00:53:02
	inet 192.168.1.1 netmask 0xffffff00 broadcast 192.168.1.255
	status: active
vlan01: flags=1008843<UP,BROADCAST,RUNNING,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
	description: IOT (opt1)
	ether 00:00:5e:00:53:02
	inet 192.168.20.1 netmask 0xffffff00 broadcast 192.168.20.255
	vlan: 20 vlanproto: 802.1q vlanpcp: 0 parent interface: igb1
igb2: flags=1008802<BROADCAST,SIMPLEX,MULTICAST,LOWER_UP> metric 0 mtu 1500
	ether 00:00:5e:00:53:03
	inet 169.254.3.1 netmask 0xffff0000 broadcast 169.254.255.255
wg0: flags=10080c1<UP,RUNNING,NOARP,MULTICAST,LOWER_UP> metric 0 mtu 1420
	description: WG_CLIENTS (opt3)
	options=80000<LINKSTATE>
	inet 10.0.0.1 --> 10.0.0.1 netmask 0xffffff00
	groups: wg wireguard
lo0: flags=1008049<UP,LOOPBACK,RUNNING,MULTICAST,LOWER_UP> metric 0 mtu 16384
	options=680003<RXCSUM,TXCSUM,LINKSTATE,RXCSUM_IPV6,TXCSUM_IPV6>
	inet 127.0.0.1 netmask 0xff000000
	inet6 ::1 prefixlen 128
```

`tests/fixtures/netstat_routes.txt`:

```text
Routing tables

Internet:
Destination        Gateway            Flags   Nhop#    Mtu      Netif Expire
default            198.51.100.1       UGS         1   1500        em0
10.0.0.0/24        link#5             U           4   1420        wg0
10.0.0.1           link#5             UHS         5  16384        lo0
127.0.0.1          link#4             UH          2  16384        lo0
192.168.1.0/24     link#2             U           3   1500       igb1
192.168.1.1        link#2             UHS         6  16384        lo0
192.168.20.0/24    link#3             U           7   1500     vlan01
198.51.100.0/24    link#1             U           8   1500        em0
198.51.100.2       link#1             UHS         9  16384        lo0
```

`tests/fixtures/ifinfo_em0.txt`:

```text
Interface em0 (em0):
	flags: 8843
	type: Ethernet
	header length: 18
	line rate: 1000000000 bit/s
	packets received: 1000
	input errors: 0
	packets transmitted: 500
	output errors: 0
	bytes received: 1500000
	bytes transmitted: 40000
	multicasts received: 3
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_live.py`. Tasks 2 and 3 append to it.

```python
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


```

- [ ] **Step 3: Run the tests and watch them fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `FAILED (errors=1)` with `ImportError: Failed to import test module: test_live`,
because `live.py` does not exist yet.

- [ ] **Step 4: Write the parsing half of the sampler**

Create `src/opnsense/scripts/topdevices/live.py`. Tasks 2 and 3 append to it.

```python
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


```

Then: `chmod 0755 src/opnsense/scripts/topdevices/live.py`

- [ ] **Step 5: Run the tests and see them pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 9 tests` … `OK`

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures tests/test_live.py src/opnsense/scripts/topdevices/live.py
git commit -m "Live sampler: parse pf states, interfaces and routes" -m "Reads pfctl -vvs state in the layout pf_print_state.c prints, finding the arrow by value so a translation on either host cannot shift it. Interfaces, routes and counters are read the way core reads them. The WAN is found by its default route as well as its address, so a firewall behind an ISP router is not mistaken for local. Anything unrecognised is counted rather than guessed."
```

---

### Task 2: Sampler: accounting

**Files:**
- Modify: `tests/test_live.py` (append)
- Modify: `src/opnsense/scripts/topdevices/live.py` (append)

**Interfaces:**
- Consumes: `parse_states`, `parse_header`, `Topology` and the constants from Task 1.
- Produces:
  - `deltas(prev, cur, dt) -> [(state, b0, b1)]`;
  - `credits(state, b0, b1, topo) -> [(scope, device, peer, port, down, up)]`, where
    scope is `'inet'`, `'all'` or `'fw'`, and for `'fw'` the device and peer are
    `None`;
  - `aggregate(rows, dt, topo) -> (devices, fw_bytes)`. Here `devices[ip]` is
    `{'all': [down, up], 'inet': [down, up], 'peers': [[ip, down, up, inet]],
    'ports': {'all': [[port, down, up]], 'inet': [...]}}` in bits per second, and
    `fw_bytes` is `[down, up]` in bytes;
  - `wan_rates(prev, cur, devs, dt) -> (down_bps, up_bps, {'rx', 'tx', 'hdr_rx', 'hdr_tx'})`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live.py`:

```python
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


```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `FAILED (errors=24)`, including `AttributeError: module 'live' has no attribute 'credits'`.

- [ ] **Step 3: Write the accounting**

Append to `src/opnsense/scripts/topdevices/live.py`:

```python
# ---------------------------------------------------------------- accounting


def deltas(prev, cur, dt):
    """Bytes each state moved since the previous sample: [(state, b0, b1)].

    A state seen before contributes the difference. A new state contributes
    everything it carries if it is young enough to have started inside the
    interval. A negative difference means pf reused the id for a different
    connection, which is then treated as new. A vanished state contributes
    nothing: its final partial interval is lost.
    """
    out = []
    horizon = dt + 1
    for sid, s in cur.items():
        p = prev.get(sid)
        if p is not None:
            d0, d1 = s['b0'] - p['b0'], s['b1'] - p['b1']
            if d0 < 0 or d1 < 0:
                if s['age'] > horizon:
                    continue
                d0, d1 = s['b0'], s['b1']
        elif s['age'] <= horizon:
            d0, d1 = s['b0'], s['b1']
        else:
            continue
        if d0 or d1:
            out.append((s, d0, d1))
    return out


def credits(state, b0, b1, topo):
    """Who a state's bytes belong to: [(scope, device, peer, port, down, up)].

    'inet' and 'all' credit a device. 'fw' is the firewall's own upstream
    traffic, kept only for the drift check: device and peer are None and
    down/up are relative to the WAN. IPv6 states return [] and are counted by
    the caller. b0 is initiator->responder and b1 the reverse; for outbound NAT
    b1 is the download, verified against the kernel counters (spec 5.4).
    """
    if state['af'] != 4:
        return []
    src, dst, nat, port = state['src'], state['dst'], state['nat'], state['dst_port']
    local, fw, upstream = topo.is_local, topo.fw_addrs, topo.upstream_addrs
    out = []
    if state['dir'] == 'out':
        # 'all' is counted on ingress states only: every routed flow also has
        # an out state with identical counters, and counting both doubles it.
        if nat is not None and nat in fw:
            out.append((FW, None, None, port, b1, b0))
        elif nat is not None and local(nat) and not local(src):
            out.append((INET, nat, dst, port, b1, b0))
        elif nat is None and src in upstream:
            out.append((FW, None, None, port, b1, b0))
        return out
    if nat is not None and not local(nat) and local(dst) and dst not in fw and not local(src):
        out.append((INET, dst, src, port, b0, b1))       # port-forward: remote initiator
    elif nat is None and dst in upstream:
        out.append((FW, None, None, port, b0, b1))
    if local(src) and src not in fw:
        out.append((ALL, src, dst, port, b1, b0))
    if local(dst) and dst not in fw:
        out.append((ALL, dst, src, port, b0, b1))
    return out


def aggregate(rows, dt, topo):
    """One interval's credits -> (devices, fw_bytes).

    devices: {ip: {'all': [down, up], 'inet': [down, up],
                   'peers': [[ip, down, up, inet]],
                   'ports': {'all': [[port, down, up]], 'inet': [[port, down, up]]}}}
    in bits per second. peers is the union of the top TOP_PEERS by all traffic
    and the top TOP_PEERS internet peers, so a device whose local traffic
    dominates still shows its internet peers. fw_bytes: [down, up] bytes of the
    firewall's own upstream traffic.
    """
    acc = {}
    fw_bytes = [0, 0]
    for scope, dev, peer, port, down, up in rows:
        if scope == FW:
            fw_bytes[0] += down
            fw_bytes[1] += up
            continue
        d = acc.get(dev)
        if d is None:
            d = acc[dev] = {ALL: [0, 0], INET: [0, 0], 'peers': {}, 'ports': {ALL: {}, INET: {}}}
        d[scope][0] += down
        d[scope][1] += up
        if scope == ALL:
            pr = d['peers'].setdefault(peer, [0, 0])
            pr[0] += down
            pr[1] += up
        pt = d['ports'][scope].setdefault(port, [0, 0])
        pt[0] += down
        pt[1] += up
    scale = 8.0 / dt

    def bps(value):
        return int(round(value * scale))

    def ranked(items):
        return sorted(items, key=lambda kv: -(kv[1][0] + kv[1][1]))

    devices = {}
    for ip, d in acc.items():
        peers = ranked(d['peers'].items())
        chosen = {k for k, _ in peers[:TOP_PEERS]}
        chosen |= {k for k, _ in [kv for kv in peers if not topo.is_local(kv[0])][:TOP_PEERS]}
        devices[ip] = {
            ALL: [bps(d[ALL][0]), bps(d[ALL][1])],
            INET: [bps(d[INET][0]), bps(d[INET][1])],
            'peers': [[k, bps(v[0]), bps(v[1]), 0 if topo.is_local(k) else 1] for k, v in peers if k in chosen],
            'ports': {s: [[p, bps(v[0]), bps(v[1])] for p, v in ranked(d['ports'][s].items())[:TOP_PORTS]]
                      for s in (ALL, INET)},
        }
    return devices, fw_bytes


def wan_rates(prev, cur, devs, dt):
    """Interface counters -> (down_bps, up_bps, sample) for the upstream devices.

    sample holds the byte deltas and the per-frame header bytes the drift check
    subtracts. A counter that went backwards (interface reset) counts as zero.
    """
    rx = tx = hrx = htx = 0
    for dev in devs:
        a, b = prev.get(dev), cur.get(dev)
        if not a or not b:
            continue
        drx, dtx = max(0, b['rx'] - a['rx']), max(0, b['tx'] - a['tx'])
        prx, ptx = max(0, b['rxp'] - a['rxp']), max(0, b['txp'] - a['txp'])
        rx += drx
        tx += dtx
        if b['ether']:
            hrx += prx * ETHER_HEADER
            htx += ptx * ETHER_HEADER
    return (int(round(rx * 8 / dt)), int(round(tx * 8 / dt)),
            {'rx': rx, 'tx': tx, 'hdr_rx': hrx, 'hdr_tx': htx})


```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 17 tests` … `OK`

- [ ] **Step 5: Commit**

```bash
git add tests/test_live.py src/opnsense/scripts/topdevices/live.py
git commit -m "Live sampler: credit each connection's bytes to devices" -m "Implements the crediting rules that reconciled with the kernel counters to 100.0% and 100.3%. Outbound NAT goes to the device behind it. Port-forwards go to the internal target, with the directions swapped. 'All traffic' is counted once, on the ingress state. The DNS redirect to a local resolver is never internet, and the firewall's own traffic is kept apart for the drift check."
```

---

### Task 3: Sampler: self-control and the stream loop

**Files:**
- Modify: `tests/test_live.py` (append)
- Modify: `src/opnsense/scripts/topdevices/live.py` (append)
- Create: `tests/mutate.py`

**Interfaces:**
- Consumes: everything from Tasks 1 and 2.
- Produces:
  - `ema(previous, value)` and `next_interval(requested, cost_ema) -> seconds`;
  - `Drift` with `.add(dt, wan, attr_down, attr_up, v6_bytes)` and
    `.coverage() -> None | {'down', 'up', 'ok'}`;
  - `safe_text(text)`, `format_event(event) -> 'data: {json}\n\n'` and `cpu_seconds()`;
  - `run_loop(interval, read_states, read_ifaddrs, read_routes, read_counters, write, clock, wall, cpu, sleep, lifetime, max_samples) -> 0`;
  - `main(argv) -> exit status`.
- The event it writes carries these keys: `v`, `t`, `dt`, `interval`, `effective`,
  `throttled`, `cost_ms`, `states`, `unparsed`, `v6_skipped`,
  `wan{devs, down, up}`, `coverage`, `devices` and `error`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_live.py`:

```python
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
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `python3 -m unittest discover -s tests -v`
Expected: `FAILED (failures=1, errors=22)`, including `AttributeError: module 'live' has no attribute 'Drift'`.

- [ ] **Step 3: Write the self-control and the loop**

Append to `src/opnsense/scripts/topdevices/live.py`:

```python
# ---------------------------------------------------------------- self-control


def ema(previous, value, samples=COST_EMA_SAMPLES):
    if previous is None:
        return value
    return previous + (2.0 / (samples + 1)) * (value - previous)


def next_interval(requested, cost_ema):
    """Seconds until the next sample: the requested interval, stretched so that
    sampling never takes more than CPU_BUDGET of one core."""
    return max(float(requested), (cost_ema or 0.0) / CPU_BUDGET)


class Drift:
    """Attributed bytes against the WAN counters over a sliding window (spec 5.8).

    Expected is the WAN interface bytes less one Ethernet header per frame.
    Attributed is internet device traffic plus the firewall's own. A direction
    is judged only above DRIFT_MIN_BPS, and the whole check is suspended while
    IPv6 carries more than DRIFT_MAX_V6_SHARE of the bytes: IPv6 is not
    attributed, and with floating states there is no telling which IPv6 states
    crossed the WAN.
    """

    def __init__(self, window=DRIFT_WINDOW):
        self.window = window
        self.samples = []

    def add(self, dt, wan, attr_down, attr_up, v6_bytes):
        self.samples.append((dt, wan['rx'], wan['tx'], wan['hdr_rx'], wan['hdr_tx'], attr_down, attr_up, v6_bytes))
        keep, total = [], 0.0
        for s in reversed(self.samples):
            keep.append(s)
            total += s[0]
            if total >= self.window:
                break
        self.samples = keep[::-1]

    def coverage(self):
        """{'down': ratio|None, 'up': ratio|None, 'ok': bool|None}, or None when not judgeable."""
        dt = sum(s[0] for s in self.samples)
        if dt < self.window:
            return None
        rx, tx, hrx, htx, down, up, v6 = (sum(s[i] for s in self.samples) for i in range(1, 8))
        if v6 > DRIFT_MAX_V6_SHARE * (rx + tx):
            return None
        result = {}
        for key, wire, hdr, attributed in (('down', rx, hrx, down), ('up', tx, htx, up)):
            expected = wire - hdr
            ok_rate = wire * 8 / dt >= DRIFT_MIN_BPS
            result[key] = round(attributed / expected, 3) if ok_rate and expected > 0 else None
        judged = [result['down'], result['up']]
        judged = [v for v in judged if v is not None]
        result['ok'] = all(DRIFT_BAND[0] <= v <= DRIFT_BAND[1] for v in judged) if judged else None
        return result


def safe_text(text):
    """The web relay HTML-escapes every line, so no event may contain <, > or &."""
    return _UNSAFE.sub(' ', str(text))[:200]


def format_event(event):
    return 'data: ' + _UNSAFE.sub(' ', json.dumps(event, separators=(',', ':'))) + '\n\n'


def cpu_seconds():
    """CPU used by this process and the commands it ran (pfctl, ifinfo)."""
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + kids.ru_utime + kids.ru_stime


# ---------------------------------------------------------------- the loop


def run_loop(interval, read_states, read_ifaddrs, read_routes, read_counters, write, clock=time.monotonic,
             wall=time.time, cpu=cpu_seconds, sleep=time.sleep, lifetime=LIFETIME, max_samples=None):
    """One event per tick until `lifetime` seconds have passed; returns 0.

    read_states() -> pfctl text, read_ifaddrs() -> ifconfig text,
    read_routes() -> netstat text and read_counters(devs) -> ifinfo text may
    raise: the error is reported in that sample's event and the loop carries on. write(chunk) sends to the client and
    raises BrokenPipeError once the client is gone.
    """
    write('retry: 1000\n\n')
    start = clock()
    topo, topo_error = _topology(read_ifaddrs, read_routes, Topology([]))
    topo_at = start
    drift = Drift()
    prev = prev_counters = prev_t = None
    cost_ema = None
    samples = 0
    next_tick = start
    while True:
        now = clock()
        if now - start >= lifetime or (max_samples is not None and samples >= max_samples):
            return 0
        if now - topo_at >= TOPOLOGY_REFRESH:
            topo, topo_error = _topology(read_ifaddrs, read_routes, topo)
            topo_at = now
        c0 = cpu()
        event = {'v': VERSION, 't': round(wall(), 3), 'dt': 0, 'interval': interval,
                 'states': 0, 'unparsed': 0, 'v6_skipped': 0,
                 'wan': {'devs': list(topo.upstream_devs), 'down': 0, 'up': 0},
                 'coverage': drift.coverage(), 'devices': {}, 'error': topo_error}
        try:
            states, unparsed = parse_states(read_states())
            counters = parse_ifinfo(read_counters(topo.upstream_devs)) if topo.upstream_devs else {}
        except Exception as exc:
            states = None
            event['error'] = safe_text(exc)
        t = clock()
        if states is not None:
            event.update(states=len(states), unparsed=unparsed,
                         v6_skipped=sum(1 for s in states.values() if s['af'] != 4))
            if prev is not None and t > prev_t:
                dt = t - prev_t
                rows, v6_bytes = [], 0
                for s, b0, b1 in deltas(prev, states, dt):
                    if s['af'] != 4:
                        v6_bytes += b0 + b1
                    else:
                        rows.extend(credits(s, b0, b1, topo))
                devices, fw_bytes = aggregate(rows, dt, topo)
                down, up, wan = wan_rates(prev_counters, counters, topo.upstream_devs, dt)
                inet_down = sum(r[4] for r in rows if r[0] == INET)
                inet_up = sum(r[5] for r in rows if r[0] == INET)
                drift.add(dt, wan, inet_down + fw_bytes[0], inet_up + fw_bytes[1], v6_bytes)
                event.update(dt=round(dt, 3), devices=devices, coverage=drift.coverage())
                event['wan'].update(down=down, up=up)
            prev, prev_counters, prev_t = states, counters, t
        cost = max(0.0, cpu() - c0)
        cost_ema = ema(cost_ema, cost)
        effective = next_interval(interval, cost_ema)
        event.update(cost_ms=int(round(cost * 1000)), effective=round(effective, 1),
                     throttled=effective > interval)
        write(format_event(event))
        samples += 1
        next_tick = max(next_tick + effective, clock())
        _sleep_until(next_tick, interval, clock, sleep, write)


def _topology(read_ifaddrs, read_routes, fallback):
    """(Topology, warning or None), for the event's error field until a refresh
    succeeds. Unreadable addresses keep the previous topology; unreadable routes
    fall back to the address rule alone; no upstream at all is said out loud."""
    try:
        ifaddrs = parse_ifconfig(read_ifaddrs())
    except Exception as exc:
        return fallback, safe_text('interface addresses: %s' % exc)
    warning = None
    try:
        devs = parse_default_devs(read_routes())
    except Exception as exc:
        devs, warning = [], safe_text('routes: %s' % exc)
    topo = Topology(ifaddrs, devs)
    if not topo.upstream_devs and warning is None:
        warning = 'no upstream interface found'
    return topo, warning


def _sleep_until(deadline, every, clock, sleep, write):
    """Sleep until `deadline`, writing an SSE comment every `every` seconds: the
    web relay drops a stream that stays silent longer than interval + 10 s."""
    last = clock()
    while True:
        now = clock()
        if now >= deadline:
            return
        sleep(max(0.0, min(deadline, last + every) - now))
        if clock() < deadline:
            write(': keepalive\n\n')
            last = clock()


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    if proc.returncode != 0:
        raise RuntimeError('%s exited with %d' % (cmd[0], proc.returncode))
    return proc.stdout


def main(argv):
    arg = argv[1] if len(argv) == 2 else ''

    def write(chunk):
        sys.stdout.write(chunk)
        sys.stdout.flush()

    try:
        if not (arg.isascii() and arg.isdigit() and MIN_INTERVAL <= int(arg) <= MAX_INTERVAL):
            write(format_event({'v': VERSION, 'error': 'interval must be a whole number of seconds from 1 to 10'}))
            return 1
        return run_loop(int(arg),
                        read_states=lambda: _run(PFCTL),
                        read_ifaddrs=lambda: _run(IFCONFIG),
                        read_routes=lambda: _run(ROUTES),
                        read_counters=lambda devs: ''.join(_run((IFINFO, d)) for d in devs),
                        write=write)
    except BrokenPipeError:
        # The reader is gone: tab closed, stream recycled or configd restarted.
        # Point stdout at /dev/null so the interpreter's final flush cannot raise again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 36 tests` … `OK`

- [ ] **Step 5: Prove the tests catch the mistakes that matter**

Create `tests/mutate.py`:

```python
"""Prove the suite catches the mistakes that matter: each mutant must fail it.

Run:  python3 tests/mutate.py        (exit status 1 if any mutant survives)
"""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = ROOT / 'src/opnsense/scripts/topdevices/live.py'

MUTANTS = [
    ('port-forward direction not swapped',
     'out.append((INET, dst, src, port, b0, b1))', 'out.append((INET, dst, src, port, b1, b0))'),
    ('port-forward rule ignores a local source (DNS redirect becomes internet)',
     'and dst not in fw and not local(src):', 'and dst not in fw:'),
    ('out states counted as well as in states',
     '        return out\n    if nat is not None and not local(nat)', '    if nat is not None and not local(nat)'),
    ('ipaddress.is_private instead of RFC 1918 on the interfaces',
     'hit = ip.version == 4 and any(ip in net for net in self.local_nets)', 'hit = ip.is_private'),
    ('reused state ids not detected',
     '            if d0 < 0 or d1 < 0:', '            if False:'),
    ('CPU budget ignored',
     'return max(float(requested), (cost_ema or 0.0) / CPU_BUDGET)', 'return float(requested)'),
    ('Ethernet headers not subtracted',
     'hrx += prx * ETHER_HEADER', 'hrx += 0'),
    ('no keepalive while throttled',
     "            write(': keepalive\\n\\n')", '            pass'),
    ('upstream found by address only (double NAT breaks)',
     'for dev in list(default_devs) + public:', 'for dev in public:'),
    ('parser trusts a fixed arrow position',
     "arrow = next((i for i, p in enumerate(parts) if p in ('->', '<-')), None)",
     "arrow = len(parts) - 3 if len(parts) >= 6 and parts[-3] in ('->', '<-') else None"),
]


def main():
    source = LIVE.read_text()
    survivors = []
    for name, old, new in MUTANTS:
        count = source.count(old)
        if count != 1:
            print('BROKEN MUTANT %r: anchor found %d times' % (name, count))
            return 2
        with tempfile.TemporaryDirectory() as tmp:
            mutant = pathlib.Path(tmp) / 'live.py'
            mutant.write_text(source.replace(old, new))
            env = dict(os.environ, LIVE_PY=str(mutant))
            proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'),
                                   '-p', 'test_live.py'], env=env, capture_output=True, text=True)
        killed = proc.returncode != 0
        print('%-8s %s' % ('killed' if killed else 'SURVIVED', name))
        if not killed:
            survivors.append(name)
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main())
```

Run: `python3 tests/mutate.py`
Expected: ten lines, each starting with `killed`, and exit status 0.

- [ ] **Step 6: Commit**

```bash
git add tests/test_live.py tests/mutate.py src/opnsense/scripts/topdevices/live.py
git commit -m "Live sampler: stream loop with a CPU budget, drift check and recycle" -m "One event per interval, timed by the firewall's clock. The sampler measures its own CPU and stretches its interval to stay under 10% of one core, keeping the relay alive with keepalives meanwhile. It checks its totals against the WAN counters over 60 s, exits after an hour so the browser reconnects to a fresh process, and exits quietly once its reader is gone. mutate.py proves the suite catches each rule being broken."
```

---

### Task 4: Parser parity with core

**Files:**
- Create: `tests/test_core_parity.py`
- Create: `tests/parity_live.py`

**Interfaces:**
- Consumes: `live.parse_states`, `live.PORT_PROTOCOLS`, and `test_live.STATES_TEXT` and
  `test_live.sid`.
- Produces: nothing used by other tasks.

- [ ] **Step 1: Get core's parser**

```bash
git clone -q --depth 1 --branch 26.7.4 --filter=blob:none --sparse https://github.com/opnsense/core.git "$TMPDIR/core-26.7.4"
git -C "$TMPDIR/core-26.7.4" sparse-checkout set src/opnsense/scripts/filter
ls "$TMPDIR/core-26.7.4/src/opnsense/scripts/filter/lib/states.py"
```

- [ ] **Step 2: Write the parity test**

Create `tests/test_core_parity.py`:

```python
"""Our pfctl parser against core's own (src/opnsense/scripts/filter/lib/states.py).

Both read the same fixture text. Skipped unless CORE_STATES_PY names core's
states.py (on the firewall: /usr/local/opnsense/scripts/filter/lib/states.py)
or OPNSENSE_CORE points at a checkout of opnsense/core 26.7.x, e.g.:

  git clone -q --depth 1 --branch 26.7.4 --filter=blob:none --sparse \
      https://github.com/opnsense/core.git /tmp/core
  git -C /tmp/core sparse-checkout set src/opnsense/scripts/filter
  OPNSENSE_CORE=/tmp/core python3 -m unittest discover -s tests -v
"""
import importlib.util
import os
import pathlib
import types
import unittest

from test_live import STATES_TEXT, live, sid

CORE_STATES = pathlib.Path(
    os.environ.get('CORE_STATES_PY')
    or pathlib.Path(os.environ.get('OPNSENSE_CORE', '/nonexistent')) / 'src/opnsense/scripts/filter/lib/states.py')

# Where core's parser is known to be wrong, so the two are expected to differ:
# it locates the arrow at a fixed index and so misreads a state whose second
# host also carries a translation (it takes '(192.168.1.1' as the source).
KNOWN_CORE_MISREAD = {sid(0x11)}


def age_seconds(text):
    h, m, s = (int(p) for p in text.split(':'))
    return h * 3600 + m * 60 + s


@unittest.skipUnless(CORE_STATES.is_file(), 'set CORE_STATES_PY or OPNSENSE_CORE (see module docstring)')
class CoreParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load the file directly: the package's __init__ imports dnspython.
        spec = importlib.util.spec_from_file_location('core_states', CORE_STATES)
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        core.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=STATES_TEXT))
        core.fetch_rule_labels = lambda: {}
        cls.core = {r['id']: r for r in core.query_states('', '')}
        cls.ours, _ = live.parse_states(STATES_TEXT)

    def test_every_state_we_parse_is_compared(self):
        missing = set(self.ours) - set(self.core)
        self.assertEqual(missing, set(), 'core did not produce these states at all')

    def test_fields_agree(self):
        compared = 0
        for key, s in self.ours.items():
            if key in KNOWN_CORE_MISREAD:
                continue
            c = self.core[key]
            with self.subTest(state=key):
                port = s['dst_port'] if s['dst_port'] else None
                core_port = int(c['dst_port']) if c['dst_port'] not in ('0', '') else None
                if c['proto'] not in live.PORT_PROTOCOLS:
                    core_port = None             # we report no port for ICMP; core keeps the query id
                self.assertEqual((s['dir'], s['src'], s['dst'], s['nat'], port, [s['b0'], s['b1']],
                                  s['age']),
                                 (c['direction'], c['src_addr'], c['dst_addr'], c['nat_addr'], core_port,
                                  c['bytes'], age_seconds(c['age'])))
                compared += 1
        self.assertEqual(compared, len(self.ours) - len(KNOWN_CORE_MISREAD))

    def test_known_core_misread_is_still_there(self):
        # If upstream fixes this, drop it from KNOWN_CORE_MISREAD and compare it too.
        self.assertTrue(self.core[sid(0x11)]['src_addr'].startswith('('))
        self.assertEqual(self.ours[sid(0x11)]['src'], '192.168.1.10')
```

- [ ] **Step 3: Prove it can fail**

Run it against a copy of `live.py` that swaps the byte counters:

```bash
python3 - <<'PY'
import pathlib, tempfile
src = pathlib.Path('src/opnsense/scripts/topdevices/live.py').read_text()
old = "cur['b0'], cur['b1'] = int(byts.group(1)), int(byts.group(2))"
assert src.count(old) == 1
pathlib.Path(tempfile.gettempdir(), 'live_swapped.py').write_text(
    src.replace(old, "cur['b0'], cur['b1'] = int(byts.group(2)), int(byts.group(1))"))
PY
LIVE_PY="$(python3 -c 'import tempfile; print(tempfile.gettempdir())')/live_swapped.py" \
  OPNSENSE_CORE="$TMPDIR/core-26.7.4" python3 -m unittest discover -s tests -p test_core_parity.py
```

Expected: `FAIL: test_fields_agree` for every compared state, and exit status 1.

- [ ] **Step 4: Run it for real**

Run: `python3 -m unittest discover -s tests -v`
Expected: `Ran 39 tests` … `OK (skipped=3)`. The parity tests skip without core.

Run: `OPNSENSE_CORE="$TMPDIR/core-26.7.4" python3 -m unittest discover -s tests -v`
Expected: `Ran 39 tests` … `OK`, with the three `CoreParity` tests passing.

- [ ] **Step 5: Add the on-firewall parity script**

Create `tests/parity_live.py`. It runs on the firewall in Task 10:

```python
"""On the firewall: our pfctl parser and core's, over the live state table.

The unit tests prove agreement on synthetic text; this proves it on the real
output of this firewall's pfctl. Run from the extracted branch tarball:

    python3 tests/parity_live.py

Exit status 0 when every state agrees and nothing was left unparsed. States
whose second host carries a translation are skipped: core reads their source
wrongly (see tests/test_core_parity.py).
"""
import importlib.util
import pathlib
import subprocess
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
CORE_STATES = '/usr/local/opnsense/scripts/filter/lib/states.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    live = load('live', HERE.parent / 'src/opnsense/scripts/topdevices/live.py')
    core = load('core_states', CORE_STATES)
    text = subprocess.run(['/sbin/pfctl', '-vvs', 'state'], capture_output=True, text=True, check=True).stdout
    core.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=text))
    core.fetch_rule_labels = lambda: {}
    ours, unparsed = live.parse_states(text)
    theirs = {r['id']: r for r in core.query_states('', '')}
    mismatches, skipped = [], 0
    for key, s in ours.items():
        c = theirs.get(key)
        if c is None:
            mismatches.append((key, 'core has no such state'))
            continue
        if str(c['src_addr']).startswith('('):
            skipped += 1
            continue
        core_port = int(c['dst_port']) if c['proto'] in live.PORT_PROTOCOLS and c['dst_port'].isdigit() else 0
        a = (s['dir'], s['src'], s['dst'], s['nat'], s['dst_port'], [s['b0'], s['b1']])
        b = (c['direction'], c['src_addr'], c['dst_addr'], c['nat_addr'], core_port, c['bytes'])
        if a != b:
            mismatches.append((key, a, b))
    print('states: ours %d, core %d | unparsed %d | skipped (core misread) %d | mismatches %d'
          % (len(ours), len(theirs), unparsed, skipped, len(mismatches)))
    for m in mismatches[:10]:
        print('  ', m)
    return 0 if not mismatches and unparsed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
```

Run: `python3 -c "import ast; ast.parse(open('tests/parity_live.py').read())" && echo parses`
Expected: `parses`

- [ ] **Step 6: Commit**

```bash
git add tests/test_core_parity.py tests/parity_live.py
git commit -m "Prove the sampler's pfctl parser agrees with core's" -m "test_core_parity.py runs core 26.7.4's own state parser on the same fixture and compares every field we use. It also pins the one state core misreads: a translation on both hosts, where it takes the parenthesised address as the source. parity_live.py makes the same comparison on the firewall, over its live state table."
```

---

### Task 5: Widget: live helpers

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js` (header note and module-level helpers)
- Create: `tests/live_view.test.mjs`

**Interfaces:**
- Consumes: the event shape from Task 3 (`dt`, `devices[ip].all|inet|peers|ports`,
  `effective`).
- Produces these module exports:
  - `LIVE_WINDOW_S = 3`, `LIVE_LINGER_MS = 10000`, `LIVE_RETRY_MS = 30000`;
  - `mergeLive(view, event, nowMs) -> view`;
  - `liveRates(view, 'all'|'inet') -> {ip: {down, up}}`;
  - `liveDetail(view, ip, scope) -> {peers: [{key, down, up}], ports: [{key, down, up}]}`;
  - `liveStatusFor(ageMs, effectiveS) -> 'live'|'reconnecting'|'unavailable'`;
  - `holdOrder(rows, previousOrder) -> rows`;
  - `fmtRate(bps) -> string`.

- [ ] **Step 1: Write the failing tests**

Create `tests/live_view.test.mjs`. Task 6 appends to it. Its fakes copy the relevant
`BaseWidget.js` behaviour from core 26.7.4.

```js
// Live-view logic of TopDevices.js. Run:  node --test tests/live_view.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

// --- what the dashboard provides, reduced to what the widget touches ----------
// FakeBase mirrors BaseWidget.openEventSource / closeEventSource /
// onVisibilityChanged / onWidgetClose from opnsense/core 26.7.4
// (src/opnsense/www/js/widgets/BaseWidget.js). Keep it in step with upstream.
class FakeEventSource {
    constructor(url) { this.url = url; this.closed = false; FakeEventSource.opened.push(url); }
    close() { this.closed = true; }
}
FakeEventSource.opened = [];

globalThis.EventSource = FakeEventSource;
globalThis.BaseWidget = class {
    constructor(config) {
        this.config = config;
        this.eventSource = null;
        this.eventSourceUrl = null;
        this.eventSourceOnData = null;
        this.eventSourceRetryCount = 0;
        this.retryLimit = 3;
    }
    async getWidgetConfig() { return (this.config && this.config.widget) || {}; }
    openEventSource(url, onMessage) {
        this.closeEventSource();
        if (this.eventSourceRetryCount >= this.retryLimit) return;
        this.eventSourceUrl = url;
        this.eventSourceOnData = onMessage;
        this.eventSource = new EventSource(url);
        this.eventSource.onmessage = onMessage;
    }
    closeEventSource() {
        if (this.eventSource !== null) { this.eventSource.close(); this.eventSource = null; }
    }
    onVisibilityChanged(visible) {
        if (this.eventSourceUrl !== null) {
            if (visible) this.openEventSource(this.eventSourceUrl, this.eventSourceOnData);
            else if (this.eventSource !== null) this.closeEventSource();
        }
    }
    onWidgetClose() { this.closeEventSource(); }
};
// jQuery and Chart.js: chainable no-ops. `length` is 0 so "element not found" paths run.
const chain = new Proxy(function () {}, {
    get: (t, p) => (p === Symbol.toPrimitive ? () => '' : p === 'length' ? 0 : chain),
    apply: () => chain
});
globalThis.$ = chain;
globalThis.document = { hidden: false };
globalThis.Chart = class {
    constructor(el, cfg) { this.config = { type: cfg.type }; this.data = cfg.data; }
    update() {}
    destroy() {}
};

const m = await import('../src/opnsense/www/js/widgets/TopDevices.js');
const TopDevices = m.default;

const ev = (dt, devices) => ({ dt, devices });
const dev = (all, inet = [0, 0], peers = [], ports = { all: [], inet: [] }) => ({ all, inet, peers, ports });

test('a baseline event changes nothing', () => {
    const v = m.mergeLive(null, ev(0, { a: dev([9, 9]) }), 0);
    assert.deepEqual(v, { events: [], seen: { all: {}, inet: {} } });
});

test('rates are time-weighted over the window', () => {
    let v = m.mergeLive(null, ev(1, { a: dev([1000, 100]) }), 1000);
    v = m.mergeLive(v, ev(2, { a: dev([4000, 400]) }), 3000);
    assert.deepEqual(m.liveRates(v, 'all'), { a: { down: 3000, up: 300 } });
});

test('the window keeps only the newest LIVE_WINDOW_S seconds', () => {
    let v = null;
    for (let i = 1; i <= 4; i++) v = m.mergeLive(v, ev(1, { a: dev([i * 100, 0]) }), i * 1000);
    assert.equal(v.events.length, 3);
    assert.deepEqual(m.liveRates(v, 'all').a, { down: 300, up: 0 });   // (200 + 300 + 400) / 3
    v = m.mergeLive(v, ev(5, { a: dev([50, 0]) }), 10000);           // one long event covers it alone
    assert.equal(v.events.length, 1);
});

test('a quiet device lingers at 0, then drops off', () => {
    let v = m.mergeLive(null, ev(1, { a: dev([800, 80]) }), 0);
    for (let t = 1000; t <= 3000; t += 1000) v = m.mergeLive(v, ev(1, {}), t);
    assert.deepEqual(m.liveRates(v, 'all'), { a: { down: 0, up: 0 } });
    v = m.mergeLive(v, ev(1, {}), 10001);
    assert.deepEqual(m.liveRates(v, 'all'), {});
});

test('each scope lists only devices with traffic in it', () => {
    const v = m.mergeLive(null, ev(1, { cam: dev([10, 5000]), phone: dev([900, 90], [900, 90]) }), 0);
    assert.deepEqual(Object.keys(m.liveRates(v, 'all')).sort(), ['cam', 'phone']);
    assert.deepEqual(Object.keys(m.liveRates(v, 'inet')), ['phone']);
});

test('detail averages peers and ports; internet scope hides local peers', () => {
    const peers = [['203.0.113.7', 800, 80, 1], ['192.168.1.1', 40, 4, 0]];
    const ports = { all: [[443, 800, 80], [53, 40, 4]], inet: [[443, 800, 80]] };
    const v = m.mergeLive(null, ev(1, { a: dev([840, 84], [800, 80], peers, ports) }), 0);
    assert.deepEqual(m.liveDetail(v, 'a', 'all'), {
        peers: [{ key: '203.0.113.7', down: 800, up: 80 }, { key: '192.168.1.1', down: 40, up: 4 }],
        ports: [{ key: '443', down: 800, up: 80 }, { key: '53', down: 40, up: 4 }]
    });
    assert.deepEqual(m.liveDetail(v, 'a', 'inet'), {
        peers: [{ key: '203.0.113.7', down: 800, up: 80 }],
        ports: [{ key: '443', down: 800, up: 80 }]
    });
});

test('status timeouts scale with the interval actually in use', () => {
    assert.equal(m.liveStatusFor(5000, 1), 'live');
    assert.equal(m.liveStatusFor(7000, 1), 'reconnecting');
    assert.equal(m.liveStatusFor(21000, 1), 'unavailable');
    assert.equal(m.liveStatusFor(7000, 5), 'live');           // 3 x 5 s
    assert.equal(m.liveStatusFor(16000, 5), 'reconnecting');
    assert.equal(m.liveStatusFor(31000, 5), 'unavailable');   // 6 x 5 s
    assert.equal(m.liveStatusFor(25000, 10), 'live');         // throttled to 10 s: 3 x 10 s
    assert.equal(m.liveStatusFor(31000, 10), 'reconnecting');
});

test('row order holds while hovering, new rows follow', () => {
    const rows = ['a', 'b', 'c', 'd'].map(ip => ({ ip }));
    assert.deepEqual(m.holdOrder(rows, ['c', 'a']).map(r => r.ip), ['c', 'a', 'b', 'd']);
});

test('fmtRate', () => {
    assert.equal(m.fmtRate(0), '0 b/s');
    assert.equal(m.fmtRate(999), '999 b/s');
    assert.equal(m.fmtRate(108000), '108 kb/s');
    assert.equal(m.fmtRate(5656000), '5.7 Mb/s');
    assert.equal(m.fmtRate(1500000000), '1.5 Gb/s');
});

```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `node --test tests/live_view.test.mjs`
Expected: `ℹ pass 0`, `ℹ fail 9`, with `TypeError: m.mergeLive is not a function`.

- [ ] **Step 3: Header note + the pure helpers, ahead of the class**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
 * Figures are TOTAL traffic - internal plus internet. An NVR pulling camera
 * streams will dominate with traffic that never reaches the WAN.
 */

export default class TopDevices extends BaseWidget {
```

Replace with:

```js
 * Figures are TOTAL traffic - internal plus internet. An NVR pulling camera
 * streams will dominate with traffic that never reaches the WAN.
 *
 * LIVE: the "Live" range shows current rates instead, streamed once per
 * interval by the plugin's own sampler (scripts/topdevices/live.py) through
 * /api/topdevices/live/stream/{interval}. The sampler reads the pf state
 * table; NetFlow is not involved. Design and measurements:
 * docs/superpowers/specs/2026-09-23-live-traffic-design.md
 */

/* ---------- live view: pure helpers (tests/live_view.test.mjs) ---------- */

export const LIVE_WINDOW_S = 3;       // rows and chart average this many seconds
export const LIVE_LINGER_MS = 10000;  // a device that goes quiet stays listed this long
export const LIVE_RETRY_MS = 30000;   // an unavailable stream retries by itself this often

// Fold one sampler event into the view: the events covering the last
// LIVE_WINDOW_S seconds, and when each device last moved traffic, per scope.
// A baseline event (dt 0) carries no rates and changes nothing.
export function mergeLive(view, event, nowMs) {
    const v = view
        ? { events: view.events.slice(), seen: { all: { ...view.seen.all }, inet: { ...view.seen.inet } } }
        : { events: [], seen: { all: {}, inet: {} } };
    if (!event || !(event.dt > 0)) return v;
    const devices = event.devices || {};
    v.events.push({ dt: event.dt, devices: devices });
    let covered = 0, keep = 0;
    for (let i = v.events.length - 1; i >= 0; i--) {
        keep++;
        covered += v.events[i].dt;
        if (covered >= LIVE_WINDOW_S) break;
    }
    v.events = v.events.slice(v.events.length - keep);
    for (const [ip, d] of Object.entries(devices)) {
        for (const scope of ['all', 'inet']) {
            const r = d[scope] || [0, 0];
            if (r[0] + r[1] > 0) v.seen[scope][ip] = nowMs;
        }
    }
    for (const scope of ['all', 'inet']) {
        for (const ip of Object.keys(v.seen[scope])) {
            if (nowMs - v.seen[scope][ip] > LIVE_LINGER_MS) delete v.seen[scope][ip];
        }
    }
    return v;
}

// Time-weighted average per device over the window: sum(rate * dt) / sum(dt).
// A device missing from an event moved nothing during it, so a lingering but
// idle device comes out at 0.
export function liveRates(view, scope) {
    const key = scope === 'inet' ? 'inet' : 'all';
    const span = view.events.reduce((s, e) => s + e.dt, 0);
    const out = {};
    for (const ip of Object.keys(view.seen[key])) {
        let down = 0, up = 0;
        for (const e of view.events) {
            const r = e.devices[ip] && e.devices[ip][key];
            if (r) { down += r[0] * e.dt; up += r[1] * e.dt; }
        }
        out[ip] = span > 0 ? { down: down / span, up: up / span } : { down: 0, up: 0 };
    }
    return out;
}

// The same averaging for one device's peers and ports. In the internet scope
// only internet peers are listed; ports come from the scope's own list.
export function liveDetail(view, ip, scope) {
    const key = scope === 'inet' ? 'inet' : 'all';
    const span = view.events.reduce((s, e) => s + e.dt, 0) || 1;
    const peers = {}, ports = {};
    for (const e of view.events) {
        const d = e.devices[ip];
        if (!d) continue;
        for (const [peer, down, up, inet] of d.peers || []) {
            if (key === 'inet' && !inet) continue;
            const p = peers[peer] || (peers[peer] = { down: 0, up: 0 });
            p.down += down * e.dt; p.up += up * e.dt;
        }
        for (const [port, down, up] of (d.ports && d.ports[key]) || []) {
            const p = ports[port] || (ports[port] = { down: 0, up: 0 });
            p.down += down * e.dt; p.up += up * e.dt;
        }
    }
    const rank = (o) => Object.entries(o)
        .map(([k, v]) => ({ key: k, down: v.down / span, up: v.up / span }))
        .sort((a, b) => (b.down + b.up) - (a.down + a.up));
    return { peers: rank(peers), ports: rank(ports) };
}

// Connection state from the age of the last event, scaled to the interval the
// sampler really uses: EventSource never surfaces the sampler's ': keepalive'
// comments, so fixed timeouts would call a throttled or 5 s stream dead.
export function liveStatusFor(ageMs, effectiveS) {
    const eff = Math.max(1, effectiveS || 1) * 1000;
    if (ageMs > Math.max(20000, 6 * eff)) return 'unavailable';
    if (ageMs > Math.max(6000, 3 * eff)) return 'reconnecting';
    return 'live';
}

// Keep the row order steady while the pointer is over the table: rows already
// shown keep their place, new ones follow in the order given.
export function holdOrder(rows, previousOrder) {
    const pos = new Map(previousOrder.map((ip, i) => [ip, i]));
    const at = (r) => (pos.has(r.ip) ? pos.get(r.ip) : previousOrder.length);
    return rows.map((r, i) => ({ r, i })).sort((a, b) => (at(a.r) - at(b.r)) || (a.i - b.i)).map(x => x.r);
}

export function fmtRate(bps) {
    if (!bps || bps < 1) return '0 b/s';
    const units = ['b/s', 'kb/s', 'Mb/s', 'Gb/s'];
    let i = 0, n = bps;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
    return `${n.toFixed(i > 0 && n < 100 ? 1 : 0)} ${units[i]}`;
}

export default class TopDevices extends BaseWidget {
```

- [ ] **Step 4: Run the tests and see them pass**

Run: `node --check src/opnsense/www/js/widgets/TopDevices.js && node --test tests/live_view.test.mjs`
Expected: `ℹ pass 9`, `ℹ fail 0`

- [ ] **Step 5: Commit**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/live_view.test.mjs
git commit -m "Widget: pure helpers for the live view" -m "Averaging over the last 3 seconds, weighted by each event's own interval. A 10-second linger for devices that go quiet. Watchdog timeouts that scale with the interval actually in use, because EventSource never surfaces the keepalive comments. A steady row order while hovering, and rate formatting. All pure, so they are tested without a dashboard."
```

---

### Task 6: Widget: Live mode

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js` (13 edits)
- Modify: `tests/live_view.test.mjs` (append)
- Create: `tests/mutate_widget.mjs`

**Interfaces:**
- Consumes:
  - Task 5's exports;
  - the endpoint `/api/topdevices/live/stream/{n}` (Task 7);
  - `BaseWidget.openEventSource`, `closeEventSource`, `onVisibilityChanged`,
    `onWidgetClose` and `eventSourceRetryCount`.
- Produces:
  - range key `'live'` and widget option `liveInterval`;
  - methods `_startLive`, `_stopLive`, `_onLiveEvent`, `_liveTick`, `_liveSummary`,
    `_renderLive`, `_renderLiveDetails`, `_renderChrome` and
    `_rowsHtml(rows, fmt, empty)`;
  - `_renderChart(rows, fmt, live)`, which takes a formatter and updates in place
    while Live.

Two of these edits refactor the NetFlow render. The row markup and the sort and chart
chrome move into `_rowsHtml` and `_renderChrome`, and `_renderChart` gains parameters
whose defaults reproduce its old behaviour. Task 11 step 8 checks every NetFlow view
by hand.

- [ ] **Step 1: Write the failing tests**

Append to `tests/live_view.test.mjs`:

```js
function widget(interval = '1') {
    const w = new TopDevices({ widget: { liveInterval: interval } });
    w.state.range = 'live';
    return w;
}

test('starting Live opens the stream at the configured interval', async (t) => {
    FakeEventSource.opened.length = 0;
    const w = widget('5');
    t.after(() => w._stopLive());
    await w._startLive();
    assert.deepEqual(FakeEventSource.opened, ['/api/topdevices/live/stream/5']);
    assert.ok(w.live.watchdog);
});

test('leaving Live forgets the stream, so a hidden/shown tab cannot reopen it', async () => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    await w._startLive();
    const es = w.eventSource;
    w.state.range = '24h';
    w._stopLive();
    assert.equal(es.closed, true);
    assert.equal(w.live.watchdog, null);
    w.onVisibilityChanged(false);
    w.onVisibilityChanged(true);
    assert.equal(FakeEventSource.opened.length, 1);
});

test('while Live, a hidden/shown tab reopens the stream', async (t) => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    t.after(() => w._stopLive());
    await w._startLive();
    w.onVisibilityChanged(false);
    w.onVisibilityChanged(true);
    assert.equal(FakeEventSource.opened.length, 2);
    assert.equal(w.live.status, 'connecting');
});

test('closing the widget closes the stream', async () => {
    const w = widget();
    await w._startLive();
    const es = w.eventSource;
    w.onWidgetClose();
    assert.equal(es.closed, true);
    assert.equal(w.eventSource, null);
    assert.equal(w.live.watchdog, null);
});

test('a range change during start-up does not leave a stream behind', async () => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    const starting = w._startLive();       // awaits config and names
    w.state.range = '24h';
    w._stopLive();
    await starting;
    assert.equal(FakeEventSource.opened.length, 0);
    assert.equal(w.live.watchdog, null);
});

test('events drive the status; the watchdog escalates and gives up', async (t) => {
    const w = widget();
    t.after(() => w._stopLive());
    await w._startLive();
    const send = (e) => w.eventSource.onmessage({ data: JSON.stringify(e) });
    send({ dt: 0, devices: {}, effective: 1, wan: { devs: ['em0'], down: 0, up: 0 } });
    assert.equal(w.live.status, 'measuring');
    send({ dt: 1, effective: 1, wan: { devs: ['em0'], down: 8, up: 8 },
           devices: { '192.168.1.10': dev([1000000, 8000], [1000000, 8000]) } });
    assert.equal(w.live.status, 'live');
    assert.deepEqual(w.state.rows.map(r => [r.ip, r.down]), [['192.168.1.10', 1000000]]);
    w.live.lastAt = Date.now() - 7000;
    w._liveTick();
    assert.equal(w.live.status, 'reconnecting');
    const es = w.eventSource;
    w.live.lastAt = Date.now() - 21000;
    w._liveTick();
    assert.equal(w.live.status, 'unavailable');
    assert.equal(es.closed, true);
});

async function running(t) {
    const w = widget();
    t.after(() => w._stopLive());
    await w._startLive();
    const send = (e) => w.eventSource.onmessage({ data: JSON.stringify(e) });
    return { w, send };
}
const wan = { devs: ['em0'], down: 0, up: 0 };

test('a recycled stream keeps its rows on screen through the new baseline', async (t) => {
    const { w, send } = await running(t);
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.10': dev([5000, 50], [5000, 50]) } });
    send({ dt: 0, effective: 1, wan, devices: {} });          // the hourly recycle's first event
    assert.equal(w.live.status, 'measuring');
    assert.deepEqual(w.state.rows.map(r => r.ip), ['192.168.1.10']);
});

test('an event carrying a warning still shows its rates, and the warning', async (t) => {
    const { w, send } = await running(t);
    send({ dt: 1, effective: 1, wan, error: 'routes: netstat exited with 1',
           devices: { '192.168.1.10': dev([5000, 50], [5000, 50]) } });
    assert.deepEqual(w.state.rows.map(r => [r.ip, r.down]), [['192.168.1.10', 5000]]);
    assert.match(w._liveSummary(), /routes: netstat exited with 1/);
});

test('an unavailable stream retries by itself', async (t) => {
    FakeEventSource.opened.length = 0;
    const { w } = await running(t);
    w.live.lastAt = Date.now() - 21000;
    w._liveTick();
    assert.equal(w.live.status, 'unavailable');
    w._liveTick();                                          // too soon: no retry yet
    assert.equal(FakeEventSource.opened.length, 1);
    w.live.retryAt = Date.now() - m.LIVE_RETRY_MS;
    w._liveTick();
    await new Promise((r) => setTimeout(r, 0));             // _startLive awaits config and names
    assert.equal(FakeEventSource.opened.length, 2);
    assert.equal(w.live.status, 'connecting');
});
```

- [ ] **Step 2: Run the tests and watch them fail**

Run: `node --test tests/live_view.test.mjs`
Expected: `ℹ pass 9`, `ℹ fail 9`, with `TypeError: w._startLive is not a function`.

- [ ] **Step 3: Live state in the constructor**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
        this.chartObj = null;
        this.loading = false;
    }
```

Replace with:

```js
        this.chartObj = null;
        this.loading = false;
        this.live = {
            interval: 1, view: null, last: null, lastAt: 0, retryAt: 0, status: 'off', token: 0,
            watchdog: null, hover: false, order: [], rowsShown: -1, ptrPending: new Set()
        };
    }
```

- [ ] **Step 4: Widget option for the live interval**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
                default: '900', required: true
            }
        };
    }
```

Replace with:

```js
                default: '900', required: true
            },
            liveInterval: {
                id: 'liveInterval', title: 'Live update interval', type: 'select',
                options: [
                    { value: '1', label: '1 second' },
                    { value: '2', label: '2 seconds' },
                    { value: '5', label: '5 seconds' }
                ],
                default: '1', required: true
            }
        };
    }
```

- [ ] **Step 5: Live is the first range**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
        return [
            { key: '1h',        label: 'Last hour' },
```

Replace with:

```js
        return [
            { key: 'live',      label: 'Live' },
            { key: '1h',        label: 'Last hour' },
```

- [ ] **Step 6: Range switching, hover freeze, retry**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
        $(document).on('change.topdevices', '.td-range', function () {
            self.state.range = $(this).val();
            self.state.selected = null;
            $('.td-custom').css('display', self.state.range === 'custom' ? 'flex' : 'none');
            self._saveView();
            if (self.state.range !== 'custom') self.refresh();
        });
```

Replace with:

```js
        $(document).on('change.topdevices', '.td-range', function () {
            const was = self.state.range;
            self.state.range = $(this).val();
            self.state.selected = null;
            $('.td-custom').css('display', self.state.range === 'custom' ? 'flex' : 'none');
            self._saveView();
            if (self.state.range === 'live') { self._startLive(); return; }
            if (was === 'live') {
                // leave nothing of the live view behind for the NetFlow render
                self._stopLive();
                self.state.rows = [];
                self.state.window = null;
                $('.td-window small').text('');
            }
            if (self.state.range !== 'custom') self.refresh();
            else self.render();
        });
        $(document).on('mouseenter.topdevices', '.td-tablewrap', function () { self.live.hover = true; });
        $(document).on('mouseleave.topdevices', '.td-tablewrap', function () { self.live.hover = false; });
        $(document).on('click.topdevices', '.td-live-retry', function (e) {
            e.preventDefault();
            self._startLive();
        });
```

- [ ] **Step 7: Refresh / tick dispatch (1 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
    async refresh(force) {
        if (this.loading) return;
```

Replace with:

```js
    async refresh(force) {
        // Live restarts its stream: the header refresh button and an options change land here
        if (this.state.range === 'live') { await this._startLive(); return; }
        if (this.loading) return;
```

- [ ] **Step 8: Refresh / tick dispatch (2 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
    async onWidgetTick() { await this.refresh(); }
```

Replace with:

```js
    async onWidgetTick() {
        if (this.state.range === 'live') return;      // the stream refreshes itself
        await this.refresh();
    }
```

- [ ] **Step 9: The row markup and the table chrome, shared by both renders (1 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
        $('.td-chartbtns button').removeClass('btn-primary').addClass('btn-default');
        $(`.td-chartbtns button[data-chart="${this.state.chart}"]`).removeClass('btn-default').addClass('btn-primary');
        $('.td-sort').each((i, el) => {
            $(el).find('.td-arrow').remove();
            if ($(el).data('key') === this.state.sortKey) {
                $(el).append(`<span class="td-arrow"> ${this.state.sortDir === 'asc' ? '▲' : '▼'}</span>`);
            }
        });

        if (rows.length === 0) {
            $('.td-body').html('<tr><td colspan="5" class="text-muted">No matching devices</td></tr>');
        } else {
            $('.td-body').html(rows.map((r) => {
                const clip = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
                const label = r.name
                    ? `<strong style="display:block;${clip}">${this._esc(r.name)}</strong>`
                      + `<small class="text-muted" style="display:block;${clip}">${this._esc(r.ip)}</small>`
                    : `<strong style="display:block;${clip}">${this._esc(r.ip)}</strong>`;
                const net = this.networks.find(n => n.key === r.net);
                const sel = this.state.selected === r.ip ? ' class="info"' : '';
                return `<tr${sel} data-ip="${this._esc(r.ip)}" style="cursor:pointer;"
                        title="${this._esc((r.name ? r.name + ' ' : '') + r.ip)}">
                    <td style="text-align:left;${clip}">${label}</td>
                    <td style="text-align:left;${clip}"><small>${this._esc(net ? net.label : '')}</small></td>
                    <td style="text-align:right;">${this._fmt(r.down)}</td>
                    <td style="text-align:right;">${this._fmt(r.up)}</td>
                    <td style="text-align:right;"><strong>${this._fmt(r.total)}</strong></td></tr>`;
            }).join(''));
        }
        this._renderChart(rows.slice(0, CHART_MAX));
```

Replace with:

```js
        this._renderChrome();
        $('.td-body').html(this._rowsHtml(rows, (v) => this._fmt(v), 'No matching devices'));
        this._renderChart(rows.slice(0, CHART_MAX));
```

- [ ] **Step 10: The row markup and the table chrome, shared by both renders (2 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
        if (!this.state.selected) $('.td-details').empty();
        this._applyLayout();
        this._fitHeight();
    }

    _renderChart(
```

Replace with:

```js
        if (!this.state.selected) $('.td-details').empty();
        this._applyLayout();
        this._fitHeight();
    }

    _renderChrome() {
        $('.td-chartbtns button').removeClass('btn-primary').addClass('btn-default');
        $(`.td-chartbtns button[data-chart="${this.state.chart}"]`).removeClass('btn-default').addClass('btn-primary');
        $('.td-sort').each((i, el) => {
            $(el).find('.td-arrow').remove();
            if ($(el).data('key') === this.state.sortKey) {
                $(el).append(`<span class="td-arrow"> ${this.state.sortDir === 'asc' ? '▲' : '▼'}</span>`);
            }
        });
    }

    // One row per device; fmt formats the three figures (bytes for NetFlow
    // ranges, bits per second for Live).
    _rowsHtml(rows, fmt, empty) {
        if (rows.length === 0) return `<tr><td colspan="5" class="text-muted">${this._esc(empty)}</td></tr>`;
        return rows.map((r) => {
            const clip = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
            const label = r.name
                ? `<strong style="display:block;${clip}">${this._esc(r.name)}</strong>`
                  + `<small class="text-muted" style="display:block;${clip}">${this._esc(r.ip)}</small>`
                : `<strong style="display:block;${clip}">${this._esc(r.ip)}</strong>`;
            const net = this.networks.find(n => n.key === r.net);
            const sel = this.state.selected === r.ip ? ' class="info"' : '';
            return `<tr${sel} data-ip="${this._esc(r.ip)}" style="cursor:pointer;"
                    title="${this._esc((r.name ? r.name + ' ' : '') + r.ip)}">
                <td style="text-align:left;${clip}">${label}</td>
                <td style="text-align:left;${clip}"><small>${this._esc(net ? net.label : '')}</small></td>
                <td style="text-align:right;">${fmt(r.down)}</td>
                <td style="text-align:right;">${fmt(r.up)}</td>
                <td style="text-align:right;"><strong>${fmt(r.total)}</strong></td></tr>`;
        }).join('');
    }

    _renderChart(
```

- [ ] **Step 11: Render dispatch (1 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
    async _render() {
        // _load() applies the scope filter while aggregating
```

Replace with:

```js
    async _render() {
        if (this.state.range === 'live') { this._renderLive(); return; }
        // _load() applies the scope filter while aggregating
```

- [ ] **Step 12: Render dispatch (2 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
    async _renderDetails(ip) {
        const $d = $('.td-details');
```

Replace with:

```js
    async _renderDetails(ip) {
        if (this.state.range === 'live') { this._renderLiveDetails(ip); return; }
        const $d = $('.td-details');
```

- [ ] **Step 13: The chart updates in place while live (1 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
    _renderChart(rows) {
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
        if (this.state.chart === 'none' || rows.length === 0) { $('.td-chartbox').hide(); return; }
        $('.td-chartbox').show();
        const el = $('.td-canvas')[0];
        if (!el) return;
        const labels = rows.map(r => r.name || r.ip);
        const isPie = this.state.chart === 'pie';
        const fmt = (v) => this._fmt(v);
        this.chartObj = new Chart(el.getContext('2d'), {
            type: isPie ? 'doughnut' : 'bar',
            data: {
                labels: labels,
                datasets: isPie
                    ? [{ data: rows.map(r => r.total), borderWidth: 0 }]
                    : [{ label: 'Down', data: rows.map(r => r.down) },
                       { label: 'Up',   data: rows.map(r => r.up) }]
            },
```

Replace with:

```js
    _renderChart(rows, fmt = (v) => this._fmt(v), live = false) {
        const isPie = this.state.chart === 'pie';
        const type = isPie ? 'doughnut' : 'bar';
        if (this.state.chart === 'none' || rows.length === 0) {
            if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
            $('.td-chartbox').hide();
            return;
        }
        $('.td-chartbox').show();
        const labels = rows.map(r => r.name || r.ip);
        const datasets = isPie
            ? [{ data: rows.map(r => r.total), borderWidth: 0 }]
            : [{ label: 'Down', data: rows.map(r => r.down) },
               { label: 'Up',   data: rows.map(r => r.up) }];
        // Live redraws every interval: update the chart in place instead of
        // destroying and rebuilding it, which flickers.
        if (live && this.chartObj && this.chartObj.$live && this.chartObj.config.type === type) {
            this.chartObj.data.labels = labels;
            this.chartObj.data.datasets.forEach((ds, i) => { ds.data = datasets[i].data; });
            this.chartObj.update('none');
            return;
        }
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
        const el = $('.td-canvas')[0];
        if (!el) return;
        this.chartObj = new Chart(el.getContext('2d'), {
            type: type,
            data: { labels: labels, datasets: datasets },
```

- [ ] **Step 14: The chart updates in place while live (2 of 2)**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
                scales: isPie ? {} : {
                    x: { stacked: true, ticks: { font: { size: 9 } } },
                    y: { stacked: true, ticks: { callback: (v) => fmt(v) } }
                }
            }
        });
    }
```

Replace with:

```js
                scales: isPie ? {} : {
                    x: { stacked: true, ticks: { font: { size: 9 } } },
                    y: { stacked: true, ticks: { callback: (v) => fmt(v) } }
                }
            }
        });
        this.chartObj.$live = live;
    }

    /* ---------- live ---------- */

    async _startLive() {
        this._stopLive();
        const token = this.live.token;
        const cfg = await this.getWidgetConfig() || {};
        const n = parseInt(cfg.liveInterval, 10);
        this.live.interval = [1, 2, 5].includes(n) ? n : 1;
        try { await this._loadNames(); } catch (e) { /* names are cosmetic */ }
        // the range may have changed while we waited
        if (token !== this.live.token || this.state.range !== 'live') return;
        Object.assign(this.live, { view: null, last: null, lastAt: Date.now(), status: 'connecting', rowsShown: -1 });
        this.eventSourceRetryCount = 0;
        this.openEventSource(`/api/topdevices/live/stream/${this.live.interval}`, (ev) => this._onLiveEvent(ev));
        this.live.watchdog = setInterval(() => this._liveTick(), 1000);
        this._renderLive();
    }

    _stopLive() {
        this.live.token++;
        if (this.live.watchdog) { clearInterval(this.live.watchdog); this.live.watchdog = null; }
        this.closeEventSource();
        // BaseWidget.onVisibilityChanged reopens any remembered stream URL, and
        // closing does not forget it: forget it here, or a hidden-then-shown tab
        // would restart the stream while a NetFlow range is on screen.
        this.eventSourceUrl = null;
        this.eventSourceOnData = null;
        this.live.status = 'off';
        this.live.view = null;
    }

    _onLiveEvent(ev) {
        let e;
        try { e = JSON.parse(ev.data); } catch (err) { return; }
        const now = Date.now();
        this.live.last = e;
        this.live.lastAt = now;
        this.live.view = mergeLive(this.live.view, e, now);
        this.live.status = e.dt > 0 ? 'live' : 'measuring';
        this._renderLive();
    }

    // Once a second: notice a stream that has gone quiet. Events clear it again.
    _liveTick() {
        if (this.state.range !== 'live') return;
        if (typeof document !== 'undefined' && document.hidden) return;
        const now = Date.now();
        if (this.live.status === 'unavailable') {
            // a laptop waking up, a firewall back from a reboot: try again by
            // ourselves rather than waiting for someone to click Retry
            if (now - this.live.retryAt >= LIVE_RETRY_MS) this._startLive();
            return;
        }
        const eff = (this.live.last && this.live.last.effective) || this.live.interval;
        const status = liveStatusFor(now - this.live.lastAt, eff);
        if (status === 'live' || status === this.live.status) return;
        this.live.status = status;
        if (status === 'unavailable') {
            this.closeEventSource();        // stop the browser's own retries; ours follow LIVE_RETRY_MS
            this.live.retryAt = now;
        }
        this._renderLive();
    }

    onVisibilityChanged(visible) {
        super.onVisibilityChanged(visible);
        if (visible && this.state.range === 'live' && this.eventSourceUrl !== null) {
            this.live.lastAt = Date.now();
            this.live.status = 'connecting';
            this._renderLive();
        }
    }

    _liveSummary() {
        const l = this.live, e = l.last;
        const parts = ['Live'];
        if (e && e.wan) {
            const via = (e.wan.devs || []).join(', ') || 'WAN';
            parts.push(`WAN \u2193 ${fmtRate(e.wan.down)} \u2191 ${fmtRate(e.wan.up)} (via ${via})`);
        }
        parts.push(this.state.scope === 'wan' ? 'internet only' : 'all traffic');
        parts.push(e && e.throttled ? `throttled to ${e.effective} s` : `${l.interval} s`);
        if (e && e.coverage && e.coverage.ok === false) parts.push('\u26a0 totals disagree with the WAN counters');
        if (e && e.error) parts.push(`\u26a0 ${e.error}`);
        const status = { connecting: 'connecting\u2026', measuring: 'measuring\u2026',
                         reconnecting: 'reconnecting\u2026' }[l.status];
        if (status) parts.push(status);
        return parts.join(' \u00b7 ');
    }

    _renderLive() {
        const l = this.live;
        const CHART_MAX = 10;
        const scope = this.state.scope === 'wan' ? 'inet' : 'all';
        const rates = l.view ? liveRates(l.view, scope) : {};
        this.state.rows = Object.entries(rates).map(([ip, r]) => ({
            ip: ip, name: this.names[ip] || (this.ifaceNames || {})[ip] || '', net: this._netOf(ip),
            down: r.down, up: r.up, total: r.down + r.up
        }));
        let all = this._visibleRows();
        // the selected device keeps its panel while it lingers, and loses it once gone
        if (this.state.selected && !all.some(r => r.ip === this.state.selected)) this.state.selected = null;
        if (l.hover) all = holdOrder(all, l.order);
        const rows = all.slice(0, this.state.rowsN || 20);
        l.order = rows.map(r => r.ip);

        $('.td-window small').text(this._liveSummary());
        this._renderChrome();
        if (l.status === 'unavailable') {
            $('.td-body').html('<tr><td colspan="5" class="text-muted">Live data unavailable. '
                             + '<a href="#" class="td-live-retry">Retry</a></td></tr>');
        } else {
            $('.td-body').html(this._rowsHtml(rows, fmtRate, l.view ? 'No active devices' : 'Measuring\u2026'));
        }
        this._renderChart(rows.slice(0, CHART_MAX), fmtRate, true);
        if (rows.length > CHART_MAX && this.state.chart !== 'none') {
            $('.td-window small').append(
                `<span class="text-muted"> \u00b7 chart: top ${CHART_MAX} of ${rows.length}</span>`);
        }
        if (this.state.selected) this._renderLiveDetails(this.state.selected);
        else $('.td-details').empty();
        this._applyLayout();
        if (rows.length !== l.rowsShown) { l.rowsShown = rows.length; this._fitHeight(); }
    }

    _renderLiveDetails(ip) {
        const $d = $('.td-details');
        const view = this.live.view;
        if (!view) { $d.html('<small class="text-muted">Measuring\u2026</small>'); return; }
        const scope = this.state.scope === 'wan' ? 'inet' : 'all';
        const rate = liveRates(view, scope)[ip] || { down: 0, up: 0 };
        const detail = liveDetail(view, ip, scope);
        // resolve peer names in the background; the next event re-renders with them
        const todo = detail.peers.map(p => p.key)
            .filter(k => !this.names[k] && this.ptrCache[k] === undefined && !this.live.ptrPending.has(k));
        if (todo.length) {
            todo.forEach(k => this.live.ptrPending.add(k));
            this._ptrMany(todo, 6).then(() => todo.forEach(k => this.live.ptrPending.delete(k)));
        }
        const row = (label, title, v) => `<tr title="${this._esc(title)}">`
            + `<td style="text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0;">`
            + `${this._esc(label)}</td>`
            + `<td style="text-align:right;white-space:nowrap;width:78px;">${fmtRate(v.down + v.up)}</td></tr>`;
        const none = '<tr><td colspan="2" class="text-muted">none</td></tr>';
        const peers = detail.peers.map((p) => {
            const name = this.names[p.key] || this.ptrCache[p.key] || p.key;
            return row(name, name === p.key ? p.key : `${name}  ${p.key}`, p);
        }).join('') || none;
        const ports = detail.ports.map(p => row(p.key === '0' ? 'other' : p.key, p.key, p)).join('') || none;
        $d.html(`
            <div style="border-top:1px solid #ddd;padding-top:6px;">
                <div title="${this._esc((this.names[ip] || ip) + ' ' + ip)}"
                     style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    <strong>${this._esc(this.names[ip] || ip)}</strong>
                    <small class="text-muted">${this._esc(ip)}</small>
                </div>
                <div style="margin-top:3px;"><small class="text-muted">down ${fmtRate(rate.down)}
                    &middot; up ${fmtRate(rate.up)} &middot; ${LIVE_WINDOW_S} s average</small></div>
                <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;">
                    <div style="flex:1 1 260px;min-width:0;">
                        <small class="text-muted">Top peers</small>
                        <table class="table table-condensed" style="margin:0;table-layout:fixed;width:100%;">${peers}</table>
                    </div>
                    <div style="flex:1 1 150px;min-width:0;">
                        <small class="text-muted">Top ports</small>
                        <table class="table table-condensed" style="margin:0;table-layout:fixed;width:100%;">${ports}</table>
                    </div>
                </div>
            </div>`);
    }
```

- [ ] **Step 15: Closing the widget closes the stream**

In `src/opnsense/www/js/widgets/TopDevices.js`, find:

```js
    onWidgetClose() {
        $(document).off('.topdevices');
```

Replace with:

```js
    onWidgetClose() {
        this._stopLive();
        super.onWidgetClose();              // BaseWidget closes the EventSource here
        $(document).off('.topdevices');
```

- [ ] **Step 16: Run the tests and see them pass**

Run: `node --check src/opnsense/www/js/widgets/TopDevices.js && node --test tests/live_view.test.mjs`
Expected: `ℹ pass 18`, `ℹ fail 0`

- [ ] **Step 17: Prove the tests catch the lifecycle mistakes**

Create `tests/mutate_widget.mjs`:

```js
// Prove the widget tests catch the lifecycle mistakes that matter.
// Run:  node tests/mutate_widget.mjs      (exit status 1 if any mutant survives)
import { readFileSync, writeFileSync, mkdtempSync, copyFileSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const widget = 'src/opnsense/www/js/widgets/TopDevices.js';
const source = readFileSync(join(root, widget), 'utf8');

const mutants = [
    ['stream URL not forgotten when leaving Live', '        this.eventSourceUrl = null;\n        this.eventSourceOnData = null;\n', ''],
    ['no guard against a range change during start-up', "        if (token !== this.live.token || this.state.range !== 'live') return;\n", ''],
    ['watchdog not cleared', '        if (this.live.watchdog) { clearInterval(this.live.watchdog); this.live.watchdog = null; }\n', ''],
    ['fixed timeouts instead of scaling with the interval', "    const eff = Math.max(1, effectiveS || 1) * 1000;", '    const eff = 1000;'],
    ['no automatic retry once unavailable', '            if (now - this.live.retryAt >= LIVE_RETRY_MS) this._startLive();\n', ''],
    ['baseline events averaged in', '    if (!event || !(event.dt > 0)) return v;', '    if (!event) return v;'],
];

let survivors = 0;
for (const [name, from, to] of mutants) {
    const count = source.split(from).length - 1;
    if (count !== 1) { console.log(`BROKEN MUTANT ${name}: anchor found ${count} times`); process.exit(2); }
    const dir = mkdtempSync(join(tmpdir(), 'tdmut-'));
    mkdirSync(join(dir, 'src/opnsense/www/js/widgets'), { recursive: true });
    mkdirSync(join(dir, 'tests'));
    writeFileSync(join(dir, widget), source.replace(from, to));
    copyFileSync(join(root, 'tests/live_view.test.mjs'), join(dir, 'tests/live_view.test.mjs'));
    // a mutant that leaves a timer running keeps node alive: a hang counts as caught
    const run = spawnSync(process.execPath, ['--test', 'tests/live_view.test.mjs'],
                          { cwd: dir, encoding: 'utf8', timeout: 20000 });
    const killed = run.status !== 0;
    console.log(`${killed ? 'killed  ' : 'SURVIVED'} ${name}${run.status === null ? ' (hung)' : ''}`);
    if (!killed) survivors++;
}
process.exit(survivors ? 1 : 0);
```

Run: `node tests/mutate_widget.mjs`
Expected: six lines, each starting with `killed`, and exit status 0.

- [ ] **Step 18: Commit**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/live_view.test.mjs tests/mutate_widget.mjs
git commit -m "Widget: Live range streaming per-device rates" -m "Live is the first range. It opens the sampler's stream through BaseWidget, so hidden tabs close it, and forgets the stream URL on leaving, because BaseWidget would otherwise reopen it while a NetFlow range is shown. Rows, chart and device panel show 3-second averages; the chart updates in place. A watchdog shows reconnecting and unavailable states and retries by itself every 30 s. The row markup and table chrome move into shared helpers, with the NetFlow output unchanged."
```

---

### Task 7: Backend glue: controller, ACL, metadata, installer

**Files:**
- Create: `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php`
- Create: `src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml`
- Modify: `src/opnsense/www/js/widgets/Metadata/TopDevices.xml`
- Modify: `install.sh` (replace the whole file)
- Create: `tests/test_install.sh`

**Interfaces:**
- Consumes: the configd command name `topdevices live` and the script path
  `/usr/local/opnsense/scripts/topdevices/live.py` (Task 3).
- Produces:
  - `GET /api/topdevices/live/stream/{interval}`;
  - the privilege `page-dashboard-topdevices-live`;
  - the configd actions `topdevices install` and `topdevices live`;
  - `ROOT=<dir>` dry-run support in `install.sh`.

The controller follows core's `TrafficController::streamAction`. The installer keeps
0.0.1's behaviour:
- codeload fetch;
- a whole-set check before writing;
- staging, then `mv`;
- action registration that restarts nothing when unchanged.

It adds:
- the three new files;
- `0755` for `*.py`;
- the `[live]` action;
- a detached, delayed configd restart;
- ACL cache invalidation through core's own `ACL::invalidateCache()`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_install.sh`:

```sh
#!/bin/sh
# Dry-run the installer twice into a scratch root and check what it leaves.
# Run from the repository root:  sh tests/test_install.sh
set -eu
R=$(mktemp -d "${TMPDIR:-/tmp}/tdroot.XXXXXX")
trap 'rm -rf "$R"' EXIT
mkdir -p "$R/usr/local/opnsense/www/js/widgets/Metadata" "$R/usr/local/opnsense/service/conf/actions.d"
fail() { echo "FAIL: $*" >&2; exit 1; }

first=$(ROOT="$R" sh install.sh)
second=$(ROOT="$R" sh install.sh)
echo "$first" | grep -q 'configd actions changed - restart skipped' || fail "first run did not register the actions"
echo "$second" | grep -q 'configd actions already current' || fail "second run changed the actions"

while IFS='|' read -r src dst mode; do
    [ -n "$src" ] || continue
    [ -f "$R$dst" ] || fail "not installed: $dst"
    cmp -s "$src" "$R$dst" || fail "differs from source: $dst"
    [ "$(stat -f '%Lp' "$R$dst" 2>/dev/null || stat -c '%a' "$R$dst")" = "$mode" ] || fail "mode of $dst is not $mode"
done <<LIST
src/opnsense/www/js/widgets/TopDevices.js|/usr/local/opnsense/www/js/widgets/TopDevices.js|644
src/opnsense/www/js/widgets/Metadata/TopDevices.xml|/usr/local/opnsense/www/js/widgets/Metadata/TopDevices.xml|644
src/opnsense/scripts/topdevices/live.py|/usr/local/opnsense/scripts/topdevices/live.py|755
src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php|/usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php|644
src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml|/usr/local/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml|644
install.sh|/usr/local/opnsense/scripts/topdevices/install.sh|755
LIST

A="$R/usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf"
grep -qx '\[live\]' "$A" || fail "no [live] action"
grep -qx 'command:/usr/local/opnsense/scripts/topdevices/live.py' "$A" || fail "live action path wrong (ROOT leaked?)"
grep -qx 'type:stream_output' "$A" || fail "live action is not a stream"
[ -z "$(find "$R" -name '*.tdnew' -o -name '.actions_topdevices.new')" ] || fail "staging files left behind"
echo "install dry run: OK"
```

- [ ] **Step 2: Run it and watch it fail**

Run: `sh tests/test_install.sh`
Expected: `widget dir not found: /usr/local/opnsense/www/js/widgets/Metadata`, exit status 1.
The 0.0.1 installer knows no `ROOT`.

- [ ] **Step 3: Create the controller**

Create `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php`:

```php
<?php

/*
 * TopDevices live stream: per-device traffic rates as server-sent events.
 * The rates come from scripts/topdevices/live.py through configd; see
 * docs/superpowers/specs/2026-09-23-live-traffic-design.md.
 */

namespace OPNsense\TopDevices\Api;

use OPNsense\Base\ApiControllerBase;

class LiveController extends ApiControllerBase
{
    /**
     * GET /api/topdevices/live/stream/{interval}
     * @param string $interval seconds between samples, 1-10
     */
    public function streamAction($interval = '1')
    {
        $interval = (string)$interval;
        if (!ctype_digit($interval) || (int)$interval < 1 || (int)$interval > 10) {
            return ['status' => 'failed', 'message' => 'interval must be a whole number of seconds from 1 to 10'];
        }
        /* The relay ends a stream that stays silent longer than this poll
           timeout. live.py writes at least once per interval, keepalives
           included, except while one sample runs - and a very large state
           table can make a sample take seconds. The margin is for that: too
           tight, and every slow sample would drop the stream and start a
           reconnect storm of fresh samplers. */
        return $this->configdStream(
            'topdevices live',
            [$interval],
            ['Content-Type: text/event-stream', 'Cache-Control: no-cache'],
            (int)$interval + 10
        );
    }
}
```

- [ ] **Step 4: Create the ACL**

Create `src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml`:

```xml
<acl>
    <page-dashboard-topdevices-live>
        <name>Dashboard: Top Devices live traffic</name>
        <patterns>
            <pattern>api/topdevices/live/*</pattern>
        </patterns>
    </page-dashboard-topdevices-live>
</acl>
```

- [ ] **Step 5: Grant the widget the endpoint**

In `src/opnsense/www/js/widgets/Metadata/TopDevices.xml`, find:

```xml
            <endpoint>/api/dnsmasq/settings/searchHost</endpoint>
```

Replace with:

```xml
            <endpoint>/api/dnsmasq/settings/searchHost</endpoint>
            <endpoint>/api/topdevices/live/*</endpoint>
```

- [ ] **Step 6: Replace the installer**

Replace the whole of `install.sh` with:

```sh
#!/bin/sh
# TopDevices widget installer / updater.
#
# Bootstrap (one command, no GitHub API involved):
#   fetch -qo /tmp/td.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/main && \
#     rm -rf /tmp/tdx && mkdir -p /tmp/tdx && tar -xzf /tmp/td.tgz -C /tmp/tdx && \
#     sh /tmp/tdx/opnsense-plugin-topdevices-main/install.sh
#
# Afterwards:  configctl topdevices install
#
# WHY codeload AND NOT THE API OR raw:
#   - the API costs one rate-limited request per file (60/hour per IP, and it is
#     the firewall's own public IP that counts). Three files is survivable, but
#     it is the same flaw that locked the sibling parentalcontrol plugin out
#     entirely at fifteen.
#   - raw.githubusercontent is CDN-cached, lags pushes by minutes and is cached
#     per edge, so it silently served stale files here more than once.
#   - codeload serves the git ref directly: one request for the whole tree, no
#     rate limit, and current.
#
# These files are not owned by any package, so a firmware upgrade can remove
# them - hence the configd action and the weekly cron job.
#
# UPGRADING FROM 0.0.1: the 0.0.1 installer only knows its own three files. Run
# through configctl or cron it installs the new widget and this script but not
# the live backend, until the next run. Use the bootstrap command once instead.
#
# NOTE: OPNsense cron runs as root regardless - configd executes jobs as root.
# Using cron avoids interactive SSH, not root privileges.
#
# ROOT=<dir> installs under that directory instead of / and skips the configd
# restart and the ACL cache: a dry run for testing, never used on a firewall.
set -e

GH_OWNER="${GH_OWNER:-nycoagung}"
GH_REPO="${GH_REPO:-opnsense-plugin-topdevices}"
GH_REF="${GH_REF:-main}"
ROOT="${ROOT:-}"
P=src/opnsense

WIDGETS=/usr/local/opnsense/www/js/widgets
SCRIPTS=/usr/local/opnsense/scripts/topdevices
MVC=/usr/local/opnsense/mvc/app
ACTIONS=/usr/local/opnsense/service/conf/actions.d

[ -d "$ROOT$WIDGETS/Metadata" ] || { echo "widget dir not found: $ROOT$WIDGETS/Metadata" >&2; exit 1; }

FILES="
$P/www/js/widgets/TopDevices.js|$WIDGETS/TopDevices.js
$P/www/js/widgets/Metadata/TopDevices.xml|$WIDGETS/Metadata/TopDevices.xml
$P/scripts/topdevices/live.py|$SCRIPTS/live.py
$P/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php|$MVC/controllers/OPNsense/TopDevices/Api/LiveController.php
$P/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml|$MVC/models/OPNsense/TopDevices/ACL/ACL.xml
install.sh|$SCRIPTS/install.sh
"

HERE=$(dirname "$0")
CLEAN=""
if [ -d "$HERE/$P/www/js/widgets" ]; then
    SRC="$HERE"
    echo "installing from $SRC"
else
    TMP=$(mktemp -d /tmp/tdinst.XXXXXX)
    CLEAN="$TMP"
    echo "fetching ${GH_OWNER}/${GH_REPO}@${GH_REF} from codeload"
    fetch -qT 30 -o "$TMP/src.tgz" \
        "https://codeload.github.com/${GH_OWNER}/${GH_REPO}/tar.gz/refs/heads/${GH_REF}"
    tar -xzf "$TMP/src.tgz" -C "$TMP"
    SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)
    [ -n "$SRC" ] && [ -d "$SRC/$P/www/js/widgets" ] || { echo "archive did not contain $P" >&2; exit 1; }
fi

# Check the whole set is present before touching anything on disk, so a
# truncated archive cannot leave a half-installed widget behind.
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}
    [ -f "$SRC/$s" ] || { echo "missing from source: $s" >&2; exit 1; }
done

# Stage beside the destination, then rename into place. Not only for atomicity:
# this script installs ITSELF, and sh reads a script incrementally, so a cp over
# the running file shifts the shell's read offset and it dies mid-script. mv
# gives the file a new inode and leaves the running descriptor untouched.
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}; d=$ROOT${entry#*|}
    mkdir -p "$(dirname "$d")"
    cp "$SRC/$s" "$d.tdnew"
done
for entry in $FILES; do
    [ -n "$entry" ] || continue
    s=${entry%%|*}; d=$ROOT${entry#*|}
    mv "$d.tdnew" "$d"
    case "$d" in *.sh|*.py) chmod 0755 "$d" ;; *) chmod 0644 "$d" ;; esac
    printf '  %-26s %6d bytes\n' "$(basename "$s")" "$(wc -c < "$d" | tr -d ' ')"
done
[ -n "$CLEAN" ] && rm -rf "$CLEAN"
echo "widget installed"

# --- register the configd actions (idempotent) ---
if [ -d "$ROOT$ACTIONS" ]; then
    TMP2="$ROOT$ACTIONS/.actions_topdevices.new"
    cat > "$TMP2" <<ACT
[install]
command:$SCRIPTS/install.sh
parameters:
type:script
message:Refreshing TopDevices widget
description:Install/refresh TopDevices dashboard widget

[live]
command:$SCRIPTS/live.py
parameters:%s
type:stream_output
message:TopDevices live stream (%s s)
ACT
    if cmp -s "$TMP2" "$ROOT$ACTIONS/actions_topdevices.conf" 2>/dev/null; then
        # Unchanged: no restart, so the weekly cron run never restarts configd.
        rm -f "$TMP2"
        echo "configd actions already current"
    else
        mv "$TMP2" "$ROOT$ACTIONS/actions_topdevices.conf"
        chmod 0644 "$ROOT$ACTIONS/actions_topdevices.conf"
        if [ -z "$ROOT" ]; then
            # Detached and a second late. configctl and the cron job run this
            # script under configd; configd_stop only signals configd itself, so
            # the script would survive a synchronous restart - but the configctl
            # caller would lose its reply halfway.
            /usr/sbin/daemon -f /bin/sh -c 'sleep 1; /usr/local/etc/rc.d/configd restart'
            echo "configd actions changed - configd restarts in 1 s"
        else
            echo "configd actions changed - restart skipped (ROOT=$ROOT)"
        fi
    fi
fi

# --- make a new ACL privilege visible now, not when the one-hour cache expires ---
if [ -z "$ROOT" ]; then
    if /usr/local/bin/php -r 'require "/usr/local/opnsense/mvc/script/load_phalcon.php"; (new OPNsense\Core\ACL())->invalidateCache();' >/dev/null 2>&1; then
        echo "ACL cache cleared"
    else
        echo "WARNING: could not clear the ACL cache; the new privilege appears within an hour" >&2
    fi
fi

echo "done - hard-refresh the dashboard (Cmd+Shift+R)"
```

- [ ] **Step 7: Check everything in this task**

```bash
sh -n install.sh && shellcheck -s sh install.sh tests/test_install.sh && echo "shell OK"
sh tests/test_install.sh
php -l src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php
xmllint --noout src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml src/opnsense/www/js/widgets/Metadata/TopDevices.xml && echo "xml OK"
```

Expected, in order:
- `shell OK`
- `install dry run: OK`
- `No syntax errors detected in …LiveController.php`
- `xml OK`

- [ ] **Step 8: Commit**

```bash
git add install.sh tests/test_install.sh src/opnsense/mvc src/opnsense/www/js/widgets/Metadata/TopDevices.xml
git commit -m "Live backend: stream endpoint, ACL and installer" -m "LiveController relays the sampler through configd the way core's traffic stream does. Its relay margin of interval + 10 s means one slow sample on a huge state table cannot drop the stream. The installer adds the three backend files and the live action, restarts configd detached and a second late so a configctl caller still gets its reply, and clears the ACL cache through core's own API. ROOT= gives a dry run, which test_install.sh uses."
```

---

### Task 8: Documentation and version 0.1.0

**Files:**
- Modify: `README.md`, `pkg-descr`, `Makefile`

**Interfaces:** none. The sampler already reports `v = '0.1.0'` (Task 1).

- [ ] **Step 1: Add Live to the feature list**

In `README.md`, find:

```markdown
- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range picked to the second
```

Replace with:

```markdown
- **Live** — each device's current download and upload, updated every second,
  from the firewall's connection table (see *Live traffic*)
- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range picked to the second
```

- [ ] **Step 2: Note that Live attributes WireGuard clients**

In `README.md`, find:

```markdown
a byte of VPN internet traffic to the peer address, on any interface, so there
is nothing for the widget to count.
```

Replace with:

```markdown
a byte of VPN internet traffic to the peer address, on any interface, so there
is nothing for the widget to count.

The **Live** view does attribute it: the pf state table keeps each connection's
address from before NAT, so a WireGuard client appears under its tunnel address.

## Live traffic

Pick **Live** in the range list for each device's current download and upload,
in bits per second, updated every second (or every 2 or 5 seconds, a widget
option). NetFlow is not involved: a small sampler on the firewall
(`/usr/local/opnsense/scripts/topdevices/live.py`) reads the pf state table once
per interval and streams per-device rates to the widget through configd, the
way core's own Traffic Graph streams interface counters.

- **Accuracy.** Measured against the kernel's interface counters on the reference
  install, this method accounts for 100.0% of WAN download and 100.3% of upload
  once the firewall's own traffic and 14 bytes of Ethernet header per packet are
  counted. Core's iftop-based *Top talkers* read 32-68% of the same steady load,
  which is why it is not used.
- **What the numbers are.** Rows and chart show a 3-second average, refreshed each
  interval; the WAN figure beside the range is the last interval alone. A device
  that goes quiet stays listed at 0 for 10 seconds. Row order holds still while
  the pointer is over the table.
- **What it cannot see.** A connection that opens and closes between two samples;
  traffic between two devices on the same network, which never reaches the
  firewall; and IPv6, which is counted but not attributed.
- **Behind an ISP router.** The WAN is found by its default route as well as by
  its address, so a WAN with a private address (double NAT) works. The NetFlow
  ranges still use the address-only rule.
- **Cost.** One sampler per open dashboard, only while Live is on screen and the
  tab is visible. It measures its own CPU and slows down rather than use more
  than 10% of one core, and says so (*throttled to N s*). It restarts itself every
  hour; the browser reconnects about a second later.
- **Self-check.** Each update compares the attributed traffic with the WAN
  counters over the last 60 seconds. If they disagree by more than 10% - the sign
  of a firmware update having changed pfctl's output - the widget says so instead
  of showing quietly wrong numbers.
- **Access.** The stream needs the *Dashboard: Top Devices live traffic*
  privilege. It shows every device's peers, as Diagnostics → States does.
```

- [ ] **Step 3: Explain the one-off upgrade from 0.0.1**

In `README.md`, find:

```markdown
Then hard-refresh the dashboard (Cmd+Shift+R) and add **Top Devices** from the
widget picker. Afterwards `configctl topdevices install` does the same thing.
```

Replace with:

```markdown
Then hard-refresh the dashboard (Cmd+Shift+R) and add **Top Devices** from the
widget picker. Afterwards `configctl topdevices install` does the same thing.

**Upgrading from 0.0.1:** run the bootstrap command above once. The 0.0.1
installer only knows its own three files, so `configctl topdevices install` (or
the weekly cron job) would install the new widget without the live backend
until its next run.
```

- [ ] **Step 4: Name the 0.1.0 package in the build instructions**

In `README.md`, find:

```markdown
That builds `os-topdevices-0.0.1.pkg`; `pkg add` it on the firewall, or run
```

Replace with:

```markdown
That builds `os-topdevices-0.1.0.pkg`; `pkg add` it on the firewall, or run
```

- [ ] **Step 5: Point the testing note at the new suites**

In `README.md`, find:

```markdown
**Not tested:** there are no automated tests. The data logic is the part worth
covering; the sibling `os-parentalcontrol` plugin shows the shape (a pure
function plus a table-driven suite that needs no OPNsense).
```

Replace with:

```markdown
**Automated tests** cover the live sampler and the widget's live logic (see
*Tests*). The NetFlow views still have none.
```

- [ ] **Step 6: Add Tests and Removing sections, and qualify the requirement**

In `README.md`, find:

```markdown
## Requirements

Reporting → NetFlow must be enabled with local aggregation on.
```

Replace with:

```markdown
## Tests

    python3 -m unittest discover -s tests -v    # sampler: parser, crediting rules, loop
    node --test tests/live_view.test.mjs         # widget: averaging, lifecycle, watchdog
    sh tests/test_install.sh                     # installer dry run into a scratch root
    python3 tests/mutate.py                      # each planted sampler bug must fail the suite
    node tests/mutate_widget.mjs                 # the same for the widget

`tests/test_core_parity.py` compares our pfctl parser with core's own on the
fixture: point `OPNSENSE_CORE` at an opnsense/core checkout, or `CORE_STATES_PY` at
its `states.py`. On the firewall, `python3 tests/parity_live.py` does the same
over the live state table.

## Removing

Remove the weekly *Install/refresh TopDevices dashboard widget* job under
System → Settings → Cron first, or it puts everything back. Then, as root:

    rm -f /usr/local/opnsense/www/js/widgets/TopDevices.js \
          /usr/local/opnsense/www/js/widgets/Metadata/TopDevices.xml \
          /usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf
    rm -rf /usr/local/opnsense/scripts/topdevices \
           /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices \
           /usr/local/opnsense/mvc/app/models/OPNsense/TopDevices
    service configd restart

Rolling back to 0.0.1 with its bootstrap command leaves the live sampler, the
controller and the ACL behind. They are inert without the `live` action, and the
commands above remove them.

## Requirements

Reporting → NetFlow must be enabled with local aggregation on, for the
historical ranges. Live needs nothing extra.
```

- [ ] **Step 7: Replace `pkg-descr`**

Replace the whole of `pkg-descr` with:

```text
Dashboard widget listing local devices by traffic, split into download and
upload. Historical ranges come from the built-in NetFlow/Insight aggregator;
a Live view streams current rates, updated every second, from the pf state
table. No extra collector is required.

Pick a range (live, last hour, last 24 hours, today, yesterday, last 7 days
or a custom range) and a scope (all traffic, or internet only). Filter by
network, hostname or IP, sort any column, and click a device for its top
peers and ports. Totals can be charted as a pie or a stacked bar.

Local networks and the upstream interface come from the firewall's own
interfaces (and, for Live, its routes), and hostnames from Dnsmasq DHCP
leases and host records. Nothing is hardcoded.

WWW: https://github.com/nycoagung/opnsense-plugin-topdevices

Plugin Changelog
----------------

0.1.0

* Live view: per-device download and upload rates, streamed from the pf
  state table and updated every second
* The live sampler throttles itself, checks its totals against the WAN
  counters and restarts hourly
* The installer adds the live backend, clears the ACL cache and restarts
  configd detached

0.0.1

* Initial release
```

- [ ] **Step 8: Bump the Makefile**

In `Makefile`, replace `PLUGIN_VERSION=		0.0.1` with `PLUGIN_VERSION=		0.1.0`, and
`PLUGIN_COMMENT=		Top devices dashboard widget (NetFlow/Insight)` with
`PLUGIN_COMMENT=		Top devices dashboard widget (NetFlow/Insight and live pf states)`.
Keep the tabs.

- [ ] **Step 9: Check the version is consistent**

Run: `grep -n "0\.0\.1\|0\.1\.0" Makefile pkg-descr README.md src/opnsense/scripts/topdevices/live.py`
Expected: `0.1.0` in the Makefile, in `pkg-descr`'s newest changelog entry, in the
README's package name and in `VERSION`. `0.0.1` should appear only in the
changelog's history and the README's upgrade note.

- [ ] **Step 10: Commit**

```bash
git add README.md pkg-descr Makefile
git commit -m "Document the Live view; version 0.1.0" -m "The README explains what Live measures, how accurately, what it cannot see and what it costs, how the one-off upgrade from 0.0.1 works, how to run the tests and how to remove everything. pkg-descr and the Makefile move to 0.1.0."
```

---

### Task 9: Pre-install gate

**Files:** none new, apart from fixes for whatever this task finds.

- [ ] **Step 1: Run every local check**

```bash
OPNSENSE_CORE="$TMPDIR/core-26.7.4" python3 -m unittest discover -s tests -v
node --check src/opnsense/www/js/widgets/TopDevices.js && node --test tests/live_view.test.mjs
sh tests/test_install.sh
python3 tests/mutate.py
node tests/mutate_widget.mjs
shellcheck -s sh install.sh tests/test_install.sh
php -l src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php
xmllint --noout src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml src/opnsense/www/js/widgets/Metadata/TopDevices.xml
```

Expected:
- `Ran 39 tests … OK`
- `ℹ pass 18`, `ℹ fail 0`
- `install dry run: OK`
- 10 × `killed`, then 6 × `killed`
- `shellcheck` silent, `php -l` reports no errors, `xmllint` silent

- [ ] **Step 2: Check nothing private is in the diff**

Run: `git diff main --stat` and read it. Then grep `git diff main` for the reference
firewall's public address, its host names, the API key file's name and the firewall's
own address. The executor knows these values; they are deliberately not written here.
Expected: no matches. Fixtures and docs use documentation ranges and placeholders
only.

- [ ] **Step 3: Get a code review of the branch**

Use superpowers:requesting-code-review on `main..live-traffic`. Fix what it confirms,
rerun Step 1, and commit the fixes with messages that say what each fixes and why.

- [ ] **Step 4: Push the branch**

```bash
git push -u origin live-traffic
git ls-remote origin refs/heads/live-traffic
```

Expected: the second command prints the same SHA as `git rev-parse HEAD`.

- [ ] **Step 5: Check codeload serves what was pushed**

```bash
D=$(mktemp -d) && curl -sf -o "$D/b.tgz" https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/live-traffic \
  && tar -xzf "$D/b.tgz" -C "$D" && for f in src/opnsense/www/js/widgets/TopDevices.js src/opnsense/www/js/widgets/Metadata/TopDevices.xml \
  src/opnsense/scripts/topdevices/live.py src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php \
  src/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml install.sh; do cmp -s "$f" "$D/opnsense-plugin-topdevices-live-traffic/$f" \
  && echo "same: $f" || echo "DIFFERENT: $f"; done
```

Expected: six `same:` lines.

---

### Task 10: Install on the firewall and check its health

In the commands below, `$FW` is the firewall's address. `$KEY` and `$SECRET` are an API
key with access to Diagnostics and to this plugin's privilege.

The weekly *Install/refresh TopDevices dashboard widget* job reinstalls `main`, which
would silently put 0.0.1 back. Either finish Task 11 before its next run, or pause
the job under System → Settings → Cron until Task 12.

- [ ] **Step 1: (user) Install the branch from an SSH shell, not through `configctl`**

```sh
fetch -qo /tmp/td.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/live-traffic && \
  rm -rf /tmp/tdx && mkdir -p /tmp/tdx && tar -xzf /tmp/td.tgz -C /tmp/tdx && \
  sh /tmp/tdx/opnsense-plugin-topdevices-live-traffic/install.sh
```

Expected:
- six file lines with byte counts;
- `configd actions changed - configd restarts in 1 s`;
- `ACL cache cleared`;
- `done - …`.

- [ ] **Step 2: Health check, straight away**

```bash
curl -sk -u "$KEY:$SECRET" -o /dev/null -w '%{http_code}\n' "https://$FW/api/diagnostics/traffic/interface"
curl -sk -u "$KEY:$SECRET" -N --max-time 5 "https://$FW/api/topdevices/live/stream/1" | head -c 600; echo
curl -sk -u "$KEY:$SECRET" "https://$FW/api/parentalcontrol/service/status" | head -c 300; echo
```

Expected:
- `200`, which means configd is answering;
- `retry: 1000` followed by `data: {"v":"0.1.0",…` events. The second event should
  carry `"dt":1.0…` and non-empty `devices`, with `error` `null`;
- Parental Control's status JSON reports the pf table agreeing. Its sync runs through
  configd every minute, so it must still be working after the restart.

If configd does not answer, run `service configd restart` in the SSH shell and report
it.

- [ ] **Step 3: (user) Run the suites on the firewall's own Python**

```sh
cd /tmp/tdx/opnsense-plugin-topdevices-live-traffic
env CORE_STATES_PY=/usr/local/opnsense/scripts/filter/lib/states.py /usr/local/bin/python3 -m unittest discover -s tests -v
/usr/local/bin/python3 tests/parity_live.py
```

Expected:
- `Ran 39 tests … OK` on Python 3.13, which settles the version-compatibility risk;
- the parity line reports `unparsed 0` and `mismatches 0`, with exit status 0. That
  settles the parser against real `pfctl` output.

- [ ] **Step 4: Check the deployed files by hash**

```bash
for p in TopDevices.js Metadata/TopDevices.xml; do
  printf '%s  ' "$p"; curl -sk -u "$KEY:$SECRET" "https://$FW/ui/js/widgets/$p" | shasum -a 256 | cut -c1-12
  shasum -a 256 "src/opnsense/www/js/widgets/$p" | cut -c1-12
done
```

Expected: each pair of hashes is identical.

- [ ] **Step 5: (user) Open Live**

Hard-refresh the dashboard, then pick **Live** in the TopDevices range list.
Expected: "measuring…" for about a second, then rows in b/s, and the summary line
`Live · WAN ↓ … ↑ … (via <WAN>) · … · 1 s`.

---

### Task 11: Verification on the firewall (spec §11)

Record every number: Task 12 puts them in the README. To capture the stream for a
step, use:

```bash
curl -sk -u "$KEY:$SECRET" -N --max-time 75 "https://$FW/api/topdevices/live/stream/1" > "$TMPDIR/stream.txt"
python3 - <<'PY'
import json, os, statistics
ev = [json.loads(l[6:]) for l in open(os.path.join(os.environ['TMPDIR'], 'stream.txt')) if l.startswith('data: ')]
cost = [e['cost_ms'] for e in ev]
print('events', len(ev), '| cost_ms median', statistics.median(cost), 'max', max(cost))
print('last coverage', ev[-1]['coverage'], '| unparsed', ev[-1]['unparsed'], '| error', ev[-1]['error'])
PY
```

- [ ] **Step 1: Deployed files.** Task 10, steps 3 and 4.
- [ ] **Step 2: Accuracy.** Capture 75 s of the stream during ordinary use.
  - Pass: the last event's `coverage` has `ok: true`. Every judged direction is within
    0.98–1.02; that is the spec's ±2%, tighter than the runtime band.
  - Also compare the IoT VLAN's `all` uploads with that interface's
    `bytes received` over the same minute (`/api/diagnostics/traffic/interface` read
    twice, 60 s apart). They should agree within ±5%.
- [ ] **Step 3: (user) Real devices.**
  - A speed test on one device shows it at the speed-test rate, within about 10%.
  - A device behind a shaper pipe tops out at the pipe's limit.
  - A phone connected over WireGuard appears under its tunnel address.
- [ ] **Step 4: Cost.** From the capture, record the median and maximum `cost_ms`. From
  `/api/diagnostics/activity/get_activity`, record the WCPU of the process whose
  `COMMAND` contains `topdevices/live.py`.
  - Pass: median `cost_ms` is at or below 30 ms at interval 1, and no `throttled: true`
    under ordinary load.
- [ ] **Step 5: Lifecycle.** Count processes whose `COMMAND` contains
  `topdevices/live.py`, through the activity API, after each of the user's actions:
  - hide the tab: 0 within 2 s;
  - show it again: 1;
  - switch to Last 24 hours: 0, and still 0 after hiding and showing the tab;
  - remove the widget: 0;
  - with Live open again, `service configd restart`: 0 within 2 s, then 1 once the
    widget reconnects.
- [ ] **Step 6: Upgrade path under configd.** The user appends a comment line to
  `/usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf`, then runs
  `configctl topdevices install`.
  - Pass: it prints `OK`; within 5 s the API answers (Task 10 step 2); the stream
    works; and the action file again holds exactly the generated content.
- [ ] **Step 7: Soak.** With Live open for 65 minutes, which spans one hourly recycle,
  sample the sampler's RES from the activity API every 5 minutes.
  - Pass: RES stays within a few MB of its first value, and a new PID appears after
    the recycle.
  - The user sees no gap longer than the reconnect.
- [ ] **Step 8: (user) NetFlow regression checklist.** Each NetFlow range: last hour,
  24 hours, today, yesterday, 7 days, and a custom range with Apply. Pass: each
  behaves exactly as in 0.0.1, under:
  - both scopes;
  - the network filter and search;
  - sorting on every column;
  - 10, 20, 50 and 100 rows;
  - a device's detail panel, including its 10/50/100 selector;
  - Pie, Bar and Off;
  - the header refresh.
- [ ] **Step 9: (user) ACL.** Straight after the install, System → Access → Groups →
  (edit any group) → privileges lists *Dashboard: Top Devices live traffic*. Don't
  save anything.

---

### Task 12: Release 0.1.0

- [ ] **Step 1: Record what was verified**

In `README.md`, under `## What has been verified, and what has not`, add one bullet per
Task 11 result, with the measured numbers:
- accuracy (coverage and the VLAN cross-check);
- cost (median and maximum `cost_ms`, WCPU);
- the lifecycle results;
- the upgrade under configd;
- the soak (RES and the recycle);
- the on-firewall parity (states compared, `unparsed 0`, `mismatches 0`);
- the Python 3.13 run.

Then list, as not verified:
- non-root users (only root exists on the reference install);
- IPv6 attribution;
- multiple WANs.

Commit: `git commit -am "README: record the Live verification results"`, with a body
listing the headline numbers.

- [ ] **Step 2: Merge to main**

```bash
git switch main && git merge --ff-only live-traffic && git log --oneline -3
```

Expected: `main` now points at the branch head. `main` has not moved since 0.0.1, so
the fast-forward applies.

- [ ] **Step 3: Tag and push**

```bash
git tag -a 0.1.0 -m "TopDevices 0.1.0

Live view: per-device rates every second, streamed from the pf state table."
git push origin main && git push origin 0.1.0 && git ls-remote origin refs/heads/main 'refs/tags/0.1.0*'
```

Expected: `main` and `0.1.0^{}` both resolve to the release commit.

- [ ] **Step 4: Publish the GitHub release**

`gh release create 0.1.0 --repo nycoagung/opnsense-plugin-topdevices --verify-tag --title 0.1.0 --notes-file <notes>`

The notes should cover, in this order:
- what Live is;
- the measured accuracy and cost from Task 11;
- that upgrading from 0.0.1 needs the bootstrap command once;
- an install command for this tag (codeload `refs/tags/0.1.0`, directory
  `opnsense-plugin-topdevices-0.1.0`);
- that the weekly job follows `main`;
- that it is source only.

Read it back with `gh release view 0.1.0`.

- [ ] **Step 5: Make sure the weekly job is now a no-op**

Download codeload `refs/heads/main` and compare the six deployed files, as in Task 9
step 5, but against the firewall's served copies from Task 10 step 4.
Expected: identical. The weekly run then restarts nothing.

- [ ] **Step 6: Resume the weekly job** if it was paused in Task 10.
