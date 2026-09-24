# Keeping the Flow Log for Two Days, and the Configured Time Zone: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Yesterday runs exactly from 00:00:00 to 23:59:59 in the time zone set on the
firewall, in both scopes, read from a raw flow log the plugin keeps for two days. The
widget's midnights, custom fields and captions follow that zone, whatever the browser's.

**Architecture:**
- A new root script, `scripts/topdevices/keep.py`, runs every 10 minutes from
  `/usr/local/etc/cron.d/topdevices`. It hard-links each flow log file core rotates out
  into `/var/log/topdevices/`, and prunes those links by age (51 h) and size (1 GB).
- `flows.py` reads core's files plus the kept ones, each file once. It finds where the
  log becomes complete (L) at the newest unbroken run of files, accepts ranges up to 50
  hours back, and reports all traffic from where it is complete (C).
- A new `GET /api/topdevices/flows/zone` gives the widget the firewall's time zone.
  Pure `Intl` helpers turn instants into wall-clock parts and back in that zone.
- The widget reads a range starting within 50 hours from the raw log. It uses a raw
  answer only when all traffic reaches back to the range's start; otherwise it reads
  NetFlow's records, as 0.2.0 would.
- Captions of ranges read from NetFlow's records start at the oldest bucket that came
  back.

**Tech Stack:**
- Python 3.13 standard library; core's NetFlow library on the firewall;
- PHP (OPNsense MVC);
- the ES-module dashboard widget, with `Intl.DateTimeFormat`;
- FreeBSD cron (`/usr/local/etc/cron.d`);
- `unittest`, `node:test` and `sh` tests.

**Spec:** `docs/superpowers/specs/2026-09-24-keep-flow-log-design.md`, which builds on
`docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md` (0.2.0).

**Prototype:** every task below was implemented once in a scratch copy of `keep-log`
before this plan was written. The code blocks are that prototype's. Every "Expected:"
line was observed there, except where a step says it is qualitative.

## Global Constraints

- Firewall: OPNsense 26.7.4, FreeBSD 15.1, Python 3.13; its zone is Australia/Brisbane
  (UTC+10, no daylight saving). Core's rotation keeps `flowd.log` plus 10 rotated files
  of just over 10 MB (`flowd_aggregate.py` `check_rotate()`, `MAX_LOGS = 10`).
- **Core is not modified.** No edit of any core file or setting.
- **The kept log:**
  - directory `/var/log/topdevices/`, mode 0700, on `/var/log`'s own ZFS dataset;
  - hard links named `flowd.<last write, epoch seconds>.<inode>`, made only for
    `/var/log/flowd.log.` followed by digits, never `flowd.log`;
  - a kept file is deleted once its last write is more than 183600 s (51 h) ago, and
    while the kept files total more than 1 GB (`1 << 30`), the oldest go first.
- **The job:** `/usr/local/etc/cron.d/topdevices`, mode 0644, exactly:

  ```
  # TopDevices: keeps NetFlow's rotated flow log for two days (installed by install.sh)
  SHELL=/bin/sh
  */10	*	*	*	*	root	/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1
  ```

  The fields are separated by tabs. `keep.py` is silent on success. On an error it
  writes `syslog(LOG_ERR, 'topdevices keep: <reason>')` and exits 1. It uses the
  standard library only and never imports `flows.py`.
- **Raw reach, 50 hours:** `flows.py` accepts totals with `FROM ≥ now − 180000 − 300`.
  The widget uses `from ≥ now − 180000 and from < now` (`RAW_REACH = 50 * 3600`).
- **L:** the receive time of the first IPv4 record of the oldest file in the newest
  unbroken run. Files are core's and the kept ones, each once, ordered by last write.
  A run breaks where more than 900 s (`GAP`) pass between one file's last write and
  the next file's first record. A file with no IPv4 record in its first 64 KB leaves
  the boundary before it unjudged.
- **C:** all traffic is complete from `min(L, now − 86400 − 300)`. `answer_totals`
  moves an earlier FROM up to C, so every answer's figures cover exactly the spans it
  reports.
- **The zone endpoint:** `GET /api/topdevices/flows/zone` →
  `{"timezone": date_default_timezone_get()}`. The existing ACL pattern
  `api/topdevices/flows/*` and widget endpoint `/api/topdevices/flows/*` cover it.
- **The widget's zone:**
  - `this.tz`, set once per page load by `_loadZone()` through the dashboard's
    `ajaxCall`, before `_loadNetworks()`;
  - `undefined`, meaning the browser's zone, when there is no answer, no
    `timezone`, or a zone `Intl` rejects.
- **Captions:** an end on a local midnight prints as the second before it. The
  widget's exact texts:
  - `NetFlow has no records before Sat 19 Sep 10:00`, formatted as `<Www> <D> <Mmm>
    <HH:MM>`, and joined to any other note with ` · `;
  - `No NetFlow data for this range`, for an export with no rows.
- **flows.py error:** `FROM is more than 50 hours ago: that range is read from NetFlow's
  records`.
- **Version 0.3.0** in `Makefile` (`PLUGIN_VERSION`), `pkg-descr`, and `VERSION` in
  `flows.py` and `live.py`.
- **Test data:** tests contain no private data. Every flow log is generated in core's
  binary format during the test.
- **Core checkout** for the parity tests, on the admin Mac:
  `OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review`
  (core 26.7.4, sparse, with `src/opnsense/scripts/netflow`).
- **Merging:** nothing reaches `main` without the user's approval. The weekly job
  installs `main` every Sunday at 05:00. Until the merge, 0.3.0's installer refuses
  `main`'s older tree and changes nothing.

## Review Focus

1. **`keep.py` prunes a kept file while `flows.py` opens the files.** The read must
   simply go on without it: no error, and nothing read twice. Pinned by Task 2's
   `test_a_kept_file_pruned_while_the_files_are_opened_is_simply_gone`.
2. **Two keep passes at once,** such as the installer's run and cron's, or core
   renaming a file between listing and linking. Neither may fail, and the file must be
   kept next time. Pinned by Task 1's
   `test_a_file_renamed_or_linked_meanwhile_is_skipped_and_kept_next_time`.
3. **A clock change in the firewall's zone:** a 23- or 25-hour Yesterday, and a midnight
   that does not occur (Santiago). Midnights follow the calendar, and a day's first
   instant stands in for a missing midnight. Pinned by Task 4's
   `clocks going forward …`, `clocks going back …` and `midnights follow the calendar …`,
   and by the existing Sydney tests, which Task 5 runs in the configured zone.
4. **The firewall gives no zone:** no answer, an unknown zone, or 0.2.0's endpoints
   (`{"errorMessage": …}`). The widget keeps the browser's zone, with no error. Pinned
   by Task 5's `the zone comes from the firewall; the browser's stays when it does not
   say`.
5. **The kept log still filling after install, or with a missing file.** The answer
   reports where all traffic starts (C), and the widget reads NetFlow's records instead
   of showing a partial day. Pinned by Task 2's gap tests, Task 3's
   `test_a_short_log_answers_all_traffic_from_where_it_begins`, and Task 6's
   `a raw answer short of the range's start …`.

## File structure

| File | Responsibility |
|---|---|
| Create `src/opnsense/scripts/topdevices/keep.py` | Link rotated files; prune by age and size |
| Create `src/etc/cron.d/topdevices` | The job: every 10 minutes, as root |
| Create `tests/test_keep.py` | `keep.py` tests, including core's own `check_rotate()` |
| Modify `src/opnsense/scripts/topdevices/flows.py` | Read the kept files; L at the newest unbroken run; the 50-hour reach; C |
| Modify `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php` | `zoneAction()` |
| Modify `src/opnsense/www/js/widgets/TopDevices.js` | Zone helpers; the firewall's zone everywhere; the 50-hour path; short answers; the oldest returned bucket |
| Modify `install.sh` | 10 files; run `keep.py` once; upgrade note |
| Modify `tests/test_flows.py`, `tests/mutate.py` | Script tests and mutants |
| Modify `tests/netflow_ranges.test.mjs`, `tests/live_view.test.mjs`, `tests/mutate_widget.mjs` | Widget tests and mutants |
| Modify `tests/test_install.sh` | 10 files, the cron line, no `keep.py` in a dry run |
| Modify `tests/parity_flows.py` | A whole local day against core; Yesterday's timing |
| Modify `README.md`, `pkg-descr`, `Makefile`, `live.py` (`VERSION`) | Docs, 0.3.0 |

Run everything from the repository root:

```bash
python3 -m unittest discover -s tests -v
OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review python3 -m unittest discover -s tests
node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs
sh tests/test_install.sh
python3 tests/mutate.py
node tests/mutate_widget.mjs
```

At the start (`da1927e` plus the spec commit): 101 Python tests (`OK (skipped=7)`),
110 widget tests, and 47 + 102 mutants.

---

### Task 1: `keep.py`, the flow log keeper

**Files:**
- Create: `src/opnsense/scripts/topdevices/keep.py`
- Create: `tests/test_keep.py`
- Modify: `tests/mutate.py`

**Interfaces:**
- Produces:
  - `keep.LOG = '/var/log/flowd.log'`, `keep.KEPT = '/var/log/topdevices'`,
    `keep.KEEP_S = 51 * 3600`, `keep.CAP = 1 << 30`;
  - `keep.rotated(log) -> [path]`;
  - `keep.kept_files(kept) -> [(path, os.stat_result)]`, oldest first;
  - `keep.run(now, log, kept, keep_s=KEEP_S, cap=CAP) -> (linked, removed)`;
  - `keep.main() -> 0 | 1`.
  - The kept names `flowd.<int(st_mtime)>.<st_ino>`, which `flows.py` (Task 2) finds as
    `<dirname(log)>/topdevices/flowd.*`.

- [ ] **Step 1: Write the failing tests.** Create `tests/test_keep.py`:

```python
"""Unit tests for the flow log keeper (scripts/topdevices/keep.py).
Standard library only; no OPNsense needed.
Run:  python3 -m unittest tests.test_keep -v
KEEP_PY=<path> points the suite at another copy of keep.py (used by mutate.py).

Class CoreRotation runs core's own rotation (flowd_aggregate.py check_rotate())
when core's NetFlow scripts are found: CORE_NETFLOW=<core>/src/opnsense/scripts/netflow,
or OPNSENSE_CORE=<checkout> (see tests/test_flows.py for fetching one), or on the
firewall.
"""
import contextlib
import glob
import importlib.util
import io
import os
import pathlib
import sys
import syslog
import tempfile
import time
import unittest
import unittest.mock
import warnings

HERE = pathlib.Path(__file__).resolve().parent
KEEP_PY = os.environ.get('KEEP_PY') or str(HERE.parent / 'src/opnsense/scripts/topdevices/keep.py')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


keep = _load('keep', KEEP_PY)

T0 = 1_790_000_000               # Mon 21 Sep 2026 12:53:20 UTC
H = 3600


class Scratch(unittest.TestCase):
    """A scratch /var/log: core's flowd.log and its rotations, keep.py's directory beside them."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.log = os.path.join(self.dir, 'flowd.log')
        self.kept = os.path.join(self.dir, 'topdevices')

    def write(self, name, data, mtime):
        path = os.path.join(self.dir, name)
        with open(path, 'wb') as f:
            f.write(data)
        os.utime(path, (mtime, mtime))
        return path

    def rotate(self, data, mtime):
        """What core's check_rotate() does: every rotated file moves one number up,
        the tenth goes, flowd.log becomes .000001; then flowd starts a new flowd.log."""
        for n in range(10, 0, -1):
            path = '%s.%06d' % (self.log, n)
            if os.path.exists(path):
                if n == 10:
                    os.remove(path)
                else:
                    os.rename(path, '%s.%06d' % (self.log, n + 1))
        os.rename(self.log, self.log + '.000001')
        self.write('flowd.log', data, mtime)

    def kept_names(self):
        return sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.kept, '*')))

    def kept_data(self):
        out = []
        for path in glob.glob(os.path.join(self.kept, '*')):
            with open(path, 'rb') as f:
                out.append(f.read())
        return sorted(out)

    def keep(self, now, **kw):
        return keep.run(now, self.log, self.kept, **kw)


class Keeping(Scratch):
    def test_each_rotated_file_is_linked_once_and_the_current_log_never(self):
        files = [self.write('flowd.log.000002', b'a', T0 - 2 * H), self.write('flowd.log.000001', b'b', T0 - H)]
        current = self.write('flowd.log', b'c', T0)
        self.assertEqual(self.keep(T0), (2, 0))
        self.assertEqual(self.kept_names(),
                         sorted('flowd.%d.%d' % (os.stat(p).st_mtime, os.stat(p).st_ino) for p in files))
        self.assertNotIn(os.stat(current).st_ino,
                         [os.stat(p).st_ino for p in glob.glob(os.path.join(self.kept, '*'))])
        os.utime(files[1], (T0 - H + 5, T0 - H + 5))      # flowd wrote once more after the rename
        self.assertEqual(self.keep(T0 + 600), (0, 0))       # the same file: kept once, under its first name
        self.assertEqual(len(self.kept_names()), 2)

    def test_kept_files_survive_core_renaming_and_deleting_them(self):
        self.write('flowd.log.000001', b'old flows', T0 - H)
        self.write('flowd.log', b'new', T0)
        self.keep(T0)
        os.rename(self.log + '.000001', self.log + '.000002')    # core: one number up
        self.assertEqual(self.keep(T0 + 600), (0, 0))
        os.remove(self.log + '.000002')                          # core: the oldest goes
        [kept] = glob.glob(os.path.join(self.kept, '*'))
        with open(kept, 'rb') as f:
            self.assertEqual((f.read(), os.stat(kept).st_nlink), (b'old flows', 1))

    def test_a_rotation_removing_the_oldest_loses_nothing_kept(self):
        for n in range(10, 0, -1):
            self.write('flowd.log.%06d' % n, b'file %d' % n, T0 - n * H)
        self.write('flowd.log', b'current', T0)
        self.keep(T0)
        self.rotate(b'next', T0 + H)                             # file 10 goes; "current" becomes .000001
        self.assertEqual(self.keep(T0 + H), (1, 0))
        self.assertEqual(self.kept_data(), sorted([b'file %d' % n for n in range(1, 11)] + [b'current']))

    def test_a_file_renamed_or_linked_meanwhile_is_skipped_and_kept_next_time(self):
        self.write('flowd.log.000001', b'b', T0 - H)
        real_link, calls = os.link, []

        def meanwhile(src, dst):
            calls.append(src)
            if len(calls) == 1:                                  # core renamed it between the listing and the link
                raise FileNotFoundError(2, 'No such file or directory', src)
            if len(calls) == 2:                                  # a pass running at the same time linked it
                raise FileExistsError(17, 'File exists', dst)
            return real_link(src, dst)

        with unittest.mock.patch('os.link', meanwhile):
            self.assertEqual(self.keep(T0), (0, 0))
            self.assertEqual(self.keep(T0 + 600), (0, 0))
            self.assertEqual(self.keep(T0 + 1200), (1, 0))

    def test_files_past_the_reach_are_pruned_and_never_linked(self):
        os.makedirs(self.kept)
        self.write('topdevices/flowd.1.1', b'too old', T0 - 51 * H - 1)
        self.write('topdevices/flowd.2.2', b'still needed', T0 - 51 * H + 1)
        self.write('flowd.log.000010', b'rotated, too old', T0 - 51 * H - 1)
        self.assertEqual(self.keep(T0), (0, 1))
        self.assertEqual(self.kept_data(), [b'still needed'])

    def test_over_the_cap_the_oldest_go_first(self):
        self.assertEqual(keep.CAP, 1 << 30)                          # 1 GB (spec §5)
        os.makedirs(self.kept)
        for i, age in enumerate((3, 2, 1)):
            self.write('topdevices/flowd.%d.%d' % (T0 - age * H, i), b'x' * 100, T0 - age * H)
        self.assertEqual(self.keep(T0, cap=250), (0, 1))
        self.assertEqual(self.kept_names(), ['flowd.%d.1' % (T0 - 2 * H), 'flowd.%d.2' % (T0 - H)])

    def test_no_kept_name_is_one_core_reads_rotates_or_cleans(self):
        self.write('flowd.log.000001', b'b', T0 - H)
        self.write('flowd.log', b'c', T0)
        self.keep(T0)
        kept = glob.glob(os.path.join(self.kept, '*'))
        self.assertTrue(kept)
        core_reads = glob.glob(glob.escape(self.log) + '*')        # parse_flow(): flowd.log*
        core_rotates = glob.glob(glob.escape(self.log) + '.*')     # check_rotate(): flowd.log.*
        self.assertFalse(set(kept) & set(core_reads + core_rotates))
        for path in kept:                                          # log_archive: <dir>/<dir>_<8 digits>….log
            name, base = os.path.basename(path), os.path.basename(os.path.dirname(path))
            self.assertFalse(name.startswith(base + '_') and name.endswith('.log')
                             and name[len(base) + 1:len(base) + 9].isdigit(), name)

    def test_the_directory_is_root_only(self):
        os.makedirs(self.kept)
        os.chmod(self.kept, 0o755)
        self.keep(T0)
        self.assertEqual(os.stat(self.kept).st_mode & 0o777, 0o700)

    def test_a_run_is_silent_and_an_error_goes_to_syslog(self):
        self.write('flowd.log.000001', b'b', time.time() - H)        # main() runs on the real clock
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.object(keep, 'LOG', self.log), unittest.mock.patch.object(keep, 'KEPT', self.kept), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(keep.main(), 0)
        self.assertEqual((out.getvalue(), err.getvalue(), len(self.kept_names())), ('', '', 1))
        with unittest.mock.patch.object(keep, 'run', side_effect=OSError(28, 'No space left on device')), \
                unittest.mock.patch('syslog.syslog') as logged:
            self.assertEqual(keep.main(), 1)
        logged.assert_called_once_with(syslog.LOG_ERR, 'topdevices keep: [Errno 28] No space left on device')


CORE_NETFLOW = next((p for p in (os.environ.get('CORE_NETFLOW'),
                                 os.path.join(os.environ.get('OPNSENSE_CORE', '/nonexistent'), 'src/opnsense/scripts/netflow'),
                                 '/usr/local/opnsense/scripts/netflow')
                     if p and os.path.isfile(os.path.join(p, 'flowd_aggregate.py'))), None)


@unittest.skipUnless(CORE_NETFLOW, 'set CORE_NETFLOW or OPNSENSE_CORE (see the module docstring), or run on the firewall')
class CoreRotation(Scratch):
    """Against core's own rotation, flowd_aggregate.py's check_rotate()."""

    def test_nothing_kept_is_lost_through_cores_own_rotations(self):
        sys.path.insert(0, CORE_NETFLOW)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', DeprecationWarning)     # core's own code, Python >= 3.12
                agg = _load('flowd_aggregate', os.path.join(CORE_NETFLOW, 'flowd_aggregate.py'))
        finally:
            sys.path.remove(CORE_NETFLOW)
        written = []
        # rotate at any size, and never signal the real flowd (/var/run/flowd.pid on the firewall)
        with unittest.mock.patch.object(agg, 'MAX_FILE_SIZE_MB', 0), unittest.mock.patch('os.kill'):
            for i in range(14):                                   # more rotations than core keeps files
                written.append(b'flows %d' % i)
                self.write('flowd.log', written[-1], T0 + i * H)
                agg.check_rotate(self.log)
                self.keep(T0 + i * H + 60)
        self.assertEqual(len(glob.glob(glob.escape(self.log) + '.*')), 10)   # core kept its ten
        self.assertEqual(self.kept_data(), sorted(written))                   # the plugin every one


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: Run them to see them fail.**

Run: `python3 -m unittest tests.test_keep -v`
Expected: the module cannot load `keep.py`:
`FileNotFoundError: [Errno 2] No such file or directory: '…/src/opnsense/scripts/topdevices/keep.py'`,
then `FAILED (errors=1)`.

- [ ] **Step 3: Write `keep.py`.** Create `src/opnsense/scripts/topdevices/keep.py`, and
make it executable with `chmod 755` and `git update-index --chmod=+x` after `git add`:

```python
#!/usr/local/bin/python3
"""TopDevices: keeps NetFlow's rotated flow log for two days.

Core's aggregator rotates /var/log/flowd.log past 10 MB and keeps ten rotated
files: about a day on the reference install. Every 10 minutes
(/usr/local/etc/cron.d/topdevices) this links each file core has rotated into
/var/log/topdevices/ - a second name for the same file, which core's renames
leave alone and its os.remove() does not free - and deletes the links older
than flows.py can ask for. flows.py reads both places.

Standard library only, and no import of flows.py, so it keeps working whatever
flows.py is installed. Silent on success; an error goes to syslog, exit 1.
Design: docs/superpowers/specs/2026-09-24-keep-flow-log-design.md §5
"""
import glob
import os
import re
import sys
import syslog
import time

LOG = '/var/log/flowd.log'
KEPT = '/var/log/topdevices'     # on /var/log's own filesystem: a hard link cannot cross one
KEEP_S = 51 * 3600               # an hour past flows.py's 50-hour reach (spec §4)
CAP = 1 << 30                    # 1 GB at most: a traffic surge cannot fill the disk
ROTATED = re.compile(r'\.[0-9]+')


def rotated(log):
    """Core's rotated files, flowd.log.000001 and up - never flowd.log itself,
    which flowd is still writing."""
    base = os.path.basename(log)
    return [p for p in glob.glob(glob.escape(log) + '.*') if ROTATED.fullmatch(os.path.basename(p)[len(base):])]


def kept_files(kept):
    """[(path, stat)] of every kept file, oldest first."""
    out = []
    for path in glob.glob(os.path.join(glob.escape(kept), 'flowd.*')):
        try:
            out.append((path, os.stat(path)))
        except FileNotFoundError:
            pass
    return sorted(out, key=lambda kv: kv[1].st_mtime)


def run(now, log, kept, keep_s=KEEP_S, cap=CAP):
    """One pass (spec §5): link each file core has rotated out since the last
    pass, then delete the kept files past the reach, and the oldest while they
    total more than the cap. Returns (linked, removed)."""
    os.makedirs(kept, mode=0o700, exist_ok=True)
    os.chmod(kept, 0o700)
    have = {(st.st_dev, st.st_ino) for _, st in kept_files(kept)}
    linked = removed = 0
    for path in rotated(log):
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue                              # renamed since the listing: the next pass finds it
        if (st.st_dev, st.st_ino) in have or st.st_mtime < now - keep_s:
            continue
        try:
            os.link(path, os.path.join(kept, 'flowd.%d.%d' % (st.st_mtime, st.st_ino)))
        except (FileNotFoundError, FileExistsError):
            continue                              # renamed meanwhile, or another pass linked it
        have.add((st.st_dev, st.st_ino))
        linked += 1
    files = kept_files(kept)
    total = sum(st.st_size for _, st in files)
    for path, st in files:
        if st.st_mtime < now - keep_s or total > cap:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass                              # another pass deleted it
            total -= st.st_size
            removed += 1
    return linked, removed


def main():
    try:
        run(time.time(), LOG, KEPT)
    except Exception as exc:              # cron discards the output: the system log is where this shows
        syslog.syslog(syslog.LOG_ERR, 'topdevices keep: %s' % (str(exc) or exc.__class__.__name__))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to see them pass.**

Run: `python3 -m unittest tests.test_keep -v`
Expected: `Ran 10 tests`, `OK (skipped=1)`. The skipped one is `CoreRotation`.

Run: `OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review python3 -m unittest tests.test_keep -v`
Expected: `Ran 10 tests`, `OK`: 14 rotations by core's own `check_rotate()`, and all 14
files kept.

- [ ] **Step 5: Add the keep mutants.** In `tests/mutate.py`, after the line
`FLOWS = ROOT / 'src/opnsense/scripts/topdevices/flows.py'`, add
`KEEP = ROOT / 'src/opnsense/scripts/topdevices/keep.py'`. After the closing `]` of
`FLOW_MUTANTS`, add:

```python


KEEP_MUTANTS = [
    ('the current log kept too',
     "    return [p for p in glob.glob(glob.escape(log) + '.*') if ROTATED.fullmatch(os.path.basename(p)[len(base):])]",
     "    return glob.glob(glob.escape(log) + '*')"),
    ('a file kept twice when flowd writes after the rename',
     '        if (st.st_dev, st.st_ino) in have or st.st_mtime < now - keep_s:', '        if st.st_mtime < now - keep_s:'),
    ('a file past the reach linked, only to be pruned',
     '        if (st.st_dev, st.st_ino) in have or st.st_mtime < now - keep_s:', '        if (st.st_dev, st.st_ino) in have:'),
    ('kept for 50 hours, not 51', 'KEEP_S = 51 * 3600', 'KEEP_S = 50 * 3600'),
    ('no size cap', '        if st.st_mtime < now - keep_s or total > cap:', '        if st.st_mtime < now - keep_s:'),
    ('the newest go first over the cap', 'key=lambda kv: kv[1].st_mtime)', 'key=lambda kv: -kv[1].st_mtime)'),
    ('the directory left as it was', '    os.chmod(kept, 0o700)\n', ''),
    ('a file renamed meanwhile stops the pass', '        except (FileNotFoundError, FileExistsError):', '        except FileExistsError:'),
    ('a file linked meanwhile stops the pass', '        except (FileNotFoundError, FileExistsError):', '        except FileNotFoundError:'),
    ('an error reported as success', '        return 1\n', '        return 0\n'),
    ('a cap of 10 GB', 'CAP = 1 << 30', 'CAP = 10 << 30'),
]
```

In `main()`, replace

```python
    for args in ((LIVE, 'LIVE_PY', 'test_live.py', MUTANTS), (FLOWS, 'FLOWS_PY', 'test_flows.py', FLOW_MUTANTS)):
```

with

```python
    for args in ((LIVE, 'LIVE_PY', 'test_live.py', MUTANTS), (FLOWS, 'FLOWS_PY', 'test_flows.py', FLOW_MUTANTS),
                 (KEEP, 'KEEP_PY', 'test_keep.py', KEEP_MUTANTS)):
```

Run: `python3 tests/mutate.py`
Expected: 58 lines starting `killed` (14 Live, 33 `flows.py`, 11 keep), none `SURVIVED`
or `BROKEN`, exit status 0.

- [ ] **Step 6: The whole suite.**

Run: `python3 -m unittest discover -s tests`
Expected: `Ran 111 tests`, `OK (skipped=8)`.

- [ ] **Step 7: Commit.**

```bash
git add src/opnsense/scripts/topdevices/keep.py tests/test_keep.py tests/mutate.py
git update-index --chmod=+x src/opnsense/scripts/topdevices/keep.py
git commit -m "0.3.0: keep.py keeps NetFlow's rotated flow log for two days"
```

Check: `git ls-files -s src/opnsense/scripts/topdevices/keep.py` starts with `100755`.

---

### Task 2: `flows.py` reads the kept log

**Files:**
- Modify: `src/opnsense/scripts/topdevices/flows.py`
- Modify: `tests/test_flows.py`
- Modify: `tests/mutate.py`

**Interfaces:**
- Consumes: Task 1's kept names, `<dirname(log)>/topdevices/flowd.*`.
- Produces:
  - `flows.KEPT_DIR = 'topdevices'` and `flows.GAP = 900`;
  - `flows._names(log) -> [path]`: core's names, then the kept ones;
  - `flows.open_log(log=LOG)`, with the same signature, opens each file once by
    `(st_dev, st_ino)`;
  - `flows._files_now(log)`, with the same signature, lists both places;
  - `flows._first_recv(fd) -> int | None`;
  - `flows.log_from(opened) -> int | None`, the new L, which Task 3's C uses.
  - `parity_flows.py` (Task 9) calls `flows._names(flows.LOG)`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_flows.py`, insert this block at the
end of `class Reading`, just before `class Attribution(unittest.TestCase):`:

```python
    # --- the kept log: keep.py's links in topdevices/ beside the log (2026-09-24 spec §4) ---

    def kept(self, files):
        """keep.py's directory beside the scratch log, holding [(name, bytes, mtime)]."""
        d = os.path.join(self.tmp.name, 'topdevices')
        os.makedirs(d, exist_ok=True)
        write_log(d, files)
        return d

    def test_kept_files_are_read_too_and_a_file_with_two_names_once(self):
        log = write_log(self.tmp.name, [('flowd.log.000001', b'a', T0 + 100), ('flowd.log', b'c', T0 + 300)])
        kept = self.kept([('flowd.%d.1' % T0, b'old', T0)])                        # core has deleted this one
        os.link(log + '.000001', os.path.join(kept, 'flowd.%d.2' % (T0 + 100)))   # keep.py's second name for .000001
        opened = flows.open_log(log)
        self.addCleanup(flows.close_log, opened)
        self.assertEqual([flows._read(fd) for fd, _, _ in opened], [b'old', b'a', b'c'])

    def gapped(self, gap):
        """A kept file, then core's flowd.log whose first record comes `gap` s after the kept one's last write."""
        self.kept([('flowd.1.1', flow('8.8.8.8', '192.168.1.10', 1, recv=T0, start=T0, end=T0), T0 + 100)])
        first = T0 + 100 + gap
        return self.opened([('flowd.log', flow('8.8.8.8', '192.168.1.10', 1, recv=first, start=first, end=first),
                             first + 50)])

    def test_the_log_is_complete_from_the_newest_unbroken_run_of_files(self):
        self.assertEqual(flows.log_from(self.gapped(900)), T0)

    def test_a_missing_file_ends_the_run(self):
        self.assertEqual(flows.log_from(self.gapped(901)), T0 + 1001)

    def test_a_file_without_an_ipv4_record_does_not_break_the_run(self):
        # between the oldest file's last write and the newest one's first record lie
        # 4900 s, but the file between them covers them, with IPv6 records only
        opened = self.opened([
            ('flowd.log.000002', EARLY, T0 + 100),
            ('flowd.log.000001', flow('8.8.8.8', '192.168.1.10', 1, recv=T0 + 4000, start=T0, end=T0, v6=True), T0 + 5000),
            ('flowd.log', flow('8.8.8.8', '192.168.1.10', 1, recv=T0 + 5001, start=T0 + 5001, end=T0 + 5001), T0 + 5100)])
        self.assertEqual(flows.log_from(opened), T0)

    def test_a_kept_file_pruned_while_the_files_are_opened_is_simply_gone(self):
        log = write_log(self.tmp.name, [('flowd.log', KINDS[0], T0 + 300)])
        kept = self.kept([('flowd.1.1', EARLY, T0), ('flowd.2.2', KINDS[1], T0 + 100)])
        pruned, real_open = os.path.join(kept, 'flowd.1.1'), os.open

        def prune_first(path, *args, **kwargs):
            if path == pruned and os.path.exists(path):
                os.remove(path)                   # keep.py prunes it between the listing and the open
            return real_open(path, *args, **kwargs)

        with unittest.mock.patch('os.open', prune_first):
            opened = flows.open_log(log)
        self.addCleanup(flows.close_log, opened)
        self.assertEqual([flows._read(fd) for fd, _, _ in opened], [KINDS[1], KINDS[0]])

```

In `class CommandLine`, insert this test just before the line
`CORE_NETFLOW = next((p for p in (os.environ.get('CORE_NETFLOW'),` (it follows
`test_the_script_never_prints_a_traceback`):

```python
    def test_a_kept_file_nobody_rotates_is_an_error_answer_too(self):
        # the 40 MB guard holds for every file read, the kept ones too (2026-09-24 spec §6)
        with tempfile.TemporaryDirectory() as d, unittest.mock.patch.object(flows, 'MAX_FILE', 200):
            log = write_log(d, [('flowd.log', KINDS[0], T0 + 100)])             # 116 bytes: under the guard
            os.mkdir(os.path.join(d, 'topdevices'))
            write_log(os.path.join(d, 'topdevices'), [('flowd.1.1', b''.join(KINDS), T0)])   # 464 bytes: over it
            a = self.run_main(['flows.py', 'totals', str(T0 + 100), str(T0 + 200)], log, now=T0 + 300)
        self.assertIn('aggregator running', a['error'])

```

- [ ] **Step 2: Run them to see them fail.**

Run: `python3 -m unittest tests.test_flows`
Expected: `Ran 63 tests`, then `FAILED (failures=3, errors=1, skipped=4)`:
- `ERROR: test_a_kept_file_nobody_rotates_is_an_error_answer_too`: no `error` key,
  because the kept file is never read;
- `FAIL:` `test_a_kept_file_pruned_while_the_files_are_opened_is_simply_gone`,
  `test_kept_files_are_read_too_and_a_file_with_two_names_once`, and
  `test_the_log_is_complete_from_the_newest_unbroken_run_of_files`.

`test_a_missing_file_ends_the_run` and
`test_a_file_without_an_ipv4_record_does_not_break_the_run` pass already, because the
old code reads no kept file at all. Their mutants in Step 5 prove they bite.

- [ ] **Step 3: Implement.** In `src/opnsense/scripts/topdevices/flows.py`:

After `LOG = '/var/log/flowd.log'` add:

```python
KEPT_DIR = 'topdevices'          # keep.py's links beside the log: rotated files core deleted, or will (2026-09-24 spec §5)
```

Before `DIGITS = re.compile(r'[0-9]+')` add:

```python
GAP = 900                # more than this between two files, and one is missing between them (2026-09-24 spec §4)
```

Replace `open_log` from its `def` line down to and including
`        except BaseException:` (the rest of the function is unchanged) with:

```python
def _names(log):
    """Every name the flow log has now: core's files, then keep.py's links in the
    directory beside them (2026-09-24 spec §4)."""
    kept = os.path.join(glob.escape(os.path.dirname(log)), KEPT_DIR, 'flowd.*')
    return glob.glob(glob.escape(log) + '*') + glob.glob(kept)


def open_log(log=LOG):
    """Every flow log file - core's and the kept ones - opened now, once each
    however many names it has, oldest first: [(fd, last write, size)].
    Reading through descriptors keeps a rotation - which renames every file and
    deletes the oldest - from mixing up which is which once they are open. One
    that lands while they are being opened is caught by listing them again: if
    any name now leads to another file, they are all opened afresh."""
    for _ in range(5):                            # a rotation takes milliseconds
        opened, ids, seen = [], {}, set()
        try:
            for path in _names(log):
                try:
                    fd = os.open(path, os.O_RDONLY)
                except FileNotFoundError:
                    continue                      # rotated or pruned away since the listing
                st = os.fstat(fd)
                ids[path] = (st.st_dev, st.st_ino)
                if ids[path] in seen:
                    os.close(fd)                  # a kept name for a file already open
                    continue
                seen.add(ids[path])
                opened.append((fd, st.st_mtime, st.st_size))
        except BaseException:
```

In `_files_now`, replace its docstring and loop head

```python
    """{path: (device, inode)} of every flow log file at this moment."""
    now = {}
    for path in glob.glob(glob.escape(log) + '*'):
```

with

```python
    """{path: (device, inode)} of every name the flow log has at this moment."""
    now = {}
    for path in _names(log):
```

Replace the whole `log_from` function with:

```python
def _first_recv(fd):
    """The receive time of the first IPv4 record in a file's first 64 KB, or None."""
    for r in records(os.pread(fd, 65536, 0)):
        return r[0]
    return None


def log_from(opened):
    """When the log becomes complete (2026-09-24 spec §4): the receive time of the
    first record of the oldest file in the newest unbroken run. More than GAP
    between one file's last write and the next one's first record means a file
    is missing between them. A file with no IPv4 record in its first 64 KB leaves
    the boundary before it unjudged. None when the log holds no IPv4 record."""
    L = later = None                      # later: the first record of the next newer file
    for fd, mtime, _ in reversed(opened):
        first = _first_recv(fd)
        if later is not None and later - mtime > GAP:
            break
        if first is not None:
            L = first
        later = first
    return L
```

- [ ] **Step 4: Run the tests to see them pass.**

Run: `python3 -m unittest tests.test_flows`
Expected: `Ran 63 tests`, `OK (skipped=4)`.

Run: `OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review python3 -m unittest tests.test_flows`
Expected: `Ran 63 tests`, `OK`.

- [ ] **Step 5: Mutants.** In `tests/mutate.py`'s `FLOW_MUTANTS`, re-anchor the existing
`'any open error taken for a rotation'`, whose comment changed. Its two strings become:

```python
     '                except FileNotFoundError:\n                    continue                      # rotated or pruned away since the listing',
     '                except OSError:\n                    continue                      # rotated or pruned away since the listing'),
```

Append these to `FLOW_MUTANTS`, before its closing `]`:

```python
    ('kept files not read', "    return glob.glob(glob.escape(log) + '*') + glob.glob(kept)", "    return glob.glob(glob.escape(log) + '*')"),
    ('a file with two names read twice', '                if ids[path] in seen:', '                if False:'),
    ('a missing file not noticed', '        if later is not None and later - mtime > GAP:', '        if False:'),
    ('a gap of exactly 900 s taken for a missing file', '        if later is not None and later - mtime > GAP:',
     '        if later is not None and later - mtime >= GAP:'),
    ('a file without an IPv4 record breaking the run', '        later = first\n', '        later = first if first is not None else mtime\n'),
    ('the rotation re-check blind to kept names', '    for path in _names(log):\n        try:\n            st = os.stat(path)',
     "    for path in glob.glob(glob.escape(log) + '*'):\n        try:\n            st = os.stat(path)"),
```

Run: `python3 tests/mutate.py`
Expected: 64 `killed` lines (14 + 39 + 11), exit status 0.

- [ ] **Step 6: The whole suite.** Run: `python3 -m unittest discover -s tests`
Expected: `Ran 117 tests`, `OK (skipped=8)`.

- [ ] **Step 7: Commit.**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py tests/mutate.py
git commit -m "0.3.0: flows.py reads the kept log, each file once, complete from its newest unbroken run"
```

---

### Task 3: `flows.py`: the 50-hour reach, and all traffic from where it is complete

**Files:**
- Modify: `src/opnsense/scripts/topdevices/flows.py`
- Modify: `tests/test_flows.py`
- Modify: `tests/mutate.py`

**Interfaces:**
- Consumes: Task 2's `log_from(opened)` (L).
- Produces:
  - `flows.REACH = 50 * HOUR`;
  - `parse_args` refuses totals with `FROM < now − REACH − SLACK`;
  - `answer_totals` reports `all.from = max(frm, min(L, now − DAY − SLACK))`, and its
    figures cover exactly that. The widget (Task 6) compares `all.from` with the
    range's start.

- [ ] **Step 1: Update and write the tests.** In `tests/test_flows.py`, `class CommandLine`:

In `test_bad_input_is_refused_with_a_reason`, replace
`(['totals', str(self.NOW - 86400 - 301), now], 'more than a day ago'),` with
`(['totals', str(self.NOW - 180000 - 301), now], 'more than 50 hours ago'),`.

Replace `test_the_clocks_may_differ_by_minutes` and
`test_the_clock_slack_includes_its_edges` with:

```python
    def test_the_clocks_may_differ_by_minutes(self):
        req = flows.parse_args(['totals', str(self.NOW - 180000 - 299), str(self.NOW + 299)], self.NOW)
        self.assertEqual((req['from'], req['to']), (self.NOW - 180000 - 299, self.NOW))

    def test_the_clock_slack_includes_its_edges(self):
        req = flows.parse_args(['totals', str(self.NOW - 180000 - 300), str(self.NOW + 300)], self.NOW)
        self.assertEqual((req['from'], req['to']), (self.NOW - 180000 - 300, self.NOW))
```

Replace `test_a_device_panel_may_ask_for_a_window_that_began_over_a_day_ago` with:

```python
    def test_a_device_panel_may_ask_for_a_window_that_began_over_50_hours_ago(self):
        # the panel asks for its table's window, which ages while the table is on screen
        req = flows.parse_args(['device', '192.168.1.10', str(self.NOW - 180000 - 900), str(self.NOW - 900)], self.NOW)
        self.assertEqual((req['from'], req['to']), (self.NOW - 180000 - 900, self.NOW - 900))
```

At the end of `class Totals`, just before `class Device(unittest.TestCase):`, add:

```python
    # --- all traffic is complete from C = min(L, now - 86400 - 300) (2026-09-24 spec §4) ---

    def test_a_range_before_the_log_and_the_hourly_records_is_answered_from_where_they_begin(self):
        # the log begins at L, core's hourly records reach back a day from now: all
        # traffic is complete from the earlier of the two, and the answer starts there
        L = flows.log_from(self.opened)
        now, to = L + 86400 - 3600, L + 600
        asked = []
        a = flows.answer_totals(L - 2 * 86400, to, now, self.opened, NET, lambda lo, hi: asked.append((lo, hi)) or [], 1)
        C = now - 86400 - 300
        self.assertEqual((a['all']['from'], asked), (C, [(C, min(-(-L // 3600) * 3600, to))]))
        self.assertEqual(a['inet'], {'from': L, 'to': to})

    def test_a_short_log_answers_all_traffic_from_where_it_begins(self):
        # the kept log still filling, or a file missing: the log begins at L, after
        # the hourly records' day, so all traffic starts at L
        L = flows.log_from(self.opened)
        a = flows.answer_totals(L - 3600, L + 900, L + 86400 + 7200, self.opened, NET,
                                lambda lo, hi: self.fail('no fill-in'), 1)
        self.assertEqual((a['all']['from'], a['inet']['from']), (L, L))

    def test_a_log_reaching_back_past_a_day_answers_alone(self):
        # the kept log: a range starting 40 hours back, inside it, needs no hourly records
        L = flows.log_from(self.opened)
        a = flows.answer_totals(L + 100, L + 700, L + 40 * 3600, self.opened, NET,
                                lambda lo, hi: self.fail('no fill-in'), 2)
        self.assertEqual(a['all'], {'from': L + 100, 'to': L + 700, 'hourly_until': None})
```

- [ ] **Step 2: Run them to see them fail.**

Run: `python3 -m unittest tests.test_flows`
Expected: `Ran 66 tests`, then `FAILED (failures=3, errors=2, skipped=4)`:
- `ERROR:` `test_the_clock_slack_includes_its_edges` and
  `test_the_clocks_may_differ_by_minutes`, refused as more than a day ago;
- `FAIL:` `test_bad_input_is_refused_with_a_reason` (args
  `['totals', '1789906099', '1790086400']`),
  `test_a_range_before_the_log_and_the_hourly_records_is_answered_from_where_they_begin`,
  and `test_a_short_log_answers_all_traffic_from_where_it_begins`.

`test_a_log_reaching_back_past_a_day_answers_alone` passes already: it pins that the
kept log answers a deep range alone. Its mutant, "the log's own reach ignored for all
traffic", proves it bites.

- [ ] **Step 3: Implement.** In `flows.py`, after the `SLACK = 300 …` line add:

```python
REACH = 50 * HOUR        # a raw-log range starts at most this far back: Yesterday at any hour (2026-09-24 spec §4)
```

In `parse_args`, replace

```python
    if mode == 'totals' and frm < now - DAY - SLACK:
        raise ValueError("FROM is more than a day ago: that range is read from NetFlow's records")
```

with

```python
    if mode == 'totals' and frm < now - REACH - SLACK:
        raise ValueError("FROM is more than 50 hours ago: that range is read from NetFlow's records")
```

In `answer_totals`, replace

```python
    L = log_from(opened)
    if L is None:
        raise ValueError('the NetFlow flow log holds no flows yet')
    p = plan(frm, to, L)
```

with

```python
    L = log_from(opened)
    if L is None:
        raise ValueError('the NetFlow flow log holds no flows yet')
    # all traffic is complete from the log's start, or through the hourly records
    # within their day: an earlier start is answered from there (2026-09-24 spec §4)
    frm = max(frm, min(L, now - DAY - SLACK))
    p = plan(frm, to, L)
```

`answer_device` keeps its own rule, `[max(frm, L), to)`.

- [ ] **Step 4: Run the tests to see them pass.**

Run: `python3 -m unittest tests.test_flows`
Expected: `Ran 66 tests`, `OK (skipped=4)`.

Run: `OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review python3 -m unittest tests.test_flows`
Expected: `Ran 66 tests`, `OK`.

- [ ] **Step 5: Mutants.** Three existing mutants in `FLOW_MUTANTS` anchor on the old
bound. Change each where it stands, and add four new ones after the third.

1. Replace the `'the clock slack dropped'` entry with:

   ```python
    ('the clock slack dropped', "    if mode == 'totals' and frm < now - REACH - SLACK:", "    if mode == 'totals' and frm < now - REACH:"),
   ```

2. Replace the `'the day bound applied to device panels'` entry with:

   ```python
    ('the reach applied to device panels', "    if mode == 'totals' and frm < now - REACH - SLACK:", '    if frm < now - REACH - SLACK:'),
   ```

3. Replace the two-line `'the day bound excluding its edge'` entry with this block,
   whose first entry is the renamed mutant and the other four are new:

   ```python
    ('the reach excluding its edge', "    if mode == 'totals' and frm < now - REACH - SLACK:",
     "    if mode == 'totals' and frm <= now - REACH - SLACK:"),
    ('the reach left at a day', "    if mode == 'totals' and frm < now - REACH - SLACK:",
     "    if mode == 'totals' and frm < now - DAY - SLACK:"),
    ('all traffic claimed from the range start', '    frm = max(frm, min(L, now - DAY - SLACK))\n', ''),
    ('the hourly records trusted past their day', '    frm = max(frm, min(L, now - DAY - SLACK))',
     '    frm = max(frm, min(L, now - REACH - SLACK))'),
    ("the log's own reach ignored for all traffic", '    frm = max(frm, min(L, now - DAY - SLACK))',
     '    frm = max(frm, now - DAY - SLACK)'),
   ```

Run: `python3 tests/mutate.py`
Expected: 68 `killed` lines (14 + 43 + 11), exit status 0.

- [ ] **Step 6: The whole suite.**

Run: `python3 -m unittest discover -s tests`
Expected: `Ran 120 tests`, `OK (skipped=8)`.

Run: `OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review python3 -m unittest discover -s tests`
Expected: `Ran 120 tests`, `OK (skipped=3)`.

- [ ] **Step 7: Commit.**

```bash
git add src/opnsense/scripts/topdevices/flows.py tests/test_flows.py tests/mutate.py
git commit -m "0.3.0: flows.py reaches 50 hours back and answers all traffic from where it is complete"
```

---

### Task 4: The widget's time zone helpers

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js`
- Modify: `tests/netflow_ranges.test.mjs`
- Modify: `tests/mutate_widget.mjs`

**Interfaces:**
- Produces pure, exported functions, each taking an IANA zone name or `undefined` for
  the browser's zone:
  - `wallParts(ts, tz) -> [year, month 1-12, day, hours, minutes, seconds, weekday 0 = Sunday]`;
  - `offsetAt(ts, tz) -> seconds east of UTC`;
  - `wallToEpoch(y, mo, d, h, mi, s, tz) -> epoch seconds`;
  - `localMidnight(ts, back, tz) -> epoch seconds`;
  - `zoneAbbr(ts, tz) -> string`, such as `'AEST'`.
- Also module constants `WEEKDAYS` and `MONTHS`, which Task 5 uses.

- [ ] **Step 1: Write the failing tests.** In `tests/netflow_ranges.test.mjs`, right after the
line `const U = (mo, d, h = 0) => Date.UTC(2026, mo - 1, d, h) / 1000;`, add:

```js

// --- the firewall's time zone: pure helpers -------------------------------------------

test('wall-clock parts of an instant in a named zone', () => {
    assert.deepEqual(m.wallParts(NOW, 'Australia/Brisbane'), [2026, 9, 23, 19, 8, 54, 3]);   // a Wednesday
    assert.deepEqual(m.wallParts(NOW, 'UTC'), [2026, 9, 23, 9, 8, 54, 3]);
    assert.deepEqual(m.wallParts(NOW, 'Asia/Kathmandu'), [2026, 9, 23, 14, 53, 54, 3]);        // +5:45
    assert.deepEqual(m.wallParts(S(14, 0, 22), 'Australia/Brisbane'), [2026, 9, 23, 0, 0, 0, 3]);   // midnight is 00, not 24
});

test('a wall-clock time in a named zone is the instant it names', () => {
    assert.equal(m.wallToEpoch(2026, 9, 23, 0, 0, 0, 'Australia/Brisbane'), S(14, 0, 22));
    assert.equal(m.wallToEpoch(2026, 9, 23, 0, 0, 0, 'Asia/Kathmandu'), S(18, 15, 22));
    assert.equal(m.wallToEpoch(2026, 9, 23, 19, 8, 54, 'Australia/Brisbane'), NOW);
    assert.equal(m.offsetAt(NOW, 'Asia/Kathmandu'), 5 * 3600 + 45 * 60);
});

test('clocks going forward: a wall time that does not occur moves on by the jump', () => {
    // Sydney, Sun 4 Oct 2026: 02:00 AEST becomes 03:00 AEDT, at 16:00 UTC the day before
    assert.equal(m.wallToEpoch(2026, 10, 4, 2, 30, 0, 'Australia/Sydney'), U(10, 3, 16) + 1800);   // 03:30 AEDT
    // Santiago, Sun 6 Sep 2026: midnight -04 becomes 01:00 -03, at 04:00 UTC
    assert.equal(m.wallToEpoch(2026, 9, 6, 0, 0, 0, 'America/Santiago'), U(9, 6, 4));             // 01:00, the day's first instant
});

test('clocks going back: a wall time that occurs twice is the earlier', () => {
    // Sydney, Sun 5 Apr 2026: 03:00 AEDT becomes 02:00 AEST, at 16:00 UTC the day before
    assert.equal(m.wallToEpoch(2026, 4, 5, 2, 30, 0, 'Australia/Sydney'), U(4, 4, 15) + 1800);     // 02:30 AEDT
});

test('midnights follow the calendar in the named zone', () => {
    assert.equal(m.localMidnight(NOW, 0, 'Australia/Brisbane'), S(14, 0, 22));                   // today, Wed 23 Sep
    assert.equal(m.localMidnight(NOW, 1, 'Australia/Brisbane'), S(14, 0, 21));                   // yesterday
    assert.equal(m.localMidnight(NOW, 0, 'UTC'), S(0, 0, 23));
    // Sydney's Sun 4 Oct 2026 lasts 23 hours; Santiago's Sun 6 Sep begins at 01:00
    const sydney = (back) => m.localMidnight(U(10, 5, 1), back, 'Australia/Sydney');
    assert.equal(sydney(0) - sydney(1), 23 * 3600);
    assert.equal(m.localMidnight(U(9, 6, 12), 0, 'America/Santiago'), U(9, 6, 4));
});

test("the zone is named as the firewall's own date command names it", () => {
    assert.equal(m.zoneAbbr(NOW, 'Australia/Brisbane'), 'AEST');
    assert.equal(m.zoneAbbr(U(10, 5, 1), 'Australia/Sydney'), 'AEDT');
    assert.equal(m.zoneAbbr(NOW, 'Asia/Kolkata'), 'IST');
});
```

- [ ] **Step 2: Run them to see them fail.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 116`, `ℹ fail 6`. Each of the six new tests fails with
`TypeError: m.wallParts is not a function`, or the same for another helper.

- [ ] **Step 3: Implement.** In `TopDevices.js`, insert this block just before the line
`export default class TopDevices extends BaseWidget {`, after `fmtRate`:

```js
/* ---------- the firewall's time zone: pure helpers (tests/netflow_ranges.test.mjs) ---------- */

// Every wall-clock conversion goes through Intl with the zone set on the firewall,
// under System: Settings: General, so the browser's own zone plays no part.
// tz undefined: the browser's zone (spec 2026-09-24-keep-flow-log §7.2).
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WALL = new Map();                  // one formatter per named zone; the browser's is made afresh

function wallFormat(tz) {
    const make = () => new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', weekday: 'short',
        year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric', second: 'numeric' });
    if (tz === undefined) return make();
    if (!WALL.has(tz)) WALL.set(tz, make());
    return WALL.get(tz);
}

// [year, month 1-12, day, hours, minutes, seconds, weekday 0 = Sunday] of an instant (epoch seconds) in tz
export function wallParts(ts, tz) {
    const p = {};
    wallFormat(tz).formatToParts(new Date(ts * 1000)).forEach((x) => { p[x.type] = x.value; });
    return [+p.year, +p.month, +p.day, +p.hour, +p.minute, +p.second, WEEKDAYS.indexOf(p.weekday)];
}

// tz's offset from UTC at an instant, in seconds
export function offsetAt(ts, tz) {
    const [y, mo, d, h, mi, s] = wallParts(ts, tz);
    return Date.UTC(y, mo - 1, d, h, mi, s) / 1000 - Math.floor(ts);
}

// The instant a wall-clock time names in tz, in epoch seconds. A time that occurs
// twice, as clocks go back, gives the earlier; one that does not occur, as clocks
// go forward, moves on by the jump, as Date does for local times.
export function wallToEpoch(y, mo, d, h, mi, s, tz) {
    const w = Date.UTC(y, mo - 1, d, h, mi, s) / 1000;          // the wall time, read as if it were UTC
    const names = (t) => t + offsetAt(t, tz) === w;
    const before = w - offsetAt(w - 43200, tz);                  // no zone changes its clock twice in a day
    if (names(before)) return before;
    const after = w - offsetAt(w + 43200, tz);
    return names(after) ? after : before;
}

// The midnight `back` calendar days before the day of ts, in tz - the day's first
// instant where midnight does not occur. A day with a clock change is 23 or 25 hours.
export function localMidnight(ts, back, tz) {
    const [y, mo, d] = wallParts(ts, tz);
    const day = new Date(Date.UTC(y, mo - 1, d - back));
    return wallToEpoch(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate(), 0, 0, 0, tz);
}

// "Australian Eastern Standard Time" -> "AEST", as the firewall's own `date` names
// it; Intl's short name, then the GMT offset, when there is no long one.
export function zoneAbbr(ts, tz) {
    const named = (style) => {
        try {
            const p = new Intl.DateTimeFormat('en-US', { timeZone: tz, timeZoneName: style })
                .formatToParts(new Date(ts * 1000)).find(x => x.type === 'timeZoneName');
            return p ? p.value : '';
        } catch (e) { return ''; }
    };
    const words = named('long').split(/[\s-]+/).filter(w => /^[A-Za-z]/.test(w));
    if (words.length > 1) return words.map(w => w[0].toUpperCase()).join('');
    if (words.length === 1) return words[0];
    const short = named('short');
    if (short) return short;
    const off = Math.round(offsetAt(ts, tz) / 60), sg = off >= 0 ? '+' : '-';
    return `GMT${sg}${String(Math.floor(Math.abs(off) / 60)).padStart(2, '0')}${String(Math.abs(off) % 60).padStart(2, '0')}`;
}

```

- [ ] **Step 4: Run the tests to see them pass.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 116`, `ℹ pass 116`, `ℹ fail 0`.

- [ ] **Step 5: Mutants.** In `tests/mutate_widget.mjs`, append to `mutants`, before its
closing `];`:

```js
    ["midnight read as hour 24", "hourCycle: 'h23'", "hourCycle: 'h24'"],
    ["a doubled wall time taken as the later", "    if (names(before)) return before;\n", ""],
    ["a missing wall time moved back", "    return names(after) ? after : before;", "    return after;"],
    ["yesterday as 24 hours, not the calendar day", "    return wallToEpoch(day.getUTCFullYear(), day.getUTCMonth() + 1, day.getUTCDate(), 0, 0, 0, tz);", "    return wallToEpoch(y, mo, d, 0, 0, 0, tz) - back * 86400;"],
    ["the long zone name not abbreviated", "    const words = named('long').split(/[\\s-]+/).filter(w => /^[A-Za-z]/.test(w));\n    if (words.length > 1) return words.map(w => w[0].toUpperCase()).join('');\n", "    const words = named('long').split(/[\\s-]+/).filter(w => /^[A-Za-z]/.test(w));\n"],
```

The last anchor spans two lines, because the old `_tzAbbr` holds the same `if` line
until Task 5 removes it. `hour12: false` would not do as the midnight mutant: Node
prints midnight as `00` with it. `hourCycle: 'h24'` prints `24`.

Run: `node tests/mutate_widget.mjs`
Expected: 107 `killed` lines, exit status 0.

- [ ] **Step 6: Commit.**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/netflow_ranges.test.mjs tests/mutate_widget.mjs
git commit -m "0.3.0: widget time zone helpers: wall-clock parts, instants, midnights and names in a named zone"
```

---

### Task 5: The firewall's time zone everywhere in the widget

**Files:**
- Modify: `src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php`
- Modify: `src/opnsense/www/js/widgets/TopDevices.js`
- Modify: `tests/netflow_ranges.test.mjs`
- Modify: `tests/live_view.test.mjs`
- Modify: `tests/mutate_widget.mjs`

**Interfaces:**
- Consumes: Task 4's helpers, `WEEKDAYS` and `MONTHS`.
- Produces:
  - `GET /api/topdevices/flows/zone` → `{"timezone": "<IANA name>"}`;
  - `TopDevices#tz` (`undefined` means the browser's zone) and `TopDevices#_loadZone()`,
    called in `onMarkupRendered` before `_loadNetworks`;
  - `TopDevices#_endStr(ts)`: a span's end, with a midnight printed as the second
    before;
  - `TopDevices#_dayTime(ts, secs=false)`: `"Sat 19 Sep 10:00"`, which Task 7 uses.
- `_window`, `_localToEpoch`, `_epochToLocal`, `_dateStr`, `_hm` and `_span` keep
  their signatures and use `this.tz`. `_tzAbbr` is removed.

- [ ] **Step 1: Switch the harness to a browser in another zone, and write the failing
tests.** In `tests/netflow_ranges.test.mjs`:

Replace

```js
// UTC+10 all year, like the reference firewall in September: a UTC-midnight
// bucket starts at 10:00 local
process.env.TZ = 'Australia/Brisbane';
```

with

```js
// The browser runs in another zone than the firewall, so nothing may depend on
// it: the widget takes the firewall's own (spec 2026-09-24-keep-flow-log §7). The
// firewall is on UTC+10 all year, like the reference one: a UTC-midnight bucket
// starts at 10:00 there.
process.env.TZ = 'America/New_York';
let ZONE = 'Australia/Brisbane';          // the zone set on the firewall, as flows/zone answers it
```

In `function widget(range, scope = 'all')`, add `    w.tz = ZONE;` right after
`    w.wanDevs = ['em0'];`.

Replace the `inZone` helper and its heading:

```js
// --- other time zones -----------------------------------------------------------

function inZone(zone, fn) {
    return async () => {
        process.env.TZ = zone;
        try { await fn(); } finally { process.env.TZ = 'Australia/Brisbane'; }
    };
}
```

with

```js
// --- other time zones on the firewall ---------------------------------------------

function inZone(zone, fn) {
    return async () => {
        ZONE = zone;
        try { await fn(); } finally { ZONE = 'Australia/Brisbane'; }
    };
}
```

In `test('Internet only further back than the 62 days kept reads nothing, and says why'`,
the caption now ends on the second before midnight (spec §7.2). Replace
`Tue Jun 30 00:00:00 AEST 2026` in its regular expression with
`Mon Jun 29 23:59:59 AEST 2026`.

Just before the line `// --- rows to download and upload ---------------------------------------------`,
add:

```js
// --- the firewall's time zone in the widget ------------------------------------------

test('Today and Yesterday start at midnight on the firewall, not in the browser', () => {
    const w = widget('today');                                      // the browser is on New York time
    assert.deepEqual(w._window('today', NOW * 1000), [S(14, 0, 22), NOW]);
    assert.deepEqual(w._window('yesterday', NOW * 1000), [S(14, 0, 21), S(14, 0, 22)]);
});

test('the custom fields are wall time on the firewall', () => {
    const w = widget('custom');
    assert.equal(w._localToEpoch('2026-09-23T17:00'), S(7, 0));
    assert.equal(w._localToEpoch('2026-09-23T17:00:30'), S(7, 0) + 30);
    assert.equal(w._epochToLocal(S(7, 0)), '2026-09-23T17:00');
    assert.equal(w._localToEpoch('not a time'), null);
});

test('a caption ending on a midnight ends at 23:59:59', async () => {
    const w = widget('custom');
    w.state.customFrom = '2026-09-22T20:00';
    w.state.customTo = '2026-09-23T00:00';
    reply = flowsOnly(() => totalsAnswer(S(10, 0, 22), S(14, 0, 22)));
    await w._load(NOW * 1000);
    assert.deepEqual(w._windowCaption(), {
        text: 'Tue Sep 22 20:00:00 AEST 2026  →  Tue Sep 22 23:59:59 AEST 2026 · all traffic', note: null });
});

test("in UTC, a note's span ending on a midnight ends the minute before", inZone('UTC', async () => {
    const { w } = await loadCustom(local(2026, 7, 15), local(2026, 8, 1), 'wan');
    assert.equal(w._windowCaption().note,
        'Internet only is kept per day (days start at 00:00), for 62 days: these cover Fri 24 Jul 00:00 → Fri 31 Jul 23:59');
}));

test("the zone comes from the firewall; the browser's stays when it does not say", async () => {
    const w = new TopDevices({});
    const asked = [];
    for (const [answer, zone] of [[{ timezone: 'Australia/Sydney' }, 'Australia/Sydney'],
                                  [{ timezone: 'Mars/Olympus_Mons' }, undefined],       // a zone Intl does not know
                                  [{ errorMessage: 'Endpoint not found' }, undefined],   // 0.2.0's endpoints
                                  [null, undefined]]) {                                   // no answer at all
        w.ajaxCall = async (url) => { asked.push(url); if (answer === null) throw new Error('timeout'); return answer; };
        await w._loadZone();
        assert.equal(w.tz, zone);
    }
    assert.deepEqual([...new Set(asked)], ['/api/topdevices/flows/zone']);
});

function inBrowserZone(zone, fn) {
    return async () => {
        process.env.TZ = zone;
        try { await fn(); } finally { process.env.TZ = 'America/New_York'; }
    };
}

test("with no zone from the firewall, the browser's own is used", inBrowserZone('Asia/Kolkata', () => {
    const w = widget('today');
    w.tz = undefined;
    assert.deepEqual(w._window('today', S(6, 30) * 1000), [S(18, 30, 22), S(6, 30)]);     // 00:00 IST
    assert.equal(w._dateStr(S(6, 30)), 'Wed Sep 23 12:00:00 IST 2026');
}));
```

In `tests/live_view.test.mjs`, just before
`test('the picker offers named devices and anything Live saw, never the firewall or a broadcast address'`,
add:

```js
test('the page load asks the firewall for its time zone before anything else', async (t) => {
    store.clear();
    store.set('opnsense.topdevices.view', JSON.stringify({ range: 'live' }));
    const w = new TopDevices({ widget: {} });
    t.after(() => { w._stopLive(); store.clear(); });
    const asked = [];
    w.ajaxCall = async (url) => { asked.push(url); return url.endsWith('/flows/zone') ? { timezone: 'Australia/Sydney' } : {}; };
    await w.onMarkupRendered();
    assert.equal(asked[0], '/api/topdevices/flows/zone');
    assert.equal(w.tz, 'Australia/Sydney');
});
```

- [ ] **Step 2: Run them to see them fail.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected (qualitative): many failures, for one reason: the widget still takes the
browser's zone, now New York.
- Every caption test that expects `AEST` gets `EDT`.
- `Today and Yesterday start at midnight on the firewall, not in the browser` gets New
  York's midnights.
- The zone test fails on `w._loadZone is not a function`.
- The Live test's `asked[0]` is `/api/interfaces/overview/export`.

- [ ] **Step 3: Implement the endpoint.** In `FlowsController.php`, add after `deviceAction`,
inside the class:

```php

    /**
     * GET /api/topdevices/flows/zone: the time zone set under System: Settings:
     * General, which core writes into PHP's date.timezone. The widget draws its
     * midnights and captions in it (spec 2026-09-24-keep-flow-log §7.1).
     */
    public function zoneAction()
    {
        return ['timezone' => date_default_timezone_get()];
    }
```

Run: `php -l src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php`
Expected: `No syntax errors detected`.

- [ ] **Step 4: Implement the widget.** In `TopDevices.js`:

In the header comment, just before ` * LIVE: the "Live" range shows current rates instead, streamed once per`, add:

```js
 * TIME ZONE: midnights, the custom range fields and every caption use the zone
 * set on the firewall (System: Settings: General), from /api/topdevices/flows/zone,
 * whatever the browser's own; the browser's only when the firewall does not say.
 * Design: docs/superpowers/specs/2026-09-24-keep-flow-log-design.md §7
 *
```

In the constructor, after `        this.wanDevs = [];       // device names of the upstream interface(s)`, add:

```js
        this.tz = undefined;     // the firewall's time zone (_loadZone); undefined: the browser's
```

Replace the start of `_window`, from its signature to the `yesterday` case:

```js
    _window(key, nowMs = Date.now()) {
        const now = new Date(nowMs);
        // local midnights, from the calendar: a day with a clock change is 23 or 25 hours
        const midnight = (back) => Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate() - back).getTime() / 1000);
        const mid = midnight(0);
        const s = Math.floor(now.getTime() / 1000);
        switch (key) {
            case '1h':        return [s - 3600, s];
            case 'today':     return [mid, s];
            case 'yesterday': return [midnight(1), mid];
```

with

```js
    _window(key, nowMs = Date.now()) {
        const s = Math.floor(nowMs / 1000);
        // midnights on the firewall, from its calendar: a day with a clock change is 23 or 25 hours
        const mid = localMidnight(s, 0, this.tz);
        switch (key) {
            case '1h':        return [s - 3600, s];
            case 'today':     return [mid, s];
            case 'yesterday': return [localMidnight(s, 1, this.tz), mid];
```

Replace `_localToEpoch` and `_epochToLocal` with:

```js
    // a datetime-local value, "2026-09-23T17:00", read as wall time on the firewall
    _localToEpoch(v) {
        const x = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(v || '');
        return x ? wallToEpoch(+x[1], +x[2], +x[3], +x[4], +x[5], +(x[6] || 0), this.tz) : null;
    }

    _epochToLocal(ts) {
        const [y, mo, d, h, mi] = wallParts(ts, this.tz);
        const p = (x) => String(x).padStart(2, '0');
        return `${y}-${p(mo)}-${p(d)}T${p(h)}:${p(mi)}`;
    }
```

Replace `_dateStr` and the whole `_tzAbbr`, with the comments above each, with:

```js
    // OPNsense/Unix `date` format on the firewall's clock, e.g. "Tue Sep 22 12:08:17 AEST 2026"
    _dateStr(ts) {
        const [y, mo, d, h, mi, s, dow] = wallParts(ts, this.tz);
        const p = (x) => String(x).padStart(2, '0');
        return `${WEEKDAYS[dow]} ${MONTHS[mo - 1]} ${p(d)} ${p(h)}:${p(mi)}:${p(s)} ${zoneAbbr(ts, this.tz)} ${y}`;
    }

    // A span's end: one on a midnight prints as the second before, so a day reads 00:00:00 → 23:59:59
    _endStr(ts) {
        return this._dateStr(localMidnight(ts, 0, this.tz) === ts ? ts - 1 : ts);
    }
```

Captions print their ends with `_endStr`. Make four one-word changes:
- in `_rawCaption`'s no-flows return:
  `${this._dateStr(q.from)}  →  ${this._dateStr(q.to)}${tail}` becomes
  `${this._dateStr(q.from)}  →  ${this._endStr(q.to)}${tail}`;
- in `_rawCaption`'s last line:
  `${this._dateStr(a)}  →  ${this._dateStr(b)}${tail}` becomes
  `${this._dateStr(a)}  →  ${this._endStr(b)}${tail}`;
- in `_exportCaption`'s nothing-recorded return:
  `${this._dateStr(q.from)}  →  ${this._dateStr(q.to)}${scopeTxt}` becomes
  `${this._dateStr(q.from)}  →  ${this._endStr(q.to)}${scopeTxt}`;
- in `_exportCaption`'s `const text = …`:
  `${this._dateStr(p.start)}  →  ${this._dateStr(p.end)}${scopeTxt}` becomes
  `${this._dateStr(p.start)}  →  ${this._endStr(p.end)}${scopeTxt}`.

Replace `_hm` and `_span`, with the comment above `_span`, with:

```js
    _hm(ts, secs = false) {
        const [, , , h, mi, s] = wallParts(ts, this.tz);
        const p = (x) => String(x).padStart(2, '0');
        return `${p(h)}:${p(mi)}` + (secs ? `:${p(s)}` : '');
    }

    // "Sat 19 Sep 10:00", on the firewall's clock
    _dayTime(ts, secs = false) {
        const [, mo, d, , , , dow] = wallParts(ts, this.tz);
        return `${WEEKDAYS[dow]} ${d} ${MONTHS[mo - 1]} ${this._hm(ts, secs)}`;
    }

    // "10:00 → 19:08" within today, otherwise with the dates; in seconds when
    // both ends fall in the same minute. An end on a midnight is the second before.
    _span(a, b, now) {
        if (b > a && localMidnight(b, 0, this.tz) === b) b -= 1;
        const day = (t) => wallParts(t, this.tz).slice(0, 3).join('-');
        const secs = day(a) === day(b) && this._hm(a) === this._hm(b);
        if (day(a) === day(now) && day(b) === day(now)) return `${this._hm(a, secs)} → ${this._hm(b, secs)}`;
        return `${this._dayTime(a, secs)} → ${this._dayTime(b, secs)}`;
    }
```

Just before `    async _loadNetworks() {`, add:

```js
    // The time zone set on the firewall (System: Settings: General), once per page
    // load, before anything is shown: every midnight, custom field and caption
    // follows it (spec 2026-09-24-keep-flow-log §7). Through the dashboard's
    // ajaxCall, as the networks are. No answer, or a zone this browser's Intl
    // does not know, leaves the browser's own.
    async _loadZone() {
        let tz;
        try {
            const r = await this.ajaxCall('/api/topdevices/flows/zone');
            tz = r && typeof r.timezone === 'string' ? r.timezone : undefined;
            if (tz) new Intl.DateTimeFormat('en-US', { timeZone: tz });   // throws for a zone it does not know
        } catch (e) { tz = undefined; }
        this.tz = tz;
    }

```

In `onMarkupRendered`, replace

```js
        await this._loadNetworks();
        this._fillNetworkSelect();
```

with

```js
        await this._loadZone();
        await this._loadNetworks();
        this._fillNetworkSelect();
```

`ajaxCall`, not `$.ajax`, because the request is small, and because the Live harness's
`$.ajax` never settles, which would hang its `onMarkupRendered` test. `_loadNetworks`
works the same way.

- [ ] **Step 5: Run the tests to see them pass.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 123`, `ℹ pass 123`, `ℹ fail 0`. The older Sydney, Kolkata and UTC
tests now run in the configured zone and still pass.

Check that the widget has no local-time call left:
`grep -n -E "getHours|getDay\(|toDateString|toTimeString|getTimezoneOffset|_tzAbbr" src/opnsense/www/js/widgets/TopDevices.js`
Expected: no output.

- [ ] **Step 6: Mutants.** In `tests/mutate_widget.mjs`, two existing mutants anchor on
replaced lines. Their new strings:

```js
    ['Yesterday taken as 24 hours back', "            case 'yesterday': return [localMidnight(s, 1, this.tz), mid];",
     "            case 'yesterday': return [mid - DAY, mid];"],
```

```js
    ['the raw caption shows the range asked for', '        return { text: `${this._dateStr(a)}  →  ${this._endStr(b)}${tail}`, note };',
     '        return { text: `${this._dateStr(q.from)}  →  ${this._endStr(q.to)}${tail}`, note };'],
```

Append to `mutants`, before its closing `];`:

```js
    ["midnights in the browser's zone", "        const mid = localMidnight(s, 0, this.tz);", "        const mid = localMidnight(s, 0);"],
    ["yesterday from the browser's calendar", "            case 'yesterday': return [localMidnight(s, 1, this.tz), mid];", "            case 'yesterday': return [localMidnight(s, 1), mid];"],
    ["custom fields read in the browser's zone", "+(x[6] || 0), this.tz) : null;", "+(x[6] || 0)) : null;"],
    ["custom fields written in the browser's zone", "        const [y, mo, d, h, mi] = wallParts(ts, this.tz);", "        const [y, mo, d, h, mi] = wallParts(ts);"],
    ["captions in the browser's zone", "        const [y, mo, d, h, mi, s, dow] = wallParts(ts, this.tz);", "        const [y, mo, d, h, mi, s, dow] = wallParts(ts);"],
    ["a day's end shown as the next midnight", "        return this._dateStr(localMidnight(ts, 0, this.tz) === ts ? ts - 1 : ts);", "        return this._dateStr(ts);"],
    ["a note's span ending on the next midnight", "        if (b > a && localMidnight(b, 0, this.tz) === b) b -= 1;\n", ""],
    ["a zone Intl does not know kept", "            if (tz) new Intl.DateTimeFormat('en-US', { timeZone: tz });   // throws for a zone it does not know\n", ""],
    ["the zone never asked", "        await this._loadZone();\n", ""],
```

Run: `node tests/mutate_widget.mjs`
Expected: 116 `killed` lines, exit status 0.

- [ ] **Step 7: Commit.**

```bash
git add src/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php \
        src/opnsense/www/js/widgets/TopDevices.js tests/netflow_ranges.test.mjs tests/live_view.test.mjs \
        tests/mutate_widget.mjs
git commit -m "0.3.0: the widget follows the firewall's time zone; a day ends at 23:59:59"
```

---

### Task 6: The widget reads 50 hours from the raw log, and passes over short answers

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js`
- Modify: `tests/netflow_ranges.test.mjs`
- Modify: `tests/mutate_widget.mjs`

**Interfaces:**
- Consumes: Task 3's `all.from`; Task 5's `_endStr`.
- Produces:
  - `export const RAW_REACH = 50 * 3600`;
  - `rawRange(fromS, nowS)` true for `nowS − RAW_REACH ≤ fromS < nowS`;
  - `_load` uses a raw answer only when `resp.all.from <= from`. Otherwise it reads
    NetFlow's records, with `state.fallback` true only when the request failed.

- [ ] **Step 1: Update and write the tests.** In `tests/netflow_ranges.test.mjs`, replace

```js
test('a range starting within the last day, and not in the future, is read from the raw log', () => {
    assert.equal(m.rawRange(NOW - 86400, NOW), true);
    assert.equal(m.rawRange(NOW - 86401, NOW), false);
    assert.equal(m.rawRange(NOW, NOW), false);
});
```

with

```js
test('a range starting within the last 50 hours, and not in the future, is read from the raw log', () => {
    assert.equal(m.rawRange(NOW - 180000, NOW), true);
    assert.equal(m.rawRange(NOW - 180001, NOW), false);
    assert.equal(m.rawRange(NOW, NOW), false);
});
```

and replace

```js
test('Yesterday and Last 7 days still read NetFlow\'s records', async () => {
    for (const range of ['yesterday', '7d']) {
        const before = requests.length;
        await load(range);
        assert.ok(!requests.slice(before).some(u => u.startsWith(`${FLOWS_API}/`)), range);
    }
});
```

with

```js
test('Last 7 days still reads NetFlow\'s records', async () => {
    const before = requests.length;
    await load('7d');
    assert.ok(!requests.slice(before).some(u => u.startsWith(`${FLOWS_API}/`)));
});

test('Yesterday reads the raw log for exactly the day, 00:00:00 to 23:59:59', async () => {
    const { w, made } = await loadRaw('yesterday', () => totalsAnswer(S(14, 0, 21), S(14, 0, 22)));
    assert.deepEqual(made, [`${FLOWS_API}/totals/${S(14, 0, 21)}/${S(14, 0, 22)}`]);
    assert.deepEqual(w._windowCaption(), {
        text: 'Tue Sep 22 00:00:00 AEST 2026  →  Tue Sep 22 23:59:59 AEST 2026 · all traffic', note: null });
});

test("a raw answer short of the range's start is read from NetFlow's records instead, with no note", async () => {
    // the kept log is still filling: all traffic only from Tue 12:51
    const w = widget('yesterday');
    reply = (url) => (url.startsWith(`${FLOWS_API}/`) ? totalsAnswer(S(2, 51, 22), S(14, 0, 22)) : serve(url));
    const before = requests.length;
    await w._load(NOW * 1000);
    assert.deepEqual(requests.slice(before), [`${FLOWS_API}/totals/${S(14, 0, 21)}/${S(14, 0, 22)}`,
                                              `${EXPORT}/FlowSourceAddrTotals/${S(0, 0, 22)}/${S(0, 0, 23)}/86400`]);
    assert.equal(w.state.request.raw, undefined);
    assert.equal(w._windowCaption().note, 'History older than a day is kept per day (days start at 10:00)');
});
```

- [ ] **Step 2: Run them to see them fail.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 125`, `ℹ fail 3`:
- the 50-hour `rawRange` test, where `rawRange(NOW - 180000, NOW)` is `false`;
- `Yesterday reads the raw log …`, which asked NetFlow's export instead;
- `a raw answer short of the range's start …`, which made no raw request.

- [ ] **Step 3: Implement.** In `TopDevices.js`, replace

```js
// A range that starts within the last day - and not in the future, where nothing
// is recorded yet - is read from NetFlow's raw flow log through the plugin's flows
// endpoints: exact to the second, both scopes in one answer (spec
// 2026-09-23-raw-flow-ranges §4). Older ranges read NetFlow's records (nfPlan).
export function rawRange(fromS, nowS) {
    return fromS >= nowS - DAY && fromS < nowS;
}
```

with

```js
// A range that starts within the last 50 hours - Yesterday at any hour, even on a
// day with a clock change - and not in the future, where nothing is recorded yet,
// is read from NetFlow's raw flow log, which keep.py keeps for two days, through
// the plugin's flows endpoints: exact to the second, both scopes in one answer
// (specs 2026-09-23-raw-flow-ranges §4, 2026-09-24-keep-flow-log §4). Older
// ranges read NetFlow's records (nfPlan).
export const RAW_REACH = 50 * 3600;
export function rawRange(fromS, nowS) {
    return fromS >= nowS - RAW_REACH && fromS < nowS;
}
```

In `_load`, replace `            if (resp) {` (the line after
`            if (token !== this._loadToken) return false;` in the raw branch) with
`            if (resp && resp.all.from <= from) {`. Then replace

```js
            fallback = true;                     // say so, and read NetFlow's records instead
```

with

```js
            // A failed read says so. A short one - all traffic not reaching back to
            // the range's start, as while the kept log fills or past a missing file -
            // is simply a range for NetFlow's records (spec 2026-09-24-keep-flow-log §8).
            fallback = !resp;
```

- [ ] **Step 4: Run the tests to see them pass.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 125`, `ℹ pass 125`, `ℹ fail 0`.

- [ ] **Step 5: Mutants.** In `tests/mutate_widget.mjs`, four existing mutants anchor on
changed lines. Their new strings:

```js
    ['the raw log used for ranges older than 50 hours', '    return fromS >= nowS - RAW_REACH && fromS < nowS;', '    return fromS < nowS;'],
    ['the raw log asked for a range in the future', '    return fromS >= nowS - RAW_REACH && fromS < nowS;', '    return fromS >= nowS - RAW_REACH;'],
```

```js
    ['a stale raw answer lands', '            if (token !== this._loadToken) return false;\n            if (resp && resp.all.from <= from) {', '            if (resp && resp.all.from <= from) {'],
    ['a failed raw read shows nothing', '            fallback = !resp;', '            return false;'],
```

The first was named `'the raw log used for ranges older than a day'`. Append to
`mutants`, before its closing `];`:

```js
    ['the raw reach left at a day', 'export const RAW_REACH = 50 * 3600;', 'export const RAW_REACH = DAY;'],
    ['a short raw answer shown as if whole', '            if (resp && resp.all.from <= from) {', '            if (resp) {'],
    ['a short raw answer called a failure', '            fallback = !resp;', '            fallback = true;'],
```

Run: `node tests/mutate_widget.mjs`
Expected: 119 `killed` lines, exit status 0.

- [ ] **Step 6: Commit.**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/netflow_ranges.test.mjs tests/mutate_widget.mjs
git commit -m "0.3.0: the widget reads 50 hours from the raw log, and NetFlow's records for a short answer"
```

---

### Task 7: Captions start at the oldest bucket NetFlow returned

**Files:**
- Modify: `src/opnsense/www/js/widgets/TopDevices.js`
- Modify: `tests/netflow_ranges.test.mjs`
- Modify: `tests/mutate_widget.mjs`

**Interfaces:**
- Consumes: Task 4's `offsetAt`; Task 5's `_endStr`, `_dayTime` and `this.tz`.
- Produces:
  - `export function exportStart(text, nowS, tz) -> epoch seconds | null`;
  - each export row gains `start`;
  - `state.first`: the oldest bucket that came back, or `null` when none did, or
    `undefined` when none is dated.

- [ ] **Step 1: Write the failing tests.** In `tests/netflow_ranges.test.mjs`, just before
`// --- rows to download and upload ---------------------------------------------`, add:

```js
// --- what NetFlow's records actually hold ------------------------------------------

test("a bucket's start is read back as core prints it: its UTC time plus the firewall's offset now", () => {
    assert.equal(m.exportStart('2026/09/19 10:00:00', NOW, 'Australia/Brisbane'), Date.UTC(2026, 8, 19) / 1000);
    assert.equal(m.exportStart('', NOW, 'Australia/Brisbane'), null);
    // core applies today's offset to every row (export_details.py): after Sydney's
    // clock change a September bucket prints an hour late, and still reads back right
    assert.equal(m.exportStart('2026/09/19 11:00:00', U(10, 5, 1), 'Australia/Sydney'), Date.UTC(2026, 8, 19) / 1000);
});

test('a range reaching back before NetFlow began starts at the oldest bucket, and says so', async () => {
    // NetFlow began collecting on the day starting Sat 19 Sep 10:00: nothing older comes back
    const days = [19, 20, 21, 22, 23].map(d => `2026/09/${d} 10:00:00`);
    const w = widget('7d');
    reply = (url) => (url.includes('/FlowSourceAddrTotals/')
        ? csv(totalsRows(FLOWS).flatMap(r => days.map(t => ({ ...r, start_time: t }))), TOTALS_HEAD) : null);
    await w._load(NOW * 1000);
    assert.deepEqual(w._windowCaption(), {
        text: 'Sat Sep 19 10:00:00 AEST 2026  →  Wed Sep 23 19:08:54 AEST 2026 · all traffic',
        note: 'History older than a day is kept per day (days start at 10:00) · NetFlow has no records before Sat 19 Sep 10:00' });
});

test('an export with no rows at all says there is no NetFlow data', async () => {
    const w = widget('7d');
    reply = (url) => (url.includes('/FlowSourceAddrTotals/') ? csv([], TOTALS_HEAD) : null);
    await w._load(NOW * 1000);
    assert.deepEqual(w._windowCaption(), {
        text: 'Wed Sep 16 19:08:54 AEST 2026  →  Wed Sep 23 19:08:54 AEST 2026 · all traffic',
        note: 'No NetFlow data for this range' });
});
```

- [ ] **Step 2: Run them to see them fail.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 128`, `ℹ fail 3`:
- `m.exportStart is not a function`;
- the week's caption starts at `Wed Sep 16 10:00:00`;
- the empty export's caption is the plan's span, with the per-day note.

- [ ] **Step 3: Implement.** In `TopDevices.js`, insert just before
`export default class TopDevices extends BaseWidget {`:

```js
// A bucket's start as core's export prints it - its UTC time plus the offset the
// firewall has at the moment of the export (export_details.py applies today's
// offset to every row) - in epoch seconds; null for anything else.
export function exportStart(text, nowS, tz) {
    const x = /^(\d{4})\/(\d{2})\/(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(text || '');
    return x ? Date.UTC(+x[1], +x[2] - 1, +x[3], +x[4], +x[5], +x[6]) / 1000 - offsetAt(nowS, tz) : null;
}

```

In `_export`, replace

```js
        const rows = [];
        lines.forEach((line) => {
            if (!line) return;
            const c = line.split(',');
            rows.push({
                src: c[ix.src_addr], dst: c[ix.dst_addr],
                port: c[ix.service_port], dir: c[ix.direction],
                iface: c[ix['if']],
                octets: parseFloat(c[ix.octets]) || 0
            });
        });
```

with

```js
        const rows = [], now = Date.now() / 1000;
        lines.forEach((line) => {
            if (!line) return;
            const c = line.split(',');
            rows.push({
                src: c[ix.src_addr], dst: c[ix.dst_addr],
                port: c[ix.service_port], dir: c[ix.direction],
                iface: c[ix['if']],
                octets: parseFloat(c[ix.octets]) || 0,
                start: exportStart(c[ix.start_time], now, this.tz)
            });
        });
```

In `_load`'s export path, after `        this.state.plan = plan;`, add:

```js
        // the oldest bucket that came back: null when nothing did, undefined when none is dated
        const dated = flows.map(r => r.start).filter(t => t !== null);
        this.state.first = !flows.length ? null : dated.length ? dated.reduce((a, b) => Math.min(a, b)) : undefined;
```

In `_exportCaption`, replace

```js
        const text = `${this._dateStr(p.start)}  →  ${this._endStr(p.end)}${scopeTxt}`;
        let note = null;
```

with

```js
        if (this.state.first === null) {         // the export came back empty: nothing recorded then
            return { text: `${this._dateStr(q.from)}  →  ${this._endStr(q.to)}${scopeTxt}`,
                     note: 'No NetFlow data for this range' };
        }
        // from the oldest bucket that came back: NetFlow may have begun collecting later
        const start = this.state.first > p.start ? this.state.first : p.start;
        const text = `${this._dateStr(start)}  →  ${this._endStr(p.end)}${scopeTxt}`;
        let note = null;
```

replace `            const span = this._span(p.start, p.end, q.now);` with
`            const span = this._span(start, p.end, q.now);`, and replace the function's
end,

```js
                : `History older than a day is ${perDay}${p.clipped ? `: these cover ${span}` : ''}`;
        }
        return { text, note };
```

with

```js
                : `History older than a day is ${perDay}${p.clipped ? `: these cover ${span}` : ''}`;
        }
        if (start > p.start) {
            const none = `NetFlow has no records before ${this._dayTime(start)}`;
            note = note ? `${note} · ${none}` : none;
        }
        return { text, note };
```

- [ ] **Step 4: Run the tests to see them pass.**

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 128`, `ℹ pass 128`, `ℹ fail 0`.

- [ ] **Step 5: Mutants.** Append to `mutants` in `tests/mutate_widget.mjs`, before its closing `];`:

```js
    ["the caption starts where the plan did, before any data", "        const start = this.state.first > p.start ? this.state.first : p.start;", "        const start = p.start;"],
    ["no word that NetFlow began later", "            const none = `NetFlow has no records before ${this._dayTime(start)}`;\n            note = note ? `${note} · ${none}` : none;\n", ""],
    ["an empty export shown as a span", "        if (this.state.first === null) {", "        if (false) {"],
    ["bucket starts read with the offset of their own day", " / 1000 - offsetAt(nowS, tz) : null;", " / 1000 - offsetAt(Date.UTC(+x[1], +x[2] - 1, +x[3], +x[4], +x[5], +x[6]) / 1000, tz) : null;"],
    ["the newest bucket taken for the oldest", "dated.reduce((a, b) => Math.min(a, b))", "dated.reduce((a, b) => Math.max(a, b))"],
```

Run: `node tests/mutate_widget.mjs`
Expected: 124 `killed` lines, exit status 0.

- [ ] **Step 6: Commit.**

```bash
git add src/opnsense/www/js/widgets/TopDevices.js tests/netflow_ranges.test.mjs tests/mutate_widget.mjs
git commit -m "0.3.0: a caption from NetFlow's records starts at the oldest bucket that came back"
```

---

### Task 8: The installer: `keep.py`, its cron file, and a first run

**Files:**
- Create: `src/etc/cron.d/topdevices`
- Modify: `install.sh`
- Modify: `tests/test_install.sh`

**Interfaces:**
- Consumes: Task 1's `keep.py`.
- Produces: 10 installed files, and the lines `keep.py not run (ROOT=…)` in a dry run and
  `flow log kept in /var/log/topdevices (cron: every 10 minutes)` on a firewall.

- [ ] **Step 1: Write the failing test.** In `tests/test_install.sh`, add two lines to the
`LIST` here-document, after the `FlowsController.php` line:

```
src/opnsense/scripts/topdevices/keep.py|/usr/local/opnsense/scripts/topdevices/keep.py|755
src/etc/cron.d/topdevices|/usr/local/etc/cron.d/topdevices|644
```

After the line
`[ -z "$(find "$R" -name '*.tdnew' -o -name '.actions_topdevices.new')" ] || fail "staging files left behind"`,
add:

```sh
# the keep job: every 10 minutes as root; a dry run never runs it, nor creates the kept log
grep -qxF "$(printf '*/10\t*\t*\t*\t*\troot\t/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1')" \
    "$R/usr/local/etc/cron.d/topdevices" || fail "the cron line is wrong"
echo "$first" | grep -q 'keep.py not run (ROOT=' || fail "the dry run did not say it skipped keep.py"
[ ! -e "$R/var/log/topdevices" ] || fail "the dry run created the kept log"
```

- [ ] **Step 2: Run it to see it fail.**

Run: `sh tests/test_install.sh`
Expected: `FAIL: not installed: /usr/local/opnsense/scripts/topdevices/keep.py`, exit status 1.

- [ ] **Step 3: Implement.** Create the cron file with exact tabs:

```bash
mkdir -p src/etc/cron.d
printf '# TopDevices: keeps NetFlow'"'"'s rotated flow log for two days (installed by install.sh)\nSHELL=/bin/sh\n*/10\t*\t*\t*\t*\troot\t/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1\n' > src/etc/cron.d/topdevices
```

Check: `cat -vet src/etc/cron.d/topdevices` shows `*/10^I*^I*^I*^I*^Iroot^I/usr/local/opnsense/scripts/topdevices/keep.py >/dev/null 2>&1$`
as its third line.

In `install.sh`:

1. In the header, `the firewall's own public IP that counts). Eight files is survivable, but`
   becomes `the firewall's own public IP that counts). Ten files is survivable, but`.
2. After the header's two lines
   `# widget falls back to NetFlow's records meanwhile). Use the bootstrap command`
   and `# once instead.`, add:

   ```sh
   # UPGRADING FROM 0.2.0: likewise, the 0.2.0 installer only knows its eight files:
   # run through configctl or cron it installs everything but keep.py and its cron
   # file, until the next run (Yesterday is read from NetFlow's records meanwhile).
   # Use the bootstrap command once instead.
   ```

3. In `FILES`, after the `FlowsController.php` line, add:

   ```
   $P/scripts/topdevices/keep.py|$SCRIPTS/keep.py
   src/etc/cron.d/topdevices|/usr/local/etc/cron.d/topdevices
   ```

4. After `echo "widget installed"`, add:

   ```sh

   # --- keep NetFlow's flow log from now on: cron runs keep.py every 10 minutes ---
   # Once now, so the kept log starts with core's current files (spec 2026-09-24 §9).
   if [ -z "$ROOT" ]; then
       if "$SCRIPTS/keep.py"; then
           echo "flow log kept in /var/log/topdevices (cron: every 10 minutes)"
       else
           echo "WARNING: keep.py failed (see the system log); cron tries again every 10 minutes" >&2
       fi
   else
       echo "keep.py not run (ROOT=$ROOT)"
   fi
   ```

The existing loops need no change. They make the destination's directory
(`/usr/local/etc/cron.d`), and give files not ending in `.sh` or `.py` mode 0644.
The "missing from source" check now also refuses a tree without `keep.py` or the cron
file, such as `main`'s 0.2.0 tree once merged.

- [ ] **Step 4: Run the test to see it pass.**

Run: `sh tests/test_install.sh`
Expected: `install dry run: OK`.

- [ ] **Step 5: Commit.**

```bash
git add src/etc/cron.d/topdevices install.sh tests/test_install.sh
git commit -m "0.3.0: the installer adds keep.py and its cron job, and runs it once"
```

---

### Task 9: A whole-day check against core, docs, and version 0.3.0

**Files:**
- Modify: `tests/parity_flows.py`
- Modify: `README.md`, `pkg-descr`, `Makefile`
- Modify: `src/opnsense/scripts/topdevices/flows.py` (`VERSION`, docstring) and
  `src/opnsense/scripts/topdevices/live.py` (`VERSION`)

**Interfaces:**
- Consumes: Task 2's `flows._names`, `flows.LOG` and `flows.open_log`; Task 3's
  `answer_totals`.
- Produces:
  - `parity_flows.sums(flows, net, totals_rows, details_rows)`;
  - `parity_flows.day_parity(flows, net, now) -> float | None`;
  - Yesterday in the configd timings.

- [ ] **Step 1: The parity script.** In `tests/parity_flows.py`:

Replace the module docstring with:

```python
"""On the firewall: flows.py against core's own parser and aggregators, over the
live raw flow log, and how long flows.py takes as configd runs it. Once the kept
log reaches yesterday's midnight, also over that whole day (a few minutes: core's
aggregators take their time).

    python3 tests/parity_flows.py        (as root, from the extracted branch tarball)

Prints counts, timings and agreement only - no addresses. Exit status 0 when every
comparison agrees to under 1 byte per device.
"""
```

After the `gap()` function, add:

```python


def sums(flows, net, totals_rows, details_rows):
    """Per-device [down, up] from core's scratch aggregates: all traffic from its
    totals ('out' rows are bytes delivered to the address), internet only from its
    details (keyed on dst_addr, rows on an upstream interface)."""
    core_all, core_inet = {}, {}
    for row in totals_rows:
        ip = flows._ip4(row['src_addr'])
        if ip is not None and net.is_device(ip):
            t = core_all.setdefault(ip, [0.0, 0.0])
            t[0 if row['direction'] == 'out' else 1] += float(row['octets'] or 0)
    for row in details_rows:
        ip = flows._ip4(row['dst_addr'])
        if ip is not None and net.is_device(ip) and row['if'] in net.upstream_names:
            t = core_inet.setdefault(ip, [0.0, 0.0])
            t[1 if row['direction'] == 'out' else 0] += float(row['octets'] or 0)
    return core_all, core_inet


def day_parity(flows, net, now):
    """Yesterday, midnight to midnight on the firewall's clock: flows.py against
    core's own parser and aggregators over the same files, core's and the kept
    ones alike (spec 2026-09-24-keep-flow-log §13). The largest per-device gap, or
    None while the log does not reach back to yesterday's midnight."""
    from lib.flowparser import FlowParser
    from lib.parse import Interfaces
    from lib.aggregates.source import FlowSourceAddrDetails, FlowSourceAddrTotals
    lt = time.localtime(now)
    end = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - 1, 0, 0, 0, 0, 0, -1)))
    opened = flows.open_log(flows.LOG)
    try:
        L = flows.log_from(opened)
        if L is None or L > start:
            print("whole day: the log is complete only from %s, after yesterday's midnight: nothing to compare yet"
                  % (time.ctime(L) if L else 'nowhere'))
            return None
        # core's buckets are UTC hours; where this zone's midnights fall between them, 5-minute ones
        res = 3600 if start % 3600 == 0 and end % 3600 == 0 else 300
        paths, seen = [], set()
        for path in flows._names(flows.LOG):
            try:
                st = os.stat(path)
            except FileNotFoundError:
                continue
            if (st.st_dev, st.st_ino) not in seen and st.st_mtime >= start:    # the others ended before the day
                seen.add((st.st_dev, st.st_ino))
                paths.append(path)
        tmp = tempfile.mkdtemp(prefix='parity_day.')
        try:
            totals, details = FlowSourceAddrTotals(res, tmp), FlowSourceAddrDetails(res, tmp)
            interfaces, n = Interfaces(), 0
            for path in paths:
                for r in FlowParser(path, start):                 # the records received from the day's start
                    r['if_in'] = interfaces.if_device(r['if_ndx_in'])
                    r['if_out'] = interfaces.if_device(r['if_ndx_out'])
                    totals.add(copy.copy(r))
                    details.add(copy.copy(r))
                    n += 1
            totals.commit()
            details.commit()
            core_all, core_inet = sums(flows, net, totals.get_data(start, end), details.get_data(start, end))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        workers = max(1, (os.cpu_count() or 2) // 2)
        a = flows.answer_totals(start, end, now, opened, net, flows.core_hourly, workers)
    finally:
        flows.close_log(opened)
    ours_all = {flows._ip4(k): v[:2] for k, v in a['devices'].items()}
    ours_inet = {flows._ip4(k): v[2:] for k, v in a['devices'].items() if v[2] or v[3]}
    dev_a, gap_a = gap(ours_all, core_all)
    dev_i, gap_i = gap(ours_inet, core_inet)
    print('whole day %s: core read %d records from %d files; all traffic %d devices, largest gap %.3f B; '
          'internet only %d devices, largest gap %.3f B'
          % (time.strftime('%a %d %b', time.localtime(start)), n, len(paths), dev_a, gap_a, dev_i, gap_i))
    return max(gap_a, gap_i)
```

In `main()`, replace

```python
        totals.commit()
        details.commit()
        core_all, core_inet = {}, {}
        for row in totals.get_data(hour, hour + 3600):
            ip = flows._ip4(row['src_addr'])
            if ip is not None and net.is_device(ip):
                t = core_all.setdefault(ip, [0.0, 0.0])
                t[0 if row['direction'] == 'out' else 1] += float(row['octets'] or 0)
        for row in details.get_data(*five):
            ip = flows._ip4(row['dst_addr'])
            if ip is not None and net.is_device(ip) and row['if'] in net.upstream_names:
                t = core_inet.setdefault(ip, [0.0, 0.0])
                t[1 if row['direction'] == 'out' else 0] += float(row['octets'] or 0)
    finally:
```

with

```python
        totals.commit()
        details.commit()
        core_all, core_inet = sums(flows, net, totals.get_data(hour, hour + 3600), details.get_data(*five))
    finally:
```

replace

```python
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    for label, frm in (('Last hour', now - 3600), ('Today', midnight), ('Last 24 hours', now - 86400)):
        t = time.monotonic()
        out = subprocess.run(['/usr/local/sbin/configctl', 'topdevices', 'flows', 'totals', str(frm), str(now)],
                             capture_output=True, text=True).stdout
```

with

```python
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    yesterday = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - 1, 0, 0, 0, 0, 0, -1)))
    for label, frm, to in (('Last hour', now - 3600, now), ('Today', midnight, now), ('Yesterday', yesterday, midnight),
                           ('Last 24 hours', now - 86400, now)):
        t = time.monotonic()
        out = subprocess.run(['/usr/local/sbin/configctl', 'topdevices', 'flows', 'totals', str(frm), str(to)],
                             capture_output=True, text=True).stdout
```

replace

```python
        print('%-14s via configd %.2f s (script %d ms, %d files, %d workers)%s'
              % (label, wall, ans['cost_ms'], ans['files'], ans['workers'],
                 '' if ans['inet']['from'] <= frm else ', internet only from the log start'))
```

with

```python
        short = ('' if ans['all']['from'] <= frm else ', all traffic from %s' % time.ctime(ans['all']['from'])) + \
                ('' if ans['inet']['from'] <= frm else ', internet only from the log start')
        print('%-14s via configd %.2f s (script %d ms, %d files, %d workers)%s'
              % (label, wall, ans['cost_ms'], ans['files'], ans['workers'], short))
```

and replace its last lines

```python
    print('records in the newest file: %d, %d of them with ports' % (total, with_ports))
    flows.close_log(opened)
    return 0 if gap_a < 1 and gap_i < 1 else 1
```

with

```python
    print('records in the newest file: %d, %d of them with ports' % (total, with_ports))
    flows.close_log(opened)
    day = day_parity(flows, net, int(time.time()))
    return 0 if gap_a < 1 and gap_i < 1 and (day is None or day < 1) else 1
```

`day_parity` passes `flows.LOG` to `open_log` explicitly. The default is bound when
`open_log` is defined, and the function names `flows.LOG` again in `_names`. The
off-firewall check in Step 2 caught that difference in the prototype.

Run: `python3 -m py_compile tests/parity_flows.py`
Expected: no output.

- [ ] **Step 2: Exercise the day check off the firewall.** Save this as `daycheck.py` in the
plan's SDD workspace (`.superpowers/sdd/2026-09-24-keep-flow-log/`, git-ignored). It is a
throwaway check, not committed:

```python
# Exercise tests/parity_flows.py's day_parity() off the firewall: a synthetic log in
# core's format across core's files and kept ones, core's own lib from a checkout.
# Usage: python3 daycheck.py <repo> <core>/src/opnsense/scripts/netflow
import importlib.util, os, random, sys, tempfile, time, warnings
REPO, CORE = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(REPO, 'tests'))
import test_flows as tf                                         # its flow(), synthetic_log(), NET, IFNAME
flows = sys.modules['flows']
sys.path.insert(0, CORE)
warnings.simplefilter('ignore', DeprecationWarning)
import lib.parse
class Interfaces:                                               # ifinfo is FreeBSD's: the synthetic numbering instead
    def if_device(self, i): return tf.IFNAME.get(i, str(i))
lib.parse.Interfaces = Interfaces
spec = importlib.util.spec_from_file_location('parity_flows', os.path.join(REPO, 'tests/parity_flows.py'))
parity = importlib.util.module_from_spec(spec); spec.loader.exec_module(parity)

tmp = tempfile.mkdtemp()
flows.LOG = os.path.join(tmp, 'flowd.log')
now = time.time()
lt = time.localtime(now)
start = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - 1, 0, 0, 0, 0, 0, -1)))
rnd = random.Random(5)
early = tf.flow('8.8.8.8', '192.168.1.11', 1, recv=start - 100, start=start - 100, end=start - 100, if_in=1, if_out=2)
a = tf.synthetic_log(20000, start + 3000, rnd)                  # 800 s of flows, 25 a second
b = tf.synthetic_log(20000, start + 3800, rnd)
c = tf.synthetic_log(20000, start + 4600, rnd)
kept = os.path.join(tmp, 'topdevices'); os.mkdir(kept)
tf.write_log(kept, [('flowd.1.1', early + a, start + 3800)])   # core has deleted it
tf.write_log(tmp, [('flowd.log.000001', b, start + 4600), ('flowd.log', c, start + 5400)])
os.link(os.path.join(tmp, 'flowd.log.000001'), os.path.join(kept, 'flowd.2.2'))   # a second name: read once
day = parity.day_parity(flows, tf.NET, int(now))
print('day_parity returned', day)
```

Run: `python3 .superpowers/sdd/2026-09-24-keep-flow-log/daycheck.py "$PWD" /private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review/src/opnsense/scripts/netflow`
Expected, as observed in the prototype on this Mac, where the zone is Australia/Brisbane:

```
whole day <yesterday, e.g. Wed 23 Sep>: core read 58804 records from 3 files; all traffic 31 devices, largest gap 0.499 B; internet only 31 devices, largest gap 0.486 B
day_parity returned 0.4994935989379883
```

The log has four names but three files, because the one with two names counts once.
Both gaps are under 0.5 B: the answer's rounding to whole bytes. The synthetic log is
seeded, so these figures repeat.

- [ ] **Step 3: Docs and version.** Save this as `docs_edit.py` in the same workspace and run
it: `python3 .superpowers/sdd/2026-09-24-keep-flow-log/docs_edit.py .`. Every
replacement asserts that its anchor is found exactly once.

```python
# Task 9's docs and version edits, as exact replacements: python3 docs_edit.py <tree>
import pathlib, sys
root = pathlib.Path(sys.argv[1])
def edit(path, pairs):
    p = root / path; s = p.read_text()
    for old, new in pairs:
        assert s.count(old) == 1, (path, old[:80])
        s = s.replace(old, new)
    p.write_text(s)

edit('README.md', [
('''- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range. One starting within the last day is exact to the second, read from
  NetFlow's raw flow log; older ones snap to the buckets NetFlow keeps. The caption
  shows the exact span (see *Upstream API limitations*)''',
'''- **Date range** — last hour, 24 hours, today, yesterday, 7 days, or a custom
  from/to range. One starting within the last 50 hours, Yesterday included, is exact
  to the second, read from NetFlow's raw flow log, which the plugin keeps for two days
  (see *Keeping the flow log*); older ones snap to the buckets NetFlow keeps. The
  caption shows the exact span (see *Upstream API limitations*)'''),
('''The active window is shown in OPNsense's own date format, to the second and with
the timezone, e.g. `Tue Sep 22 12:11:42 AEST 2026`.''',
'''The active window is shown in OPNsense's own date format, to the second and with
the timezone, e.g. `Tue Sep 22 12:11:42 AEST 2026`. Midnights, the custom range
fields and every caption follow the time zone set under **System → Settings →
General**, whatever the browser's own; the browser's is used only when the firewall
does not say (before 0.3.0 it always was). A day reads `00:00:00 → 23:59:59`.'''),
('''configuration, never hardcoded: for Live and for ranges starting within the last
day, every interface with a default route or a public address; for older ranges,''',
'''configuration, never hardcoded: for Live and for ranges starting within the last
50 hours, every interface with a default route or a public address; for older ranges,'''),
('''  its address, so a WAN with a private address (double NAT) works. Since 0.2.0
  so do NetFlow ranges starting within the last day; older ranges still use the
  address-only rule.''',
'''  its address, so a WAN with a private address (double NAT) works. Since 0.2.0
  so do NetFlow ranges starting within the last day, and since 0.3.0 within the last
  50 hours; older ranges still use the address-only rule.'''),
('''   the panel says so, and its download and upload stay the table's.''',
'''   the panel says so, and its download and upload stay the table's. A range
   reaching back before NetFlow began collecting starts at the oldest bucket that
   came back, with the note *"NetFlow has no records before Sat 19 Sep 10:00"*; one
   with nothing at all says *"No NetFlow data for this range"*.'''),
('''3. **Since 0.2.0, recent ranges read the raw flow log instead.** A range that starts
   within the last day - Last hour, Today, Last 24 hours, or a custom one - is read by
   the plugin's `flows.py` from `/var/log/flowd.log` and its rotations, which hold
   every flow with its interfaces. It is exact to the second in both scopes, and
   agrees to the byte with core's own parser and aggregators run over the same log.
   A read takes about a second on up to half the firewall's cores (three on the
   reference install), and there is no rate limit: the widget refreshes slowly, and
   only while a dashboard is open. Core keeps that log by size, not time: about 110
   MB, which is roughly a day on the reference install and less on a busy network.
   Where a range reaches further back, all traffic is filled in from core's hourly
   records (the oldest hour counted in proportion), and internet only says how much
   it covers. The table above still applies to older ranges, and to recent ones when
   the raw log cannot be read (the caption says so).''',
'''3. **Since 0.2.0, recent ranges read the raw flow log instead.** A range that starts
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
   ones when the raw log cannot be read (the caption says so).'''),
('''
## Install without building
''',
'''
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
- **How long.** A kept file is deleted once its newest record is 51 hours old, and the
  directory never holds more than 1 GB, oldest first: about 250 MB on the reference
  install.
- **What it holds.** Raw flow records, readable by root only, as core's are.
- **After install** the kept log starts with what core still holds, about 22 hours
  here, so Yesterday is exact from the first midnight after install, as long as the
  install day's first records were still in core's log. Until then, and wherever the
  kept log has a gap (the job stopped for most of a day, or the cap was reached), a
  range reaching back past it is read from NetFlow's records, as before.

## Install without building
'''),
('''back to NetFlow's records, with the widget's note.
''',
'''back to NetFlow's records, with the widget's note.

**Upgrading from 0.2.0 to 0.3.0:** run the bootstrap command once, too. The 0.2.0
installer only knows its own eight files, so one run of `configctl topdevices install`
(or of the weekly job) installs everything but `keep.py` and its cron file; until a
second run, Yesterday is read from NetFlow's records.
'''),
("That builds `os-topdevices-0.2.0.pkg`; `pkg add` it on the firewall, or run",
 "That builds `os-topdevices-0.3.0.pkg`; `pkg add` it on the firewall, or run"),
('''    OPNSENSE_CORE=<core checkout> python3 -m unittest tests.test_flows -v   # flows.py against core's parser and aggregators''',
'''    OPNSENSE_CORE=<core checkout> python3 -m unittest tests.test_flows -v   # flows.py against core's parser and aggregators
    OPNSENSE_CORE=<core checkout> python3 -m unittest tests.test_keep -v    # keep.py against core's own rotation'''),
('''On the firewall, `python3 tests/parity_flows.py` compares `flows.py` with core's
own code over the live log and times it through configd.''',
'''On the firewall, `python3 tests/parity_flows.py` compares `flows.py` with core's
own code over the live log and times it through configd. Once the kept log reaches
yesterday's midnight it also compares that whole day, which takes a few minutes.'''),
('''    rm -f /usr/local/opnsense/www/js/widgets/TopDevices.js \\
          /usr/local/opnsense/www/js/widgets/Metadata/TopDevices.xml \\
          /usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf
    rm -rf /usr/local/opnsense/scripts/topdevices \\
           /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices \\
           /usr/local/opnsense/mvc/app/models/OPNsense/TopDevices''',
'''    rm -f /usr/local/opnsense/www/js/widgets/TopDevices.js \\
          /usr/local/opnsense/www/js/widgets/Metadata/TopDevices.xml \\
          /usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf \\
          /usr/local/etc/cron.d/topdevices
    rm -rf /usr/local/opnsense/scripts/topdevices \\
           /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices \\
           /usr/local/opnsense/mvc/app/models/OPNsense/TopDevices \\
           /var/log/topdevices'''),
('''the `flows` actions, which the earlier installer's actions file does not list,
and the commands above remove them.
''',
'''the `flows` actions, which the earlier installer's actions file does not list,
and the commands above remove them.

Rolling back from 0.3.0 works the same way, with that release's tag. `keep.py` and its
cron file stay behind and keep running, harmless: an earlier `flows.py` reads only
core's files. Remove them with `rm /usr/local/etc/cron.d/topdevices
/usr/local/opnsense/scripts/topdevices/keep.py && rm -r /var/log/topdevices`.
'''),
])

edit('pkg-descr', [('''Plugin Changelog
----------------

0.2.0
''', '''Plugin Changelog
----------------

0.3.0

* Yesterday runs from 00:00:00 to 23:59:59, exactly, in both scopes: the
  plugin keeps NetFlow's raw flow log for two days (hard links to the files
  core rotates out, about 250 MB here), and ranges starting within the last
  50 hours are read from it
* Midnights, the custom range fields and every caption follow the time zone
  set under System: Settings: General, not the browser's
* Internet only for Today and Last 24 hours no longer falls short late in the
  evening, when the day outgrew core's own log
* A range reaching back before NetFlow began collecting starts its caption
  at the oldest bucket NetFlow returned, and says so

0.2.0
''')])
edit('Makefile', [('PLUGIN_VERSION=\t\t0.2.0', 'PLUGIN_VERSION=\t\t0.3.0')])
edit('src/opnsense/scripts/topdevices/flows.py', [
    ("VERSION = '0.2.0'", "VERSION = '0.3.0'"),
    ('''failing exit into "Execute error". Design:
docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md
"""''', '''failing exit into "Execute error". Design:
docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md, and for the kept log
beside it and the 50-hour reach, docs/superpowers/specs/2026-09-24-keep-flow-log-design.md
"""''')])
edit('src/opnsense/scripts/topdevices/live.py', [("VERSION = '0.2.0'", "VERSION = '0.3.0'")])
print('docs and versions edited')
```

Expected: `docs and versions edited`. Then check: `grep -n "PLUGIN_VERSION" Makefile`
shows `0.3.0`, and `grep -n "^VERSION" src/opnsense/scripts/topdevices/*.py` shows
`'0.3.0'` twice.

- [ ] **Step 4: The whole suite.**

Run: `python3 -m unittest discover -s tests`
Expected: `Ran 120 tests`, `OK (skipped=8)`.

Run: `OPNSENSE_CORE=/private/tmp/claude-501/-Users-nycoagung-Desktop/108180e1-4390-441e-9a01-b70320252fd3/scratchpad/core-review python3 -m unittest discover -s tests`
Expected: `Ran 120 tests`, `OK (skipped=3)`.

Run: `node --test tests/live_view.test.mjs tests/netflow_ranges.test.mjs`
Expected: `ℹ tests 128`, `ℹ fail 0`.

Run: `sh tests/test_install.sh`
Expected: `install dry run: OK`.

Run: `python3 tests/mutate.py`
Expected: 68 killed, exit 0.

Run: `node tests/mutate_widget.mjs`
Expected: 124 killed, exit 0.

- [ ] **Step 5: Commit.**

```bash
git add tests/parity_flows.py README.md pkg-descr Makefile \
        src/opnsense/scripts/topdevices/flows.py src/opnsense/scripts/topdevices/live.py
git commit -m "0.3.0: a whole-day check against core; README, changelog and version"
```

---

### Task 10: Verification on the firewall, review, and release gates

The controller and the user do this task together. The user runs the firewall commands
over SSH (the root shell is csh); the controller runs the API and Node checks. Nothing
here merges or publishes anything without the user's explicit approval.

**Timing:** install before about 22:00 on the install day, while core's log still holds
that day's first records. Yesterday is then exact from the next midnight.

- [ ] **Step 1: Push the branch.** `git push -u origin keep-log`. SSH to GitHub needs the
sandbox lifted for this one command, as for `raw-flows`.

- [ ] **Step 2: Install, as the user, with the bootstrap command** (the branch's own
10-file installer):

```
fetch -qo /tmp/td.tgz https://codeload.github.com/nycoagung/opnsense-plugin-topdevices/tar.gz/refs/heads/keep-log && rm -rf /tmp/tdx && mkdir -p /tmp/tdx && tar -xzf /tmp/td.tgz -C /tmp/tdx && sh /tmp/tdx/opnsense-plugin-topdevices-keep-log/install.sh
```

Expected:
- 10 files listed, including `keep.py` and `topdevices`;
- `flow log kept in /var/log/topdevices (cron: every 10 minutes)`;
- `configd actions already current`: 0.3.0 adds no configd action;
- `ACL cache cleared`.

- [ ] **Step 3: Check what is installed, as the user:**

```
sha256 -r /usr/local/opnsense/scripts/topdevices/flows.py /usr/local/opnsense/scripts/topdevices/keep.py /usr/local/opnsense/scripts/topdevices/install.sh /usr/local/opnsense/scripts/topdevices/live.py /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/LiveController.php /usr/local/opnsense/mvc/app/models/OPNsense/TopDevices/ACL/ACL.xml /usr/local/opnsense/service/conf/actions.d/actions_topdevices.conf /usr/local/etc/cron.d/topdevices | cut -c1-12,65-
ls -li /var/log/flowd.log.* /var/log/topdevices/ ; ls -ld /var/log/topdevices
```

Expected:
- the hashes equal a `ROOT=<scratch> sh install.sh` dry run of the same commit on the
  admin Mac;
- the widget and its metadata equal the tree, fetched over the API as for 0.2.0;
- every rotated `flowd.log.0000NN` appears in `/var/log/topdevices/` under the same
  inode number, with a link count of 2;
- the directory is `drwx------ root`.

- [ ] **Step 4: Tests on the firewall, as the user:**

```
cd /tmp/tdx/opnsense-plugin-topdevices-keep-log && python3 -m unittest tests.test_flows tests.test_keep ; php -l /usr/local/opnsense/mvc/app/controllers/OPNsense/TopDevices/Api/FlowsController.php ; cd ~
```

Expected:
- `Ran 76 tests` and `OK`, with core's parser, aggregators and `check_rotate()` all
  found on the firewall. The rotation test works in a scratch directory with
  `os.kill` stubbed.
- `No syntax errors detected`.

- [ ] **Step 5: API checks, as the controller**, with the scratchpad's `api.sh`:
  - `/api/topdevices/flows/zone` answers `{"timezone":"Australia/Brisbane"}`.
  - A Last hour totals request answers with `log_from` equal to the oldest kept record.
    Hours later, after core has rotated, the same request still answers with that
    `log_from`, and `now − log_from` has grown past 23 hours. That shows the job
    keeping files.
  - A totals request starting 50 h 6 min ago is refused with the 50-hour message.

- [ ] **Step 6: After the first midnight, as the user:**
`cd /tmp/tdx/opnsense-plugin-topdevices-keep-log && python3 tests/parity_flows.py ; cd ~`

Expected:
- the hourly comparison as in 0.2.0, with gaps under 1 B;
- `Yesterday via configd` in at most 2 s, with no "all traffic from" suffix;
- `whole day <Www DD Mmm>: …` with both largest gaps under 1 B;
- exit status 0.

- [ ] **Step 7: The widget end to end, as the controller.** Adapt the scratchpad's
`e2e_raw.mjs`: the widget's own code, run in Node against the firewall under
`TZ=America/New_York`, with the real `flows/zone` answer. Expected:
- Yesterday makes one request, `/api/topdevices/flows/totals/<Thu 00:00>/<Fri 00:00>`,
  and its rows equal the answer in both scopes;
- the caption is `Thu Sep 24 00:00:00 AEST 2026  →  Thu Sep 24 23:59:59 AEST 2026 · internet only (via em0)`,
  with no note;
- Today starts at `00:00:00 AEST`;
- Last 7 days' caption starts at the oldest bucket, with the note, until Sat 26 Sep
  22:00.

- [ ] **Step 8: The user's own dashboard:** Yesterday, Today and Last 7 days in both scopes.

- [ ] **Step 9: A fresh reviewer over the whole branch**, then its fix pass, as the
execution skill prescribes. Give it the spec, this plan, the Review Focus above and the
ledger's rulings.

- [ ] **Step 10: README verification paragraph.** Add **Kept log and time zone (0.3.0)**
under *What has been verified*, with the measured results of Steps 3–8. Extend the
codeload sentence to "and all ten of 0.3.0". Push `keep-log`.

- [ ] **Step 11: Release gates, only on the user's explicit go-ahead:**
  1. If the user wants 0.2.0 released first: fast-forward `main` to `raw-flows`, tag
     `0.2.0` and publish it as prepared.
  2. Fast-forward `main` to `keep-log` and rerun every suite on `main`.
  3. Push `main`, tag `0.3.0` and push the tag.
  4. Publish the release, in the format of 0.1.1's and 0.2.0's notes.
  5. Then keep all branches; the user asked for that before.
