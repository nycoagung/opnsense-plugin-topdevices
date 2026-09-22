# os-topdevices

OPNsense dashboard widget: top local devices by traffic, from the built-in
NetFlow/Insight aggregator. No extra collector required.

## Features

- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range picked to the second
- **Download / upload split** per device, plus the combined total
- **Filter** by network (LAN / IOT / GUEST / …), or free-text on hostname or IP
- **Sort** on any column, ascending or descending
- **Drill-down** — click a device for its top peers, top ports and direction split
- **Charts** — pie of totals, or a stacked bar of download vs upload
- **Selections persist** across refreshes and reloads (localStorage)
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

Figures are **total traffic per device — internal plus internet**. An NVR pulling
camera streams will dominate the list with traffic that never reaches the WAN.
For WAN-only internet usage you need a different query; this widget deliberately
does not claim to provide it.

Totals are keyed on `dst_addr`. NetFlow records each flow once per interface it
crosses, with source and destination swapped between the two observations, so
matching "device is source *or* destination" double-counts every byte and makes
the in/out split meaningless. Keying on destination counts each flow once and
makes the drill-down reconcile with the table row (verified to within 0.1%).

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
