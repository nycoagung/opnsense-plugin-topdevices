"""Our pfctl parser against core's own (src/opnsense/scripts/filter/lib/states.py).

Both read the same fixture text. Skipped unless CORE_STATES_PY names core's
states.py (on the firewall: /usr/local/opnsense/scripts/filter/lib/states.py)
or OPNSENSE_CORE points at a checkout of opnsense/core 26.7.x, e.g.:

  git clone -q --depth 1 --branch 26.7.4 --filter=blob:none --sparse \
      https://github.com/opnsense/core.git /tmp/core
  git -C /tmp/core sparse-checkout set src/opnsense/scripts/filter
  OPNSENSE_CORE=/tmp/core python3 -m unittest discover -s tests -v
"""
import importlib.util
import os
import pathlib
import types
import unittest

from test_live import STATES_TEXT, live, sid

CORE_STATES = pathlib.Path(
    os.environ.get('CORE_STATES_PY')
    or pathlib.Path(os.environ.get('OPNSENSE_CORE', '/nonexistent')) / 'src/opnsense/scripts/filter/lib/states.py')

# Where core's parser is known to be wrong, so the two are expected to differ:
# it locates the arrow at a fixed index and so misreads a state whose second
# host also carries a translation (it takes '(192.168.1.1' as the source).
KNOWN_CORE_MISREAD = {sid(0x11)}


def age_seconds(text):
    h, m, s = (int(p) for p in text.split(':'))
    return h * 3600 + m * 60 + s


@unittest.skipUnless(CORE_STATES.is_file(), 'set CORE_STATES_PY or OPNSENSE_CORE (see module docstring)')
class CoreParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load the file directly: the package's __init__ imports dnspython.
        spec = importlib.util.spec_from_file_location('core_states', CORE_STATES)
        core = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(core)
        core.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=STATES_TEXT))
        core.fetch_rule_labels = lambda: {}
        cls.core = {r['id']: r for r in core.query_states('', '')}
        cls.ours, _ = live.parse_states(STATES_TEXT)

    def test_every_state_we_parse_is_compared(self):
        missing = set(self.ours) - set(self.core)
        self.assertEqual(missing, set(), 'core did not produce these states at all')

    def test_fields_agree(self):
        compared = 0
        for key, s in self.ours.items():
            if key in KNOWN_CORE_MISREAD:
                continue
            c = self.core[key]
            with self.subTest(state=key):
                port = s['dst_port'] if s['dst_port'] else None
                core_port = int(c['dst_port']) if c['dst_port'] not in ('0', '') else None
                if c['proto'] not in live.PORT_PROTOCOLS:
                    core_port = None             # we report no port for ICMP; core keeps the query id
                self.assertEqual((s['dir'], s['src'], s['dst'], s['nat'], port, [s['b0'], s['b1']],
                                  s['age']),
                                 (c['direction'], c['src_addr'], c['dst_addr'], c['nat_addr'], core_port,
                                  c['bytes'], age_seconds(c['age'])))
                compared += 1
        self.assertEqual(compared, len(self.ours) - len(KNOWN_CORE_MISREAD))

    def test_known_core_misread_is_still_there(self):
        # If upstream fixes this, drop it from KNOWN_CORE_MISREAD and compare it too.
        self.assertTrue(self.core[sid(0x11)]['src_addr'].startswith('('))
        self.assertEqual(self.ours[sid(0x11)]['src'], '192.168.1.10')
