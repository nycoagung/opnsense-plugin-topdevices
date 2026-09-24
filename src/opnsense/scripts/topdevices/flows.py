#!/usr/local/bin/python3
"""TopDevices: exact per-device totals for a recent window, read on demand from
NetFlow's raw flow log (/var/log/flowd.log and its rotations). Run by configd:

    flows.py totals FROM TO           every device's bytes, both scopes
    flows.py device IP FROM TO        one device's peers and ports, both scopes

FROM and TO are epoch seconds. It prints one JSON object and exits 0, errors
included ({"error": "..."}): configd returns stdout as the answer, and turns a
failing exit into "Execute error". Design:
docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md, and for the kept log
beside it and the 50-hour reach, docs/superpowers/specs/2026-09-24-keep-flow-log-design.md
"""
import calendar
import datetime
import glob
import ipaddress
import json
import math
import multiprocessing
import os
import re
import socket
import struct
import sys
import time

VERSION = '0.3.0'

LOG = '/var/log/flowd.log'
KEPT_DIR = 'topdevices'          # keep.py's links beside the log: rotated files core deleted, or will (2026-09-24 spec §5)
NETFLOW_LIB = '/usr/local/opnsense/scripts/netflow'  # core's parser, interface map, aggregates
HOUR = 3600
DAY = 86400
SLACK = 300              # the browser's clock may be a few minutes off the firewall's
REACH = 50 * HOUR        # a raw-log range starts at most this far back: Yesterday at any hour (2026-09-24 spec §4)
TOP = 100                # the device panel lists at most 100 rows
MAX_FILE = 40 * 1024 * 1024  # core rotates the log at 10 MB (flowd_aggregate.py): far past that, nothing rotates it
GAP = 900                # more than this between two files, and one is missing between them (2026-09-24 spec §4)
DIGITS = re.compile(r'[0-9]+')

# core's lib/flowparser.py: the fields a record may carry, in order of appearance
FIELDS = [('tag', 4), ('recv_time', 8), ('proto_flags_tos', 4),
          ('agent_addr4', 4), ('agent_addr6', 16), ('src_addr4', 4), ('src_addr6', 16),
          ('dst_addr4', 4), ('dst_addr6', 16), ('gateway_addr4', 4), ('gateway_addr6', 16),
          ('srcdst_port', 4), ('packets', 8), ('octets', 8), ('if_indices', 8),
          ('agent_info', 16), ('flow_times', 8), ('as_info', 12), ('flow_engine_info', 12)]
HEAD = struct.Struct('>BBHI')        # version, length in 32-bit words, reserved, field mask
# a record core's parser skips lacks one of these; an IPv6 one lacks the IPv4 addresses
REQUIRED = ('recv_time', 'agent_info', 'packets', 'octets', 'src_addr4', 'dst_addr4')


# ---------------------------------------------------------------- one record


def layout(mask, ports=False):
    """One Struct for the fields needed from records with this field mask, or None
    for a record core skips and for an IPv6 one. It unpacks, in this order:
    recv_sec, src, dst, [src_port, dst_port,] octets, [if_in, if_out,] uptime_ms,
    [flow_start, flow_finish] - the bracketed ones only when the mask has them."""
    at, off = {}, 0
    for i, (name, size) in enumerate(FIELDS):
        if mask & (1 << i):
            at[name] = off
            off += size
    if not all(k in at for k in REQUIRED):
        return None
    parts = [(at['recv_time'], 'I'), (at['src_addr4'], 'I'), (at['dst_addr4'], 'I')]
    has_ports = ports and 'srcdst_port' in at
    if has_ports:
        parts.append((at['srcdst_port'], 'HH'))
    parts.append((at['octets'], 'Q'))
    has_if = 'if_indices' in at
    if has_if:
        parts.append((at['if_indices'], 'II'))
    parts.append((at['agent_info'], 'I'))
    has_ft = 'flow_times' in at
    if has_ft:
        parts.append((at['flow_times'], 'II'))
    fmt, pos = '>', 0
    for o, code in parts:                    # offsets increase in this order
        if o > pos:
            fmt += '%dx' % (o - pos)
        fmt += code
        pos = o + struct.calcsize('>' + code)
    return struct.Struct(fmt), has_ports, has_if, has_ft


def records(buf, ports=False):
    """Every IPv4 record core's parser would yield, as (recv, src, dst, src_port,
    dst_port, octets, if_in, if_out, flow_start, flow_end, dur_ms), the times from
    core's own expressions (lib/flowparser.py). Ports are 0 and interfaces -1 when
    a record lacks them, as core leaves them; ports are read only when asked for."""
    layouts, off, n = {}, 0, len(buf)
    head = HEAD.unpack_from
    while off + 8 <= n:
        _, words, _, mask = head(buf, off)
        body = off + 8
        off = body + words * 4
        if off > n:
            break                            # the last record is still being written
        lay = layouts.get(mask)
        if lay is None:
            lay = layouts[mask] = layout(mask, ports) or False
        if not lay:
            continue
        st, has_ports, has_if, has_ft = lay
        v = st.unpack_from(buf, body)
        if has_ports:
            src_port, dst_port, i = v[3], v[4], 5
        else:
            src_port = dst_port = 0
            i = 3
        octets = v[i]
        if has_if:
            if_in, if_out = v[i + 1], v[i + 2]
            i += 3
        else:
            if_in = if_out = -1
            i += 1
        uptime = v[i]
        start, finish = (v[i + 1], v[i + 2]) if has_ft else (uptime, uptime)
        if finish > uptime:
            continue                         # core: impossible data
        flow_end = v[0] - (uptime - finish) / 1000.0
        flow_start = flow_end - (finish - start) / 1000.0
        yield v[0], v[1], v[2], src_port, dst_port, octets, if_in, if_out, flow_start, flow_end, finish - start


def share(flow_start, flow_end, dur_ms, octets, lo, hi):
    """A record's bytes inside [lo, hi): spread evenly over its duration with core's
    own expressions in core's order (lib/aggregates/__init__.py add()), so sums match
    core's aggregates to the byte. A record of zero duration counts whole where it
    starts, and one of negative duration (bogus) as core's arithmetic has it."""
    if dur_ms > 0:
        ov = min(flow_end, hi) - max(flow_start, lo)
        return ov / (dur_ms / 1000.0) * octets if ov > 0 else 0.0
    if not lo <= flow_start < hi:
        return 0.0
    if dur_ms < 0:
        return (flow_end - flow_start) / (dur_ms / 1000.0) * octets
    return octets


# ---------------------------------------------------------------- coverage


def plan(frm, to, L):
    """What covers each part of a totals answer for [frm, to), the log being
    complete from L (spec §4): all traffic from the raw log over raw_all and from
    core's hourly records over hourly (or None); internet only from the raw log
    over inet. A span whose start is not before its end is empty."""
    if frm >= L:
        return {'raw_all': (frm, to), 'hourly': None, 'inet': (frm, to)}
    seam = min(math.ceil(L / HOUR) * HOUR, to)
    return {'raw_all': (seam, to), 'hourly': (frm, seam), 'inet': (L, to)}


def hourly_weight(bucket, lo, hi, now):
    """How much of an hourly bucket falls in [lo, hi): its overlap over the time it
    spans, which ends at now while the bucket is still in progress (spec §4)."""
    end = min(bucket + HOUR, now)
    if end <= bucket:
        return 0.0
    ov = min(end, hi) - max(bucket, lo)
    return ov / (end - bucket) if ov > 0 else 0.0


class Net:
    """Which addresses are devices and which interfaces lead upstream, as integers
    for speed. A device is an IPv4 address in a local subnet, except each subnet's
    broadcast address; the firewall's own addresses are kept (the table lists them
    as '<network> gateway')."""

    def __init__(self, local_nets, upstream_ifindex, upstream_names):
        self.nets = [(int(n.network_address), int(n.netmask)) for n in local_nets]
        self.bcast = {int(n.broadcast_address) for n in local_nets if n.prefixlen < 31}
        self.upstream = frozenset(upstream_ifindex)
        self.upstream_names = list(upstream_names)
        self._dev = {}

    def is_device(self, ip):
        d = self._dev.get(ip)
        if d is None:
            d = self._dev[ip] = ip not in self.bcast and any(ip & m == n for n, m in self.nets)
        return d


def _ip4(text):
    """An IPv4 address as an integer, or None for anything else (IPv6)."""
    try:
        return int(ipaddress.IPv4Address(text))
    except ValueError:
        return None


def _ip_str(ip):
    return socket.inet_ntoa(struct.pack('>I', ip))


def _epoch(value):
    """A bucket's start as core's aggregates return it (a naive UTC datetime), or seconds."""
    if isinstance(value, datetime.datetime):
        return calendar.timegm(value.timetuple())
    return int(value)


def hourly_fill(rows, lo, hi, now, net):
    """All-traffic [down, up] per device from core's hourly totals over [lo, hi).
    A totals row holds one address: 'out' rows are bytes delivered to it, 'in' rows
    bytes it sent (core's FlowSourceAddrTotals.add())."""
    out = {}
    for row in rows:
        ip = _ip4(row['src_addr'])
        if ip is None or not net.is_device(ip):
            continue
        w = hourly_weight(_epoch(row['start_time']), lo, hi, now)
        if w:
            t = out.setdefault(ip, [0.0, 0.0])
            t[0 if row['direction'] == 'out' else 1] += w * float(row['octets'] or 0)
    return out


# ---------------------------------------------------------------- the log


def _names(log):
    """Every name the flow log has now: core's files, then keep.py's links in the
    directory beside them (2026-09-24 spec §4)."""
    kept = os.path.join(glob.escape(os.path.dirname(log)), KEPT_DIR, 'flowd.*')
    return glob.glob(glob.escape(log) + '*') + glob.glob(kept)


def open_log(log=LOG):
    """Every flow log file - core's and the kept ones - opened now, once each
    however many names it has, oldest first: [(fd, last write, size)].
    Reading through descriptors keeps a rotation - which renames every file and
    deletes the oldest - from mixing up which is which once they are open. One
    that lands while they are being opened is caught by listing them again: if
    any name now leads to another file, they are all opened afresh."""
    for _ in range(5):                            # a rotation takes milliseconds
        opened, ids, seen = [], {}, set()
        try:
            for path in _names(log):
                try:
                    fd = os.open(path, os.O_RDONLY)
                except FileNotFoundError:
                    continue                      # rotated or pruned away since the listing
                st = os.fstat(fd)
                ids[path] = (st.st_dev, st.st_ino)
                if ids[path] in seen:
                    os.close(fd)                  # a kept name for a file already open
                    continue
                seen.add(ids[path])
                opened.append((fd, st.st_mtime, st.st_size))
        except BaseException:
            close_log(opened)
            raise
        if _files_now(log) == ids:
            opened.sort(key=lambda f: f[1])
            return opened
        close_log(opened)
    raise RuntimeError('the NetFlow flow log kept rotating while it was opened')


def _files_now(log):
    """{path: (device, inode)} of every name the flow log has at this moment."""
    now = {}
    for path in _names(log):
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue
        now[path] = (st.st_dev, st.st_ino)
    return now


def close_log(opened):
    for fd, _, _ in opened:
        try:
            os.close(fd)
        except OSError:
            pass


def _read(fd):
    """The whole file behind fd, as far as it is written now."""
    size = os.fstat(fd).st_size
    chunks, off = [], 0
    while off < size:
        chunk = os.pread(fd, size - off, off)
        if not chunk:
            break
        chunks.append(chunk)
        off += len(chunk)
    return b''.join(chunks)


def _first_recv(fd):
    """The receive time of the first IPv4 record in a file's first 64 KB, or None."""
    for r in records(os.pread(fd, 65536, 0)):
        return r[0]
    return None


def log_from(opened):
    """When the log becomes complete (2026-09-24 spec §4): the receive time of the
    first record of the oldest file in the newest unbroken run. More than GAP
    between one file's last write and the next one's first record means a file
    is missing between them. A file with no IPv4 record in its first 64 KB leaves
    the boundary before it unjudged. None when the log holds no IPv4 record."""
    L = later = None                      # later: the first record of the next newer file
    for fd, mtime, _ in reversed(opened):
        first = _first_recv(fd)
        if later is not None and later - mtime > GAP:
            break
        if first is not None:
            L = first
        later = first
    return L


def files_for(opened, lo):
    """The files that can hold records overlapping a span from lo - those written to
    at or after lo, since a record received before lo ended before it - biggest
    first, so the last file a worker starts is a small one."""
    return sorted((f for f in opened if f[1] >= lo), key=lambda f: -f[2])


def scan_totals(job):
    """One file's bytes per device: {ip: [all_down, all_up, inet_down, inet_up]}, all
    traffic over [a_lo, a_hi) and internet only over [i_lo, i_hi) (spec §5.3). The
    destination device downloads, the source device uploads; a download is internet
    when it came in upstream, an upload when it left upstream."""
    fd, a_lo, a_hi, i_lo, i_hi, net = job
    lo = min(a_lo, i_lo)
    acc = {}
    for recv, src, dst, _, _, octets, if_in, if_out, fs, fe, dur in records(_read(fd)):
        if recv < lo:
            continue                              # ended before either span
        a = share(fs, fe, dur, octets, a_lo, a_hi) if a_hi > a_lo else 0.0
        i = share(fs, fe, dur, octets, i_lo, i_hi) if i_hi > i_lo else 0.0
        if not (a or i):
            continue
        if net.is_device(dst):
            t = acc.get(dst) or acc.setdefault(dst, [0.0, 0.0, 0.0, 0.0])
            t[0] += a
            if if_in in net.upstream:
                t[2] += i
        if net.is_device(src):
            t = acc.get(src) or acc.setdefault(src, [0.0, 0.0, 0.0, 0.0])
            t[1] += a
            if if_out in net.upstream:
                t[3] += i
    return acc


def merge_totals(parts):
    out = {}
    for part in parts:
        for ip, v in part.items():
            t = out.setdefault(ip, [0.0, 0.0, 0.0, 0.0])
            for k in range(4):
                t[k] += v[k]
    return out


def run_parallel(fn, jobs, workers):
    """fn over every job, in up to `workers` forked processes. The descriptors in
    the jobs are inherited: the log files are opened before the fork."""
    if workers <= 1 or len(jobs) <= 1:
        return [fn(j) for j in jobs]
    with multiprocessing.get_context('fork').Pool(min(workers, len(jobs))) as pool:
        return pool.map(fn, jobs, chunksize=1)


def answer_totals(frm, to, now, opened, net, hourly_rows, workers):
    """The totals answer (spec §5.1) for [frm, to) at now. hourly_rows(lo, hi) gives
    core's hourly FlowSourceAddrTotals rows for buckets starting in [lo, hi)."""
    L = log_from(opened)
    if L is None:
        raise ValueError('the NetFlow flow log holds no flows yet')
    # all traffic is complete from the log's start, or through the hourly records
    # within their day: an earlier start is answered from there (2026-09-24 spec §4).
    # Clamped to `to` as well: a range wholly before that start is answered as an
    # empty span at `to`, not one with frm pushed past it.
    frm = min(to, max(frm, min(L, now - DAY - SLACK)))
    p = plan(frm, to, L)
    (a_lo, a_hi), (i_lo, i_hi) = p['raw_all'], p['inet']
    jobs = [(fd, a_lo, a_hi, i_lo, i_hi, net) for fd, _, _ in files_for(opened, min(a_lo, i_lo))]
    acc = merge_totals(run_parallel(scan_totals, jobs, workers))
    if p['hourly']:
        h_lo, h_hi = p['hourly']
        for ip, (down, up) in hourly_fill(hourly_rows(h_lo, h_hi), h_lo, h_hi, now, net).items():
            t = acc.setdefault(ip, [0.0, 0.0, 0.0, 0.0])
            t[0] += down
            t[1] += up
    return {'now': now, 'log_from': L,
            'all': {'from': frm, 'to': to, 'hourly_until': p['hourly'][1] if p['hourly'] else None},
            'inet': {'from': i_lo, 'to': to},
            'wan': net.upstream_names,
            'devices': {_ip_str(ip): [int(round(x)) for x in v] for ip, v in sorted(acc.items())},
            'files': len(jobs), 'workers': max(1, min(workers, len(jobs)))}


# ---------------------------------------------------------------- one device


def scan_device(job):
    """One file's peers and ports for one device over [lo, hi), in both scopes:
    ({peer: [all, inet]}, {port: [all, inet]}), both directions summed. A port is the
    service port as core's details define it: the lower of the flow's two."""
    fd, lo, hi, ip, net = job
    peers, ports = {}, {}
    for recv, src, dst, sp, dp, octets, if_in, if_out, fs, fe, dur in records(_read(fd), ports=True):
        if recv < lo or (src != ip and dst != ip):
            continue
        b = share(fs, fe, dur, octets, lo, hi)
        if not b:
            continue
        if dst == ip:                     # its download: internet when it came in upstream
            peer, inet = src, if_in in net.upstream
        else:                             # its upload: internet when it left upstream
            peer, inet = dst, if_out in net.upstream
        port = min(sp, dp)
        for key, table in ((peer, peers), (port, ports)):
            t = table.get(key) or table.setdefault(key, [0.0, 0.0])
            t[0] += b
            if inet:
                t[1] += b
    return peers, ports


def _merge2(into, part):
    for key, v in part.items():
        t = into.setdefault(key, [0.0, 0.0])
        t[0] += v[0]
        t[1] += v[1]


def top(table, k, name, n=TOP):
    """The n biggest entries of table by scope k (0 all, 1 internet), biggest first,
    as [[name(key), bytes]]."""
    items = sorted(((key, v[k]) for key, v in table.items() if v[k] > 0), key=lambda kv: (-kv[1], kv[0]))
    return [[name(key), int(round(b))] for key, b in items[:n]]


def _port_str(port):
    return str(port) if port else 'other'


def answer_device(ip, frm, to, now, opened, net, workers):
    """One device's peers and ports over [max(frm, L), to), both scopes (spec §5.5)."""
    if not net.is_device(ip):
        raise ValueError('%s is not a local device' % _ip_str(ip))
    L = log_from(opened)
    if L is None:
        raise ValueError('the NetFlow flow log holds no flows yet')
    lo = max(frm, L)
    peers, ports, jobs = {}, {}, []
    if lo < to:
        jobs = [(fd, lo, to, ip, net) for fd, _, _ in files_for(opened, lo)]
        for p, q in run_parallel(scan_device, jobs, workers):
            _merge2(peers, p)
            _merge2(ports, q)
    return {'now': now, 'log_from': L, 'ip': _ip_str(ip), 'from': lo, 'to': to,
            'peers': {'all': top(peers, 0, _ip_str), 'inet': top(peers, 1, _ip_str)},
            'ports': {'all': top(ports, 0, _port_str), 'inet': top(ports, 1, _port_str)},
            'files': len(jobs), 'workers': max(1, min(workers, len(jobs)))}


# ---------------------------------------------------------------- command line


def parse_args(args, now):
    """The request {'mode', 'from', 'to'[, 'ip']} from the command line, checked as
    spec §5.1 says. Raises ValueError with the reason."""
    if len(args) == 3 and args[0] == 'totals':
        mode, ip, frm, to = 'totals', None, args[1], args[2]
    elif len(args) == 4 and args[0] == 'device':
        mode, ip, frm, to = 'device', args[1], args[2], args[3]
    else:
        raise ValueError('usage: flows.py totals FROM TO | flows.py device IP FROM TO')
    if not (DIGITS.fullmatch(frm) and DIGITS.fullmatch(to)):
        raise ValueError('FROM and TO must be whole epoch seconds')
    frm, to = int(frm), int(to)
    if frm >= to:
        raise ValueError('FROM must be before TO')
    # Totals only: a device panel asks for its table's window, which ages while
    # the table is on screen, and its lists cover what the log holds (answer_device)
    if mode == 'totals' and frm < now - REACH - SLACK:
        raise ValueError("FROM is more than 50 hours ago: that range is read from NetFlow's records")
    if to > now + SLACK:
        raise ValueError('TO is in the future')
    to = min(to, now)
    if frm >= to:
        raise ValueError('the range has not begun yet')
    req = {'mode': mode, 'from': frm, 'to': to}
    if mode == 'device':
        req['ip'] = _ip4(ip)
        if req['ip'] is None:
            raise ValueError('IP must be an IPv4 address')
    return req


def _core():
    """Core's NetFlow library (lib.flowparser, lib.parse, lib.aggregates) on the path."""
    if NETFLOW_LIB not in sys.path:
        sys.path.insert(0, NETFLOW_LIB)


def core_interfaces():
    """{ifindex: device}: core's own map (lib/parse.py Interfaces, from ifinfo)."""
    _core()
    from lib.parse import Interfaces
    index = getattr(Interfaces(), '_if_index', None)
    if not isinstance(index, dict):
        raise RuntimeError("core's interface map has changed (lib/parse.py)")
    return {int(k): v for k, v in index.items()}


def core_hourly(lo, hi):
    """Core's hourly FlowSourceAddrTotals rows for buckets starting in [lo, hi)."""
    _core()
    from lib.aggregates.source import FlowSourceAddrTotals
    return list(FlowSourceAddrTotals(HOUR).get_data(lo, hi))


def upstream_ifindex(index, upstream_devs):
    """The interface numbers core's aggregator gives the upstream devices, from
    its map {number: device}. No upstream device at all (no default route and no
    public address), or one missing from the map (ifinfo failed, or its output
    changed), would silently leave internet only empty: an error."""
    if not upstream_devs:
        raise RuntimeError('no upstream interface: no default route and no public address')
    missing = [d for d in upstream_devs if d not in index.values()]
    if missing:
        raise RuntimeError("core's interface map has no number for %s (ifinfo)" % ', '.join(missing))
    return sorted(i for i, name in index.items() if name in upstream_devs)


def system_net():
    """This firewall's devices and upstream interfaces, by the Live sampler's rules
    (live.py Topology), with upstream numbered as core's aggregator numbers it."""
    import live                                   # the sampler, beside this script
    topo = live.Topology(live.parse_ifconfig(live._run(live.IFCONFIG)),
                         live.parse_default_devs(live._run(live.ROUTES)))
    upstream = upstream_ifindex(core_interfaces(), topo.upstream_devs)
    return Net(topo.local_nets, upstream, list(topo.upstream_devs))


def main(argv, now=None, log=LOG, system=None, hourly=None):
    """Answer the command line with one JSON line on stdout. Always returns 0."""
    sys.dont_write_bytecode = True        # nothing written on the firewall: no __pycache__ for live.py or core's library
    t0 = time.monotonic()
    opened = []
    try:
        now = int(time.time()) if now is None else int(now)
        req = parse_args(argv[1:], now)
        net = (system or system_net)()
        opened = open_log(log)
        if not opened:
            raise ValueError('no NetFlow flow log: is NetFlow capture enabled?')
        biggest = max(size for _, _, size in opened)
        if biggest > MAX_FILE:            # only core's aggregator rotates the log
            raise ValueError("the NetFlow flow log has a %d MB file, far past the 10 MB core rotates at: "
                             "is NetFlow's aggregator running?" % (biggest // (1024 * 1024)))
        workers = max(1, (os.cpu_count() or 2) // 2)
        if req['mode'] == 'totals':
            out = answer_totals(req['from'], req['to'], now, opened, net, hourly or core_hourly, workers)
        else:
            out = answer_device(req['ip'], req['from'], req['to'], now, opened, net, workers)
        out['cost_ms'] = int(round((time.monotonic() - t0) * 1000))
    except Exception as exc:          # never a traceback: configd returns stdout as the answer
        out = {'error': str(exc) or exc.__class__.__name__}
    finally:
        close_log(opened)
    out['v'] = VERSION
    sys.stdout.write(json.dumps(out, separators=(',', ':')) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
