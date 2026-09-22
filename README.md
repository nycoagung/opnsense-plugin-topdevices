# os-topdevices

OPNsense dashboard widget: top local devices by traffic, from the built-in
NetFlow/Insight aggregator. No extra collector required.

## Features

- **Date range** — last hour, last 24 hours, today, yesterday, last 7 days
- **Filter** by network (LAN / IOT / GUEST / …), or free-text on hostname or IP
- **Sort** by device name, network or traffic, ascending or descending
- **Drill-down** — click a device for its top peers, top ports and in/out split
- **Charts** — pie (doughnut) or bar, or off
- **Configurable** — rows to show, default range, default chart, refresh interval

Nothing is hardcoded. Local networks are derived from the firewall's own
interface configuration (anything outside RFC1918 is treated as upstream, so a
public WAN subnet is never mistaken for local devices). Hostnames come from DHCP
leases. Every limit and default is a widget option.

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

1. **`top` ignores filter arguments.** Eight different filter syntaxes (path
   segment, query string, `if=`, `direction=`, `dst_addr=`, …) all returned
   byte-identical results. Per-device drill-down therefore pulls the full detail
   export (~6 MB per day, ~3 s) and filters in the browser. It loads only when a
   device is opened, and is cached per range.

2. **Wide windows snap to day buckets aligned to UTC midnight.** A 6-hour and a
   24-hour query return identical totals; only windows of roughly an hour return
   finer data. **"Today" and "Yesterday" are therefore approximate.** The widget
   always displays the window it actually requested, so the figure is never
   silently wrong.

## Install without building

Run on the firewall as root:

    fetch -o - https://raw.githubusercontent.com/nycoagung/opnsense-plugin-topdevices/main/install.sh | sh

Then hard-refresh the dashboard (Cmd+Shift+R) and add **Top Devices** from the
widget picker. Re-run after each OPNsense upgrade, or put it in cron to self-heal.

## Build as a real package

Requires a FreeBSD host matching the target ABI (26.7 / amd64 / FreeBSD 15.1):

    git clone https://github.com/opnsense/plugins
    cp -R opnsense-plugin-topdevices plugins/net-mgmt/topdevices
    cd plugins/net-mgmt/topdevices && make package

    pkg add https://github.com/nycoagung/opnsense-plugin-topdevices/releases/download/v1.1/os-topdevices-1.1.pkg

Rebuild whenever the OPNsense ABI changes (major releases).

## Requirements

Reporting → NetFlow must be enabled with local aggregation on.
