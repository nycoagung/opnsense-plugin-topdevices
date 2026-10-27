"""The release number agrees everywhere it is written: the Makefile, the newest
pkg-descr entry, and each script's VERSION (the "v" in its answers).
Run:  python3 -m unittest tests.test_version -v
"""
import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def found(path, pattern):
    m = re.search(pattern, (ROOT / path).read_text(), re.M)
    return m.group(1) if m else None


class Version(unittest.TestCase):
    def test_one_number_everywhere(self):
        make = found('Makefile', r'^PLUGIN_VERSION=\s*(\S+)$')
        self.assertIsNotNone(make, 'no PLUGIN_VERSION in the Makefile')
        self.assertEqual(found('pkg-descr', r'^-+\n\n(\S+)$'), make, 'the newest pkg-descr entry')
        for script in ('flows.py', 'live.py', 'connected.py'):
            self.assertEqual(found('src/opnsense/scripts/topdevices/' + script, r"^VERSION = '([^']+)'$"), make, script)


if __name__ == '__main__':
    unittest.main()
