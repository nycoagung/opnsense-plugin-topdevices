# os-topdevices

OPNsense dashboard widget: top local devices by traffic, from the built-in
NetFlow/Insight aggregator. No extra collector required.

## Features

- **Live** — each device's current download and upload, updated every second,
  from the firewall's connection table (see *Live traffic*)
- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range. One starting within the last 50 hours, Yesterday included, is exact
  to the second, read from NetFlow's raw flow log, which the plugin keeps for two days
  (see *Keeping the flow log*); older ones snap to the buckets NetFlow keeps. The
  caption shows the exact span (see *Upstream API limitations*)
- **Download / upload split** per device, plus the combined total
- **All traffic or internet only** — the latter counts flows at the WAN,
  so purely local traffic is excluded
- **Filter** by network (LAN / IOT / GUEST / …), or free-text on hostname or IP
- **Sort** on any column, ascending or descending (NetFlow ranges; Live keeps its
  own order)
- **Row count** selectable at 10 / 20 / 50 / 100
- **Detail column** beside the table, so the list stays visible while you click
  through devices; collapses to below the table when the widget is narrow
- **Drill-down** — click a device for its top peers, top ports and direction split
- **Charts** — pie of totals, or a stacked bar of download vs upload; in Live,
  also a line graph of each device's download over the last minute
- **Selections persist** across refreshes and reloads (localStorage)
- **Refresh control** in the widget header, and a loading overlay while the
  export is being fetched
- The widget **resizes to its content** after filtering, via gridstack's
  `resizeToContent`
- **Configurable** — rows to show, default range, default chart, refresh interval

The active window is shown in OPNsense's own date format, to the second and with
the timezone, e.g. `Tue Sep 22 12:11:42 AEST 2026`. Midnights, the custom range
fields and every caption follow the time zone set under **System → Settings →
General**, whatever the browser's own; the browser's is used only when the firewall
does not say (before 0.3.0 it always was). A day reads `00:00:00 → 23:59:59`.

Nothing is hardcoded. Local networks are derived from the firewall's own
interface configuration (anything outside RFC1918 is treated as upstream, so a
public WAN subnet is never mistaken for local devices). Hostnames are merged from DHCP leases and
static dnsmasq host records, with the static record winning where both exist -
static hosts (servers, cameras, the firewall) never appear in the lease table
at all. Every limit and default is a widget option.

## What the numbers mean

The **All traffic / Internet only** selector decides this.

*All traffic* counts everything — internal plus internet — so an NVR pulling
camera streams dominates the list with bytes that never reach the WAN.

*Internet only* counts a flow just once, on the upstream interface, so purely local
traffic disappears entirely. The upstream device is derived from the interface
configuration, never hardcoded: for Live and for ranges starting within the last
50 hours, every interface with a default route or a public address; for older ranges,
the one addressed outside RFC1918, excluding loopback and link-local. On the
reference install the difference is dramatic: an NVR showing 53.4 GB of total
traffic is 16.1 MB of internet, and cameras showing 16.3 GB of upload are ~5 MB.

The active scope is always printed beside the date range, so a figure is never
ambiguous about which it is.

Totals are keyed on `dst_addr`. NetFlow records each flow once per interface it
crosses, with source and destination swapped between the two observations, so
matching "device is source *or* destination" double-counts every byte and makes
the in/out split meaningless. Keying on destination counts each flow once and
makes the drill-down reconcile with the table row (verified to within 0.1%).

## Peer names in the drill-down

Peer addresses are resolved via reverse DNS (`diagnostics/dns/reverse_lookup`),
cached per address. Be realistic about what this gives you: it names the *hosting
provider*, not the site. Apple and Instagram addresses resolve usefully; AWS,
Google Cloud and Akamai resolve to generic infrastructure names; Cloudflare
publishes no PTR at all. On a typical sample 5 of 8 peers resolved, but only one
identified an actual service.

NetFlow records addresses, ports and byte counts - there is no hostname, no TLS
SNI and no HTTP path in the data, so true per-flow domains are not obtainable
from this source however it is queried. Pi-hole knows which domains a client
asked for, but its query log records only the reply *type*, not the answer
address, so domain-to-IP correlation is not possible either. Real per-flow
domains require deep packet inspection (Zenarmor, Suricata), which is a
different tool entirely.

## WireGuard peers and the internet-only scope

WireGuard peers show traffic under *All traffic* but nothing under *Internet
only*, and that is correct rather than a filtering bug. Measured over 7 days on
the reference install: a peer address appears as a flow endpoint only alongside
*local* peers (26 MB on `ue0`/`vlan01`). Tunnel-interface flows carrying the
peer's internet traffic have **neither** endpoint local (233 MB on `wg0`) —
the peer address is already translated away. NetFlow therefore never attributes
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

- **Accuracy.** Checked against the kernel's WAN counters, less 14 bytes of
  Ethernet header per frame, on the reference install: over 380 sliding 60-second
  windows above 1 Mb/s, attributed traffic came to 98.7-102.7% of WAN download and
  98.5-101.8% of upload, and a cross-check against the IoT VLAN's own counters
  agreed to 97.0-99.2%. Core's iftop-based *Top talkers* read 32-68% of the same
  steady load, which is why it is not used.
- **What the numbers are.** Rows and the pie and bar charts show a 3-second
  average, refreshed each interval; the line graph plots each interval's own rate,
  and the WAN figure beside the range is the last interval alone.
- **Which devices.** With nothing picked, the table always shows exactly the row
  count you chose. It starts busiest first, and after that only the busiest device
  moves: to the top, entering if it was not listed, while the bottom row drops
  off. The rest are topped up with recently seen devices, then other known ones,
  at 0 b/s, and nothing moves while the pointer is over the table. A device you
  have selected stays listed until you deselect it. To watch
  particular devices, tick them in the *Devices* picker beside the network filter:
  it lists every known device (DHCP leases, host records and anything Live has
  seen), with a search box and select all / none. The table then shows exactly
  those devices, idle ones at 0 b/s, A to Z. Picks are remembered in the browser.
  The network filter and search still apply.
- **Charts.** Live adds a **Line** graph, its default: each listed device's
  download over the last 60 seconds, scrolling like core's Traffic Graph, with
  upload in the tooltip. Live remembers its own chart choice; the NetFlow ranges
  keep theirs. The line and bar charts' y-axis only grows - its top holds the
  highest value seen, rounded up - and starts fresh when you enter Live, change
  scope, network, search or picks, or press refresh.
- **No column sorting in Live.** A header click changes nothing there; the NetFlow
  ranges sort as before. The figures update in place, so a click on a row always
  lands.
- **What it cannot see.** A connection that opens and closes between two samples;
  traffic between two devices on the same network, which never reaches the
  firewall; and IPv6, which is counted but not attributed - the summary line says
  how many connections that is.
- **Behind an ISP router.** The WAN is found by its default route as well as by
  its address, so a WAN with a private address (double NAT) works. Since 0.2.0
  so do NetFlow ranges starting within the last day, and since 0.3.0 within the last
  50 hours; older ranges still use the address-only rule.
- **Cost.** One sampler per open dashboard, only while Live is on screen and the
  tab is visible. It measures its own CPU and slows down rather than use more
  than 10% of one core, and says so (*throttled to N s*). It restarts itself every
  hour; the browser reconnects about a second later.
- **Self-check.** Each update compares the attributed traffic with the WAN
  counters over the last 60 seconds. If they disagree by more than 10% - the sign
  of a firmware update having changed pfctl's output - the widget says so instead
  of showing quietly wrong numbers.
- **Access.** The stream and the recent ranges need the *Dashboard: Top Devices*
  privilege. It shows every device's peers, as Diagnostics → States does. OPNsense
  offers a widget only to users who hold the privileges of every endpoint it
  declares, so from 0.1.0 a non-root user needs this privilege to see the widget
  at all. The recent ranges also need *Diagnostics: Network Insight*, so they
  never show more than core's own NetFlow pages would.

## Upstream API limitations

Both were measured against a live firewall, not assumed:

1. **`top` ignores filter arguments, and has no notion of direction.** Eight
   filter syntaxes (path segment, query string, `if=`, `direction=`,
   `dst_addr=`, …) all returned byte-identical results, and the endpoint returns
   one scalar per address, so it cannot produce a download/upload split. Two
   exports can:
   - **`FlowSourceAddrTotals`**: download and upload per address, in 5-minute
     buckets for the last hour, hourly ones for the last 24 hours and daily ones
     for a year. It backs the table and chart for *all traffic*.
   - **`FlowSourceAddrDetails`**: adds the peer, the port and the interface a flow
     crossed, in daily buckets only (kept 62 days, ~6 MB per day, ~3 s). It backs
     *internet only* and the drill-down.

   Each export is fetched once per window and cached. That is why the default
   refresh interval is deliberately slow — changing filters, sorting or charts
   costs nothing.

2. **Buckets are aligned to UTC, and the export rounds a window's start down to
   a bucket.** A daily bucket starts at 00:00 UTC: 10:00 in AEST, 11:00 in AEDT.
   The firewall's time zone changes only how buckets are printed. Up to 0.1.1 the
   widget read the daily details for every range and showed the window it had
   asked for, so *Last hour* really showed everything since 10:00, *Last 24
   hours* and *Today* both everything since 10:00 the day before, and
   *Yesterday* two days. Since 0.1.2 it reads the finest buckets still kept for
   the window, snapped to the nearest bucket boundaries, and never a bucket core
   may already have dropped:

   | Range | All traffic | Internet only |
   |---|---|---|
   | Last hour | the 5-minute buckets within it (55–60 min) | daily |
   | Last 24 hours | the hourly buckets within it (23–24 h) | daily |
   | Today | from local midnight: 5-minute buckets in its first hour, then hourly | daily |
   | Yesterday | the calendar day: hourly just after midnight (from 01:00), then daily | daily |
   | Last 7 days | daily | daily |
   | Custom | the finest buckets still kept for its start | daily |

   Hourly buckets are UTC hours. In a time zone with a half-hour offset, Today
   starts at the nearest one — 00:30 in India. A day with a clock change is 23
   or 25 hours long, and Yesterday follows it.

   The caption shows the span the figures cover. When daily buckets stand in
   for a shorter range, a note under it says so — for internet only, for
   example, *"Internet only is kept per day (days start at 10:00): these cover
   10:00 → 19:08"*. Core keeps the details for 62 days and the daily totals for
   a year. A range reaching further back is cut to what is kept, and the note
   says so, or says there is no data at all. The drill-down's peers and ports
   come from the daily details. When those cover more than the table's window,
   the panel says so, and its download and upload stay the table's. A range
   reaching back before NetFlow began collecting starts at the oldest bucket that
   came back, with the note *"NetFlow has no records before Sat 19 Sep 10:00"*; one
   with nothing at all says *"No NetFlow data for this range"*.

   Each scope reads its own export. Switching scope reloads at the moment already
   on screen, so both scopes describe the same span, and switching back is served
   from the cache.

3. **Since 0.2.0, recent ranges read the raw flow log instead.** A range that starts
   within the last 50 hours - Last hour, Today, Last 24 hours, Yesterday, or a custom
   one (the last day only, before 0.3.0) - is read by the plugin's `flows.py` from
   `/var/log/flowd.log`, its rotations and the files the plugin keeps (see *Keeping
   the flow log*), which hold every flow with its interfaces. It is exact to the
   second in both scopes, and agrees to the byte with core's own parser and
   aggregators run over the same files. A read takes about a second on up to half the
   firewall's cores (three on the reference install), and there is no rate limit: the
   widget refreshes slowly, and only while a dashboard is open. Core keeps that log by
   size, not time: about 110 MB, which is roughly a day on the reference install and
   less on a busy network; the plugin keeps it for two days. Where the log does not
   reach back to a range's start, all traffic is filled in from core's hourly records
   within their day (the oldest hour counted in proportion), and internet only says
   how much it covers; a range reaching back past both is read from NetFlow's records
   instead, with no note. The table above still applies to older ranges, and to recent
   ones when the raw log cannot be read (the caption says so). NetFlow reports a long
   connection every 30 minutes (`activeTimeout`), so the most recent half hour can
   under-count a long download. One known edge: while core holds rows stamped in the
   future (after the firewall's clock jumped back), its cleanup can drop the oldest
   hourly record early, and Last 24 hours can then be short by up to that hour's part
   of the range, with no note.

## Keeping the flow log

Core rotates `/var/log/flowd.log` at 10 MB and keeps ten rotated files: about a day
on the reference install. Since 0.3.0 the plugin keeps them for two days, so Yesterday
is exact.

- **How.** Every 10 minutes `/usr/local/etc/cron.d/topdevices` runs
  `/usr/local/opnsense/scripts/topdevices/keep.py`, which hard-links each file core has
  rotated into `/var/log/topdevices/`. A hard link is a second name for the same file:
  core's renames leave it alone and its delete drops only core's name, so nothing is
  copied and nothing of core's is changed. The job is not in the GUI's cron list; the
  installer puts it in place, and the weekly job keeps it there.
- **How long.** A kept file is deleted once it was last written to more than 51 hours ago, and the
  directory never holds more than 1 GB, oldest first: about 240 MB on the reference
  install, and never the last 512 MB free on that filesystem (the root filesystem on a
  single-partition install, RAM if /var/log is a RAM disk). Resetting NetFlow's data
  (Reporting → Settings) also clears the kept log within 10 minutes.
- **What it holds.** Raw flow records, readable by root only, as core's are.
- **After install** the kept log starts with what core still holds, about 22 hours
  here, so Yesterday is exact from the first midnight after install, as long as the
  install day's first records were still in core's log. Until then, and wherever the
  kept log has a gap (the job stopped for most of a day, or the cap was reached), a
  range reaching back past it is read from NetFlow's records, as before.

## Install without building

Run once on the firewall as root:

    fetch -qo /tmp/td.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/main && \
      rm -rf /tmp/tdx && mkdir -p /tmp/tdx && tar -xzf /tmp/td.tgz -C /tmp/tdx && \
      sh /tmp/tdx/opnsense-plugin-topdevices-main/install.sh

Then hard-refresh the dashboard (Cmd+Shift+R) and add **Top Devices** from the
widget picker. Afterwards `configctl topdevices install` does the same thing.

**Upgrading from 0.0.1:** run the bootstrap command above once. The 0.0.1
installer only knows its own three files, so `configctl topdevices install` (or
the weekly cron job) installs the new widget and installer but not the live
backend; running it a second time completes the upgrade, still without SSH. From
0.1.0 on, non-root users need the *Dashboard: Top Devices* privilege (called
*… live traffic* before 0.2.0), or the widget disappears for them (see
*Access* under *Live traffic*).

**Upgrading from 0.1.x to 0.2.0:** run the bootstrap command above once, too.
The 0.1.x installer only knows its own six files, so one run of
`configctl topdevices install` (or of the weekly job, once `main` carries 0.2.0)
installs the new widget and installer but not `flows.py`, its controller or its
configd actions. Until a second run completes the upgrade, recent ranges fall
back to NetFlow's records, with the widget's note.

**Upgrading from 0.2.0 to 0.3.0:** run the bootstrap command once, too. The 0.2.0
installer only knows its own eight files, so one run of `configctl topdevices install`
(or of the weekly job) installs everything but `keep.py` and its cron file; until a
second run, Yesterday is read from NetFlow's records.

Sources come from **codeload**, which serves the git ref directly: one request
for the whole tree, no rate limit, current content. The two alternatives both
fail here — the **GitHub API** costs one rate-limited request per file (60/hour
per IP, and it is the firewall's own public address that counts), and
**raw.githubusercontent** is CDN-cached, lags pushes by minutes and is cached per
edge, so it silently served stale files here more than once.

Files are staged beside their destination and renamed into place, so a failed
fetch cannot half-install, and the script can safely replace itself — `cp` over a
running script shifts the shell's read offset and kills it mid-file, `mv` does
not. Running it from an already-extracted archive installs from there, making the
bootstrap exactly one fetch.

### Self-healing via cron

These files are not owned by any package, so a firmware upgrade can remove them.
The installer therefore also drops a copy of itself in
`/usr/local/opnsense/scripts/topdevices/` and registers a configd action, so the
refresh can then be scheduled from **System → Settings → Cron** by picking
*Install/refresh TopDevices dashboard widget* — no further SSH.

Two things worth being clear about:

- **Cron does not avoid root.** OPNsense runs cron jobs through configd, as root.
  What cron avoids is an interactive SSH session.
- **Cron cannot run arbitrary commands.** It only invokes registered configd
  actions, so the action file has to be installed first - which is itself a root
  filesystem write. The first install needs a shell either way; the payoff is
  that every subsequent refresh does not.

The installer is idempotent and safe to run repeatedly. It rewrites the configd
action file only when its content changes, so the weekly run normally restarts
nothing. When the file does change, configd restarts detached, a second after the
installer finishes: `configctl` and cron run the installer through configd, and a
synchronous restart would cut off configd's reply to them.

## Build as a real package

Requires a FreeBSD host matching the target ABI (26.7 / amd64 / FreeBSD 15.1):

    git clone https://github.com/opnsense/plugins
    cp -R opnsense-plugin-topdevices plugins/net-mgmt/topdevices
    cd plugins/net-mgmt/topdevices && make package

That builds `os-topdevices-0.3.0.pkg`; `pkg add` it on the firewall, or run
`make upgrade` instead of `make package` to build and install in one step.
GitHub releases carry source only - no prebuilt package is published.

Rebuild whenever the OPNsense ABI changes (major releases).

## What has been verified, and what has not

Verified by measurement against a live firewall, not by reading the code:

- **The download/upload split reconciles to 0.00%** against the `top` leaderboard
  for every device, and the direction is semantically right — cameras read as
  almost entirely upload, the NVR as almost entirely download, clients the
  reverse.
- **Local-network derivation** finds all five interface networks and correctly
  rejects the public WAN, loopback and CGNAT `100.64/10` — the last of which a
  naive "is private" check accepts.
- **The drill-down reconciles** with its table row to within 0.1%, once keyed on
  `dst_addr`; matching src-or-dst double-counts every byte and makes the
  direction split come out identically 50/50.
- **The date format** matches the firewall's own `date` output character for
  character, including the timezone abbreviation.
- **Cron self-heal works unattended** — observed pulling a new build and swapping
  the widget on schedule with no manual trigger.

**Live (0.1.0)**, measured on the reference install (OPNsense 26.7.4, i5-8500)
on 2026-09-23:

- **The parser agrees with core's own** on all 841 states of the live table, with
  none unreadable and no mismatch, and the test suite passes on the firewall's
  Python 3.13.
- **Accuracy**: the 60-second windows and the VLAN cross-check under *Live
  traffic*. A speed test through an IPsec VPN reconciled with the test's own byte
  count plus the tunnel's overhead, and named devices carried 99% of the WAN
  download while it ran.
- **WireGuard clients** appear under their tunnel addresses. A tunnel from the
  LAN - a phone on home Wi-Fi - no longer upsets the self-check.
- **Cost**: a median 24 ms of CPU per 1-second sample (95th percentile 52 ms) with
  700-1,600 states; the sampler used about 1.6% of one core and never throttled.
- **Lifecycle**: hiding the dashboard tab stops the sampler within about 2 s,
  showing it starts a new stream at once, NetFlow ranges run none, and after
  `service configd restart` the widget reconnects by itself within about 2 s.
- **Upgrade under configd**: `configctl topdevices install` with a changed action
  file rewrote it, restarted configd detached and returned - the weekly job's
  path.
- **Soak**: the sampler restarted after exactly one hour and the stream was back
  2.2 s later; its memory stayed at 18-22 MB, following the size of the state
  table rather than time.
- **The NetFlow ranges** behave as in 0.0.1, checked by hand through every range
  and control. (0.0.1's ranges were wrong; see below.)

Not verified: non-root users (only root exists on the reference install), IPv6
attribution (not attempted: no routable IPv6 there), multiple WANs, and a device
behind a traffic-shaper pipe.

**NetFlow ranges (0.1.2)**, measured on the reference install on 2026-09-23 (AEST):

- **The bug**: at 19:08, *Last hour* showed 98.4 GB, everything since 10:00; the
  last hour held 6.1 GB. *Last 24 hours* showed 253.0 GB against 187.6 GB.
  *Today* returned the same export as *Last 24 hours*: 253.0 GB against 154.0 GB.
  *Yesterday* covered 48 hours.
- **The two exports agree to the byte**: over the same UTC day,
  `FlowSourceAddrTotals` gave every device the same download and upload as the
  `dst_addr`-keyed details (98.359 GB both). Over the current hour, the 5-minute
  buckets summed to the hourly one exactly.
- **End to end**: the widget's own code was run against the firewall for four
  ranges in both scopes. It matched an independent recomputation from the raw
  exports to 0 bytes per device.
- **In a browser**, with the dashboard's own jQuery and Bootstrap, the captions,
  the per-day notice and the drill-down note render as described.
- Core's export writes an extra empty column before any field that is zero. It
  only ever hit 0-byte rows, which the widget reads as 0.

**Recent ranges (0.2.0)**, measured on the reference install (6 cores, Python 3.13)
on 2026-09-24 (AEST), installed with the bootstrap command from the branch:

- **Matches core's own code**: `flows.py` against core's parser and aggregators
  run once over the live log (179,356 records) matched every device to within a
  byte - all traffic for the whole hour 07:00-08:00 (39 devices), internet only
  for 07:15-07:45 (31 devices). The largest gap, 0.49 B, is the answer's rounding
  to whole bytes, well inside the under-1-byte pass mark. The unit tests pass on
  the firewall against its own core library.
- **Speed** through configd: Last hour 0.33 s (2 files, 2 workers), Today 0.59 s
  (5 files, 3 workers), Last 24 hours 1.20 s (11 files, 3 workers).
- **Reach**: the log held 20.9 hours that day, so Last 24 hours' internet only
  started at the log, with its note, and all traffic took the hours before it
  from the hourly records. Every record carried ports (9,567 of 9,567 in the
  newest file), so the panel's Top ports work.
- **The endpoints**, called from the admin machine, refused bad input with a
  reason: non-digits, IPv6, a zero-padded IPv4, a range starting over a day ago,
  a reversed range. A device panel over a window that began 24 h 15 min ago
  answered.
- **End to end**: the widget's own code, run against the live endpoints, made one
  request per recent range, showed rows equal to the answer in both scopes, sent
  nothing on a scope switch, and captioned each range to the second. A device
  panel opened 400 s after a Last 24 hours load answered. On the dashboard itself
  the captions read right, and the panel still works 6 minutes on.
- **Upgrade and the weekly job**, simulated in scratch roots: the 0.1.2
  installer's first run installs only the new widget and installer (the widget
  falls back), and a second run completes it. The 0.2.0 installer refuses
  `main`'s 0.1.1 tree and changes nothing.
- **Installed files**: all eight, and the generated actions file, byte-identical
  to a dry run of the branch.

Not verified: non-root users, and so the endpoints' Network Insight check (only
root exists on the reference install); IPv6 (not attributed, by design); multiple
WANs; a log that nothing rotates (unit tests only); and the headless-browser run
of 0.1.2, replaced here by the widget's code against the live endpoints and the
dashboard itself.

**Kept log and time zone (0.3.0)**, measured on the reference install
(Australia/Brisbane, 6 cores, Python 3.13) on 2026-09-24 and 25 (AEST), installed
with the bootstrap command from the branch:

- **A whole day matches core's own code**: for Thu 24 Sep, 00:00:00 to 23:59:59,
  `flows.py` against core's parser and aggregators run once over the same files,
  core's and the kept ones (1,374,698 records from 14 files), agreed for every
  device to within a byte: all traffic 48 devices, internet only 41 devices,
  largest gap 0.500 B, the answer's rounding to whole bytes. The hourly comparison
  of 0.2.0 agreed as before (35 and 28 devices, largest gap 0.490 B). The unit
  tests pass on the firewall against its own core library, core's rotation code
  included.
- **Speed** through configd: Yesterday 1.29 s (14 files, 3 workers), read from the
  log alone; Last hour 0.20 s, Today 0.38 s, Last 24 hours 1.14 s.
- **Keeping**: at install, each of core's ten rotated files was linked into
  `/var/log/topdevices/` (same inode, link count 2). Core's next rotation was kept
  within 10 minutes, and the log's start stayed where it was. When the log reached
  back 37 hours (core's 22 at install, plus the night) it held 18 files, 171 MB; at
  that rate, about 240 MB once it reaches its full 51 hours.
- **The time zone**: the widget's own code, run on New York time against the live
  endpoints, read `Australia/Brisbane` from the firewall and captioned Yesterday
  `Thu Sep 24 00:00:00 AEST 2026  →  Thu Sep 24 23:59:59 AEST 2026` in both
  scopes, with no note, in one request, its rows equal to the answer. Today started
  at 00:00:00 AEST, Last 24 hours read the log, and Last 7 days started at NetFlow's
  oldest daily bucket, Sat 19 Sep 10:00, with a note saying so. Yesterday's device
  panel answered in one request.
- **The endpoints** answered the zone and refused a range starting 50 h 6 min ago
  with the 50-hour message.
- **On the dashboard**, Yesterday, Today, Last 24 hours and Last hour read as
  above in both scopes, with no note, and the Live detail panel grows the widget as
  a device's peers appear.
- **Installed files**: all ten, byte-identical to a dry run of the branch.

Not verified: non-root users; IPv6; multiple WANs; a zone with daylight saving on
the firewall (unit tests only: the reference zone has none); a NetFlow reset, a gap
in the kept log, the 1 GB cap and the free-space floor (unit tests only).

**Automated tests** cover the live sampler, the widget's live logic, since 0.1.2
the NetFlow ranges, since 0.2.0 the raw flow log reader, run against core's own
parser and aggregators, and since 0.3.0 the keeper, run against core's own
rotation (see *Tests*).

**The installer was ported to codeload** after the GitHub API's per-file rate
limit locked the sibling `os-parentalcontrol` plugin out entirely. No GitHub API
request is made at all now, and no hop goes through raw's per-edge cache — both
of which silently served stale files here before. The codeload path was dry-run
against the live repo and installed all six files of 0.1.x, all eight of 0.2.0,
and all ten of 0.3.0, byte-identically.

## Tests

    python3 -m unittest discover -s tests -v    # sampler: parser, crediting rules, loop
    node --test tests/live_view.test.mjs         # widget: averaging, lifecycle, watchdog
    node --test tests/netflow_ranges.test.mjs    # widget: NetFlow exports, buckets, windows, captions
    OPNSENSE_CORE=<core checkout> python3 -m unittest tests.test_flows -v   # flows.py against core's parser and aggregators
    OPNSENSE_CORE=<core checkout> python3 -m unittest tests.test_keep -v    # keep.py against core's own rotation
    sh tests/test_install.sh                     # installer dry run into a scratch root
    python3 tests/mutate.py                      # each planted sampler bug must fail the suite
    node tests/mutate_widget.mjs                 # the same for the widget

`tests/test_core_parity.py` compares our pfctl parser with core's own on the
fixture: point `OPNSENSE_CORE` at an opnsense/core checkout, or `CORE_STATES_PY` at
its `states.py`. On the firewall, `python3 tests/parity_live.py` does the same
over the live state table.

On the firewall, `python3 tests/parity_flows.py` compares `flows.py` with core's
own code over the live log and times it through configd. Once the kept log reaches
yesterday's midnight it also compares that whole day, which takes a few minutes.

## Removing

Remove the weekly *Install/refresh TopDevices dashboard widget* job under
System → Settings → Cron first, or it puts everything back. Then, as root:

    rm -f /usr/local/opnsense/www/js/widgets/TopDevices.js \
          /usr/local/opnsense/www/js/widgets/Metadata/TopDevices.xml \
          /usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf \
          /usr/local/etc/cron.d/topdevices
    rm -rf /usr/local/opnsense/scripts/topdevices \
           /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices \
           /usr/local/opnsense/mvc/app/models/OPNsense/TopDevices \
           /var/log/topdevices
    service configd restart

Removing `keep.py` alone leaves the cron line failing silently every 10 minutes and up
to 1 GB in `/var/log/topdevices`; remove all three together, as above.

Rolling back to 0.0.1 with its bootstrap command leaves the live sampler, the
controller and the ACL behind. They are inert without the `live` action, and the
commands above remove them.

To roll back from 0.2.0, pause the weekly job, then run the bootstrap command
with the earlier release's tag: `refs/tags/0.1.1` in the URL, and the
`opnsense-plugin-topdevices-0.1.1` directory. The installed 0.2.0 installer
refuses a source without `flows.py`, so `configctl topdevices install` cannot
go back. `flows.py` and `Api/FlowsController.php` stay behind, inert without
the `flows` actions, which the earlier installer's actions file does not list,
and the commands above remove them.

Rolling back from 0.3.0 works the same way, with that release's tag. `keep.py` and its
cron file stay behind and keep running, harmless: an earlier `flows.py` reads only
core's files. Remove them with `rm /usr/local/etc/cron.d/topdevices
/usr/local/opnsense/scripts/topdevices/keep.py && rm -r /var/log/topdevices`.

## Requirements

Reporting → NetFlow must be enabled with local aggregation on, for the
historical ranges. Live needs nothing extra.
