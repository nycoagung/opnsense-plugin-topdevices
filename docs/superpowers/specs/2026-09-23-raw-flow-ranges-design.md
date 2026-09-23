# Exact NetFlow ranges from the raw flow log: design

- **Status:** approved section by section in conversation on 2026-09-23; this written
  spec awaits review.
- **Date:** 2026-09-23
- **Branch:** `raw-flows`, from `fix-0.1.2` (`5eb6a91`)
- **Baseline:** 0.1.2 as installed from `fix-0.1.2` on the reference firewall
  (OPNsense 26.7.4, FreeBSD 15.1, Python 3.13, i5-8500 with 6 cores).
- **Release:** 0.2.0, which is 0.1.2 plus this. 0.1.2 is held until then.

## 1. Goal

Ranges that start within the last 24 hours should show exactly the window asked
for, to the second, in both scopes. That means Last hour, Today, Last 24 hours, and
custom ranges starting within the last day. Each device's download and upload must
equal what OPNsense's own code computes from the same data.

The user's examples, as captions:

- Last hour: `Wed Sep 23 19:28:32 AEST 2026 → Wed Sep 23 20:28:32 AEST 2026 · internet only (via em0)`
- Last 24 hours: `Tue Sep 22 20:29:54 AEST 2026 → Wed Sep 23 20:29:54 AEST 2026 · …`
- Today: `Wed Sep 23 00:00:00 AEST 2026 → Wed Sep 23 20:31:08 AEST 2026 · …`

Success criteria, each checked on the firewall before merge (§11):

- `flows.py` totals equal core's own parser and aggregator, run once over the same
  log, to 0 B per device. This is checked for an hour-aligned window in all traffic,
  and for a window the log fully covers in internet only.
- Wherever the log reaches, the captions read as above, with no note.
- End to end on the reference firewall, with 3 workers: Today and Last 24 hours in at
  most 2 s, and Last hour in at most 1 s.
- No cron job, no database, and nothing written on the firewall.
- Yesterday, Last 7 days, older custom ranges and the Live view behave exactly as in
  0.1.2.

## 2. Non-goals

- No history beyond what the raw log holds: no recorder and no cache.
- No change to how ranges older than 24 hours are read (0.1.2's daily and hourly
  records).
- No IPv6 attribution. IPv6 records are skipped, as in every other view.
- No change to core's NetFlow settings, such as the active timeout or log sizes.
- No fix for core's own aggregation gap (§3). The widget reads what the log holds.

## 3. Why the raw log (measured on the reference firewall)

- **Core's records cannot do it.** `FlowSourceAddrDetails`, the only aggregate that
  records the interface a flow crossed, keeps one bucket per UTC day, which is 10:00 →
  10:00 in AEST. `FlowSourceAddrTotals` has 5-minute and hourly buckets but no
  internet/local split.
- **The raw log can.** `/var/log/flowd.log`, with rotated `.000001` and up, is
  root-only. It holds every flow with its source, destination, byte count, ports,
  interfaces and times.
  - Core keeps it by size, not by time: `MAX_FILE_SIZE_MB = 10` per file in
    `flowd_aggregate.py`. 11 files (110 MB) held about **23 hours** here, at about
    100 B per flow and 105,000 flows per file.
- **Speed.** Core's `FlowParser` takes 1.85 s per 10.8 MB file. A purpose-built reader
  takes 0.18 s per file, per-device sums included. That puts the whole log at 1.80 s
  on one core, or 0.41 s on six.
- **Accuracy.**
  - The reader is record-identical to core's parser: 107,790 records, 0 differences.
  - For 19:00–20:00 its per-device totals are byte-identical to core's own parser and
    aggregator run once over the same log (39 devices, 0.0 B gap).
  - Core's *live* database was 0.0041% lower for that hour. The reason is
    `parse_flow()`, which resumes by skipping `recv_sec <= last_sync`: records written
    within the already-synced second after a run read the file are never aggregated.
- **Stability.** One worker and six gave identical results for a window that ended 45
  minutes ago. A window ending now changes between reads as records arrive: 1.3 MB of
  difference for 9.4 KB of new log.
- **Limit.** NetFlow's active timeout is 1800 s here. A long connection is reported
  every 30 minutes, so the most recent half hour can under-count it.

## 4. Coverage rules

**Raw-log ranges** are those with `from >= now − 86400 − 300`. The 5 minutes of slack
covers a browser clock that differs from the firewall's. Everything else uses 0.1.2's
path, unchanged.

**L: where the log becomes complete.** L is the receive time of the first record in
the oldest log file, taking files in order of last write. The log holds every flow
that was active at any moment t ≥ L. A flow's bytes at t are reported in a record
received after t, so after L, and records are only ever appended.

**S: the seam** is the first whole hour at or after L: `S = ceil(L / 3600) · 3600`.
Let `S' = min(S, to)`.

**All traffic, for [from, to]:**

- If `from ≥ L`, the raw log alone covers [from, to].
- If not, core's hourly records cover [from, S') and the raw log covers [S', to].
  - An hourly bucket b counts in proportion to its overlap with [from, S') over the
    time it spans, `min(b + 3600, now) − b`. The bucket containing *from* is therefore
    partial, and a bucket still in progress counts for the part already recorded.
  - When `S' = to`, the raw part is empty and the hourly records cover the whole
    range.
- The caption shows [from, to], with no note.

**Internet only, for [from, to]:**

- The raw log covers [max(from, L), to].
- If `max(from, L) > from`, the caption starts at L, with a note: *"Internet only
  covers the last 23 h 0 min: older flows are no longer in the log"*. The duration is
  `to − L`, in hours and minutes, rounded down to the minute.
- If `max(from, L) ≥ to`, the range has no rows, with a note: *"Internet only: no
  flows in the log for this range"*.

**Device panel:**

- Peers and ports come from the raw log over [max(from, L), to], in both scopes.
- If that is shorter than the range, a note says so: *"Peers and ports cover the last
  23 h 0 min"*.
- The panel's download and upload are the row's figures.

**Spreading a record over time.** This follows core's `add()` exactly, with the same
expressions in the same order, so results match core to the byte:

- `flow_end = recv − (uptime − finish) / 1000`, and
  `flow_start = flow_end − (finish − start) / 1000`.
- A record's share of [lo, hi) is
  `(min(flow_end, hi) − max(flow_start, lo)) / (duration_ms / 1000) · octets`, when
  positive.
- A zero-duration record counts whole if `lo ≤ flow_start < hi`.
- A negative-duration record, which is bogus, counts as core's own arithmetic makes
  it: `(flow_end − flow_start) / (duration_ms / 1000) · octets`, if
  `lo ≤ flow_start < hi`.

**Freshness.** Every request reads the log as it is at that moment. The most recent
half hour can under-count long connections (§3). This is documented in the README,
not shown in the widget.

**Time zones.** Within the log's reach, Today starts at the browser's exact local
midnight, including half-hour zones and days with a clock change. Only an hourly
fill-in is bound to UTC hours, and that happens when the log does not reach back to
*from*.

**Known edge.** Core's cleanup can drop an hourly bucket early in one branch: while it
holds rows stamped in the future, it expires from `now − history` rather than from its
newest bucket. Last 24 hours can then be short, with no note, by at most the part of
its first hour inside the range. This is documented rather than worked around, so the
normal case keeps the exact 24 hours.

## 5. Script: `scripts/topdevices/flows.py`

### 5.1 Command line and output

`flows.py totals FROM TO` and `flows.py device IP FROM TO`, where FROM and TO are
integer epoch seconds. The script prints one JSON line and exits 0.

Validation, repeated here after the controller has checked the same things:

- FROM < TO.
- FROM ≥ now − 86400 − 300.
- TO ≤ now + 300. TO is then capped at the firewall's now.
- IP is a dotted IPv4 address and a device (§5.3).

`totals` returns:

```json
{"v": "0.2.0", "now": 1790155000, "log_from": 1790082376,
 "all":  {"from": 1790151400, "to": 1790155000, "hourly_until": null},
 "inet": {"from": 1790151400, "to": 1790155000},
 "wan": ["em0"],
 "devices": {"192.168.1.10": [1000, 357, 1000, 300]},
 "files": 2, "workers": 3, "cost_ms": 212}
```

- `devices` holds, per IPv4 address, `[all_down, all_up, inet_down, inet_up]` in whole
  bytes.
- `all.hourly_until` is S' when hourly records filled [from, S'), and null otherwise.

`device` returns:

```json
{"v": "0.2.0", "ip": "192.168.1.10", "from": 1790151400, "to": 1790155000,
 "peers": {"all": [["8.8.8.8", 1000]], "inet": [["8.8.8.8", 1000]]},
 "ports": {"all": [["443", 1300]], "inet": [["443", 1300]]}}
```

- Each list is the top 100 by bytes in both directions, sorted by its own scope's
  bytes.
- A port is the service port as core defines it, `min(src_port, dst_port)`. Port 0 is
  listed as `other`.

On any failure, such as bad arguments, a missing or unreadable log, or core's NetFlow
library not importable, the script prints `{"error": "<reason>"}`, never a traceback.

### 5.2 Reading the log

- **Files.** The script uses `/var/log/flowd.log*`, in order of last write. It opens
  every file whose last write is at or after *lo*, the earliest start of the raw spans
  it needs. For totals that is the earlier of the all-traffic raw start and the
  internet-only start (§4); for a device it is `max(from, L)`. A record received
  before *lo* ended before *lo*. Newer files are never skipped, so correctness does
  not depend on the active timeout.
- **Decoding.**
  - Each record has an 8-byte header, unpacked as `>BBHI`: version, length in 32-bit
    words, reserved, field mask. The fields follow in core's order
    (`lib/flowparser.py`).
  - Each distinct mask gets one precompiled `struct.Struct` for the fields needed:
    `recv_time` seconds, `src_addr4`, `dst_addr4`, `octets`, `if_indices`, the uptime
    from `agent_info`, `flow_times`, and `srcdst_port` in device mode.
- **Skipped records,** exactly those core skips:
  - no `recv_time`, `agent_info`, `packets`, `octets`, source or destination;
  - `flow_finish > uptime`.
  - IPv6 records are skipped too.
- **Workers.** `max(1, (os.cpu_count() or 2) // 2)` processes, forked, one file per task,
  largest first, with the results summed. Each worker holds one file, at most about 11
  MB, at a time.

### 5.3 Networks and interfaces

- **Upstream and local.** These come from `live.py`, imported from the same directory:
  `parse_ifconfig`, `parse_default_devs` and `Topology`, the Live sampler's own
  rules.
  - Upstream is every interface with a default route, plus any with a public address.
  - Local is the RFC 1918 subnets of the other interfaces.
  - Live and these ranges therefore agree on what "internet" means, double NAT
    included.
- **Device.** An IPv4 address in a local subnet that is not the subnet's broadcast
  address. The firewall's own addresses are kept, so they appear as "… gateway" in the
  table as they do today.
- **Interfaces.** Interface numbers map to names with core's `lib.parse.Interfaces`,
  which reads `ifinfo`, exactly as core's aggregator does.
- **Attribution per record:**
  - destination is a device: its download, counted as internet if the ingress
    interface is upstream;
  - source is a device: its upload, counted as internet if the egress interface is
    upstream.

### 5.4 Hourly fill-in (all traffic only, when from < L)

- Core's class does the reading: `lib.aggregates.source.FlowSourceAddrTotals(3600)`,
  whose database lives in `/var/netflow`, called with
  `get_data(floor(from / 3600) · 3600, S')`.
- For each row, the address is `src_addr`, and the direction decides the figure:
  `out` → download, `in` → upload. Only devices are kept, and each bucket is weighted
  as in §4.

### 5.5 Device mode

- For the device, every record where it is the source or destination adds to the
  other end's peer entry and to the record's service port, in both directions.
- Internet entries count only records that crossed upstream on that device's side:
  ingress for its downloads, egress for its uploads.
- The span is [max(from, L), to].

## 6. API, configd, ACL

### 6.1 configd actions (generated by `install.sh`)

```
[flows.totals]
command:/usr/local/opnsense/scripts/topdevices/flows.py totals
parameters:%s %s
type:script_output
message:TopDevices flows totals %s %s

[flows.device]
command:/usr/local/opnsense/scripts/topdevices/flows.py device
parameters:%s %s %s
type:script_output
message:TopDevices flows device %s
```

### 6.2 Controller: `Api/FlowsController.php`

- `GET /api/topdevices/flows/totals/{from}/{to}`: both arguments must pass
  `ctype_digit`. The controller then calls
  `configdpRun('topdevices flows totals', [$from, $to])` and returns the decoded
  JSON.
- `GET /api/topdevices/flows/device/{ip}/{from}/{to}`: the address must pass
  `filter_var(…, FILTER_VALIDATE_IP, FILTER_FLAG_IPV4)` and both times `ctype_digit`.
- Anything else returns `{"error": …}`. configd's default timeout (120 s) is kept.
- **Least privilege.** The controller refuses a caller who may not also use
  `api/diagnostics/networkinsight/*`, core's own NetFlow access. The privilege
  therefore never reveals more than core already would (§14).

### 6.3 ACL and widget metadata

- The ACL's `page-dashboard-topdevices-live` gains the pattern `api/topdevices/flows/*`.
  Its display name becomes *Dashboard: Top Devices*; the key is unchanged, so
  existing grants carry over.
- The widget's `<endpoints>` gains `/api/topdevices/flows/*`.

## 7. Widget

- **Path.** A range starting within the last 24 hours (§4) is read with one `$.ajax`
  GET to `…/flows/totals/{from}/{to}`, with a 60 s timeout. `ajaxCall` would give up
  after 5 s and retry, stacking reads. Other ranges use 0.1.2's code unchanged.
- **Rows.** Both scopes' rows come from the one response. A scope switch redraws from
  it, with no request. 0.1.2's reload-until-rows-match loop stays for the export path
  only.
- **Captions.** These come from the response's spans: `all` or `inet`, with the
  upstream names from `wan`. Notes are only those in §4.
- **Device panel.** It sends `…/flows/device/{ip}/{from}/{to}` for the table's request
  when a device is opened. A scope switch redraws from that response.
- **Fallback.** A failed request, a timeout or `{"error"}` makes that load use 0.1.2's
  path, with the note *"The raw flow log could not be read: showing NetFlow's
  records"*.
- **Unchanged:**
  - concurrency, since 0.1.2's load token drops a load a newer one replaced;
  - refreshes, each a fresh read at a new now;
  - the cache, which keeps only the current range's response and the open device's;
  - sorting, filters, the network selector, row counts, charts and Live.
- **Testable core.** Pure, exported functions:
  - `rawRange(range, from, now)`: which path a range takes;
  - `rawRows(response, scope)`;
  - `rawCaption(response, scope)`: text and note.

## 8. Installer

- 8 files instead of 6; `flows.py` and `Api/FlowsController.php` are new.
- The generated `actions_topdevices.conf` gains the two actions in §6.1. Its content
  therefore changes once, and the installer restarts configd once, detached, 1 s
  later, as it already does.
- The dry run (`ROOT=`) and its test cover the new files and actions.

## 9. Security

- **Inputs** are checked twice, in PHP and in Python. Only digits and dotted IPv4
  reach configd.
- **Exposure.** Per-device totals and peers are exactly what the widget already shows
  through core's NetFlow exports. The least-privilege check (§6.2) keeps it that way.
- **Cost.** One request uses at most 3 cores for about a second. There is no rate
  limit: the widget refreshes slowly and only while a dashboard is open. This is
  documented.
- **Writes.** The log is read as root through configd, and nothing is written.

## 10. Tests (local, before anything reaches the firewall)

No private data appears in any test: every log is generated in core's binary format
during the test.

- **`tests/test_flows.py`:**
  - decoding, with hand-built records for IPv4 and IPv6, missing `if_indices` or
    `flow_times`, zero, negative and impossible durations, and records core skips;
  - coverage: L, S and S', when the fill-in applies, internet only cut at L, the
    empty cases, and hourly weights including a bucket in progress;
  - attribution: download and upload, local, broadcast, and upstream in both
    directions;
  - file selection, and that 3 workers give exactly what 1 gives;
  - the command line: every bad input gives `{"error"}`, and the output shape is
    right.
- **Parity with core,** skipped when core's NetFlow library is absent. It runs when
  `OPNSENSE_CORE` points at a checkout, and always on the firewall. Records must equal
  core's `FlowParser`, and totals over bucket-aligned windows must equal core's
  aggregator.
- **Widget, in `tests/netflow_ranges.test.mjs`:**
  - which path each range takes, and its request;
  - both scopes from one response, and a scope switch with no request;
  - exact captions, and notes only when §4 says;
  - the device panel;
  - the fallback and its note;
  - a replaced load being dropped.
- **Mutation checks** in `tests/mutate.py` (script) and `tests/mutate_widget.mjs`
  (widget). Every rule above has a mutant that a test kills.
- **`tests/test_install.sh`:** 8 files, the two actions with fixed arguments, and the
  ACL pattern.

## 11. Verification on the firewall (before merge)

1. A one-off check, as in the spike, comparing `flows.py` with core's own code run
   once over the same log. It must show 0 B per device for an hour-aligned all-traffic
   window and a fully covered internet-only window. It also measures real timings with
   3 workers, and the parity tests run on the firewall's Python 3.13.
2. Both endpoints called from the admin Mac with the API key: sensible JSON, bad input
   rejected, and the least-privilege check exercised.
3. The widget end to end:
   - its code run in Node against the firewall;
   - the headless-Chrome harness;
   - the user's own dashboard, with the three captions in §1.
4. A fresh reviewer over the whole branch.

## 12. Rollout and rollback

- **Rollout.** Work goes on `raw-flows`. The user installs it from the branch and
  verifies it. It merges to `main`, and is tagged and released as 0.2.0, only with the
  user's approval.
- **The Sunday job.** The weekly 05:00 job installs `main`, which is 0.1.1 until the
  merge. If the merge is not done by Sun 27 Sep 05:00, pause the job under System →
  Settings → Cron, or the firewall goes back to 0.1.1.
- **Rollback.** Install an earlier ref. The installer rewrites the actions file
  without the `flows` actions and restarts configd. `flows.py` and
  `FlowsController.php` are left behind, harmless and unreferenced, and README
  *Removing* lists them.

## 13. Risks

- **A busy network shortens the log.** More of Last 24 hours then comes from hourly
  records, and internet-only notes appear more often. The caption stays honest.
- **Core changes the flowd format or its NetFlow library.** The parity tests on the
  firewall catch it, and the widget falls back to 0.1.2's path when the script
  reports an error.
- **Several dashboards open.** Each refresh is a short burst on up to 3 cores.
- **A larger active timeout.** Records span longer. Completeness from L still holds,
  but the recent under-count grows.

## 14. To confirm during implementation

- How configd formats `parameters:%s …` for `script_output` actions: separate argv
  items, and quoting. Read `service/modules/actions/` in core.
- The API for the least-privilege check in a controller: the caller's name, and
  `ACL::isPageAccessible()` or equivalent.
- Whether `srcdst_port` is present in the reference firewall's field masks, which
  device mode needs.
- Whether importing core's `lib.parse` and `lib.aggregates.source` from `flows.py`
  works under configd's environment (working directory and `sys.path`).
- Whether forked workers under configd, with no tty and configd's signal handling,
  start and exit cleanly.
