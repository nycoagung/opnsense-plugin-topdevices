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
import types
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

    def test_a_file_with_extra_suffixes_beside_the_log_is_never_linked(self):
        # ROTATED matches ".<digits>" exactly (fullmatch): a stray file such as a
        # manual backup or a leftover from an old rotation scheme must not be
        # mistaken for one of core's own rotated files.
        self.write('flowd.log.000001', b'a', T0 - H)
        self.write('flowd.log.bak', b'stray', T0 - H)
        self.write('flowd.log.000001.old', b'stray too', T0 - H)
        self.write('flowd.log', b'c', T0)
        self.assertEqual(self.keep(T0), (1, 0))
        self.assertEqual(self.kept_data(), [b'a'])

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
        # F1: the stub now creates the real link (as a genuine concurrent pass
        # would) before raising FileExistsError, so the post-link identity check
        # finds a file there that matches, and takes the "another pass already
        # linked it" branch (have.add()) instead of the FileNotFoundError one.
        # Still a skip, still kept - the pass's return values are unchanged - but
        # the kept name now exists for real, once, without a second link.
        self.write('flowd.log.000001', b'b', T0 - H)
        real_link, calls = os.link, []

        def meanwhile(src, dst):
            calls.append(src)
            if len(calls) == 1:                                  # core renamed it between the listing and the link
                raise FileNotFoundError(2, 'No such file or directory', src)
            if len(calls) == 2:                                  # a pass running at the same time linked it
                real_link(src, dst)
                raise FileExistsError(17, 'File exists', dst)
            return real_link(src, dst)

        with unittest.mock.patch('os.link', meanwhile):
            self.assertEqual(self.keep(T0), (0, 0))
            self.assertEqual(self.keep(T0 + 600), (0, 0))
        self.assertEqual(len(self.kept_names()), 1)

    def test_a_file_confirmed_kept_by_that_branch_is_not_linked_again_this_same_pass(self):
        # Mutant: have.add() removed from the "another pass already linked it"
        # branch. Two rotated names momentarily resolving to the same identity -
        # a rename core's own rotation cannot produce, but a second keep.py pass
        # racing this one can, by linking the file under this pass's own listing
        # in between - are stood in for here with a stat stub, so both are seen
        # in the same run() call. The first hits FileExistsError and have.add()s
        # it; without that, the second would try os.link again for what is
        # already kept.
        first = self.write('flowd.log.000001', b'b', T0 - H)
        second = self.write('flowd.log.000002', b'b', T0 - H)
        identity = os.stat(first)                              # the one identity both names are made to share
        real_stat, real_link = os.stat, os.link

        def same_identity(path, *a, **kw):
            return identity if path in (first, second) else real_stat(path, *a, **kw)

        calls = []

        def racing_link(src, dst):
            calls.append(src)
            if len(calls) == 1:
                real_link(first, dst)                           # a concurrent pass links it for real
                raise FileExistsError(17, 'File exists', dst)
            raise AssertionError('os.link called a second time for the same identity: %r' % calls)

        with unittest.mock.patch('os.stat', same_identity), unittest.mock.patch('os.link', racing_link):
            self.assertEqual(self.keep(T0), (0, 0))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.kept_names()), 1)

    def test_a_rotation_between_the_stat_and_the_link_keeps_the_right_file(self):
        # F1: core's rotation renames every file one number up. Landing here
        # makes `path` (.000001, holding A) name a different file (B, renamed
        # from flowd.log) by the time os.link runs, so the link succeeds on B
        # under the name A's stat built - the bug this fix closes.
        self.write('flowd.log.000001', b'A', T0 - H)
        self.write('flowd.log', b'B', T0)
        real_link, calls = os.link, []

        def racing(src, dst):
            calls.append(src)
            if len(calls) == 1:
                self.rotate(b'C', T0 + 5)                         # core's rotation lands mid-link
            return real_link(src, dst)

        with unittest.mock.patch('os.link', racing):
            self.keep(T0)
        self.assertEqual(self.kept_data(), [])                    # nothing wrongly kept under A's name
        self.assertEqual(self.keep(T0 + 600), (2, 0))
        self.assertEqual(self.kept_data(), [b'A', b'B'])

    def test_a_kept_name_holding_the_wrong_file_is_replaced(self):
        # F1: a FileExistsError where the existing name holds a different id -
        # an earlier pass linked the wrong file under it (as above) - is the
        # stale name being unlinked so the next pass can link the right file.
        path = self.write('flowd.log.000001', b'A', T0 - H)
        st = os.stat(path)
        name = os.path.join(self.kept, 'flowd.%d.%d' % (st.st_mtime, st.st_ino))
        os.makedirs(self.kept)
        other = self.write('other', b'X', T0)
        os.link(other, name)                                      # an earlier pass linked the wrong file here
        self.assertEqual(self.keep(T0), (0, 0))
        self.assertEqual(self.kept_names(), [])                   # the stale name is gone
        self.assertEqual(self.keep(T0 + 600), (1, 0))              # the next pass keeps the right file
        self.assertEqual(self.kept_data(), [b'A'])

    def test_an_unexpected_link_error_does_not_skip_pruning(self):
        # F2: any OSError other than FileNotFound/FileExists from os.link
        # (ENOSPC, EMLINK ...) must not turn the 51h/1GB guards off - exactly
        # when the disk is full and pruning matters most.
        self.write('flowd.log.000001', b'new', T0 - H)
        os.makedirs(self.kept)
        old = self.write('topdevices/flowd.1.1', b'too old', T0 - 51 * H - 1)
        with unittest.mock.patch('os.link', side_effect=OSError(28, 'No space left on device')):
            with self.assertRaises(OSError):
                self.keep(T0)
        self.assertFalse(os.path.exists(old))

    def test_a_rotated_file_removed_before_its_stat_is_skipped(self):
        # F4: a file core rotated away right after the glob, before run() gets
        # to stat it.
        path = self.write('flowd.log.000001', b'b', T0 - H)
        real_stat = os.stat

        def vanishing(p, *a, **kw):
            if p == path:
                os.remove(path)                                   # core renamed it away just now
            return real_stat(p, *a, **kw)

        with unittest.mock.patch('os.stat', vanishing):
            self.assertEqual(self.keep(T0), (0, 0))
        self.assertEqual(self.kept_names(), [])

    def test_a_kept_file_removed_before_its_unlink_is_skipped(self):
        # F4: a kept file another pass already deleted, between this pass's
        # kept_files() listing and its own os.unlink of the same file.
        os.makedirs(self.kept)
        old = self.write('topdevices/flowd.1.1', b'too old', T0 - 51 * H - 1)
        real_unlink = os.unlink

        def vanishing(p, *a, **kw):
            real_unlink(p)                                        # another pass deleted it first
            return real_unlink(p, *a, **kw)

        with unittest.mock.patch('os.unlink', vanishing):
            self.assertEqual(self.keep(T0), (0, 1))
        self.assertFalse(os.path.exists(old))

    def test_kept_files_skips_one_removed_before_its_stat(self):
        # F4: kept_files() lists a kept file that is gone by the time it is
        # stat()ed - deleted by another pass between the glob and the stat -
        # and simply returns the rest.
        os.makedirs(self.kept)
        keep_path = self.write('topdevices/flowd.1.1', b'a', T0 - H)
        gone_path = self.write('topdevices/flowd.2.2', b'b', T0 - 2 * H)
        real_stat = os.stat

        def vanishing(p, *a, **kw):
            if p == gone_path:
                os.remove(gone_path)
            return real_stat(p, *a, **kw)

        with unittest.mock.patch('os.stat', vanishing):
            out = keep.kept_files(self.kept)
        self.assertEqual([p for p, _ in out], [keep_path])

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

    def test_the_floor_keeps_512_mb_free_on_the_filesystem(self):
        self.assertEqual(keep.FLOOR, 512 * 1024 * 1024)               # spec §5 / G3
        os.makedirs(self.kept)
        for i, age in enumerate((3, 2, 1)):
            self.write('topdevices/flowd.%d.%d' % (T0 - age * H, i), b'x' * 100, T0 - age * H)
        low = types.SimpleNamespace(f_bavail=100 * 1024 * 1024, f_frsize=1)     # under the floor
        high = types.SimpleNamespace(f_bavail=2 * 1024 * 1024 * 1024, f_frsize=1)  # over it, after one deletion
        with unittest.mock.patch('os.statvfs', side_effect=[low, high]):
            self.assertEqual(self.keep(T0), (0, 1))
        self.assertEqual(self.kept_names(), ['flowd.%d.1' % (T0 - 2 * H), 'flowd.%d.2' % (T0 - H)])

    def test_the_floor_is_not_checked_once_nothing_is_kept(self):
        # an empty (or just-created) kept directory never calls statvfs: nothing
        # left to delete even if free space is still short.
        with unittest.mock.patch('os.statvfs', side_effect=AssertionError('should not be called')):
            self.assertEqual(self.keep(T0), (0, 0))

    def test_a_reset_of_netflows_data_drops_every_kept_file_and_logs_once(self):
        # flush_all.sh deletes /var/log/flowd.log* (current and rotated) without
        # rotating it: the marker the first pass left then names an id found
        # nowhere in the second pass's listing - a reset (G2).
        self.write('flowd.log.000001', b'old', T0 - H)
        self.write('flowd.log', b'current', T0)
        self.assertEqual(self.keep(T0), (1, 0))
        self.assertEqual(len(self.kept_names()), 1)
        for p in glob.glob(glob.escape(self.log) + '*'):
            os.remove(p)
        self.write('flowd.log', b'after the reset', T0 + 600)
        with unittest.mock.patch('syslog.syslog') as logged:
            self.assertEqual(self.keep(T0 + 600), (0, 1))
        self.assertEqual(self.kept_names(), [])
        logged.assert_called_once_with(
            syslog.LOG_NOTICE, "topdevices keep: NetFlow's flow log was reset: dropped 1 kept files")

    def test_a_normal_rotation_between_passes_drops_nothing_kept(self):
        self.write('flowd.log.000001', b'old', T0 - H)
        self.write('flowd.log', b'current', T0)
        self.keep(T0)                                       # keeps 'old'; marks 'current' as of this pass
        with unittest.mock.patch('syslog.syslog') as logged:
            self.rotate(b'next', T0 + 600)                  # core: 'current' becomes .000001, a new flowd.log begins
            self.assertEqual(self.keep(T0 + 600), (1, 0))
        logged.assert_not_called()
        self.assertEqual(self.kept_data(), sorted([b'old', b'current']))

    def test_flowd_log_absent_at_pass_start_skips_the_check_and_keeps_the_marker(self):
        self.write('flowd.log', b'c', T0)
        self.keep(T0)
        marker = os.path.join(self.kept, '.current')
        self.assertTrue(os.path.exists(marker))
        with open(marker, 'rb') as f:
            before = f.read()
        os.remove(self.log)                                 # NetFlow capture disabled, say
        with unittest.mock.patch('syslog.syslog') as logged:
            self.assertEqual(self.keep(T0 + 600), (0, 0))
        logged.assert_not_called()
        with open(marker, 'rb') as f:
            self.assertEqual(f.read(), before)                # untouched, for when it returns

    def test_the_marker_is_never_a_name_flows_py_would_read(self):
        self.write('flowd.log', b'c', T0)
        self.keep(T0)
        self.assertTrue(os.path.exists(os.path.join(self.kept, '.current')))
        self.assertNotIn('.current', [os.path.basename(p) for p in glob.glob(os.path.join(self.kept, 'flowd.*'))])

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
        with unittest.mock.patch.object(agg, 'MAX_FILE_SIZE_MB', 0), unittest.mock.patch('os.kill'), \
                warnings.catch_warnings():
            warnings.simplefilter('ignore', ResourceWarning)  # core's check_rotate() opens /var/run/flowd.pid without closing it
            for i in range(14):                                   # more rotations than core keeps files
                written.append(b'flows %d' % i)
                self.write('flowd.log', written[-1], T0 + i * H)
                agg.check_rotate(self.log)
                self.keep(T0 + i * H + 60)
        self.assertEqual(len(glob.glob(glob.escape(self.log) + '.*')), 10)   # core kept its ten
        self.assertEqual(self.kept_data(), sorted(written))                   # the plugin every one


if __name__ == '__main__':
    unittest.main()
