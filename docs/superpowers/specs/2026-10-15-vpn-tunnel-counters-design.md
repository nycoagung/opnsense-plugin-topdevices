# VPN devices in the ranges, from the tunnel counters: design

- **Status:** approved in conversation on 2026-10-15 (scope option A, one-minute
  sampling, a separate sampler script); this written spec awaits review.
- **Date:** 2026-10-15
- **Branch:** `tunnel-counters`, from `main` (`7c9a506`, 0.3.4)
- **Baseline:** 0.3.4 as installed on the reference firewall (OPNsense 26.7.6,
  FreeBSD 15.1, Python 3.13, WireGuard in core).
- **Release:** 0.4.0, on the user's word only.
- **Builds on:** `2026-09-23-raw-flow-ranges-design.md` (the raw log, "0.2.0 §n")
  and `2026-09-24-keep-flow-log-design.md` (the kept log and its cron job, "0.3.0 §n").

## 1. Goal

A WireGuard client's row in Today, Yesterday, Last hour, Last 24 hours and any
custom range within the raw reach should show what that client really moved
through its tunnel, in both scopes, instead of the sliver of it that NetFlow sees.

The motivating example: a VPN peer read a few hundred KB for Today in the widget,
while the WireGuard widget's counters for it stood at well over a hundred MB.

Success criteria, each checked on the firewall before merge (§11):

- For every connected peer, Today's download and upload in the widget agree with
  the growth of the WireGuard widget's counters over the same minutes, to within
  the tunnel overhead (a few percent), after the samples have covered the range.
- Last hour reads from 60 one-minute steps; the midnight split between Yesterday
  and Today is accurate to a minute.
- Internet only for a VPN device that also talks to local devices is its tunnel
  figure minus its local traffic, never negative.
- Every non-VPN row is byte-identical to 0.3.4's answer for the same range.
- Live is unchanged.
- Core is untouched: its files, its NetFlow settings, the WireGuard configuration.
- A firewall without WireGuard, or without peers, behaves exactly as 0.3.4.

## 2. Non-goals

- No history before the first sample: the figures start when 0.4.0 is installed,
  as the kept log did in 0.3.0.
- No VPN internet figures for ranges served from NetFlow's exports (Last 7 days,
  custom ranges older than the 50-hour reach). Those stay as today, local-only for
  VPN devices, with the existing explanation.
- No internet peers or ports for a VPN device: the tunnel counters carry no
  addresses, and NetFlow never sees the peer's internet flows (§3).
- No change to Live, to core's WireGuard widget, or to how names resolve.
- Only WireGuard instances on the firewall itself (`wg show` runs there). A
  tunnel terminated elsewhere is not this feature's business.

## 3. Why the tunnel counters (measured on the reference firewall, 2026-10-15)

NetFlow capture is `ng_netflow` attached to each interface's `ng_ether` node
(core's `/etc/rc.d/netflow`, `ngctl mkpeer <if>: netflow lower`). A `wg` interface
has no `ng_ether` node, so a ticked WireGuard interface gets no collector at all:
`diagnostics/netflow/status` reports two collectors per interface that works, none
for the ticked WireGuard interfaces. This has always been so, not a side effect
of any change: two weeks of NetFlow's records hold 0 bytes between a tunnel
address and a public address, on any interface.

What NetFlow does see of a VPN client is its traffic with local devices, recorded
where it leaves on the LAN or VLAN side with the tunnel address intact (those rows
also appear tagged with the tunnel interface, by input interface). Its internet
traffic leaves on the WAN after NAT, carrying the firewall's address: nothing to
credit. Hence the README's "WireGuard peers show nothing under Internet only" and
the few hundred KB above, which was that peer's DNS queries to the firewall and
nothing else.

The kernel keeps per-peer counters: `wg show all dump` prints, per peer, the bytes
received from it and sent to it since the interface was created. Core's own
WireGuard widget reads exactly these. They count the encrypted packets, so they
include WireGuard's overhead (handshakes, keepalives, per-packet headers), a few
percent on bulk traffic. Sampled every minute and differenced, they give each VPN
device's traffic per minute, which is what the ranges need.

The Live view is unaffected: it reads the pf state table, which keeps each
connection's pre-NAT address (0.1.0), and credits a VPN client under its tunnel
address.

## 4. Coverage rules

- **Tunnel figures exist for [S, now]**, S being the first sample kept. Samples
  are kept 51 hours, the kept log's rule (0.3.0 §5), so the tunnel figures reach
  exactly as far as the raw log does.
- **A range in the raw path** (0.3.0 §4: from within 50 hours) gets tunnel
  figures for its VPN devices over [max(from, S), to). A range starting before S
  says so in the caption (§7).
- **A range in the export path** (older) is unchanged: NetFlow's exports, VPN
  devices local-only.
- **Sample intervals.** Two consecutive samples of one peer bound an interval
  (t0, t1]. The counter growth over it is spread evenly across it and the part
  inside [lo, hi) is counted, the same proportional share the flow records get
  (0.2.0 §5.2). A longer interval, from a sampler that was stopped or a firewall
  that rebooted, is still one interval: the bytes happened within it. A negative
  interval (the clock jumped back) is skipped.
- **A counter that went down** means the tunnel was recreated and the counter
  restarted at 0: the growth is the new value itself.
- **The first sample of a peer** bounds nothing: its counters are the baseline.
  After an install, the first figures appear with the second sample.

## 5. Sampler: `scripts/topdevices/tunnels.py` and its cron line

**Schedule.** `/usr/local/etc/cron.d/topdevices` becomes:

```
# TopDevices: keeps NetFlow's rotated flow log for two days, and samples the WireGuard
# tunnel counters every minute (installed by install.sh)
SHELL=/bin/sh
*/10	*	*	*	*	root	/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1
*	*	*	*	*	root	/usr/local/opnsense/scripts/topdevices/tunnels.py >/dev/null 2>&1
```

Its own line in the plugin's own file: nothing shared with any other plugin.

**Each run:**

1. Runs `/usr/bin/wg show all dump`. No binary, a non-zero exit (no WireGuard
   module) or no peer lines: exits 0 having written nothing. That is a normal state.
2. Parses peer lines only: 9 tab-separated fields, `interface, public key,
   preshared key, endpoint, allowed-ips, latest handshake, received, sent,
   keepalive` (`wg(8)`; core's `wg_show.py` reads the same). Interface lines have
   5 fields and are skipped. A line with any other count is skipped and counted;
   if every line was skipped, one syslog line says the output is not understood.
3. The device address is the first IPv4 `/32` in `allowed-ips` (comma-separated).
   A peer with none is skipped. A peer with several `/32`s is credited to the
   first; the others are named in one syslog line per run at most (LOG_NOTICE),
   so a wrong guess is visible rather than silent.
4. Creates `/var/log/topdevices/` if it is missing, mode 0700 (the keeper's
   folder, 0.3.0 §5).
5. Appends, to `tunnels.<YYYYMMDD>` of the current UTC day, one line per peer:
   `<epoch> <interface> <address> <received> <sent>\n`, space-separated, all
   decimal. The whole sample is one `os.write` of one buffer, so a reader sees
   whole samples (a torn final line is tolerated by the reader anyway, §6).
   Every peer every run, idle or not: every interval is then one minute long and
   a burst is never spread over idle hours (§4).
6. Deletes `tunnels.*` files whose last write is more than 183600 s (51 h) ago.

**Output.** Nothing on success. On an unexpected error, one syslog line
(`topdevices tunnels: <reason>`, LOG_ERR), then exit 1.

**Self-contained.** Standard library only, imports nothing from the plugin, so a
rollback that replaces `flows.py` leaves it working, as `keep.py` is.

**Cost.** One `wg` call and one small write a minute: milliseconds. About 90
bytes per peer per minute, ~130 KB a day per peer, three day files at most.

**UTC days** only name the files; nothing is read by file name but the pruning
glob. A range reads every kept file (§6), so the firewall's time zone and the
file boundaries never interact.

## 6. `flows.py` changes

- **Reading the samples.** Every `tunnels.*` file in the kept folder, oldest
  first, into `{(interface, address): [(epoch, rx, tx), ...]}`. A malformed or
  torn line is skipped. The oldest epoch read is S (§4). No file, or no line:
  the tunnel part is empty and everything else is as 0.3.4.
- **Per-device tunnel totals** for [lo, hi): for each key, over consecutive
  samples, the growth per §4 times the share of the interval inside [lo, hi),
  summed; `down` from `sent`, `up` from `received`. Keys with the same address
  (one device on two instances) are summed.
- **Totals mode (§5.1 of 0.2.0).** After the raw scan and the hourly fill-in, for
  each tunnel address present in the samples over the all-traffic span
  [a_lo, a_hi): `all_down, all_up` are replaced by the tunnel totals over the same
  span; `inet_down = max(0, tunnel_down - nf_all_down)` and likewise for up, where
  `nf_*` is NetFlow's all-traffic figure for that device over the internet span
  [i_lo, i_hi). The tunnel totals for the internet scope are computed over the
  internet span, which may be shorter than the all-traffic one (0.2.0 §4). A tunnel
  address with no NetFlow rows at all still gets a row.
- **Device mode (0.2.0 §5.5).** Unchanged lists; the answer gains the device's
  tunnel totals over the same spans.
- **Answer.** Totals gains `"tunnel": {"from": S or null, "devices": [addresses
  with tunnel figures in this answer]}`; `devices[address]` keeps its four-number
  shape. Device gains `"tunnel": {"from": S or null, "all": [down, up],
  "inet": [down, up]}` or `null` for a non-tunnel device.
- **Which addresses are tunnel devices:** those in the samples, nothing else. The
  interface name is never consulted.
- **Cost.** Three files of ~2 MB, parsed in one pass: well under the 2 s budget
  (0.3.0 §1), measured in §11.

## 7. Widget

- **Rows.** A tunnel-counted device shows the new figures in the usual columns and
  a small `tunnel` tag after its name, with the title "Counted from the WireGuard
  tunnel: includes the tunnel's own overhead; internet only is the tunnel figure
  less local traffic." The tag is text in the name cell, so sorting and the filter
  are unaffected.
- **Caption note.** When the loaded raw answer has tunnel devices and
  `tunnel.from` is later than the request's start: "VPN figures cover from
  <day time>" (the same formatter as the existing "NetFlow has no records before"
  note), joined to any other note with " · ".
- **Device panel.** For a tunnel device, one line above Top peers: "Internet
  ↓ <down> ↑ <up>, counted from the tunnel · internet peers are not recorded for
  VPN devices", with the scope's figures. The lists below are as today.
- **Charts** use the per-device totals, so nothing changes there.
- **Export path** (older ranges): unchanged, including its notes.
- **Live:** untouched.

## 8. Installer and uninstaller

- `install.sh`: `src/opnsense/scripts/topdevices/tunnels.py` joins the file list
  (13 files); `src/etc/cron.d/topdevices` carries the second line. The staged
  dotted copy and rename (0.3.0 §9) already keep cron from reading a half-written
  file.
- **Upgrade from 0.3.4:** the installed 0.3.4 installer knows its own 11 files, so
  its first run installs those from the new tree (the new installer among them);
  the second run installs all 12. The bootstrap one-liner does it in one. The
  README's upgrade note says so, as for 0.2.0 → 0.3.0.
- `uninstall.sh`: `tunnels.py` joins its list. The cron file and
  `/var/log/topdevices` are already removed by it.
- The configd actions are unchanged (no new action, no new endpoint, no ACL change).

## 9. Docs and version

- Version 0.4.0: `VERSION` in `flows.py` and `live.py`, `PLUGIN_VERSION` in the
  Makefile, `pkg-descr`, the README.
- README: the "WireGuard peers and the internet-only scope" section is rewritten
  around the tunnel counting (what it includes, minute resolution, from install
  time, the export path's limit); "Keeping the flow log" gains the sampler and the
  second cron line; the features list, the upgrade notes and "What has been
  verified" each gain their line. The 12-file count replaces 11 where stated.
- Documentation values only (the repo's rule): tunnel examples use `10.8.0.0/24`
  as the existing tests do, never the reference network's addresses.

## 10. Security

- The sampler runs as root from cron, like the keeper, because `wg show` needs
  root. It reads one command's output and writes under a root-only folder. It
  never records keys: the public and preshared key fields are parsed past and
  dropped; the written line has an address and two counters.
- The samples are root-only (0700 folder), as the kept log is.
- `flows.py` reads them through configd as today; the answers expose per-device
  byte counts, which the widget already shows, and no addresses that NetFlow did
  not already expose.

## 11. Verification on the firewall (before merge)

1. Install from the branch with the bootstrap. Confirm the 13 files, the two cron
   lines, and that `tunnels.<today>` appears within a minute and grows by one
   sample a minute (line count).
2. For each connected peer, note the WireGuard widget's counters, wait N whole
   minutes, note them again, and compare the growth with Today's change in the
   TopDevices row (reload the widget between). Expect agreement within the
   overhead, a few percent, and Down against the WireGuard widget's ↑ (sent to
   the peer).
3. Last hour for a peer that was active: 60 steps, matching the same comparison.
4. A peer with local traffic (streams from a local device): Internet only is lower
   than All traffic by about its local bytes, and never negative.
5. The device panel of a VPN device shows the internet line and the local lists.
6. Non-VPN rows: the branch's totals answer for a fixed past window equals
   0.3.4's answer saved before the install, device by device.
7. `cost_ms` of Yesterday still under 2 s.
8. Nothing in syslog from `topdevices tunnels` after an hour.
9. Live unchanged (a VPN device still appears under its tunnel address).

## 12. Rollout and rollback

- The firewall runs the branch until 0.4.0 is released. If a weekly auto-install
  job is in use, disable it meanwhile.
- Rollback: the bootstrap against `main` (0.3.4). Its installer rewrites its 11
  files, including the one-line cron file, so the sampler stops; the day files are
  harmless and age out, or `rm -rf /var/log/topdevices/tunnels.*`.

## 13. Tests (local, before anything reaches the firewall)

- `tests/test_tunnels.py` (new): parsing of real `wg show all dump` lines
  (interface lines skipped, 9-field peer lines read, other counts skipped and
  reported); no binary, non-zero exit, no peers → nothing written, exit 0; the
  first `/32` chosen, a peer without one skipped, several `/32`s noted once; the
  day file's name and line format; one write per sample; pruning at 51 hours,
  never the files still needed; the folder created 0700.
- `tests/test_flows.py` (grown): tunnel totals over an interval, proportional
  share at a range edge and across midnight, a counter reset, a gap between
  samples, a negative interval skipped, a torn last line, a device on two
  instances, totals replacing NetFlow's all-traffic figure and Internet only
  clamped at zero, a range starting before the first sample (`tunnel.from`), the
  device answer's `tunnel` block, a non-tunnel device unchanged, and the answer
  with no samples equal to 0.3.4's.
- Widget tests (`netflow_ranges.test.mjs`): the tag, the caption note, the panel
  line, and the export path untouched.
- `mutate.py` gains the sampler; every planted bug must fail the suite, as the
  mutation runs require today. `mutate_widget.mjs` as before.
- `test_install.sh` / `test_uninstall.sh`: the 13th file and the two-line cron file.

## 14. Risks

- `wg show all dump` changing shape: parsing is by field count, a surprise is
  logged and skipped, never guessed at.
- A peer with several `/32` addresses: the first is credited and the rest named
  in syslog; the user can see it and pick the right address in the peer's config.
- Clock jumps: a negative interval is skipped; a forward jump stretches one
  interval, which §4 treats as one interval.
- The folder on tmpfs (`/var/log` in RAM on some installs): the files are small
  and the keeper's free-space floor (0.3.0 §5) still applies to its own files;
  the sampler writes at most a few MB.

## 15. To confirm during implementation

- The exact `wg show all dump` output on OPNsense 26.7.6 for an interface with no
  peers, and whether `wg` prints anything on stderr when the module is absent.
- Whether configd's `flows.totals` cost stays under 2 s with three day files.
- The widget's name cell markup, so the tag never breaks the hostname search.
