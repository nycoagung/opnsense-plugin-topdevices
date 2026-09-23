"""On the firewall: our pfctl parser and core's, over the live state table.

The unit tests prove agreement on synthetic text; this proves it on the real
output of this firewall's pfctl. Run from the extracted branch tarball:

    python3 tests/parity_live.py

Exit status 0 when every state agrees and nothing was left unparsed. States
whose second host carries a translation are skipped: core reads their source
wrongly (see tests/test_core_parity.py).
"""
import importlib.util
import pathlib
import subprocess
import sys
import types

HERE = pathlib.Path(__file__).resolve().parent
CORE_STATES = '/usr/local/opnsense/scripts/filter/lib/states.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    live = load('live', HERE.parent / 'src/opnsense/scripts/topdevices/live.py')
    core = load('core_states', CORE_STATES)
    text = subprocess.run(['/sbin/pfctl', '-vvs', 'state'], capture_output=True, text=True, check=True).stdout
    core.subprocess = types.SimpleNamespace(run=lambda *a, **k: types.SimpleNamespace(stdout=text))
    core.fetch_rule_labels = lambda: {}
    ours, unparsed = live.parse_states(text)
    theirs = {r['id']: r for r in core.query_states('', '')}
    mismatches, skipped = [], 0
    for key, s in ours.items():
        c = theirs.get(key)
        if c is None:
            mismatches.append((key, 'core has no such state'))
            continue
        if str(c['src_addr']).startswith('('):
            skipped += 1
            continue
        core_port = int(c['dst_port']) if c['proto'] in live.PORT_PROTOCOLS and c['dst_port'].isdigit() else 0
        a = (s['dir'], s['src'], s['dst'], s['nat'], s['dst_port'], [s['b0'], s['b1']])
        b = (c['direction'], c['src_addr'], c['dst_addr'], c['nat_addr'], core_port, c['bytes'])
        if a != b:
            mismatches.append((key, a, b))
    print('states: ours %d, core %d | unparsed %d | skipped (core misread) %d | mismatches %d'
          % (len(ours), len(theirs), unparsed, skipped, len(mismatches)))
    for m in mismatches[:10]:
        print('  ', m)
    return 0 if not mismatches and unparsed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
