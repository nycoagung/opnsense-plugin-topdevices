#!/usr/local/bin/python3
"""
TopDevices live sampler.

Reads the pf state table once per interval and writes per-device traffic rates
to stdout as server-sent events. configd runs it as a stream_output action and
relays the stream to the dashboard widget:

    GET /api/topdevices/live/stream/{interval}
      -> LiveController -> configd 'topdevices live <interval>' -> live.py

Design and the measurements behind it:
docs/superpowers/specs/2026-09-23-live-traffic-design.md

All logic lives in the pure functions below (parse_*, Topology, deltas,
credits, aggregate, wan_rates, next_interval, Drift, format_event) so it is
unit-tested without OPNsense. run_loop() wires them to the real commands, and
main() is the only place that touches the process's stdout.
"""
import ipaddress
import json
import os
import re
import resource
import subprocess
import sys
import time

VERSION = '0.1.0'

PFCTL = ('/sbin/pfctl', '-vvs', 'state')
IFCONFIG = ('/sbin/ifconfig',)
IFINFO = '/usr/local/sbin/ifinfo'
ROUTES = ('/usr/bin/netstat', '-rWn', '-f', 'inet')

# Local means "inside RFC 1918 and configured on a firewall interface". Never use
# ipaddress.is_private: it also answers True for the documentation ranges and
# others, and its meaning has changed between Python releases.
RFC1918 = tuple(ipaddress.ip_network(n) for n in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
LOOPBACK = ipaddress.ip_network('127.0.0.0/8')
LINK_LOCAL = ipaddress.ip_network('169.254.0.0/16')

MIN_INTERVAL, MAX_INTERVAL = 1, 10
ETHER_HEADER = 14            # bytes per frame the interface counts and pf does not
CPU_BUDGET = 0.10            # a sampler may use at most this share of one core
COST_EMA_SAMPLES = 5
LIFETIME = 3600              # seconds; then a clean exit and the browser reconnects
TOPOLOGY_REFRESH = 60        # seconds between re-reading interface addresses
DRIFT_WINDOW = 60            # seconds of history the drift check judges
DRIFT_MIN_BPS = 1000000      # a direction is judged only above this average rate
DRIFT_BAND = (0.90, 1.10)
DRIFT_MAX_V6_SHARE = 0.01    # IPv6 above this share of WAN bytes suspends the check
TOP_PEERS = 10
TOP_PORTS = 5

INET, ALL, FW = 'inet', 'all', 'fw'
PORT_PROTOCOLS = ('tcp', 'udp', 'sctp')

_AGE = re.compile(r'^age (\d+):(\d+):(\d+)')
_BYTES = re.compile(r', (\d+):(\d+) bytes(?:,|$)')
_ID = re.compile(r'^id: ([0-9a-f]+) creatorid: ([0-9a-f]+)')
_IFINFO_HEAD = re.compile(r'^Interface (\S+) \(([^)]+)\):$')
_UNSAFE = re.compile(r'[<>&]')


# ---------------------------------------------------------------- parsing


def _host(token):
    """'192.0.2.1:443' -> ('192.0.2.1', 443, 4); '2001:db8::1[443]' -> ('2001:db8::1', 443, 6)."""
    token = token.strip('()')
    if token.count(':') > 1 or '[' in token:
        addr, _, rest = token.partition('[')
        port = rest.rstrip(']')
        return addr, int(port) if port.isdigit() else 0, 6
    addr, _, port = token.partition(':')
    return addr, int(port) if port.isdigit() else 0, 4


def parse_header(line):
    """First line of a state -> dict, or None when the line is not a state header.

    Layout, from pfctl's pf_print_state.c:
        ifname proto host1 [(nat1)] ->|<- host2 [(nat2)] STATE
    With '->' (out) host1 is the source and nat1 its address before NAT. With
    '<-' (in) host1 is the destination after any redirect and nat1 the original
    destination. The arrow is found by value, not by position: either host may
    carry a translation, and a fixed index misreads states where both do.
    """
    parts = line.split()
    arrow = next((i for i, p in enumerate(parts) if p in ('->', '<-')), None)
    if arrow is None or arrow < 3 or arrow + 2 >= len(parts):
        return None
    left, right = parts[2:arrow], parts[arrow + 1:-1]
    if not left or not right or left[0].startswith('(') or right[0].startswith('('):
        return None
    h1, h2 = _host(left[0]), _host(right[0])
    nat = _host(left[1]) if len(left) > 1 and left[1].startswith('(') else None
    if parts[arrow] == '->':
        direction, src, dst = 'out', h1, h2
    else:
        direction, src, dst = 'in', h2, h1
    # only TCP, UDP and SCTP have ports; for ICMP pfctl prints the query id there
    port = dst[1] if parts[1] in PORT_PROTOCOLS else 0
    return {'dir': direction, 'af': h1[2], 'src': src[0], 'dst': dst[0], 'dst_port': port,
            'nat': nat[0] if nat else None}


def parse_states(text):
    """`pfctl -vvs state` output -> ({'id/creatorid': state}, unparsed).

    A state is a header line at column 0 followed by indented lines: a TCP
    sequence-window line, the counters line (age, pkts, bytes, ...), the id line
    and possibly an origif line. `unparsed` counts header lines that are not
    states, indented lines of an unknown shape and states that never got a
    usable counters or id line, so a format change shows up as a number instead
    of as silently wrong rates.
    """
    states = {}
    unparsed = 0
    cur = None
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            if cur is not None:
                unparsed += 1                    # the previous state never reached its id line
            cur = parse_header(line)
            if cur is None:
                unparsed += 1
            continue
        body = line.strip()
        if body.startswith('age '):
            age, byts = _AGE.match(body), _BYTES.search(body)
            if cur is None:
                continue
            if age and byts:
                cur['age'] = int(age.group(1)) * 3600 + int(age.group(2)) * 60 + int(age.group(3))
                cur['b0'], cur['b1'] = int(byts.group(1)), int(byts.group(2))
            else:
                unparsed += 1
                cur = None
        elif body.startswith('id: '):
            m = _ID.match(body)
            if cur is not None:
                if m and 'b0' in cur:
                    states['%s/%s' % (m.group(1), m.group(2))] = cur
                else:
                    unparsed += 1
            cur = None
        elif body.startswith('[') or body.startswith('origif: '):
            continue                             # TCP sequence window / original interface
        else:
            unparsed += 1
    if cur is not None:
        unparsed += 1
    return states, unparsed


def parse_ifconfig(text):
    """`ifconfig` output -> [(device, IPv4Interface)] for every inet address.

    Same reading as core's legacy_interfaces_details(): a device line carries
    'flags=' at column 0; an address line starts with a tab and 'inet ', and
    its netmask is the hex word after 'netmask'.
    """
    out = []
    dev = None
    for line in text.splitlines():
        if line and not line[0].isspace() and 'flags=' in line:
            dev = line.split(':', 1)[0]
        elif dev and line.startswith('\tinet '):
            parts = line.split()
            if 'netmask' not in parts:
                continue
            try:
                bits = bin(int(parts[parts.index('netmask') + 1], 16)).count('1')
                out.append((dev, ipaddress.ip_interface('%s/%d' % (parts[1], bits))))
            except (IndexError, ValueError):
                continue
    return out


def parse_ifinfo(text):
    """`ifinfo <dev>` output -> {device: {'rx', 'tx', 'rxp', 'txp', 'ether'}}.

    Same reading as core's legacy_interface_stats(): a heading per interface,
    then 'key: value' lines.
    """
    raw = {}
    cur = None
    for line in text.splitlines():
        m = _IFINFO_HEAD.match(line.strip())
        if m:
            cur = raw.setdefault(m.group(1), {})
        elif cur is not None and ':' in line:
            key, _, value = line.partition(':')
            cur[key.strip()] = value.strip()
    out = {}
    for dev, kv in raw.items():
        try:
            out[dev] = {
                'rx': int(kv['bytes received']), 'tx': int(kv['bytes transmitted']),
                'rxp': int(kv['packets received']), 'txp': int(kv['packets transmitted']),
                'ether': kv.get('type', '') == 'Ethernet',
            }
        except (KeyError, ValueError):
            continue
    return out


def parse_default_devs(text):
    """`netstat -rWn -f inet` output -> interfaces carrying an IPv4 default route.

    Same reading as core's show_routes.py: the columns are named by the
    'Destination Gateway ...' header line.
    """
    names, devs = None, []
    for line in text.splitlines():
        f = line.split()
        if len(f) > 2 and f[0] == 'Destination' and f[1] == 'Gateway':
            names = [x.lower() for x in f]
        elif names and 'netif' in names and len(f) > names.index('netif') and f[0] == 'default':
            dev = f[names.index('netif')]
            if dev not in devs:
                devs.append(dev)
    return devs


class Topology:
    """What the firewall's own interfaces and routes say about the network.

    Upstream is every interface carrying a default route, plus any interface
    with a public address. The route matters behind an ISP router (double
    NAT): there the WAN has an RFC 1918 address, and an address-only rule
    would call it local and credit no internet traffic at all. Local is then
    the RFC 1918 subnets of every other interface.
    """

    def __init__(self, ifaddrs, default_devs=()):
        self.local_nets = []
        self.fw_addrs = set()
        self.upstream_devs = []
        self.upstream_addrs = set()
        public = [dev for dev, iface in ifaddrs
                  if not any(iface.ip in net for net in RFC1918)
                  and iface.ip not in LOOPBACK and iface.ip not in LINK_LOCAL]
        for dev in list(default_devs) + public:
            if dev not in self.upstream_devs:
                self.upstream_devs.append(dev)
        for dev, iface in ifaddrs:
            ip = iface.ip
            self.fw_addrs.add(str(ip))
            if dev in self.upstream_devs:
                self.upstream_addrs.add(str(ip))
            elif any(ip in net for net in RFC1918) and iface.network not in self.local_nets:
                self.local_nets.append(iface.network)
        # traffic reaches a subnet's broadcast address (NetBIOS, SSDP), but it is
        # not a device - the NetFlow view drops these addresses too
        self.never_devices = set(self.fw_addrs) | {
            str(net.broadcast_address) for net in self.local_nets if net.prefixlen < 31}
        self._local = {}

    def is_local(self, addr):
        hit = self._local.get(addr)
        if hit is None:
            try:
                ip = ipaddress.ip_address(addr)
                hit = ip.version == 4 and any(ip in net for net in self.local_nets)
            except ValueError:
                hit = False
            self._local[addr] = hit
        return hit


# ---------------------------------------------------------------- accounting


def deltas(prev, cur, dt):
    """Bytes each state moved since the previous sample: [(state, b0, b1)].

    A state seen before contributes the difference. A new state contributes
    everything it carries if it is young enough to have started inside the
    interval. A negative difference means pf reused the id for a different
    connection, which is then treated as new. A vanished state contributes
    nothing: its final partial interval is lost.
    """
    out = []
    horizon = dt + 1
    for sid, s in cur.items():
        p = prev.get(sid)
        if p is not None:
            d0, d1 = s['b0'] - p['b0'], s['b1'] - p['b1']
            if d0 < 0 or d1 < 0:
                if s['age'] > horizon:
                    continue
                d0, d1 = s['b0'], s['b1']
        elif s['age'] <= horizon:
            d0, d1 = s['b0'], s['b1']
        else:
            continue
        if d0 or d1:
            out.append((s, d0, d1))
    return out


def credits(state, b0, b1, topo):
    """Who a state's bytes belong to: [(scope, device, peer, port, down, up)].

    'inet' and 'all' credit a device. 'fw' is the firewall's own upstream
    traffic, kept only for the drift check: device and peer are None and
    down/up are relative to the WAN. IPv6 states return [] and are counted by
    the caller. b0 is initiator->responder and b1 the reverse; for outbound NAT
    b1 is the download, verified against the kernel counters (spec 5.4).
    """
    if state['af'] != 4:
        return []
    src, dst, nat, port = state['src'], state['dst'], state['nat'], state['dst_port']
    local, fw, upstream, never = topo.is_local, topo.fw_addrs, topo.upstream_addrs, topo.never_devices
    out = []
    if state['dir'] == 'out':
        # 'all' is counted on ingress states only: every routed flow also has
        # an out state with identical counters, and counting both doubles it.
        if nat is not None and nat in fw:
            out.append((FW, None, None, port, b1, b0))
        elif nat is not None and local(nat) and not local(src):
            out.append((INET, nat, dst, port, b1, b0))
        elif nat is None and src in upstream and not local(dst):
            out.append((FW, None, None, port, b1, b0))
        return out
    if nat is not None and not local(nat) and local(dst) and dst not in never and not local(src):
        out.append((INET, dst, src, port, b0, b1))       # port-forward: remote initiator
    elif nat is None and dst in upstream and not local(src):
        # the WAN address reached from a local device (WireGuard from home Wi-Fi)
        # crosses the LAN only, so it is not upstream traffic
        out.append((FW, None, None, port, b0, b1))
    if local(src) and src not in never:
        out.append((ALL, src, dst, port, b1, b0))
    if local(dst) and dst not in never:
        out.append((ALL, dst, src, port, b0, b1))
    return out


def aggregate(rows, dt, topo):
    """One interval's credits -> (devices, fw_bytes).

    devices: {ip: {'all': [down, up], 'inet': [down, up],
                   'peers': [[ip, down, up, inet]],
                   'ports': {'all': [[port, down, up]], 'inet': [[port, down, up]]}}}
    in bits per second. peers is the union of the top TOP_PEERS by all traffic
    and the top TOP_PEERS internet peers, so a device whose local traffic
    dominates still shows its internet peers. fw_bytes: [down, up] bytes of the
    firewall's own upstream traffic.
    """
    acc = {}
    fw_bytes = [0, 0]
    for scope, dev, peer, port, down, up in rows:
        if scope == FW:
            fw_bytes[0] += down
            fw_bytes[1] += up
            continue
        d = acc.get(dev)
        if d is None:
            d = acc[dev] = {ALL: [0, 0], INET: [0, 0], 'peers': {}, 'ports': {ALL: {}, INET: {}}}
        d[scope][0] += down
        d[scope][1] += up
        if scope == ALL:
            pr = d['peers'].setdefault(peer, [0, 0])
            pr[0] += down
            pr[1] += up
        pt = d['ports'][scope].setdefault(port, [0, 0])
        pt[0] += down
        pt[1] += up
    scale = 8.0 / dt

    def bps(value):
        return int(round(value * scale))

    def ranked(items):
        return sorted(items, key=lambda kv: -(kv[1][0] + kv[1][1]))

    devices = {}
    for ip, d in acc.items():
        peers = ranked(d['peers'].items())
        chosen = {k for k, _ in peers[:TOP_PEERS]}
        chosen |= {k for k, _ in [kv for kv in peers if not topo.is_local(kv[0])][:TOP_PEERS]}
        devices[ip] = {
            ALL: [bps(d[ALL][0]), bps(d[ALL][1])],
            INET: [bps(d[INET][0]), bps(d[INET][1])],
            'peers': [[k, bps(v[0]), bps(v[1]), 0 if topo.is_local(k) else 1] for k, v in peers if k in chosen],
            'ports': {s: [[p, bps(v[0]), bps(v[1])] for p, v in ranked(d['ports'][s].items())[:TOP_PORTS]]
                      for s in (ALL, INET)},
        }
    return devices, fw_bytes


def wan_rates(prev, cur, devs, dt):
    """Interface counters -> (down_bps, up_bps, sample) for the upstream devices.

    sample holds the byte deltas and the per-frame header bytes the drift check
    subtracts. A counter that went backwards (interface reset) counts as zero.
    """
    rx = tx = hrx = htx = 0
    for dev in devs:
        a, b = prev.get(dev), cur.get(dev)
        if not a or not b:
            continue
        drx, dtx = max(0, b['rx'] - a['rx']), max(0, b['tx'] - a['tx'])
        prx, ptx = max(0, b['rxp'] - a['rxp']), max(0, b['txp'] - a['txp'])
        rx += drx
        tx += dtx
        if b['ether']:
            hrx += prx * ETHER_HEADER
            htx += ptx * ETHER_HEADER
    return (int(round(rx * 8 / dt)), int(round(tx * 8 / dt)),
            {'rx': rx, 'tx': tx, 'hdr_rx': hrx, 'hdr_tx': htx})


# ---------------------------------------------------------------- self-control


def ema(previous, value, samples=COST_EMA_SAMPLES):
    if previous is None:
        return value
    return previous + (2.0 / (samples + 1)) * (value - previous)


def next_interval(requested, cost_ema):
    """Seconds until the next sample: the requested interval, stretched so that
    sampling never takes more than CPU_BUDGET of one core."""
    return max(float(requested), (cost_ema or 0.0) / CPU_BUDGET)


class Drift:
    """Attributed bytes against the WAN counters over a sliding window (spec 5.8).

    Expected is the WAN interface bytes less one Ethernet header per frame.
    Attributed is internet device traffic plus the firewall's own. A direction
    is judged only above DRIFT_MIN_BPS, and the whole check is suspended while
    IPv6 carries more than DRIFT_MAX_V6_SHARE of the bytes: IPv6 is not
    attributed, and with floating states there is no telling which IPv6 states
    crossed the WAN.
    """

    def __init__(self, window=DRIFT_WINDOW):
        self.window = window
        self.samples = []

    def add(self, dt, wan, attr_down, attr_up, v6_bytes):
        self.samples.append((dt, wan['rx'], wan['tx'], wan['hdr_rx'], wan['hdr_tx'], attr_down, attr_up, v6_bytes))
        keep, total = [], 0.0
        for s in reversed(self.samples):
            keep.append(s)
            total += s[0]
            if total >= self.window:
                break
        self.samples = keep[::-1]

    def coverage(self):
        """{'down': ratio|None, 'up': ratio|None, 'ok': bool|None}, or None when not judgeable."""
        dt = sum(s[0] for s in self.samples)
        if dt < self.window:
            return None
        rx, tx, hrx, htx, down, up, v6 = (sum(s[i] for s in self.samples) for i in range(1, 8))
        if v6 > DRIFT_MAX_V6_SHARE * (rx + tx):
            return None
        result = {}
        for key, wire, hdr, attributed in (('down', rx, hrx, down), ('up', tx, htx, up)):
            expected = wire - hdr
            ok_rate = wire * 8 / dt >= DRIFT_MIN_BPS
            result[key] = round(attributed / expected, 3) if ok_rate and expected > 0 else None
        judged = [result['down'], result['up']]
        judged = [v for v in judged if v is not None]
        result['ok'] = all(DRIFT_BAND[0] <= v <= DRIFT_BAND[1] for v in judged) if judged else None
        return result


def safe_text(text):
    """The web relay HTML-escapes every line, so no event may contain <, > or &."""
    return _UNSAFE.sub(' ', str(text))[:200]


def format_event(event):
    return 'data: ' + _UNSAFE.sub(' ', json.dumps(event, separators=(',', ':'))) + '\n\n'


def cpu_seconds():
    """CPU used by this process and the commands it ran (pfctl, ifinfo)."""
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + kids.ru_utime + kids.ru_stime


# ---------------------------------------------------------------- the loop


def run_loop(interval, read_states, read_ifaddrs, read_routes, read_counters, write, clock=time.monotonic,
             wall=time.time, cpu=cpu_seconds, sleep=time.sleep, lifetime=LIFETIME, max_samples=None):
    """One event per tick until `lifetime` seconds have passed; returns 0.

    read_states() -> pfctl text, read_ifaddrs() -> ifconfig text,
    read_routes() -> netstat text and read_counters(devs) -> ifinfo text may
    raise: the error is reported in that sample's event and the loop carries on. write(chunk) sends to the client and
    raises BrokenPipeError once the client is gone.
    """
    write('retry: 1000\n\n')
    start = clock()
    topo, topo_error = _topology(read_ifaddrs, read_routes, Topology([]))
    topo_at = start
    drift = Drift()
    prev = prev_counters = prev_t = None
    cost_ema = None
    samples = 0
    next_tick = start
    while True:
        now = clock()
        if now - start >= lifetime or (max_samples is not None and samples >= max_samples):
            return 0
        if now - topo_at >= TOPOLOGY_REFRESH:
            topo, topo_error = _topology(read_ifaddrs, read_routes, topo)
            topo_at = now
        c0 = cpu()
        event = {'v': VERSION, 't': round(wall(), 3), 'dt': 0, 'interval': interval,
                 'states': 0, 'unparsed': 0, 'v6_skipped': 0,
                 'wan': {'devs': list(topo.upstream_devs), 'down': 0, 'up': 0},
                 'coverage': drift.coverage(), 'devices': {}, 'error': topo_error}
        try:
            states, unparsed = parse_states(read_states())
            counters = parse_ifinfo(read_counters(topo.upstream_devs)) if topo.upstream_devs else {}
        except Exception as exc:
            states = None
            event['error'] = safe_text(exc)
        t = clock()
        if states is not None:
            event.update(states=len(states), unparsed=unparsed,
                         v6_skipped=sum(1 for s in states.values() if s['af'] != 4))
            if prev is not None and t > prev_t:
                dt = t - prev_t
                rows, v6_bytes = [], 0
                for s, b0, b1 in deltas(prev, states, dt):
                    if s['af'] != 4:
                        v6_bytes += b0 + b1
                    else:
                        rows.extend(credits(s, b0, b1, topo))
                devices, fw_bytes = aggregate(rows, dt, topo)
                down, up, wan = wan_rates(prev_counters, counters, topo.upstream_devs, dt)
                inet_down = sum(r[4] for r in rows if r[0] == INET)
                inet_up = sum(r[5] for r in rows if r[0] == INET)
                drift.add(dt, wan, inet_down + fw_bytes[0], inet_up + fw_bytes[1], v6_bytes)
                event.update(dt=round(dt, 3), devices=devices, coverage=drift.coverage())
                event['wan'].update(down=down, up=up)
            prev, prev_counters, prev_t = states, counters, t
        cost = max(0.0, cpu() - c0)
        cost_ema = ema(cost_ema, cost)
        effective = next_interval(interval, cost_ema)
        event.update(cost_ms=int(round(cost * 1000)), effective=round(effective, 1),
                     throttled=effective > interval)
        write(format_event(event))
        samples += 1
        next_tick = max(next_tick + effective, clock())
        _sleep_until(next_tick, interval, clock, sleep, write)


def _topology(read_ifaddrs, read_routes, fallback):
    """(Topology, warning or None), for the event's error field until a refresh
    succeeds. Unreadable addresses keep the previous topology; unreadable routes
    fall back to the address rule alone; no upstream at all is said out loud."""
    try:
        ifaddrs = parse_ifconfig(read_ifaddrs())
    except Exception as exc:
        return fallback, safe_text('interface addresses: %s' % exc)
    warning = None
    try:
        devs = parse_default_devs(read_routes())
    except Exception as exc:
        devs, warning = [], safe_text('routes: %s' % exc)
    topo = Topology(ifaddrs, devs)
    if not topo.upstream_devs and warning is None:
        warning = 'no upstream interface found'
    return topo, warning


def _sleep_until(deadline, every, clock, sleep, write):
    """Sleep until `deadline`, writing an SSE comment every `every` seconds: the
    web relay drops a stream that stays silent longer than interval + 10 s."""
    last = clock()
    while True:
        now = clock()
        if now >= deadline:
            return
        sleep(max(0.0, min(deadline, last + every) - now))
        if clock() < deadline:
            write(': keepalive\n\n')
            last = clock()


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    if proc.returncode != 0:
        raise RuntimeError('%s exited with %d' % (cmd[0], proc.returncode))
    return proc.stdout


def main(argv):
    arg = argv[1] if len(argv) == 2 else ''

    def write(chunk):
        sys.stdout.write(chunk)
        sys.stdout.flush()

    try:
        if not (arg.isascii() and arg.isdigit() and MIN_INTERVAL <= int(arg) <= MAX_INTERVAL):
            write(format_event({'v': VERSION, 'error': 'interval must be a whole number of seconds from 1 to 10'}))
            return 1
        return run_loop(int(arg),
                        read_states=lambda: _run(PFCTL),
                        read_ifaddrs=lambda: _run(IFCONFIG),
                        read_routes=lambda: _run(ROUTES),
                        read_counters=lambda devs: ''.join(_run((IFINFO, d)) for d in devs),
                        write=write)
    except BrokenPipeError:
        # The reader is gone: tab closed, stream recycled or configd restarted.
        # Point stdout at /dev/null so the interpreter's final flush cannot raise again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
