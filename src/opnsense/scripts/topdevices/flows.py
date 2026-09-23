#!/usr/local/bin/python3
"""TopDevices: exact per-device totals for a recent window, read on demand from
NetFlow's raw flow log (/var/log/flowd.log and its rotations). Run by configd:

    flows.py totals FROM TO           every device's bytes, both scopes
    flows.py device IP FROM TO        one device's peers and ports, both scopes

FROM and TO are epoch seconds. It prints one JSON object and exits 0, errors
included ({"error": "..."}): configd returns stdout as the answer, and turns a
failing exit into "Execute error". Design:
docs/superpowers/specs/2026-09-23-raw-flow-ranges-design.md
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

VERSION = '0.2.0'

LOG = '/var/log/flowd.log'
NETFLOW_LIB = '/usr/local/opnsense/scripts/netflow'  # core's parser, interface map, aggregates
HOUR = 3600
DAY = 86400
SLACK = 300              # the browser's clock may be a few minutes off the firewall's
TOP = 100                # the device panel lists at most 100 rows
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
