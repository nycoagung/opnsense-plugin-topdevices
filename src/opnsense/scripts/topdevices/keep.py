#!/usr/local/bin/python3
"""TopDevices: keeps NetFlow's rotated flow log for two days.

Core's aggregator rotates /var/log/flowd.log past 10 MB and keeps ten rotated
files: about a day on the reference install. Every 10 minutes
(/usr/local/etc/cron.d/topdevices) this links each file core has rotated into
/var/log/topdevices/ - a second name for the same file, which core's renames
leave alone and its os.remove() does not free - and deletes the links older
than flows.py can ask for. flows.py reads both places.

Standard library only, and no import of flows.py, so it keeps working whatever
flows.py is installed. Silent on success; an error goes to syslog, exit 1.
Design: docs/superpowers/specs/2026-09-24-keep-flow-log-design.md §5
"""
import glob
import os
import re
import sys
import syslog
import time

LOG = '/var/log/flowd.log'
KEPT = '/var/log/topdevices'     # on /var/log's own filesystem: a hard link cannot cross one
KEEP_S = 51 * 3600               # an hour past flows.py's 50-hour reach (spec §4)
CAP = 1 << 30                    # 1 GB at most: a traffic surge cannot fill the disk
ROTATED = re.compile(r'\.[0-9]+')


def rotated(log):
    """Core's rotated files, flowd.log.000001 and up - never flowd.log itself,
    which flowd is still writing."""
    base = os.path.basename(log)
    return [p for p in glob.glob(glob.escape(log) + '.*') if ROTATED.fullmatch(os.path.basename(p)[len(base):])]


def kept_files(kept):
    """[(path, stat)] of every kept file, oldest first."""
    out = []
    for path in glob.glob(os.path.join(glob.escape(kept), 'flowd.*')):
        try:
            out.append((path, os.stat(path)))
        except FileNotFoundError:
            pass
    return sorted(out, key=lambda kv: kv[1].st_mtime)


def run(now, log, kept, keep_s=KEEP_S, cap=CAP):
    """One pass (spec §5): link each file core has rotated out since the last
    pass, then delete the kept files past the reach, and the oldest while they
    total more than the cap. Returns (linked, removed).

    Core's rotation - every rotated file renamed one number up, flowd.log
    itself becoming .000001 - can land between any two of the operations
    below; each `except` here is one place that can happen. Landing between
    the stat and the link is the awkward one: `path` then names a different
    file, and os.link happily links THAT file under the name built from the
    first file's identity, so the name is checked again after a successful
    link and undone if it no longer matches. An os.link error that is
    neither of those (ENOSPC, EMLINK ...) is remembered, not raised here, so
    a persistent error on one file cannot turn the pruning below off; it is
    raised after pruning runs, so main() still syslogs it and exits 1."""
    os.makedirs(kept, mode=0o700, exist_ok=True)
    os.chmod(kept, 0o700)
    have = {(st.st_dev, st.st_ino) for _, st in kept_files(kept)}
    linked = removed = 0
    link_error = None
    for path in rotated(log):
        try:
            st = os.stat(path)
        except FileNotFoundError:
            continue                              # renamed since the listing: the next pass finds it
        if (st.st_dev, st.st_ino) in have or st.st_mtime < now - keep_s:
            continue
        name = os.path.join(kept, 'flowd.%d.%d' % (st.st_mtime, st.st_ino))
        try:
            os.link(path, name)
        except FileNotFoundError:
            continue                              # renamed before the link: the next pass finds it under its new name
        except FileExistsError:
            try:
                held = (os.stat(name).st_dev, os.stat(name).st_ino)
            except FileNotFoundError:
                continue                          # gone already: the next pass links it fresh
            if held == (st.st_dev, st.st_ino):
                have.add((st.st_dev, st.st_ino))  # another pass already linked it under this name
            else:
                try:
                    os.unlink(name)               # an earlier pass linked the wrong file under this name
                except FileNotFoundError:
                    pass
            continue
        except OSError as exc:
            if link_error is None:
                link_error = exc
            continue
        try:
            held = (os.stat(name).st_dev, os.stat(name).st_ino)
        except FileNotFoundError:
            continue                              # renamed again right after the link: the next pass finds it
        if held != (st.st_dev, st.st_ino):
            try:
                os.unlink(name)                   # the rotation landed between the stat and the link: wrong file
            except FileNotFoundError:
                pass
            continue
        have.add((st.st_dev, st.st_ino))
        linked += 1
    files = kept_files(kept)
    total = sum(st.st_size for _, st in files)
    for path, st in files:
        if st.st_mtime < now - keep_s or total > cap:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass                              # another pass deleted it
            total -= st.st_size
            removed += 1
    if link_error is not None:
        raise link_error
    return linked, removed


def main():
    try:
        run(time.time(), LOG, KEPT)
    except Exception as exc:              # cron discards the output: the system log is where this shows
        syslog.syslog(syslog.LOG_ERR, 'topdevices keep: %s' % (str(exc) or exc.__class__.__name__))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
