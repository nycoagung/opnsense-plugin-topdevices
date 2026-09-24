"""On the firewall: flows.py against core's own parser and aggregators, over the
live raw flow log, and how long flows.py takes as configd runs it. Once the kept
log reaches yesterday's midnight, also over that whole day (a few minutes: core's
aggregators take their time).

    python3 tests/parity_flows.py        (as root, from the extracted branch tarball)

Prints counts, timings and agreement only - no addresses. Exit status 0 when every
comparison agrees to under 1 byte per device.
"""
import copy
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE.parent / 'src/opnsense/scripts/topdevices'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def gap(ours, core):
    ips = set(ours) | set(core)
    worst = max((abs(ours.get(i, [0, 0])[k] - core.get(i, [0, 0])[k]) for i in ips for k in (0, 1)), default=0.0)
    return len(ips), worst


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


def main():
    load('live', SRC / 'live.py')
    flows = load('flows', SRC / 'flows.py')
    flows._core()
    from lib.parse import parse_flow
    from lib.aggregates.source import FlowSourceAddrDetails, FlowSourceAddrTotals
    now = int(time.time())
    net = flows.system_net()
    opened = flows.open_log()
    L = flows.log_from(opened)
    print('log: %d files, complete from %s (%.1f h); upstream %s' % (len(opened), time.ctime(L), (now - L) / 3600,
                                                                   net.upstream_names))

    # the last whole hour that ended 45 min ago: no record still to come can touch
    # it, and it lies in the newest files, so core reads little of the log and a
    # rotation meanwhile drops nothing it needs
    hour = (now - 2700) // 3600 * 3600 - 3600
    if hour < L:
        print('the log is too short for the comparison (needs a whole hour ending 45 min ago)')
        return 1
    five = hour + 900, hour + 2700

    tmp = tempfile.mkdtemp(prefix='parity_flows.')
    try:
        totals, details = FlowSourceAddrTotals(3600, tmp), FlowSourceAddrDetails(300, tmp)
        n = 0
        for r in parse_flow(hour - 3600, flows.LOG):       # received after an hour before it: the rest ended before it
            if r is None:
                continue
            totals.add(copy.copy(r))
            details.add(copy.copy(r))
            n += 1
        totals.commit()
        details.commit()
        core_all, core_inet = sums(flows, net, totals.get_data(hour, hour + 3600), details.get_data(*five))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    workers = max(1, (os.cpu_count() or 2) // 2)
    a = flows.answer_totals(hour, hour + 3600, now, opened, net, flows.core_hourly, workers)
    i = flows.answer_totals(five[0], five[1], now, opened, net, flows.core_hourly, workers)
    ours_all = {flows._ip4(k): v[:2] for k, v in a['devices'].items()}
    ours_inet = {flows._ip4(k): v[2:] for k, v in i['devices'].items() if v[2] or v[3]}
    dev_a, gap_a = gap(ours_all, core_all)
    dev_i, gap_i = gap(ours_inet, core_inet)
    print('core read %d records once, into scratch aggregates' % n)
    print('all traffic, %s-%s:   %3d devices, largest per-device gap %.3f B'
          % (time.strftime('%H:%M', time.localtime(hour)), time.strftime('%H:%M', time.localtime(hour + 3600)), dev_a, gap_a))
    print('internet only, %s-%s: %3d devices, largest per-device gap %.3f B'
          % (time.strftime('%H:%M', time.localtime(five[0])), time.strftime('%H:%M', time.localtime(five[1])), dev_i, gap_i))

    # as configd runs it, through the installed action when there is one, for ranges
    # ending now: core's comparison above took its time
    now = int(time.time())
    lt = time.localtime(now)
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    yesterday = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday - 1, 0, 0, 0, 0, 0, -1)))
    for label, frm, to in (('Last hour', now - 3600, now), ('Today', midnight, now), ('Yesterday', yesterday, midnight),
                           ('Last 24 hours', now - 86400, now)):
        t = time.monotonic()
        out = subprocess.run(['/usr/local/sbin/configctl', 'topdevices', 'flows', 'totals', str(frm), str(to)],
                             capture_output=True, text=True).stdout
        wall = time.monotonic() - t
        try:
            ans = json.loads(out)
        except ValueError:
            print('%-14s configctl gave no JSON: %r' % (label, out[:80]))
            continue
        if 'error' in ans:
            print('%-14s error: %s' % (label, ans['error']))
            continue
        short = ('' if ans['all']['from'] <= frm else ', all traffic from %s' % time.ctime(ans['all']['from'])) + \
                ('' if ans['inet']['from'] <= frm else ', internet only from the log start')
        print('%-14s via configd %.2f s (script %d ms, %d files, %d workers)%s'
              % (label, wall, ans['cost_ms'], ans['files'], ans['workers'], short))

    # spec §14: do this firewall's records carry ports (the device panel's Top ports)?
    buf, off, total, with_ports = flows._read(opened[-1][0]), 0, 0, 0
    bit = 1 << [name for name, _ in flows.FIELDS].index('srcdst_port')
    while off + 8 <= len(buf):
        _, words, _, mask = flows.HEAD.unpack_from(buf, off)
        off += 8 + words * 4
        total += 1
        with_ports += bool(mask & bit)
    print('records in the newest file: %d, %d of them with ports' % (total, with_ports))
    flows.close_log(opened)
    day = day_parity(flows, net, int(time.time()))
    return 0 if gap_a < 1 and gap_i < 1 and (day is None or day < 1) else 1


if __name__ == '__main__':
    sys.exit(main())
