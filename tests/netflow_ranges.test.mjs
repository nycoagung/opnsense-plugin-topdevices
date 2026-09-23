// NetFlow ranges of TopDevices.js: which aggregate, bucket size and window each
// range reads, and how the rows become per-device download and upload.
// Run:  node --test tests/netflow_ranges.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

// UTC+10 all year, like the reference firewall in September: a UTC-midnight
// bucket starts at 10:00 local
process.env.TZ = 'Australia/Brisbane';

// --- what the dashboard provides, reduced to what the NetFlow path touches ---
globalThis.BaseWidget = class {
    constructor(config) { this.config = config; }
    async getWidgetConfig() { return {}; }
};
const requests = [];
let reply = () => '';
globalThis.$ = Object.assign(() => ({}), {
    ajax(opts) {
        requests.push(opts.url);
        const text = reply(opts.url);              // null: the request fails
        const p = { done(fn) { if (text !== null) Promise.resolve().then(() => fn(text)); return p; },
                    fail(fn) { if (text === null) Promise.resolve().then(fn); return p; } };
        return p;
    }
});
const m = await import('../src/opnsense/www/js/widgets/TopDevices.js');
const TopDevices = m.default;

// --- rows exactly as core 26.7.4 writes them (scripts/netflow/lib/aggregates/source.py):
// every flow once per interface it crosses. FlowSourceAddrDetails swaps src and
// dst for the 'out' row; FlowSourceAddrTotals keeps one address, the flow's
// source for 'in' and its destination for 'out'.
const DETAILS_HEAD = ['start_time', 'if', 'direction', 'src_addr', 'dst_addr', 'service_port', 'protocol', 'octets', 'packets', 'last_seen'];
const TOTALS_HEAD = ['start_time', 'if', 'src_addr', 'direction', 'octets', 'packets', 'last_seen'];
function detailsRows(flows) {
    return flows.flatMap(f => [
        { if: f.in, direction: 'in', src_addr: f.src, dst_addr: f.dst, service_port: f.port, octets: f.bytes },
        { if: f.out, direction: 'out', src_addr: f.dst, dst_addr: f.src, service_port: f.port, octets: f.bytes }]);
}
function totalsRows(flows) {
    return flows.flatMap(f => [
        { if: f.in, src_addr: f.src, direction: 'in', octets: f.bytes },
        { if: f.out, src_addr: f.dst, direction: 'out', octets: f.bytes }]);
}
const csv = (rows, head) => [head.join(','),
    ...rows.map(r => head.map(h => (r[h] === undefined ? '' : r[h])).join(','))].join('\n') + '\n';
const FLOWS = [
    { src: '8.8.8.8', dst: '192.168.1.10', in: 'em0', out: 'ue0', port: 443, bytes: 1000 },          // a download
    { src: '192.168.1.10', dst: '1.1.1.1', in: 'ue0', out: 'em0', port: 443, bytes: 300 },           // an upload
    { src: '192.168.1.10', dst: '192.168.20.5', in: 'ue0', out: 'vlan01', port: 445, bytes: 50 },    // local, across networks
    { src: '192.168.1.10', dst: '192.168.1.255', in: 'ue0', out: 'ue0', port: 137, bytes: 7 }        // broadcast chatter
];
const serve = (url) => (url.includes('/FlowSourceAddrTotals/') ? csv(totalsRows(FLOWS), TOTALS_HEAD)
                                                              : csv(detailsRows(FLOWS), DETAILS_HEAD));

// 2026-09-23 19:08:54 AEST; S(h, min, day) is a UTC instant in epoch seconds
const S = (h, min = 0, day = 23) => Date.UTC(2026, 8, day, h, min) / 1000;
const NOW = S(9, 8) + 54;
const EXPORT = '/api/diagnostics/networkinsight/export';

function widget(range, scope = 'all') {
    const w = new TopDevices({});
    w.state.range = range;
    w.state.scope = scope;
    // built as _loadNetworks builds them: signed 32-bit, like the `v & mask` they meet
    const net = (a, key) => ({ key, label: key, net: w._ip2int(a) & (0xffffff00 | 0), mask: 0xffffff00 | 0,
                               bcast: w._ip2int(a) | 0xff });
    w.networks = [net('192.168.1.0', 'lan'), net('192.168.20.0', 'opt1')];
    w.wanDevs = ['em0'];
    return w;
}
const byIp = (w) => Object.fromEntries(w.state.rows.map(r => [r.ip, [r.down, r.up]]));
async function load(range, scope = 'all') {
    const w = widget(range, scope);
    reply = serve;
    await w._load(NOW * 1000);
    return { w, url: requests[requests.length - 1] };
}

// --- which aggregate, bucket size and window ---------------------------------

test('Last hour reads 5-minute totals for the last hour, not the whole day', async () => {
    const { w, url } = await load('1h');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(8, 10)}/${NOW}/300`);
    assert.deepEqual(w.state.window, [S(8, 10), NOW]);
});

test('Last 24 hours reads hourly totals', async () => {
    const { w, url } = await load('24h');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(9, 0, 22)}/${NOW}/3600`);
    assert.deepEqual(w.state.window, [S(9, 0, 22), NOW]);
});

test('Today reads hourly totals from local midnight, not from yesterday 10:00', async () => {
    const { w, url } = await load('today');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(14, 0, 22)}/${NOW}/3600`);
    assert.deepEqual(w.state.window, [S(14, 0, 22), NOW]);
});

test('Yesterday is one day bucket, not two', async () => {
    const { w, url } = await load('yesterday');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(0, 0, 22)}/${S(0, 0, 23)}/86400`);
    assert.deepEqual(w.state.window, [S(0, 0, 22), S(0, 0, 23)]);
});

test('Last 7 days starts at the nearest day boundary', async () => {
    const { url } = await load('7d');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(0, 0, 16)}/${NOW}/86400`);
});

test('Internet only reads the daily details, the only aggregate with the interface', async () => {
    const { w, url } = await load('1h', 'wan');
    assert.equal(url, `${EXPORT}/FlowSourceAddrDetails/${S(0, 0, 23)}/${NOW}/86400`);
    assert.deepEqual(w.state.window, [S(0, 0, 23), NOW]);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 300] });       // the local flow never crossed em0
});

test('the plan: a short window far back takes the day it falls in', () => {
    const from = S(3, 0, 20);
    assert.deepEqual(m.nfPlan(from, from + 600, NOW, 'all'),
        { provider: 'FlowSourceAddrTotals', res: 86400, start: S(0, 0, 20), end: S(0, 0, 21) });
});

test('the plan: a day bucket still in progress ends now', () => {
    const now = S(23, 0, 22);                          // 23 Sep 09:00 AEST: today's UTC day has not begun
    assert.deepEqual(m.nfPlan(S(14, 0, 21), S(14, 0, 22), now, 'all'),
        { provider: 'FlowSourceAddrTotals', res: 86400, start: S(0, 0, 22), end: now });
});

test('the plan: 5-minute buckets whenever the hour is still kept', () => {
    const now = S(9, 7) + 29;                          // the start rounds down to 08:05, the oldest bucket kept
    assert.equal(m.nfPlan(now - 3600, now, now, 'all').res, 300);
    assert.equal(m.nfPlan(now - 3600 - 300, now, now, 'all').res, 3600);
});

// --- rows to download and upload ---------------------------------------------

test('both aggregates give every device the same download and upload', () => {
    const keep = (ip) => ip.startsWith('192.168.') && !ip.endsWith('.255');
    const parse = (rows) => rows.map(r => ({ src: r.src_addr, dst: r.dst_addr, dir: r.direction, iface: r.if, octets: r.octets }));
    const expected = { '192.168.1.10': { ip: '192.168.1.10', down: 1000, up: 357 },
                       '192.168.20.5': { ip: '192.168.20.5', down: 50, up: 0 } };
    assert.deepEqual(m.deviceTotals(parse(detailsRows(FLOWS)), 'FlowSourceAddrDetails', keep), expected);
    assert.deepEqual(m.deviceTotals(parse(totalsRows(FLOWS)), 'FlowSourceAddrTotals', keep), expected);
});

test('all traffic from the totals: downloads, uploads and local traffic, broadcasts dropped', async () => {
    const w = widget('24h');
    reply = (url) => (url.includes('/FlowSourceAddrTotals/') ? csv(totalsRows(FLOWS), TOTALS_HEAD) : '');
    await w._load(NOW * 1000);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
});

// --- saying what the figures cover --------------------------------------------

test('the caption gives the span the figures cover', async () => {
    const { w } = await load('1h');
    assert.match(w._windowCaption().text, /^Wed Sep 23 18:10:00 AEST 2026 {2}→ {2}Wed Sep 23 19:08:54 AEST 2026 · all traffic$/);
    assert.equal(w._windowCaption().note, null);
});

test('Internet only on a sub-day range says it is kept per day, and from when', async () => {
    const { w } = await load('1h', 'wan');
    assert.match(w._windowCaption().note, /^Internet only is kept per day \(days start at 10:00\): these cover 10:00 → 19:08$/);
});

test('a day range older than the hourly data says why its days start at 10:00', async () => {
    const { w } = await load('yesterday');
    assert.match(w._windowCaption().note, /kept per day \(days start at 10:00\)/);
});

test('no note when the buckets fit the range', async () => {
    for (const range of ['1h', '24h', 'today']) {
        const { w } = await load(range);
        assert.equal(w._windowCaption().note, null, range);
    }
});

test('a scope change whose export fails keeps the caption true to the rows shown', async () => {
    const { w } = await load('1h');                    // all traffic on screen
    w.state.scope = 'wan';
    reply = () => null;                                // the details export fails
    await assert.rejects(w._load(NOW * 1000));
    assert.match(w._windowCaption().text, / · all traffic$/);
});

// --- the device panel ---------------------------------------------------------

test('the device panel reads the daily details and labels their span when it differs', async () => {
    const { w } = await load('1h');
    // the day since 10:00 holds more than the last hour does
    const day = FLOWS.map(f => ({ ...f, bytes: f.bytes * 10 }));
    reply = (url) => (url.includes('/FlowSourceAddrDetails/') ? csv(detailsRows(day), DETAILS_HEAD) : '');
    const d = await w._detailsData('192.168.1.10');
    assert.equal(requests[requests.length - 1], `${EXPORT}/FlowSourceAddrDetails/${S(0, 0, 23)}/${NOW}/86400`);
    assert.deepEqual(d.peers, { '8.8.8.8': 10000, '1.1.1.1': 3000, '192.168.20.5': 500, '192.168.1.255': 70 });
    assert.equal(d.note, 'Peers and ports cover 10:00 → 19:08 (kept per day)');
    assert.deepEqual([d.down, d.up], [1000, 357]);                    // the table row's, for the table's window
});

test('the device panel needs no note when its span is the table span', async () => {
    const { w } = await load('1h', 'wan');
    const d = await w._detailsData('192.168.1.10');
    assert.equal(d.note, null);
    assert.deepEqual(d.peers, { '8.8.8.8': 1000, '1.1.1.1': 300 });  // internet peers only
});
