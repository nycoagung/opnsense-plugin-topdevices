# Keeping the flow log for two days, and the configured time zone: design

- **Status:** approved in conversation on 2026-09-24 (option 1, two days); this
  written spec awaits review.
- **Date:** 2026-09-24
- **Branch:** `keep-log`, from `raw-flows` (`da1927e`)
- **Baseline:** 0.2.0 as installed from `raw-flows` on the reference firewall
  (OPNsense 26.7.4, FreeBSD 15.1, Python 3.13, 6 cores, time zone Australia/Brisbane).
- **Release:** 0.3.0. 0.2.0 can be released first as prepared, or folded into 0.3.0
  as 0.1.2 was into 0.2.0: the user's call.
- **Builds on:** `2026-09-23-raw-flow-ranges-design.md`. References such as
  "0.2.0 §4" point there.

## 1. Goal

Yesterday should run from 00:00:00 to 23:59:59 in the time zone set under System →
Settings → General, exactly, in both scopes. It now shows 10:00 → 10:00, because
anything older than 24 hours exists only in NetFlow's daily records, and those start
at 00:00 UTC (§3).

The user's example, as the caption should read when installed on Thursday 24 Sep and
viewed on Friday:

- Yesterday: `Thu Sep 24 00:00:00 AEST 2026 → Thu Sep 24 23:59:59 AEST 2026 · internet only (via em0)`,
  with no note.

Success criteria, each checked on the firewall before merge (§13):

- From the first midnight after install, Yesterday reads as above in both scopes,
  with no note. The install must happen while core's log still holds the start of
  that day: up to about 22 hours after its midnight here.
- For a whole local day, `flows.py` equals core's own parser and aggregator
  arithmetic run over the same files, to within a byte per device, in both scopes.
- Yesterday end to end in at most 2 s.
- Today, Yesterday, the custom range fields and every caption follow the configured
  time zone, whatever the browser's zone.
- The caption of a range read from NetFlow's records starts at the oldest bucket the
  export returned. Last 7 days is otherwise unchanged.
- Core is untouched: its files, its rotation and its settings.
- Never worse than 0.2.0: a range the kept log cannot cover is read as 0.2.0 reads
  it.

## 2. Non-goals

- No history past 50 hours. Last 7 days and older custom ranges stay on NetFlow's
  daily records, whose start is rounded to 10:00, as the caption notes.
- No change to core's files or settings: no edit of `MAX_LOGS` or `MAX_FILE_SIZE_MB`
  (§3).
- No per-device store and no database.
- No IPv6 attribution, as in 0.2.0.
- The browser's time zone is used only as a fallback.

## 3. Why (core 26.7.4's source and the reference firewall, 2026-09-24)

- **Nothing core keeps can give an exact Yesterday.**
  - `FlowSourceAddrTotals` keeps 5-minute buckets for 1 hour, hourly ones for 24 hours
    and daily ones for 365 days. `FlowSourceAddrDetails`, the only store with the
    interface that internet only needs, keeps daily buckets for 62 days. Yesterday
    always starts at least 24 hours ago.
  - Buckets count from the epoch (`int(t / res) · res`, stamped with
    `utcfromtimestamp`). A daily one therefore runs 00:00 → 00:00 UTC, which is 10:00
    → 10:00 in Brisbane, whatever the time zone setting. The first ten hours of a
    Brisbane day share their bucket with the afternoon before.
  - None of these periods is a setting. They are constants in `lib/aggregates/`, and
    NetFlow's settings cover only capture, collection and flow timeouts.
- **The raw log is kept by size.** `check_rotate()` in `flowd_aggregate.py` rotates
  `flowd.log` once it passes 10 MB and keeps 10 rotated files, deleting the oldest
  with `os.remove`. On 2026-09-24 the log reached back 20.9 to 22.2 hours, and its
  start moved about every 2 hours, as a file rotated out.
- **Raising core's limits was rejected.** More files would cost only disk: core
  decodes just the current file on each pass, about every 2 minutes, from its first
  record, so a bigger *size* would multiply that work instead. But editing
  `flowd_aggregate.py` has costs of its own:
  - every firmware update undoes it, after which the first rotation deletes every
    file beyond the tenth;
  - the firmware health audit (`pkg check -sa`) reports the file as altered;
  - the log would still be kept by size, so a busy day would shorten it.
- **Hard links keep the files without touching core.** A hard link is a second name
  for the same file. Core's renames don't affect it, and core's `os.remove` drops only
  core's name. A link costs no space while core still holds the file. Links must stay
  within one filesystem, and `/var/log` is its own ZFS dataset (`zroot/var/log`, 221
  GB free), so the kept files live in `/var/log/topdevices/`.
- **Nothing in core touches that directory.**
  - Core's rotation globs `/var/log/flowd.log.*`, and its aggregator
    `/var/log/flowd.log*`.
  - Core's log cleanup (`scripts/syslog/log_archive`) deletes only files named
    `<dir>/<dir>_<8 digits>….log`.
  - newsyslog handles only the files it is configured for.
- **Scheduling.** FreeBSD 15's cron reads every regular file in `/etc/cron.d` and
  `/usr/local/etc/cron.d` whose name does not start with a dot, and reloads when one
  changes (`usr.sbin/cron/cron/database.c`). OPNsense installs its own
  `/etc/cron.d/at` the same way.
- **The time zone.** Core writes the setting into PHP's `date.timezone` (its
  `php.ini` template) and copies its zoneinfo file to `/etc/localtime`. The reference
  firewall is set to Australia/Brisbane: UTC+10, with no daylight saving. The widget
  currently takes the browser's zone (`_window`, `_localToEpoch`, `_dateStr`). A
  browser on Sydney time after 4 October would get Yesterday as 23:00 → 23:00
  Brisbane time; this was reproduced.
- **Captions of ranges read from NetFlow's records can overstate their coverage.**
  NetFlow on the reference firewall started collecting at 02:00 on Sun 20 Sep. The
  week's export returns buckets from the one starting Sat 19 Sep 10:00, yet the
  caption starts at Thu 17 Sep 10:00: it prints the span asked for, not the span
  returned.

## 4. Coverage rules (changes to 0.2.0 §4)

**Raw-log ranges** are those with `from >= now − 180000 − 300`. The 50 hours cover
Yesterday at any hour, even on a day with a clock change, when it can start 49 hours
back. The 5 minutes are 0.2.0's slack for a browser clock.

**The log** is core's files, `/var/log/flowd.log*`, plus the kept files,
`/var/log/topdevices/flowd.*`, in order of last write. A file is read once, however
many names it has.

**L**, where the log becomes complete, is the receive time of the first record of
the oldest file in the newest unbroken run of files.

- A run breaks where more than 900 s pass between one file's last write and the next
  file's first record: a file is missing there, because the job was down for most of
  a day or the size cap was reached (§5).
- flowd appends to `flowd.log` across its own restarts, so every file boundary is a
  rotation, normally seconds apart.
- A file whose first 64 KB hold no IPv4 record does not break a run.

**All traffic** is complete from `C = min(L, now − 86400 − 300)`: from L through the
raw log, and within the last day through 0.2.0's hourly fill-in.

- `from ≥ C`: as in 0.2.0 §4. When L ≤ from, which the kept log makes the normal
  case, the raw log alone answers and no hourly records are read.
- `from < C`: the answer covers [C, to] and reports `all.from = C`. The widget then
  reads NetFlow's records instead (§8).

**Internet only and the device panel** follow 0.2.0 §4, with this L.

**Every answer's figures cover exactly the spans it reports.**

## 5. Keeping the log: `scripts/topdevices/keep.py` and its cron job

**Schedule.** `/usr/local/etc/cron.d/topdevices`:

```
# TopDevices: keeps NetFlow's rotated flow log for two days (installed by install.sh)
SHELL=/bin/sh
*/10	*	*	*	*	root	/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1
```

**Each run:**

1. Creates `/var/log/topdevices/` if it is missing, and sets its mode to 0700.
2. Links each of core's rotated files, `/var/log/flowd.log.` followed by digits only,
   whose last write is within 51 hours and which is not already kept (same device and
   inode). The link is named `flowd.<last write, epoch seconds>.<inode>`.
   - The current `flowd.log` is never linked.
   - A file renamed or deleted between listing and linking is skipped. The next run
     finds it under its new name.
3. Deletes a kept file once its last write is more than 183600 s (51 hours) ago.
   That is an hour past the raw reach (§4), so nothing a raw-log range needs is ever
   deleted: a record received before a range starts ended before it (0.2.0 §5.2).
4. Then, while the kept files total more than 1 GB, deletes the oldest. Normal use is
   about 250 MB here; the cap only guards against a traffic surge.

**Output.** Nothing on success. On an unexpected error, one syslog line
(`topdevices keep: <reason>`, LOG_ERR), then exit 1.

**Self-contained.** It uses the standard library only and does not import `flows.py`,
so a rollback that replaces `flows.py` leaves it working (§14).

**Cost.** Each run stats about 25 files and links or deletes at most a few. A file
stays in core's set about 20 hours here, so a job every 10 minutes has a wide margin.

## 6. `flows.py` changes

- **Files.** `open_log` lists both places. A file seen under two names is opened once,
  by device and inode. The rotation re-check (0.2.0 §5.2) compares both listings. The
  40 MB guard applies to every file.
- **L** follows §4. Each file's first record is read, from at most 64 KB, to find the
  newest unbroken run.
- **Command line.** For totals, `FROM ≥ now − 180000 − 300`, where it was 86400. The
  error reads *"FROM is more than 50 hours ago: that range is read from NetFlow's
  records"*.
- **Totals** follow §4: C, and `all.from`. `hourly_until` is unchanged.
- **Version** 0.3.0. The output is otherwise unchanged.

## 7. The configured time zone

### 7.1 API

- `GET /api/topdevices/flows/zone` returns `{"timezone": "Australia/Brisbane"}` from
  PHP's `date_default_timezone_get()`, which core sets from the setting. It makes no
  configd call.
- The existing ACL pattern and endpoint list (`api/topdevices/flows/*`) cover it. The
  zone already shows in every caption, so it needs no check beyond the ACL.

### 7.2 Widget

- The widget asks once per page load, before its first NetFlow load, with a 5 s
  timeout. A failure, or a zone the browser's `Intl` rejects, leaves the browser's
  own zone.
- Every wall-clock conversion goes through `Intl.DateTimeFormat` with that zone, so
  the browser's own zone plays no part:
  - Today's and Yesterday's midnights (`_window`);
  - the custom range fields, read and written (`_localToEpoch`, `_epochToLocal`);
  - every caption and note (`_dateStr`, `_hm`, `_span`, "days start at …");
  - the export's `start_time` strings (§8).
- **Pure, exported helpers**, tested under several browser zones:
  - `wallParts(ts, tz)`: the year, month, day, hours, minutes, seconds and weekday of
    `ts` in `tz`.
  - `wallToEpoch(y, mo, d, h, mi, s, tz)`: the instant that wall time names in `tz`.
    A time that occurs twice gives the earlier instant. A time that does not occur,
    because the clock jumps forward, moves forward by the jump, as `Date` does for
    local times. A midnight that does not occur therefore gives the day's first
    instant.
  - `localMidnight(ts, back, tz)`: the midnight `back` calendar days before the day
    of `ts`, in `tz`.
- **The zone's name** in captions is the acronym of Intl's long name in `tz`
  ("Australian Eastern Standard Time" → `AEST`), else Intl's short name, else
  `GMT+hhmm`, as 0.2.0 does for the browser's zone.
- **A caption end on a midnight** prints as the second before it, so Yesterday ends
  `… 23:59:59 AEST 2026`. A note's span follows the same rule.

## 8. Widget: other changes

- **Path.** A range starting within the last 50 hours reads the raw log, where it was
  24 hours. `rawRange(from, now)` changes to match.
- **Short answers.** A raw answer whose `all.from` is after the range's start is not
  used. The load reads NetFlow's records instead, as 0.2.0 would, with no note. This
  happens only on the first day after install, or after a missing file.
- **Captions of ranges read from NetFlow's records:**
  - When the oldest bucket the export returned starts after the plan's start, the
    caption starts there, with the note *"NetFlow has no records before Sat 19 Sep
    10:00"*, giving that bucket's start in the configured zone. The note joins any
    other with " · ".
  - An export that returns no rows shows *"No NetFlow data for this range"*.
- **Unchanged:** scopes, the device panel, the fallback on failure, the cache,
  refreshes and Live.

## 9. Installer

- 10 files instead of 8:
  - `scripts/topdevices/keep.py`, mode 0755;
  - `src/etc/cron.d/topdevices`, installed as `/usr/local/etc/cron.d/topdevices`,
    mode 0644. Cron needs no restart.
- After installing, unless `ROOT` is set, the installer runs `keep.py` once, so the
  kept log starts with core's current files. A failure there is a warning, not an
  error.
- **Upgrading from 0.2.0.** The 0.2.0 installer knows only its 8 files, so a
  `configctl` run installs everything except `keep.py` and the cron file, until the
  next run. Meanwhile the widget reads Yesterday as 0.2.0 does. Use the bootstrap
  command once, or run `configctl topdevices install` twice.
- The dry run covers the new files, and never runs `keep.py`.

## 10. Docs and version

- README:
  - *Recent ranges*: 50 hours, and the kept log.
  - A new *Keeping the flow log* section: what is kept, where, for how long, and the
    disk it takes.
  - *Time zone*: the configured zone, with the browser's as the fallback.
  - *Removing* and *Rollback*: the cron file, `keep.py` and `/var/log/topdevices/`.
- `pkg-descr` gains a 0.3.0 entry, and the Makefile, `flows.py` and `live.py`
  versions become 0.3.0.

## 11. Security

- The job runs as root every 10 minutes and takes no input. It links and deletes
  files in one fixed directory only, under names it chose itself.
- Raw flow records are kept for about 2 days instead of about 1, and stay readable
  by root only, as core's are. Core's own Details store already keeps per-device
  peers and ports for 62 days.
- The zone endpoint shows the configured time zone to holders of the widget's
  privilege, who see it in every caption anyway.
- Nothing else is written. The least-privilege check of 0.2.0 §6.2 is unchanged.

## 12. Tests (local, before anything reaches the firewall)

No private data appears in any test: every log is generated in core's binary format
during the test.

- **`tests/test_keep.py`** (new), over a temporary directory:
  - each rotated file is linked once, and `flowd.log` never;
  - kept files survive renames and deletes, including a simulated rotation removing
    the oldest, and a file vanishing mid-run is skipped;
  - files are pruned past 51 hours, and to the cap oldest first;
  - no name matches any of core's patterns: rotation, aggregator or log cleanup;
  - the directory has mode 0700, a run is silent on success, and an error goes to
    syslog with exit 1;
  - against core's own `check_rotate()` when `OPNSENSE_CORE` points at a checkout,
    and always on the firewall.
- **`tests/test_flows.py`:**
  - a file with two names is read once;
  - L is found at the newest unbroken run, with gaps of 900 s and 901 s either side
    of the break;
  - the 50-hour bound;
  - C and `all.from` in each case of §4;
  - the 40 MB guard on a kept file.
- **Widget, `tests/netflow_ranges.test.mjs`:**
  - the zone helpers against known instants in:
    - Australia/Brisbane;
    - Australia/Sydney, whose 4 October 2026 lasts 23 hours;
    - America/Santiago, whose 6 September 2026 has no midnight;
    - Asia/Kathmandu, at +5:45;
    - UTC;
  - Today, Yesterday and the custom fields in the configured zone, under a different
    browser zone;
  - caption ends on a midnight;
  - the 50-hour path, a short answer read from NetFlow's records with no note, and
    the zone request failing;
  - export captions starting at the oldest returned bucket, and an export with no
    rows.
  - The whole file also runs under `TZ=America/New_York`, to show the browser's zone
    plays no part.
- **Mutation checks:** every rule above has a mutant that a test kills.
- **`tests/test_install.sh`:** 10 files, the cron file's content, and no run of
  `keep.py` in a dry run.

## 13. Verification on the firewall (before merge)

1. Install from `keep-log` with the bootstrap command. Check the hashes and the cron
   file, and that `/var/log/topdevices/` holds core's rotated files under the same
   inodes, each with a link count of 2.
2. Run the unit tests on the firewall, including `keep.py` against core's own
   `check_rotate()`.
3. After core's next rotation, the new file is kept within 10 minutes, which shows
   the cron job running.
4. The zone endpoint answers Australia/Brisbane.
5. After the first midnight:
   - a parity check of the whole previous Brisbane day in both scopes, against core's
     parser and aggregator arithmetic over the same files;
   - Yesterday through configd in at most 2 s.
6. The widget's code run in Node against the firewall under `TZ=America/New_York`:
   the Yesterday caption of §1, and Today starting at midnight Brisbane time.
7. The user's own dashboard, then a fresh reviewer over the whole branch.

## 14. Rollout and rollback

- **Rollout.** Work goes on `keep-log`. The user installs it from the branch and
  verifies it. It merges to `main`, and is tagged and released as 0.3.0, only with
  the user's approval. 0.2.0 may be released first from `raw-flows`, as prepared.
- **The Sunday job** installs `main`. Until the merge, 0.3.0's installer refuses
  `main`'s older tree ("missing from source") and changes nothing, and the keep job
  keeps running.
- **Rollback** to 0.2.0 or earlier leaves `keep.py` and the cron file in place, still
  running and harmless: the older `flows.py` reads only core's files. README
  *Removing* gives the commands:
  `rm /usr/local/etc/cron.d/topdevices /usr/local/opnsense/scripts/topdevices/keep.py && rm -r /var/log/topdevices`.

## 15. Risks

- **The job stops.** The kept files age out within two days, and Yesterday is read
  from NetFlow's records again. Nothing wrong is shown.
- **A traffic surge** reaches the cap sooner and shortens the reach. Ranges past it
  are read from NetFlow's records.
- **Core changes its rotation,** its names or its pattern. Nothing is kept, and the
  same fallback applies. The tests against core's `check_rotate()` catch this on the
  firewall.
- **A long NetFlow pause right at a rotation** looks like a missing file. Ranges
  reaching past it are read from NetFlow's records until it ages out.

## 16. To confirm during implementation

- That the API's PHP reports the configured zone: `date_default_timezone_get()` on
  the firewall.
- The export's `start_time` format and zone. `2026/09/19 10:00:00` was observed for a
  bucket starting at 00:00 UTC.
- Intl's long zone names in Chrome, Firefox and Safari for Australia/Brisbane.
- FreeBSD cron's checks on files in `cron.d`, such as owner and mode, in
  `process_crontab()`.
- flowd's writes at a rotation: whether it appends to the renamed file until it
  reopens the log. A negative gap between files is fine.
