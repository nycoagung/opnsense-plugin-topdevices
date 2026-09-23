"""Prove the suite catches the mistakes that matter: each mutant must fail it.

Run:  python3 tests/mutate.py        (exit status 1 if any mutant survives)
"""
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
LIVE = ROOT / 'src/opnsense/scripts/topdevices/live.py'

MUTANTS = [
    ('port-forward direction not swapped',
     'out.append((INET, dst, src, port, b0, b1))', 'out.append((INET, dst, src, port, b1, b0))'),
    ('port-forward rule ignores a local source (DNS redirect becomes internet)',
     'and dst not in never and not local(src):', 'and dst not in never:'),
    ('out states counted as well as in states',
     '        return out\n    if nat is not None and not local(nat)', '    if nat is not None and not local(nat)'),
    ('ipaddress.is_private instead of RFC 1918 on the interfaces',
     'hit = ip.version == 4 and any(ip in net for net in self.local_nets)', 'hit = ip.is_private'),
    ('reused state ids not detected',
     '            if d0 < 0 or d1 < 0:', '            if False:'),
    ('CPU budget ignored',
     'return max(float(requested), (cost_ema or 0.0) / CPU_BUDGET)', 'return float(requested)'),
    ('Ethernet headers not subtracted',
     'hrx += prx * ETHER_HEADER', 'hrx += 0'),
    ('no keepalive while throttled',
     "            write(': keepalive\\n\\n')", '            pass'),
    ('upstream found by address only (double NAT breaks)',
     'for dev in list(default_devs) + public:', 'for dev in public:'),
    ('subnet broadcasts credited as devices',
     'self.never_devices = set(self.fw_addrs) | {', 'self.never_devices = set(self.fw_addrs) or {'),
    ('WireGuard from the LAN counted as upstream (in state)',
     'elif nat is None and dst in upstream and not local(src):', 'elif nat is None and dst in upstream:'),
    ('WireGuard from the LAN counted as upstream (out state)',
     'elif nat is None and src in upstream and not local(dst):', 'elif nat is None and src in upstream:'),
    ('a failed WAN counter read discards the device rates too',
     "                counters = None\n", "                states = None\n"),
    ('parser trusts a fixed arrow position',
     "arrow = next((i for i, p in enumerate(parts) if p in ('->', '<-')), None)",
     "arrow = len(parts) - 3 if len(parts) >= 6 and parts[-3] in ('->', '<-') else None"),
]


def main():
    source = LIVE.read_text()
    survivors = []
    for name, old, new in MUTANTS:
        count = source.count(old)
        if count != 1:
            print('BROKEN MUTANT %r: anchor found %d times' % (name, count))
            return 2
        with tempfile.TemporaryDirectory() as tmp:
            mutant = pathlib.Path(tmp) / 'live.py'
            mutant.write_text(source.replace(old, new))
            env = dict(os.environ, LIVE_PY=str(mutant))
            proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'),
                                   '-p', 'test_live.py'], env=env, capture_output=True, text=True)
        killed = proc.returncode != 0
        print('%-8s %s' % ('killed' if killed else 'SURVIVED', name))
        if not killed:
            survivors.append(name)
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main())
