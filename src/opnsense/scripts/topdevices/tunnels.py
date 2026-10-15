#!/usr/local/bin/python3
"""TopDevices: samples the WireGuard tunnel counters every minute.

NetFlow cannot see a WireGuard client's internet traffic: capture attaches to an
interface's ng_ether node, and a wg interface has none. The kernel does keep a
byte count per peer, received from it and sent to it since the interface was
created (`wg show all dump`, what core's own WireGuard widget reads). Every
minute (/usr/local/etc/cron.d/topdevices) this appends those counters, one line
per peer, to /var/log/topdevices/tunnels.<UTC day>; flows.py differences
consecutive samples into per-minute traffic for the ranges. Every peer every
run, idle or not, so each interval is one minute long and a burst is never
spread over idle hours.

Standard library only, and no import of flows.py, so it keeps working whatever
flows.py is installed. Silent on success; an error goes to syslog, exit 1. No
WireGuard, no wg binary or no peers is a normal state: nothing written, exit 0.
Design: docs/superpowers/specs/2026-10-15-vpn-tunnel-counters-design.md §5
"""
import glob
import ipaddress
import os
import subprocess
import sys
import syslog
import time

WG = '/usr/bin/wg'
KEPT = '/var/log/topdevices'     # the keeper's folder (keep.py), root only
KEEP_S = 51 * 3600               # an hour past flows.py's 50-hour reach, as the kept log
PREFIX = 'tunnels.'              # tunnels.<YYYYMMDD>; flows.py's flowd.* glob never sees these
PEER_FIELDS = 9                  # wg(8) dump: a peer line; an interface line has 5


def parse_dump(text):
    """`wg show all dump` -> ([(interface, address, rx, tx)], lines of another
    shape, [(interface, address, [other /32s])]). The device address is the first
    IPv4 /32 among a peer's allowed IPs; a peer with none is skipped: there is no
    one device to credit. Keys are parsed past and dropped."""
    peers, skipped, extras = [], 0, []
    for line in text.splitlines():
        parts = line.split('\t')
        if len(parts) == 5:
            continue                                  # an interface line
        if len(parts) != PEER_FIELDS or not (parts[6].isdigit() and parts[7].isdigit()):
            skipped += 1
            continue
        hosts = []
        for item in parts[4].split(','):
            try:
                net = ipaddress.ip_network(item.strip(), strict=False)
            except ValueError:
                continue
            if net.version == 4 and net.prefixlen == 32:
                hosts.append(str(net.network_address))
        if not hosts:
            continue
        peers.append((parts[0], hosts[0], int(parts[6]), int(parts[7])))
        if len(hosts) > 1:
            extras.append((parts[0], hosts[0], hosts[1:]))
    return peers, skipped, extras


def day_file(kept, now):
    return os.path.join(kept, PREFIX + time.strftime('%Y%m%d', time.gmtime(now)))


def write_sample(kept, now, peers):
    """One line per peer, appended to the day's file in a single write."""
    data = ''.join('%d %s %s %d %d\n' % (int(now), iface, ip, rx, tx) for iface, ip, rx, tx in peers).encode()
    path = day_file(kept, now)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, data) != len(data):           # a regular file takes the whole buffer; anything else is an error
            raise OSError('short write to %s' % path)
    finally:
        os.close(fd)
    return path


def prune(kept, now, keep_s=KEEP_S):
    """Delete this script's day files last written more than keep_s ago."""
    removed = 0
    for path in glob.glob(os.path.join(glob.escape(kept), PREFIX + '*')):
        try:
            if os.stat(path).st_mtime < now - keep_s:
                os.unlink(path)
                removed += 1
        except FileNotFoundError:
            pass
    return removed


def run(now, kept, dump, keep_s=KEEP_S):
    """One pass (spec §5): write the sample, note what could not be read, prune.
    Returns the number of peers written."""
    os.makedirs(kept, mode=0o700, exist_ok=True)
    os.chmod(kept, 0o700)
    written = 0
    if dump is not None:
        peers, skipped, extras = parse_dump(dump)
        if peers:
            write_sample(kept, now, peers)
            written = len(peers)
        elif skipped:
            syslog.syslog(syslog.LOG_NOTICE, 'topdevices tunnels: wg show output not understood (%d lines skipped)' % skipped)
        if extras:
            syslog.syslog(syslog.LOG_NOTICE, 'topdevices tunnels: credited to the first /32 only: ' + '; '.join(
                '%s %s (also %s)' % (iface, ip, ', '.join(others)) for iface, ip, others in extras))
    prune(kept, now, keep_s)
    return written


def wg_dump():
    """`wg show all dump`, or None when there is no wg or it fails (no WireGuard).
    A wg that does not answer is an error, not "no WireGuard": it is raised."""
    try:
        proc = subprocess.run([WG, 'show', 'all', 'dump'], capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        raise RuntimeError('wg show all dump did not finish within 10 s') from None
    return proc.stdout if proc.returncode == 0 else None


def main():
    try:
        run(time.time(), KEPT, wg_dump())
    except Exception as exc:              # cron discards the output: the system log is where this shows
        syslog.syslog(syslog.LOG_ERR, 'topdevices tunnels: %s' % (str(exc) or exc.__class__.__name__))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
