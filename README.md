# os-topdevices

OPNsense dashboard widget: top local devices by traffic, from the built-in
NetFlow/Insight aggregator. No extra collector required.

## Features

- **Live** — each device's current download and upload, updated every second,
  from the firewall's connection table (see *Live traffic*)
- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range picked to the second
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
the timezone, e.g. `Tue Sep 22 12:11:42 AEST 2026`.

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

*Internet only* counts a flow just once, on the upstream interface, so purely
local traffic disappears entirely. The upstream device is derived from the
interface configuration (the one addressed outside RFC1918, excluding loopback
and link-local), never hardcoded. On the reference install the difference is
dramatic: an NVR showing 53.4 GB of total traffic is 16.1 MB of internet, and
cameras showing 16.3 GB of upload are ~5 MB.

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

- **Accuracy.** Measured against the kernel's interface counters on the reference
  install, this method accounts for 100.0% of WAN download and 100.3% of upload
  once the firewall's own traffic and 14 bytes of Ethernet header per packet are
  counted. Core's iftop-based *Top talkers* read 32-68% of the same steady load,
  which is why it is not used.
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
  OPNsense offers a widget only to users who hold the privileges of every
  endpoint it declares, so from 0.1.0 a non-root user needs this privilege to
  see the widget at all.

## Two upstream API limitations

Both were measured against a live firewall, not assumed:

1. **`top` ignores filter arguments, and has no notion of direction.** Eight
   filter syntaxes (path segment, query string, `if=`, `direction=`,
   `dst_addr=`, …) all returned byte-identical results, and the endpoint returns
   one scalar per address, so it cannot produce a download/upload split. The
   detail export is therefore the source for the table, chart and drill-down
   alike (~6 MB per day, ~3 s), fetched once per range and cached. That is why
   the default refresh interval is deliberately slow — changing filters, sorting
   or charts costs nothing, only changing the range refetches.

2. **Wide windows snap to day buckets aligned to UTC midnight.** A 6-hour and a
   24-hour query return identical totals; only windows of roughly an hour return
   finer data. **"Today" and "Yesterday" are therefore approximate.** The widget
   always displays the window it actually requested, so the figure is never
   silently wrong.

## Install without building

Run once on the firewall as root:

    fetch -qo /tmp/td.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/main && \
      rm -rf /tmp/tdx && mkdir -p /tmp/tdx && tar -xzf /tmp/td.tgz -C /tmp/tdx && \
      sh /tmp/tdx/opnsense-plugin-topdevices-main/install.sh

Then hard-refresh the dashboard (Cmd+Shift+R) and add **Top Devices** from the
widget picker. Afterwards `configctl topdevices install` does the same thing.

**Upgrading from 0.0.1:** run the bootstrap command above once. The 0.0.1
installer only knows its own three files, so `configctl topdevices install` (or
the weekly cron job) would install the new widget without the live backend
until its next run.

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

That builds `os-topdevices-0.1.0.pkg`; `pkg add` it on the firewall, or run
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

**Automated tests** cover the live sampler and the widget's live logic (see
*Tests*). The NetFlow views still have none.

**The installer was ported to codeload** after the GitHub API's per-file rate
limit locked the sibling `os-parentalcontrol` plugin out entirely. No GitHub API
request is made at all now, and no hop goes through raw's per-edge cache — both
of which silently served stale files here before. The codeload path was dry-run
against the live repo and installs all six files byte-identically.

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
