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
FLOWS = ROOT / 'src/opnsense/scripts/topdevices/flows.py'

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

FLOW_MUTANTS = [
    ("core's order of operations not kept",
     '        return ov / (dur_ms / 1000.0) * octets if ov > 0 else 0.0',
     '        return octets * ov / (dur_ms / 1000.0) if ov > 0 else 0.0'),
    ('negative duration counted like a zero one',
     '    if dur_ms < 0:\n        return (flow_end - flow_start) / (dur_ms / 1000.0) * octets\n', ''),
    ('a record finished after its export kept',
     '        if finish > uptime:\n            continue', '        if False:\n            continue'),
    ('records without packets kept',
     "REQUIRED = ('recv_time', 'agent_info', 'packets', 'octets', 'src_addr4', 'dst_addr4')",
     "REQUIRED = ('recv_time', 'agent_info', 'octets', 'src_addr4', 'dst_addr4')"),
    ('a record still being written read anyway', '        if off > n:\n            break', '        pass'),
    ('ingress and egress confused for internet downloads',
     '            if if_in in net.upstream:\n                t[2] += i', '            if if_out in net.upstream:\n                t[2] += i'),
    ('broadcasts counted as devices',
     '            d = self._dev[ip] = ip not in self.bcast and any(', '            d = self._dev[ip] = any('),
    ('the seam on the hour before the log began',
     '    seam = min(math.ceil(L / HOUR) * HOUR, to)', '    seam = min(math.floor(L / HOUR) * HOUR, to)'),
    ('internet only from the range start, not the log start',
     "    return {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (L, to)}",
     "    return {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (frm, to)}"),
    ('a bucket in progress weighed as a full hour', '    end = min(bucket + HOUR, now)', '    end = bucket + HOUR'),
    ('hourly rows read with the details convention',
     "            t[0 if row['direction'] == 'out' else 1] += w * float(row['octets'] or 0)",
     "            t[1 if row['direction'] == 'out' else 0] += w * float(row['octets'] or 0)"),
    ('files taken in the wrong order', '    opened.sort(key=lambda f: f[1])', '    opened.sort(key=lambda f: f[2])'),
    ('a file written since the span began left unread',
     '    return sorted((f for f in opened if f[1] >= lo), key=lambda f: -f[2])',
     '    return sorted((f for f in opened if f[1] >= lo + 60), key=lambda f: -f[2])'),
    ('a record received just after the span began skipped', '        if recv < lo:\n', '        if recv < lo + 20:\n'),
    ("the device's port taken as the higher one", '        port = min(sp, dp)', '        port = max(sp, dp)'),
    ("a device's upload judged by where it came in",
     '            peer, inet = dst, if_out in net.upstream', '            peer, inet = dst, if_in in net.upstream'),
    ("lists not capped at the panel's 100 rows", 'TOP = 100 ', 'TOP = 1000 '),
    ('the clock slack dropped', '    if frm < now - DAY - SLACK:', '    if frm < now - DAY:'),
    ('non-ASCII digits accepted', '    if not (DIGITS.fullmatch(frm) and DIGITS.fullmatch(to)):',
     '    if not (frm.isdigit() and to.isdigit()):'),
    ('a non-device given a panel', '    if not net.is_device(ip):\n        raise', '    if False:\n        raise'),
    ('the two scopes swapped in the jobs',
     '    jobs = [(fd, a_lo, a_hi, i_lo, i_hi, net) for fd, _, _ in files_for(opened, min(a_lo, i_lo))]',
     '    jobs = [(fd, i_lo, i_hi, a_lo, a_hi, net) for fd, _, _ in files_for(opened, min(a_lo, i_lo))]'),
    ("hourly records replacing the log's figures",
     '            t = acc.setdefault(ip, [0.0, 0.0, 0.0, 0.0])\n            t[0] += down\n            t[1] += up\n',
     '            acc[ip] = [down, up, 0.0, 0.0]\n'),
    ('a rotation while the files are opened accepted', '        if _files_now(log) == ids:', '        if True:'),
    ('any open error taken for a rotation',
     '                except FileNotFoundError:\n                    continue                      # rotated away since the listing',
     '                except OSError:\n                    continue                      # rotated away since the listing'),
    ('an upstream device missing from the map ignored', '    if missing:\n        raise', '    if False:\n        raise'),
    ('only ValueError answered', '    except Exception as exc:', '    except ValueError as exc:'),
]


def run(target, env_name, pattern, mutants, survivors):
    source = target.read_text()
    for name, old, new in mutants:
        count = source.count(old)
        if count != 1:
            print('BROKEN MUTANT %r: anchor found %d times' % (name, count))
            return False
        with tempfile.TemporaryDirectory() as tmp:
            mutant = pathlib.Path(tmp) / target.name
            mutant.write_text(source.replace(old, new))
            env = dict(os.environ, **{env_name: str(mutant)})
            proc = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(ROOT / 'tests'),
                                   '-p', pattern], env=env, capture_output=True, text=True)
        killed = proc.returncode != 0
        print('%-8s %s' % ('killed' if killed else 'SURVIVED', name))
        if not killed:
            survivors.append(name)
    return True


def main():
    survivors = []
    for args in ((LIVE, 'LIVE_PY', 'test_live.py', MUTANTS), (FLOWS, 'FLOWS_PY', 'test_flows.py', FLOW_MUTANTS)):
        if not run(*args, survivors):
            return 2
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main())
