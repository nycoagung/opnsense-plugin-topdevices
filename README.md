# os-topdevices

OPNsense dashboard widget: top local devices by traffic, from the built-in
NetFlow/Insight aggregator. No extra collector required.

## Features

- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range picked to the second
- **Download / upload split** per device, plus the combined total
- **All traffic or internet only** — the latter counts flows at the WAN,
  so purely local traffic is excluded
- **Filter** by network (LAN / IOT / GUEST / …), or free-text on hostname or IP
- **Sort** on any column, ascending or descending
- **Row count** selectable at 10 / 20 / 50 / 100
- **Detail column** beside the table, so the list stays visible while you click
  through devices; collapses to below the table when the widget is narrow
- **Drill-down** — click a device for its top peers, top ports and direction split
- **Charts** — pie of totals, or a stacked bar of download vs upload
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

    fetch -o - https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main/install.sh | sh

The installer pulls each file from the **GitHub API**, not from
raw.githubusercontent, and verifies it against the git blob SHA the API reports.
raw is CDN-cached, lags pushes by minutes, and is cached *per edge*, so two
machines can see different content at the same moment - three installs in a row
silently fetched stale files and reported success. A stale or truncated download
now fails loudly, and every install prints the SHA and byte count of what it
actually wrote. (The bootstrap line above still comes from raw, since there is
nothing installed yet to verify with; everything it then fetches is verified.)

Then hard-refresh the dashboard (Cmd+Shift+R) and add **Top Devices** from the
widget picker.

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
action only when the content actually changes, because restarting configd from a
script that configd itself launched would kill that script mid-run.

## Build as a real package

Requires a FreeBSD host matching the target ABI (26.7 / amd64 / FreeBSD 15.1):

    git clone https://github.com/opnsense/plugins
    cp -R opnsense-plugin-topdevices plugins/net-mgmt/topdevices
    cd plugins/net-mgmt/topdevices && make package

    pkg add https://github.com/nycoagung/opnsense-plugin-topdevices/releases/download/v1.1/os-topdevices-1.1.pkg

Rebuild whenever the OPNsense ABI changes (major releases).

## Requirements

Reporting → NetFlow must be enabled with local aggregation on.
