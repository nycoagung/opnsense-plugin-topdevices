"""Unit tests for the WireGuard tunnel counter sampler (scripts/topdevices/tunnels.py).
Standard library only; no OPNsense needed.
Run:  python3 -m unittest tests.test_tunnels -v
TUNNELS_PY=<path> points the suite at another copy (used by mutate.py).
"""
import glob
import importlib.util
import os
import pathlib
import stat
import sys
import syslog
import tempfile
import unittest
import unittest.mock

HERE = pathlib.Path(__file__).resolve().parent
TUNNELS_PY = os.environ.get('TUNNELS_PY') or str(HERE.parent / 'src/opnsense/scripts/topdevices/tunnels.py')


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


tunnels = _load('tunnels', TUNNELS_PY)

T0 = 1_790_000_000               # Mon 21 Sep 2026 14:13:20 UTC
H = 3600
KEY = 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='      # a documentation key, never a real one

# `wg show all dump`, tab-separated (wg(8)): an interface line has 5 fields, a peer line 9
DUMP = '\n'.join([
    '\t'.join(['wg0', KEY, KEY, '51820', 'off']),
    '\t'.join(['wg0', KEY, '(none)', '(none)', '10.8.0.2/32', '0', '1000', '5000', 'off']),
    '\t'.join(['wg0', KEY, '(none)', '198.51.100.7:4567', '10.8.0.3/32,fd00:8::3/128', '1790000000', '20', '30', '25']),
    '\t'.join(['wg1', KEY, KEY, '51821', 'off']),
    '\t'.join(['wg1', KEY, '(none)', '(none)', '10.8.1.2/32', '0', '0', '0', 'off']),
    '\t'.join(['wg1', KEY, '(none)', '(none)', '192.0.2.0/24', '0', '7', '7', 'off']),          # site-to-site: no /32
    '\t'.join(['wg1', KEY, '(none)', '(none)', '10.8.1.5/32,10.8.1.6/32', '0', '1', '2', 'off']),  # two /32s
]) + '\n'


class Parsing(unittest.TestCase):
    def test_peer_lines_are_read_and_interface_lines_skipped(self):
        peers, skipped, extras = tunnels.parse_dump(DUMP)
        self.assertEqual(peers, [('wg0', '10.8.0.2', 1000, 5000), ('wg0', '10.8.0.3', 20, 30),
                                 ('wg1', '10.8.1.2', 0, 0), ('wg1', '10.8.1.5', 1, 2)])
        self.assertEqual(skipped, 0)

    def test_the_first_ipv4_slash32_is_the_device_and_the_rest_are_reported(self):
        _, _, extras = tunnels.parse_dump(DUMP)
        self.assertEqual(extras, [('wg1', '10.8.1.5', ['10.8.1.6'])])

    def test_a_peer_without_a_slash32_is_skipped_silently(self):
        peers, skipped, _ = tunnels.parse_dump('\t'.join(['wg1', KEY, '(none)', '(none)', '192.0.2.0/24', '0', '7', '7', 'off']) + '\n')
        self.assertEqual((peers, skipped), ([], 0))

    def test_lines_of_another_shape_are_skipped_and_counted(self):
        peers, skipped, _ = tunnels.parse_dump('wg0\tsomething\tnew\n' + DUMP.splitlines()[1] + '\n')
        self.assertEqual(len(peers), 1)
        self.assertEqual(skipped, 1)

    def test_counters_that_are_not_numbers_skip_the_line(self):
        line = '\t'.join(['wg0', KEY, '(none)', '(none)', '10.8.0.2/32', '0', 'x', '5000', 'off'])
        peers, skipped, _ = tunnels.parse_dump(line + '\n')
        self.assertEqual((peers, skipped), ([], 1))

    def test_an_empty_dump_has_no_peers(self):
        self.assertEqual(tunnels.parse_dump(''), ([], 0, []))


class Scratch(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.kept = os.path.join(tmp.name, 'topdevices')
        self.syslog = unittest.mock.patch.object(tunnels.syslog, 'syslog')
        self.logged = self.syslog.start()
        self.addCleanup(self.syslog.stop)

    def files(self):
        return sorted(os.path.basename(p) for p in glob.glob(os.path.join(self.kept, '*')))

    def read(self, name):
        with open(os.path.join(self.kept, name)) as f:
            return f.read()


class Sampling(Scratch):
    def test_one_line_per_peer_in_the_utc_day_file(self):
        self.assertEqual(tunnels.run(T0, self.kept, DUMP), 4)
        self.assertEqual(self.files(), ['tunnels.20260921'])
        self.assertEqual(self.read('tunnels.20260921'),
                         '%d wg0 10.8.0.2 1000 5000\n%d wg0 10.8.0.3 20 30\n%d wg1 10.8.1.2 0 0\n%d wg1 10.8.1.5 1 2\n'
                         % (T0, T0, T0, T0))

    def test_the_folder_is_created_root_only(self):
        tunnels.run(T0, self.kept, DUMP)
        self.assertEqual(stat.S_IMODE(os.stat(self.kept).st_mode), 0o700)

    def test_a_later_run_appends_and_a_new_utc_day_starts_a_new_file(self):
        tunnels.run(T0, self.kept, DUMP)
        tunnels.run(T0 + 60, self.kept, DUMP)
        self.assertEqual(self.read('tunnels.20260921').count('\n'), 8)
        tunnels.run(T0 + 12 * H, self.kept, DUMP)            # 02:13 UTC the next day
        self.assertEqual(self.files(), ['tunnels.20260921', 'tunnels.20260922'])

    def test_a_sample_is_one_write(self):
        real = os.write
        calls = []
        with unittest.mock.patch.object(tunnels.os, 'write', side_effect=lambda fd, b: calls.append(len(b)) or real(fd, b)):
            tunnels.run(T0, self.kept, DUMP)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], len(self.read('tunnels.20260921')))

    def test_no_wireguard_writes_nothing_and_is_not_an_error(self):
        self.assertEqual(tunnels.run(T0, self.kept, None), 0)
        self.assertEqual(self.files(), [])
        self.logged.assert_not_called()

    def test_no_peers_writes_nothing(self):
        only_interfaces = '\t'.join(['wg0', KEY, KEY, '51820', 'off']) + '\n'
        self.assertEqual(tunnels.run(T0, self.kept, only_interfaces), 0)
        self.assertEqual(self.files(), [])
        self.logged.assert_not_called()

    def test_output_nobody_understands_is_logged_once(self):
        tunnels.run(T0, self.kept, 'wg0\tsomething\tnew\n')
        self.assertEqual(self.files(), [])
        self.logged.assert_called_once()
        self.assertIn('not understood', self.logged.call_args[0][1])

    def test_extra_slash32s_are_logged_once_per_run(self):
        tunnels.run(T0, self.kept, DUMP)
        self.logged.assert_called_once()
        self.assertEqual(self.logged.call_args[0][0], syslog.LOG_NOTICE)
        self.assertIn('10.8.1.6', self.logged.call_args[0][1])

    def test_day_files_past_the_reach_are_deleted(self):
        os.makedirs(self.kept, mode=0o700)
        for name, age in (('tunnels.20260919', 52 * H), ('tunnels.20260920', 50 * H), ('flowd.1.2', 52 * H)):
            p = os.path.join(self.kept, name)
            with open(p, 'w') as f:
                f.write('x')
            os.utime(p, (T0 - age, T0 - age))
        tunnels.run(T0, self.kept, DUMP)
        self.assertEqual(self.files(), ['flowd.1.2', 'tunnels.20260920', 'tunnels.20260921'])   # the keeper's file is not ours to delete

    def test_main_logs_an_error_and_exits_1(self):
        with unittest.mock.patch.object(tunnels, 'wg_dump', return_value=DUMP), \
             unittest.mock.patch.object(tunnels, 'KEPT', os.path.join(self.kept, 'x\0y')):
            self.assertEqual(tunnels.main(), 1)
        self.assertEqual(self.logged.call_args[0][0], syslog.LOG_ERR)
        self.assertIn('topdevices tunnels:', self.logged.call_args[0][1])


class Dump(unittest.TestCase):
    def test_a_missing_binary_or_a_failing_wg_is_none(self):
        with unittest.mock.patch.object(tunnels, 'WG', '/nonexistent/wg'):
            self.assertIsNone(tunnels.wg_dump())
        with unittest.mock.patch.object(tunnels, 'WG', '/usr/bin/false'):
            self.assertIsNone(tunnels.wg_dump())

    def test_a_working_wg_gives_its_output(self):
        with unittest.mock.patch.object(tunnels, 'WG', '/bin/echo'):
            self.assertEqual(tunnels.wg_dump(), 'show all dump\n')


if __name__ == '__main__':
    unittest.main()
