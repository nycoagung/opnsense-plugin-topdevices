"""On the firewall: flows.py against core's own parser and aggregators, over the
live raw flow log, and how long flows.py takes as configd runs it.

    python3 tests/parity_flows.py        (as root, from the extracted branch tarball)

Prints counts, timings and agreement only - no addresses. Exit status 0 when both
comparisons agree to under 1 byte per device.
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

    # windows inside the log, ended 45 min ago: no record still to come can touch them
    hour = -(-L // 3600) * 3600
    if hour + 3600 > now - 2700:
        print('the log is too short for the comparison (needs an hour ending 45 min ago)')
        return 1
    five = hour + 900, hour + 2700

    tmp = tempfile.mkdtemp(prefix='parity_flows.')
    try:
        totals, details = FlowSourceAddrTotals(3600, tmp), FlowSourceAddrDetails(300, tmp)
        n = 0
        for r in parse_flow(hour - 3600, flows.LOG):       # every record received since an hour before
            if r is None:
                continue
            totals.add(copy.copy(r))
            details.add(copy.copy(r))
            n += 1
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

    # as configd runs it, through the installed action when there is one
    lt = time.localtime(now)
    midnight = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
    for label, frm in (('Last hour', now - 3600), ('Today', midnight), ('Last 24 hours', now - 86400)):
        t = time.monotonic()
        out = subprocess.run(['/usr/local/sbin/configctl', 'topdevices', 'flows', 'totals', str(frm), str(now)],
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
        print('%-14s via configd %.2f s (script %d ms, %d files, %d workers)%s'
              % (label, wall, ans['cost_ms'], ans['files'], ans['workers'],
                 '' if ans['inet']['from'] <= frm else ', internet only from the log start'))

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
    return 0 if gap_a < 1 and gap_i < 1 else 1


if __name__ == '__main__':
    sys.exit(main())
