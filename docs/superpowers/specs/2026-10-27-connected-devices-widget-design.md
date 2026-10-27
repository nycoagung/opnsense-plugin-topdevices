# Connected devices widget: design

- **Status:** designed and approved on 2026-10-27 for another plugin, redesigned the
  same day (one Status column and a "Not connected" section, approved from a mockup),
  then moved here at the user's request ("this should be in topdevices"), as its own
  widget. This document carries that design over to Top Devices; awaiting the user's
  check. A builder check the same day raised five questions, settled in §4, §5a, §3,
  §12, §13 and §17.
- **Date:** 2026-10-27
- **Branch:** `connected-devices`, from `main` (`66b1ab4`, 0.4.1)
- **Baseline:** 0.4.1 as installed on the reference firewall (OPNsense 26.7.7).
- **Release:** 0.5.0, on the user's word only.
- **Builds on:** `2026-10-15-vpn-tunnel-counters-design.md` (the plugin's cron.d file
  and its per-minute sampler).

## 1. Goal

Someone looking after a home network wants to see at a glance which devices are on
it right now and since when, and, for the ones that have left this week, when each
was last seen: when a phone came home, whether the tablet is still on, which device
nobody has named. OPNsense shows pieces of this in different places (the ARP table,
DHCP leases, Host discovery, WireGuard status); none of them says when a device
arrived this time, and none puts the departed next to the present.

This adds a second dashboard widget to the plugin, **Connected devices**. The Top
Devices widget is unchanged.

Success criteria, each checked on the firewall before merge (§12):

- The connected list is exactly the devices the firewall heard from on its internal
  networks in the last 20 minutes (ARP table or Host discovery), plus the WireGuard
  peers online by core's rule.
- A device that arrives after install shows how long ago it arrived; one already there
  when tracking began reads "before" that time.
- The "Not connected" list is exactly the devices Host discovery saw within the last 7
  days but not the last 20 minutes, plus the WireGuard peers whose latest handshake is
  within 7 days but not 5 minutes; each one's last-seen time is Host discovery's
  newest sighting of it, or the handshake.
- Nothing else changes: the Top Devices widget's answers are the same, and the
  plugin's other cron jobs keep running.

## 2. Non-goals and anti-goals

Non-goals:

- Controlling devices (blocking, schedules).
- Traffic figures: the Top Devices widget has them.
- Wi-Fi association, band or signal: only the access point knows them.
- IPv6 addresses, a history of past visits, notifications when a device joins.
- Names from Kea or ISC DHCP.

Anti-goals:

- A device shown as connected long after it left, or a stale list shown as current.
- Needing anything beyond OPNsense core and this plugin: no other plugin, no Pi-hole.
- Slowing down or breaking the plugin's other work (the tunnel sampler, the flow-log
  keeper, the Top Devices widget's answers).
- Setup details in this public repository: tests and documents use documentation
  values only (RFC 5737 addresses, RFC 7042 documentation MACs `00:00:5e:00:53:xx`,
  `.example` names).

## 3. What the user sees

- The widget, **Connected devices**, is added from the dashboard's widget list. Its
  header has a refresh button before the link icon, like the Top Devices widget's,
  which hides with the link icon while the dashboard is edited. The link opens core's
  host discovery page (Interfaces → Neighbors → Automatic Discovery,
  `/ui/hostdiscovery/settings`).
- A line above the table counts both lists: "12 connected · 3 seen this week" (just
  "12 connected" when no other device was seen).
- One table with four columns: **Host name**, **IP address**, **MAC address**,
  **Status**.
- Connected devices are grouped under a header per internal network (the interface's
  description) with its count ("LAN · 12 connected"), in the order the interfaces are
  assigned (`config.xml` order: LAN, then OPT1, OPT2 and so on), then a **VPN** group
  for WireGuard clients. Within a group, named
  devices come first, by name in natural order (9 before 10), then the unnamed ones by
  address.
- **Host name** and **IP address** as in §7; *(unnamed)* is in italics, with a tooltip
  saying there is no DHCP reservation and no name from DHCP. **MAC address** in lower
  case with colons; a VPN client shows "—".
- **Status** of a connected device: a filled green dot, and how long ago this visit
  began ("under 1 min", "6 min", "2 h 14 min", "3 d 4 h"); "Connected" stays in words
  for screen readers only. Its tooltip gives the time it connected, the vendor (from
  the MAC) and when Host discovery first saw the device. A device already connected
  when tracking began shows the dot alone, its tooltip saying "Connected since before"
  that date and time "(when tracking began)". While tracking isn't running every
  device shows the dot alone, and a line under the table says why. (Changed with the
  maintainer on 2026-10-27, after the first install: the words "Connected before
  17:31" on every row read as noise, and the time of day alone misleads once the day
  has passed.)
- **Not connected · seen in the last 7 days (3)**: a section under the connected
  groups, newest first. It starts closed; a click on its header, or Enter or Space on
  it (it takes keyboard focus, and keeps it), opens and closes it; it stays as left in
  that browser. Each row is greyed, and its status is a hollow dot and "Last
  seen 3 h ago" ("25 min ago") when seen earlier the same local day, else "Last seen
  Sat 09:29". The tooltip gives the date and time ("Last heard from …", or "Latest
  handshake …" for a VPN client), the vendor and the first sighting. A muted line ends
  the section: "Devices not seen for 7 days drop off the widget. They stay under
  Interfaces → Neighbors."
- Footnotes under the table: when tracking isn't running; when Host discovery is off
  (the devices then come from the ARP table, without first-seen times, and the ones
  not connected can't be listed); when it is idle (§4); and when an input couldn't be
  read (§5a).
- Clock times, and "the same local day", follow the firewall's time zone (System →
  Settings → General), as the Top Devices widget's do; the answer carries the zone. A
  zone the browser doesn't know falls back to the browser's own.
- If the host list can't be read, the widget says so ("Cannot read the host list.")
  and lists nothing. A stale list is never left standing.
- It refreshes every 60 seconds, and at once from the refresh button; a click while a
  read is under way starts no second one.
- The state never rests on colour alone: the dots differ in shape, screen readers hear
  "Connected", and the rows not connected say "Last seen".
- Column widths: in a widget about 620 px wide, names of about 25 characters, any IPv4
  address, MACs and the status texts fit on one line, and the header stays on one
  line. A narrower widget cuts long values with "…" and shows them whole on hover.

Mockup (documentation values):

3 connected · 2 seen this week

| Host name | IP address | MAC address | Status |
|---|---|---|---|
| **LAN · 2 connected** | | | |
| Laptop-Example | 192.0.2.10 | 00:00:5e:00:53:01 | ● 2 h 14 min |
| *(unnamed)* | 192.0.2.88 | 00:00:5e:00:53:02 | ● |
| **VPN · 1 connected** | | | |
| Phone-Example | 198.51.100.3 | — | ● 45 min |
| **▾ Not connected · seen in the last 7 days (2)** | | | |
| Tablet-Example | 192.0.2.40 | 00:00:5e:00:53:04 | ○ Last seen 3 h ago |
| Phone-Example-2 | 198.51.100.4 | — | ○ Last seen Sat 09:29 |

## 4. Who counts as connected

- Two sources, read through the same scripts core's own configd actions run:
  - **Host discovery**, core's host list behind Interfaces → Neighbors (configd
    `hostwatch dump_full`, which runs `list_hosts.py -v -n`). It is on by default in
    26.7 and holds every MAC/IP pair it has seen, IPv4 and IPv6, each with its first
    and last sighting (UTC). Its rows count only when its answer's `source` is
    `discovery`; otherwise core has handed back the ARP table again, without times,
    and Host discovery is **off**: none of its data is used.
  - Host discovery is **idle** when its `source` is `discovery` but its newest
    sighting of anything on the internal networks is more than 20 minutes old (a
    stopped daemon, or networks it doesn't watch). Its data is still used for what it
    is, first sightings and when it last heard each device, so the 7-day list stays;
    presence then rests on the ARP table, and a footnote says Host discovery has
    heard nothing for that long, so last-seen times may be earlier than the devices
    really left.
  - **The ARP table** (configd `interface list arp`, `list_arp.py`): the devices heard
    from recently, without times. Permanent entries are the firewall's own: they and
    their MACs are never listed. Expired entries are skipped.
- A device is a MAC address. It is connected when it is in the ARP table now, which
  counts as seen at the moment it was read, or when Host discovery saw it, through any
  of its pairs, IPv4 or IPv6, within the last **20 minutes** (1200 s, inclusive).
  Taking either source covers networks Host discovery doesn't watch, a stopped Host
  discovery daemon, and Host discovery switched off.
- Only internal networks count: enabled interfaces with a static IPv4 address and no
  upstream gateway in their settings (a gateway of "none" is none), the interfaces
  core's NAT reflection treats as internal. A dynamic address (DHCP, PPPoE) makes an
  interface upstream; a gateway entry alone, such as a router for a static route,
  does not. WireGuard interfaces are not networks here; their peers are the VPN
  clients below. The firewall's own addresses are never listed.
- A sighting that cannot be the device's address (IPv6, or IPv4 outside its
  interface's subnet) still keeps it present. A device with no IPv4 address on an
  internal network is not listed.
- **VPN:** a WireGuard peer is connected when its latest handshake is at most **300
  seconds** old, core's own "online" rule. A peer that never completed a handshake is
  never listed.

## 5. Visits: when a device arrived

- `connected.py track`, run every minute by the plugin's cron.d file, records when
  each connected device's current visit began, in a small state file
  (`/var/db/topdevices/connected.json`).
- A visit continues while the device is seen again within **30 minutes** (1800 s,
  inclusive) of its previous sighting. A longer gap ends it, and the next sighting
  starts a new visit whose start is that sighting's time: Host discovery's time, or
  the run's own time when only the ARP table had it.
- A device that moves to another network keeps its visit: it is the same MAC.
- Devices already connected at the first run have no start; they read "before" that
  run's time.
- The file records each run's time. When the previous run is longer ago than the gap,
  or the clock went back, nothing is known about who left meanwhile: tracking starts
  afresh as on the first run, and the devices connected then read "before" its time.
- The file holds current visits only; a device gone for longer than the gap is
  dropped. VPN peers are tracked the same way, keyed by their public key.
- The file is written atomically (a temporary file, then a rename).
- Tracking is "running" when its last run is at most 180 s old. Any error in it goes
  to the system log; it never touches the plugin's other jobs.

## 5a. When an input can't be read

The answer carries on with what it could read and says what it couldn't, so a missing
group is never silent:

- Host discovery unreadable (its script fails): as when it is off, with a footnote
  that it couldn't be read.
- The ARP table unreadable: Host discovery alone, with a footnote that the ARP table
  couldn't be read.
- Both unreadable, or `config.xml` unreadable (no internal networks can be known):
  the whole answer fails, and the widget says it cannot read the host list.
- WireGuard absent (no `wg`, or no tunnels): no VPN group and no footnote. `wg`
  present but failing, or not answering within 10 seconds, as `tunnels.py` already
  treats it: no VPN rows, and a footnote that WireGuard's status couldn't be read.
- The Dnsmasq lease file missing or unreadable (Dnsmasq not in use): no lease names
  or addresses, no footnote.
- In `track`, a failed read keeps the visits and the last run as they were and logs
  the error; if it keeps failing, tracking reads as not running after 180 s.

## 6. Not connected: seen in the last 7 days

- A device not connected now (§4) is listed when Host discovery's newest sighting of
  it is within **7 days** (604,800 s, inclusive). Its last seen is that sighting.
- A WireGuard peer not connected now is listed when its latest handshake is within 7
  days. Its last seen is that handshake. A peer that never connected is never listed.
- Newest first; equal times then by name (named first, natural order), then by
  address.
- Only Host discovery remembers devices that have left, so without it the section
  lists VPN clients alone. WireGuard forgets its handshakes when the firewall
  restarts, so VPN clients drop off after a reboot.
- A device unseen for more than 7 days is not listed; it stays on core's Discovered
  Hosts page.

## 7. Names and addresses

- **Address:** the device's unexpired Dnsmasq lease inside an internal network (the
  longest-lasting one; a lease that never expires lasts longest), else the IPv4
  address of its newest IPv4 sighting inside its network. Its group is that address's
  network. A VPN client's address is the first IPv4 /32 among its allowed IPs.
- **Name:** the Dnsmasq reservation (host override) holding its MAC, else the one
  holding its address, else the name it gave DHCP (its lease's host name; "*" is
  none), else *(unnamed)*. A VPN client's name is its WireGuard peer's name. This is
  the Top Devices widget's preference too (reservations first), so the two widgets
  agree.
- Names come from Dnsmasq only: with Kea or ISC DHCP, every device but the VPN
  clients reads *(unnamed)*.

## 8. Components

- **`scripts/topdevices/connected.py`**: one script, two commands. `list` prints the
  widget's answer as JSON and changes nothing; `track` records the visits (§5). The
  rules live in functions whose inputs are passed in, so the tests run without a
  firewall.
- **Its inputs:** core's `list_hosts.py -v -n` and `list_arp.py`; WireGuard's latest
  handshakes and allowed IPs, read without any key material (`wg show all
  latest-handshakes` and `wg show all allowed-ips` give public keys only, unlike
  `wg show all dump`); the Dnsmasq lease file; and `config.xml`, read only, for the
  interfaces, the Dnsmasq reservations and the WireGuard peers' names.
- **A configd action** `[connected]` running `connected.py list` (`script_output`),
  written by install.sh's heredoc like the plugin's others.
- **`Api/ConnectedController.php`**: one read-only GET action that calls that action
  and returns its JSON; anything unreadable becomes `{"status": "failed", "message":
  "could not read the host list"}`.
- **The answer:** `status`, `now`, `zone` (the firewall's time zone), `source`
  (Host discovery on, idle or off), the footnotes' facts (§5a), `tracking` (`active`,
  `started`, `run`), `groups` (connected, as §3 orders them, each device
  with its name, address, MAC, vendor, visit start or "before" time, how long in words,
  first seen and last seen) and `recent` (§6, each with its name, address, MAC, vendor,
  whether it is a VPN client, first seen and last seen). Times are epoch seconds; the
  widget only renders.
- **ACL:** a new privilege, **Dashboard: Connected devices**, for
  `api/topdevices/connected/*`. The widget's metadata names that endpoint, so the
  dashboard offers the widget only to users allowed it.
- **Widget:** `ConnectedDevices.js` and its metadata.
- **Cron:** one more line in the plugin's cron.d file: `connected.py track` every
  minute, as root, beside `tunnels.py`.
- **State:** `/var/db/topdevices/connected.json`, root only (0600, in a 0700
  directory): only configd and cron, both root, read it.

## 9. Installer and uninstaller

- `install.sh` installs the new files and writes the `[connected]` action. The
  actions file's content changes, so this install restarts configd once (detached, 1 s
  later), as the installer already does when that file changes.
- **Upgrade from 0.4.1:** its installer knows only its own file list. One
  `configctl topdevices install` from 0.4.1 would install the new cron file and
  actions but not `connected.py` or the widget, so the cron line fails silently and
  the widget errors until a second run. As with 0.4.0, the README says: use the
  bootstrap once, or run the install twice.
- `uninstall.sh` removes the new files and `/var/db/topdevices`, and its dry run lists
  them.

## 10. Docs and version

- README: a **Connected devices** section (what it shows, the rules, the 7-day list,
  the refresh), its known limits (§14), the upgrade note, the tests table, and "What
  has been verified".
- 0.5.0 in the Makefile, pkg-descr, and the `VERSION` that `flows.py` and `live.py`
  answer with.
- A checklist of the firewall checks in §12, in the repository's usual place for
  them.

## 11. Security

- The endpoint is read-only and behind its own ACL privilege. The GUI gains no write
  path.
- No key material is read: WireGuard public keys, handshakes and allowed IPs only.
  Public keys stay in the root-only state file and never reach the browser.
- `config.xml` is read, never written. The state file and its directory are root
  only.

## 12. Verification on the firewall (before merge)

- First, the earlier version is gone from the firewall: the user removes it on
  2026-10-27 (the other plugin restored to its release). It used other names
  (`ParentalControlConnected.js`, widget id `parentalcontrolconnected`, an endpoint
  under `api/parentalcontrol/`), so the two could not collide anyway.
- Install from the branch with the bootstrap (`refs/heads/connected-devices`). The
  served widget and metadata match the branch by hash; configd restarted once; the
  Top Devices widget's metadata and script unchanged.
- The tracker runs every minute: the state file's run time advances, and the system
  log has no lines from it.
- The connected list matches the firewall's own lists by MAC: the ARP table, Host
  discovery within 20 minutes, the WireGuard peers online; names match the
  reservations and leases.
- The "Not connected" list matches Host discovery's devices seen within 7 days but not
  the last 20 minutes, and the peers by handshake, with the same times.
- A device that arrives after install shows its arrival; the others read "before"
  the first run.
- The Top Devices widget's flows and Live answers are as before, apart from the
  version.
- The list answers in well under a second.

## 13. Tests (local, before anything reaches the firewall)

- Python unittest for `connected.py`, in the repository's style: every rule in §4–§7
  (the 20-minute window inclusive, the ARP table as now against Host discovery's
  times, IPv6 keeping a device present, the firewall's own and expired and upstream
  entries out, internal networks, addresses and names, grouping and order, the VPN
  rules, the 7-day list and its inclusive edge, peers that never connected, Host
  discovery off), the visits (first run, continued and new after the gap, the gap's
  edge, a pause or a clock gone back starting afresh, dropped after the gap), the
  state file (permissions, atomic write, junk read as empty), durations in words,
  reading each input in core's own format, Host discovery off and idle, and each case
  in §5a.
- The cases start from the earlier version's 39 checks, ported from the other
  plugin's `connected-devices` branch (kept until this work is committed, then
  deleted). Where they and this document differ, this document wins.
- Core parity, with `OPNSENSE_CORE`: `connected.py` reads what core's own
  `list_hosts.py` and `list_arp.py` print.
- Node tests for the widget with the repository's jQuery stub: rows, dots, status
  texts, the time formats (same local day against day and time), the section (closed
  at first, click, Enter and Space, remembered, kept across a refresh), the refresh
  guard and the failure message.
- `mutate.py` and `mutate_widget.mjs` cover the new code, all mutants killed.
- `test_install.sh` and `test_uninstall.sh` cover the new files.
- A layout check in headless Chrome with core's CSS and fonts (a scratch harness, not
  in the repository).

## 14. Known limits

- A device that leaves stays connected for up to 20 minutes: the firewall keeps it in
  its ARP table that long. An absence of under 30 minutes, and sometimes one of up to
  50, keeps its visit going.
- A battery device that sleeps between events shows as connected only while awake.
- A device that only talks to others on its own network, such as a server used only
  from the LAN, can show as not connected while it runs: the firewall hears a device
  only when it uses the firewall or announces itself.
- Without Host discovery, "Not connected" lists VPN clients only, and a reboot clears
  those.
- Names from Dnsmasq only; IPv4 only.
- A WAN with a static address and no upstream gateway in its settings counts as
  internal, as it does for core's NAT reflection, so its modem is listed.
- The upgrade from 0.4.1 needs the bootstrap once, or two installs (§9).

## 15. Left to the builder

- Endpoint, file, field and CSS names, and the answer's exact shape within §8.
- How `connected.py` runs core's scripts, within §8's no-keys rule.
- How `list` and `track` share code, and the tests' structure.
- The widget's height and scrolling with many devices.

## 16. Decided with the maintainer

- "Connected since" means when the device arrived this time, not when it was first
  ever seen.
- A refresh button in the widget's header, like the Top Devices widget's.
- One Status column with a dot instead of separate time columns, and a section for
  the devices not connected but seen in the last 7 days, with when each was last seen
  (approved from a mockup).
- The widget belongs to Top Devices, as its own widget; the earlier version in the
  other plugin is retired.
- After the first install (2026-10-27): the status is a dot, not words. The dot moves
  from the name into the Status column, with how long when that is known and alone
  when it isn't; the start before tracking moves into the tooltip, with its date
  (approved from a mockup).

## 17. My calls (open to the maintainer's veto)

- Python, like the plugin's other scripts, instead of carrying over the earlier
  version's PHP. Its behaviour, proven by its tests and on the firewall, becomes the
  Python tests' cases.
- Its own cron line, separate from the tunnel sampler, and its own configd action.
- Its own ACL privilege, so a user can be allowed Top Devices without this list.
- A root-only state file under `/var/db/topdevices`.
- Names worked out on the firewall, reservations first, matched by MAC and then by
  address, so a device with no current lease (common among the not-connected) still
  has its name.
- The 20-minute presence window, the 30-minute visit gap, the 7-day window and the
  300-second WireGuard rule.
- The "Not connected" section starts closed.
- After the builder check:
  - An input that can't be read leaves a footnote and the rest of the answer
    (§5a); only both host sources, or the configuration, failing fails it.
  - Host discovery "off" (no data used) and "idle" (nothing heard for 20 minutes:
    its history still used, with a footnote), defined in §4.
  - Clock times in the firewall's time zone, like the Top Devices widget, carried in
    the answer so the widget needs no other privilege.
  - Network groups in assignment order (`config.xml`), as in the earlier version.
