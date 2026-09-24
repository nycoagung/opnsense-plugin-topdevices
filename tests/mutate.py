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
KEEP = ROOT / 'src/opnsense/scripts/topdevices/keep.py'

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
    ('the clock slack dropped', "    if mode == 'totals' and frm < now - REACH - SLACK:", "    if mode == 'totals' and frm < now - REACH:"),
    ('non-ASCII digits accepted', '    if not (DIGITS.fullmatch(frm) and DIGITS.fullmatch(to)):',
     '    if not (frm.isdigit() and to.isdigit()):'),
    ('a non-device given a panel', '    if not net.is_device(ip):\n        raise', '    if False:\n        raise'),
    ('the two scopes swapped in the jobs',
     '    jobs = [(fd, a_lo, a_hi, i_lo, i_hi, net) for fd, _, _ in files]',
     '    jobs = [(fd, i_lo, i_hi, a_lo, a_hi, net) for fd, _, _ in files]'),
    ("hourly records replacing the log's figures",
     '            t = acc.setdefault(ip, [0.0, 0.0, 0.0, 0.0])\n            t[0] += down\n            t[1] += up\n',
     '            acc[ip] = [down, up, 0.0, 0.0]\n'),
    ('a rotation while the files are opened accepted', '        if _files_now(log) == ids:', '        if True:'),
    ('any open error taken for a rotation',
     '                except FileNotFoundError:\n                    continue                      # rotated or pruned away since the listing',
     '                except OSError:\n                    continue                      # rotated or pruned away since the listing'),
    ('an upstream device missing from the map ignored', '    if missing:\n        raise', '    if False:\n        raise'),
    ('only ValueError answered', '    except Exception as exc:', '    except ValueError as exc:'),
    ('the reach applied to device panels', "    if mode == 'totals' and frm < now - REACH - SLACK:", '    if frm < now - REACH - SLACK:'),
    ('the device answer given its range reversed',
     "            out = answer_device(req['ip'], req['from'], req['to'], now, opened, net, workers)",
     "            out = answer_device(req['ip'], req['to'], req['from'], now, opened, net, workers)"),
    ('a log nobody rotates read anyway', '        if newest_size > MAX_FILE:', '        if False:'),
    ('a big file in the range read anyway', '        if size > MAX_FILE:', '        if False:'),
    ('the aggregator check over every file', '        newest_size = opened[-1][2]',
     '        newest_size = max(size for _, _, size in opened)'),
    ("each device list sorted by all traffic",
     '    items = sorted(((key, v[k]) for key, v in table.items() if v[k] > 0), key=lambda kv: (-kv[1], kv[0]))',
     '    items = sorted(((key, v[k]) for key, v in table.items() if v[k] > 0), key=lambda kv: (-table[kv[0]][0], kv[0]))'),
    ('the reach excluding its edge', "    if mode == 'totals' and frm < now - REACH - SLACK:",
     "    if mode == 'totals' and frm <= now - REACH - SLACK:"),
    ('the reach left at a day', "    if mode == 'totals' and frm < now - REACH - SLACK:",
     "    if mode == 'totals' and frm < now - DAY - SLACK:"),
    ('all traffic claimed from the range start', '    frm = min(to, max(frm, min(L, now - DAY - SLACK)))\n', ''),
    ('the hourly records trusted past their day', '    frm = min(to, max(frm, min(L, now - DAY - SLACK)))',
     '    frm = min(to, max(frm, min(L, now - REACH - SLACK)))'),
    ("the log's own reach ignored for all traffic", '    frm = min(to, max(frm, min(L, now - DAY - SLACK)))',
     '    frm = min(to, max(frm, now - DAY - SLACK))'),
    ('a range wholly before C answers with frm past to instead of an empty span at to',
     '    frm = min(to, max(frm, min(L, now - DAY - SLACK)))',
     '    frm = max(frm, min(L, now - DAY - SLACK))'),
    ('the future bound excluding its edge', '    if to > now + SLACK:', '    if to >= now + SLACK:'),
    ('no upstream interface ignored', '    if not upstream_devs:\n        raise', '    if False:\n        raise'),
    ('kept files not read', "    return glob.glob(glob.escape(log) + '*') + glob.glob(kept)", "    return glob.glob(glob.escape(log) + '*')"),
    ('a file with two names read twice', '                if ids[path] in seen:', '                if False:'),
    ('a missing file not noticed', '        if later is not None and later - mtime > GAP:', '        if False:'),
    ('a gap of exactly 900 s taken for a missing file', '        if later is not None and later - mtime > GAP:',
     '        if later is not None and later - mtime >= GAP:'),
    ('a file without an IPv4 record breaking the run', '        later = first\n', '        later = first if first is not None else mtime\n'),
    ('an ipv6-only oldest file erasing a newer one\'s start',
     '        if first is not None:\n            L = first\n', '        L = first\n'),
    ('the rotation re-check blind to kept names', '    for path in _names(log):\n        try:\n            st = os.stat(path)',
     "    for path in glob.glob(glob.escape(log) + '*'):\n        try:\n            st = os.stat(path)"),
]

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
    ('a file renamed just before the link stops the pass',
     '        except FileNotFoundError:\n            continue                              # renamed before the link: the next pass finds it under its new name\n',
     ''),
    ('a file linked meanwhile treated as an unexpected error, raised after pruning',
     '        except FileExistsError:\n            try:\n                held = (os.stat(name).st_dev, os.stat(name).st_ino)\n'
     '            except FileNotFoundError:\n                continue                          # gone already: the next pass links it fresh\n'
     '            if held == (st.st_dev, st.st_ino):\n                have.add((st.st_dev, st.st_ino))  # another pass already linked it under this name\n'
     '            else:\n                try:\n                    os.unlink(name)               # an earlier pass linked the wrong file under this name\n'
     '                except FileNotFoundError:\n                    pass\n            continue\n',
     ''),
    ('an error reported as success', '        return 1\n', '        return 0\n'),
    ('a cap of 10 GB', 'CAP = 1 << 30', 'CAP = 10 << 30'),
    # F1: a rotation lands between the stat and the link
    ('the post-link identity is not checked, so the wrong file can be kept under the right name',
     '        try:\n            held = (os.stat(name).st_dev, os.stat(name).st_ino)\n'
     '        except FileNotFoundError:\n            continue                              # renamed again right after the link: the next pass finds it\n'
     '        if held != (st.st_dev, st.st_ino):\n            try:\n                os.unlink(name)                   # the rotation landed between the stat and the link: wrong file\n'
     '            except FileNotFoundError:\n                pass\n            continue\n',
     ''),
    ('a kept name holding the wrong file is never replaced',
     '            else:\n                try:\n                    os.unlink(name)               # an earlier pass linked the wrong file under this name\n'
     '                except FileNotFoundError:\n                    pass\n',
     ''),
    # F2: an os.link error other than FileNotFound/FileExists must not skip pruning
    ('a persistent link error skips pruning',
     '        except OSError as exc:\n            if link_error is None:\n                link_error = exc\n            continue\n',
     '        except OSError as exc:\n            raise exc\n'),
    # F4: races the reviewer's ad-hoc mutants found untested
    ('a rotated file gone before its stat crashes the pass instead of being skipped',
     '        try:\n            st = os.stat(path)\n        except FileNotFoundError:\n            continue                              # renamed since the listing: the next pass finds it\n',
     '        st = os.stat(path)\n'),
    ('a kept file gone before its unlink crashes the pass instead of being skipped',
     '            try:\n                os.unlink(path)\n            except FileNotFoundError:\n                pass                              # another pass deleted it\n',
     '            os.unlink(path)\n'),
    ('kept_files crashes on a file gone before its stat instead of skipping it',
     '        try:\n            out.append((path, os.stat(path)))\n        except FileNotFoundError:\n            pass\n',
     '        out.append((path, os.stat(path)))\n'),
    # G5a: within one pass, a file already confirmed kept under this branch must
    # not be linked again if another rotated name resolves to the same identity
    ('a file confirmed kept by that branch is not remembered, so it can be linked again this same pass',
     '                have.add((st.st_dev, st.st_ino))  # another pass already linked it under this name\n',
     '                pass\n'),
    # G2: a NetFlow data reset (flush_all.sh) deletes flowd.log* without
    # rotating it; the marker lets the next pass notice and drop what it kept
    ('a NetFlow data reset is not detected, so the stale kept log outlives it',
     '            if marker not in rotated_ids:', '            if False:'),
    ('the marker is never written, so a reset is never noticed next time',
     '    if current_id is not None:\n        _write_marker(kept, current_id)\n', ''),
    # G3: the kept files are blind to how little space is left on their filesystem
    ('the free-space floor is disabled', '    while remaining and _free(kept) < floor:', '    while False:'),
    # G5c: a stray file beside the log (a manual backup, a leftover naming
    # scheme) must not be mistaken for one of core's own rotated files
    ('a stray file beside the log mistaken for one of cores rotated ones',
     "ROTATED = re.compile(r'\\.[0-9]+')", "ROTATED = re.compile(r'\\..+')"),
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
    for args in ((LIVE, 'LIVE_PY', 'test_live.py', MUTANTS), (FLOWS, 'FLOWS_PY', 'test_flows.py', FLOW_MUTANTS),
                 (KEEP, 'KEEP_PY', 'test_keep.py', KEEP_MUTANTS)):
        if not run(*args, survivors):
            return 2
    return 1 if survivors else 0


if __name__ == '__main__':
    sys.exit(main())
