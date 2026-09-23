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
// jQuery: chainable no-ops, except what the tests read back - the last .html()
// or .text() written to a selector - and $.ajax, answered by `reply`.
const requests = [];
let reply = () => '';
const dom = {};
const chain = new Proxy(function () {}, {
    get: (t, p) => (p === Symbol.toPrimitive ? () => '' : p === 'length' ? 0 : chain),
    apply: () => chain
});
const element = (sel) => new Proxy(function () {}, {
    get: (t, p) => (p === 'html' || p === 'text'
        ? (v) => { if (v !== undefined) dom[sel] = String(v); return chain; }
        : p === Symbol.toPrimitive ? () => '' : p === 'length' ? 0 : chain),
    apply: () => chain
});
function ajax(opts) {
    requests.push(opts.url);
    const text = Promise.resolve(reply(opts.url));   // a string, null (the request fails), or a promise of one
    const p = { done(fn) { text.then(t => { if (t !== null) fn(t); }); return p; },
                fail(fn) { text.then(t => { if (t === null) fn(); }); return p; } };
    return p;
}
globalThis.$ = new Proxy(function () {}, {
    apply: (t, self, [arg]) => (typeof arg === 'string' ? element(arg) : chain),
    get: (t, p) => (p === 'ajax' ? ajax : chain)
});
const tick = () => new Promise(r => setTimeout(r, 0));
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
const DAY = FLOWS.map(f => ({ ...f, bytes: f.bytes * 10 }));      // the day since 10:00 holds more
const serve = (url) => (url.includes('/FlowSourceAddrTotals/') ? csv(totalsRows(FLOWS), TOTALS_HEAD)
                                                              : csv(detailsRows(FLOWS), DETAILS_HEAD));

// 2026-09-23 19:08:54 AEST; S(h, min, day) is a UTC instant in epoch seconds
const S = (h, min = 0, day = 23) => Date.UTC(2026, 8, day, h, min) / 1000;
const NOW = S(9, 8) + 54;
const EXPORT = '/api/diagnostics/networkinsight/export';
const FLOWS_API = '/api/topdevices/flows';
const FALLBACK = "The raw flow log could not be read: showing NetFlow's records";

function widget(range, scope = 'all') {
    const w = new TopDevices({});
    w.state.range = range;
    w.state.scope = scope;
    w.state.chart = 'none';
    // built as _loadNetworks builds them: signed 32-bit, like the `v & mask` they meet
    const net = (a, key) => ({ key, label: key, net: w._ip2int(a) & (0xffffff00 | 0), mask: 0xffffff00 | 0,
                               bcast: w._ip2int(a) | 0xff });
    w.networks = [net('192.168.1.0', 'lan'), net('192.168.20.0', 'opt1')];
    w.wanDevs = ['em0'];
    return w;
}
const byIp = (w) => Object.fromEntries(w.state.rows.map(r => [r.ip, [r.down, r.up]]));
async function load(range, scope = 'all', now = NOW) {
    const w = widget(range, scope);
    reply = serve;
    await w._load(now * 1000);
    return { w, url: requests[requests.length - 1] };
}

// --- which aggregate, bucket size and window ---------------------------------

test('Last hour reads 5-minute totals for the last hour, not the whole day', async () => {
    const { w, url } = await load('1h');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(8, 10)}/${NOW}/300`);
    assert.deepEqual(w.state.window, [S(8, 10), NOW]);
});

test('Last 24 hours reads the hourly totals within the last 24 hours', async () => {
    const { w, url } = await load('24h');
    assert.equal(url, `${EXPORT}/FlowSourceAddrTotals/${S(10, 0, 22)}/${NOW}/3600`);
    assert.deepEqual(w.state.window, [S(10, 0, 22), NOW]);
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
        { provider: 'FlowSourceAddrTotals', res: 86400, start: S(0, 0, 20), end: S(0, 0, 21), clipped: false });
});

test('the plan: a day bucket still in progress ends now', () => {
    const now = S(23, 0, 22);                          // 23 Sep 09:00 AEST: today's UTC day has not begun
    assert.deepEqual(m.nfPlan(S(14, 0, 21), S(14, 0, 22), now, 'all'),
        { provider: 'FlowSourceAddrTotals', res: 86400, start: S(0, 0, 22), end: now, clipped: false });
});

test('the plan: 5-minute buckets while the hour is kept, none core may have dropped', () => {
    // 19:07:29. Core drops 5-minute buckets older than its newest one less an
    // hour - or than now less an hour, while it holds rows stamped in the future:
    // 18:05 may be gone, 18:10 is kept either way
    const now = S(9, 7) + 29;
    assert.deepEqual(m.nfPlan(now - 3600, now, now, 'all'),
        { provider: 'FlowSourceAddrTotals', res: 300, start: S(8, 10), end: now, clipped: true });
    assert.equal(m.nfPlan(now - 3600 - 300, now, now, 'all').res, 3600);
});

// --- what core no longer keeps, or has not recorded yet ------------------------

// local wall time in the test's zone -> the custom range's datetime-local value
const local = (y, mo, d, h = 0) => `${y}-${String(mo).padStart(2, '0')}-${String(d).padStart(2, '0')}T${String(h).padStart(2, '0')}:00`;
const U = (mo, d, h = 0) => Date.UTC(2026, mo - 1, d, h) / 1000;

async function loadCustom(from, to, scope) {
    const w = widget('custom', scope);
    w.state.customFrom = from;
    w.state.customTo = to;
    reply = serve;
    const before = requests.length;
    await w._load(NOW * 1000);
    return { w, made: requests.slice(before) };
}

test('Internet only further back than the 62 days kept reads nothing, and says why', async () => {
    const { w, made } = await loadCustom(local(2026, 6, 1), local(2026, 6, 30), 'wan');
    assert.deepEqual(made, []);
    assert.deepEqual(w.state.rows, []);
    const c = w._windowCaption();
    assert.match(c.text, /^Mon Jun 01 00:00:00 AEST 2026 {2}→ {2}Tue Jun 30 00:00:00 AEST 2026 · internet only/);
    assert.equal(c.note, 'No NetFlow data: only the last 62 days are kept');
});

test('a range reaching past the 62 days is cut to what is kept, and says so', async () => {
    const { w, made } = await loadCustom(local(2026, 7, 15), local(2026, 8, 1), 'wan');
    assert.deepEqual(made, [`${EXPORT}/FlowSourceAddrDetails/${U(7, 24)}/${U(8, 1)}/86400`]);
    assert.equal(w._windowCaption().note,
        'Internet only is kept per day (days start at 10:00), for 62 days: these cover Fri 24 Jul 10:00 → Sat 1 Aug 10:00');
});

test('a custom range in the future reads nothing, and says so', async () => {
    const { w, made } = await loadCustom(local(2026, 9, 23, 20), local(2026, 9, 23, 21), 'all');
    assert.deepEqual(made, []);
    const c = w._windowCaption();
    assert.match(c.text, /^Wed Sep 23 20:00:00 AEST 2026 {2}→ {2}Wed Sep 23 21:00:00 AEST 2026 · all traffic$/);
    assert.equal(c.note, 'No NetFlow data for this range');
});

test('the device panel says when no peers are kept for the range', async () => {
    const { w } = await loadCustom(local(2026, 6, 1), local(2026, 6, 30), 'all');   // daily totals keep a year
    const before = requests.length;
    const d = await w._detailsData('192.168.1.10');
    assert.equal(requests.length, before);
    assert.deepEqual(d.peers, {});
    assert.equal(d.note, 'Peers and ports are only kept for 62 days');
});

// --- other time zones -----------------------------------------------------------

function inZone(zone, fn) {
    return async () => {
        process.env.TZ = zone;
        try { await fn(); } finally { process.env.TZ = 'Australia/Brisbane'; }
    };
}

test('Yesterday is the calendar day, 23 hours the day after clocks go forward', inZone('Australia/Sydney', () => {
    const w = widget('yesterday');
    // Mon 5 Oct 2026 12:00 AEDT; Sun 4 Oct ran from 00:00 AEST to 00:00 AEDT
    assert.deepEqual(w._window('yesterday', U(10, 5, 1) * 1000), [U(10, 3, 14), U(10, 4, 13)]);
}));

test('Yesterday is the calendar day, 25 hours the day after clocks go back', inZone('Australia/Sydney', () => {
    const w = widget('yesterday');
    // Mon 6 Apr 2026 12:00 AEST; Sun 5 Apr ran from 00:00 AEDT to 00:00 AEST
    assert.deepEqual(w._window('yesterday', U(4, 6, 2) * 1000), [U(4, 4, 13), U(4, 5, 14)]);
}));

test('a week across a clock change says when its days start, before and after', inZone('Australia/Sydney', async () => {
    const { w } = await load('7d', 'wan', U(10, 7, 3));                // Wed 7 Oct 14:00 AEDT
    assert.equal(w._windowCaption().note,
        'Internet only is kept per day (days start at 10:00, then 11:00): these cover Wed 30 Sep 10:00 → Wed 7 Oct 14:00');
}));

test('Today in a half-hour time zone starts at the nearest UTC hour', inZone('Asia/Kolkata', async () => {
    const { w } = await load('today', 'all', S(6, 30));                // 12:00 IST
    assert.deepEqual(w.state.window, [S(19, 0, 22), S(6, 30)]);        // 00:30 IST
    assert.match(w._windowCaption().text, /^Wed Sep 23 00:30:00 IST 2026/);
    assert.equal(w._windowCaption().note, FALLBACK);
}));

test('in UTC, Internet only · Last hour just after midnight still says it is kept per day', inZone('UTC', async () => {
    const { w } = await load('1h', 'wan', S(0, 20));
    assert.equal(w._windowCaption().note,
        `${FALLBACK} · Internet only is kept per day (days start at 00:00): these cover 00:00 → 00:20`);
}));

// --- rows to download and upload ---------------------------------------------

test('both aggregates give every device the same download and upload', () => {
    const keep = (ip) => ip.startsWith('192.168.') && !ip.endsWith('.255');
    const parse = (rows) => rows.map(r => ({ src: r.src_addr, dst: r.dst_addr, dir: r.direction, iface: r.if, octets: r.octets }));
    const expected = { '192.168.1.10': { ip: '192.168.1.10', down: 1000, up: 357 },
                       '192.168.20.5': { ip: '192.168.20.5', down: 50, up: 0 } };
    assert.deepEqual(m.deviceTotals(parse(detailsRows(FLOWS)), 'FlowSourceAddrDetails', keep), expected);
    assert.deepEqual(m.deviceTotals(parse(totalsRows(FLOWS)), 'FlowSourceAddrTotals', keep), expected);
});

test("core's extra column on a 0-byte row changes nothing", async () => {
    // export_details.py writes '' before a falsy value: octets and packets 0
    const quirk = ['2026/09/23 19:20:00,em0,142.250.183.37,out,,0,,0,2026/09/23 19:20:00',
                   '2026/09/23 19:20:00,ue0,192.168.1.10,out,,0,,0,2026/09/23 19:20:00'].join('\n') + '\n';
    const w = widget('24h');
    reply = (url) => (url.includes('/FlowSourceAddrTotals/') ? csv(totalsRows(FLOWS), TOTALS_HEAD) + quirk : '');
    await w._load(NOW * 1000);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
});

test('all traffic from the totals: downloads, uploads and local traffic, broadcasts dropped', async () => {
    const w = widget('24h');
    reply = (url) => (url.includes('/FlowSourceAddrTotals/') ? csv(totalsRows(FLOWS), TOTALS_HEAD) : '');
    await w._load(NOW * 1000);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
});

// --- saying what the figures cover --------------------------------------------

test('on the fallback path, the caption gives the span the figures cover', async () => {
    const { w } = await load('1h');
    assert.match(w._windowCaption().text, /^Wed Sep 23 18:10:00 AEST 2026 {2}→ {2}Wed Sep 23 19:08:54 AEST 2026 · all traffic$/);
    assert.equal(w._windowCaption().note, FALLBACK);
});

test('Internet only on a sub-day range says it is kept per day, and from when', async () => {
    const { w } = await load('1h', 'wan');
    assert.equal(w._windowCaption().note, `${FALLBACK} · Internet only is kept per day (days start at 10:00): these cover 10:00 → 19:08`);
});

test('a day range older than the hourly data says why its days start at 10:00', async () => {
    const { w } = await load('yesterday');
    assert.match(w._windowCaption().note, /kept per day \(days start at 10:00\)/);
});

test('Internet only · Last hour right after 10:00 still says it is kept per day', async () => {
    for (const [h, min, shown] of [[0, 5, '10:05'], [0, 30, '10:30'], [1, 30, '11:30']]) {
        const { w } = await load('1h', 'wan', S(h, min));
        assert.equal(w._windowCaption().note,
            `${FALLBACK} · Internet only is kept per day (days start at 10:00): these cover 10:00 → ${shown}`, shown);
    }
    const { w } = await load('1h', 'all', S(0, 5));
    assert.equal(w._windowCaption().note, FALLBACK);
});

test('a span inside one minute is told in seconds', async () => {
    const { w } = await load('today', 'wan', S(0, 0) + 30);        // 10:00:30
    assert.match(w._windowCaption().note, /these cover 10:00:00 → 10:00:30$/);
});

test('on the fallback path, the only note is the fallback\'s when the buckets fit', async () => {
    for (const range of ['1h', '24h', 'today']) {
        const { w } = await load(range);
        assert.equal(w._windowCaption().note, FALLBACK, range);
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
    reply = (url) => (url.includes('/FlowSourceAddrDetails/') ? csv(detailsRows(DAY), DETAILS_HEAD) : '');
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

test('the device panel needs no note when its lists cover the same day as the table', async () => {
    const { w } = await load('yesterday');              // all traffic: daily totals, the same day
    const d = await w._detailsData('192.168.1.10');
    assert.equal(d.note, null);
});

// --- scope changes, concurrent loads and the cache ------------------------------

test('switching to internet only while the table loads ends on internet only', async () => {
    const w = widget('1h');
    let release;
    reply = (url) => (url.includes('/FlowSourceAddrTotals/')
        ? new Promise(r => { release = () => r(csv(totalsRows(FLOWS), TOTALS_HEAD)); })
        : csv(detailsRows(FLOWS), DETAILS_HEAD));
    const loading = w.refresh();                       // all traffic, and its export is slow
    await tick();
    w.state.scope = 'wan';                             // the user switches meanwhile
    await w.render();                                  // what the scope control runs
    release();
    await loading;
    assert.equal(w.state.request.scope, 'wan');
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 300] });
    assert.match(dom['.td-window small'], / · internet only \(via em0\)$/);
});

test('a late internet-only export does not overwrite all traffic', async () => {
    const { w } = await load('1h');
    let release;
    reply = (url) => (url.includes('/FlowSourceAddrDetails/')
        ? new Promise(r => { release = () => r(csv(detailsRows(DAY), DETAILS_HEAD)); })
        : csv(totalsRows(FLOWS), TOTALS_HEAD));
    w.state.scope = 'wan';
    const toWan = w.render();                          // the details export is slow
    await tick();
    w.state.scope = 'all';
    await w.render();                                  // back before it arrives
    release();
    await toWan;
    assert.equal(w.state.request.scope, 'all');
    assert.equal(w.state.plan.provider, 'FlowSourceAddrTotals');
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
    assert.match(dom['.td-window small'], / · all traffic$/);
});

test('on the fallback path, switching scope and back reads each export once', async () => {
    const { w } = await load('1h');
    const before = requests.length;
    w.state.scope = 'wan';
    await w.render();
    w.state.scope = 'all';
    await w.render();
    assert.deepEqual(requests.slice(before), [`${FLOWS_API}/totals/${NOW - 3600}/${NOW}`, `${EXPORT}/FlowSourceAddrDetails/${S(0, 0, 23)}/${NOW}/86400`, `${FLOWS_API}/totals/${NOW - 3600}/${NOW}`]);
    assert.deepEqual(w.state.window, [S(8, 10), NOW]);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
});

test('a refresh that lands after a scope change is dropped, not shown and then corrected', async () => {
    const { w } = await load('1h');
    let release;
    reply = (url) => (url.includes('/FlowSourceAddrTotals/')
        ? new Promise(r => { release = () => r(csv(totalsRows(DAY), TOTALS_HEAD)); })
        : csv(detailsRows(FLOWS), DETAILS_HEAD));
    const refreshing = w.refresh();                    // the periodic refresh: all traffic, slow
    await tick();
    w.state.scope = 'wan';
    await w.render();                                  // internet only, at the moment on screen
    const before = requests.length;
    release();
    await refreshing;
    assert.equal(requests.length, before);             // nothing reloaded to undo it
    assert.deepEqual([w.state.request.scope, w.state.request.now], ['wan', NOW]);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 300] });
});

test('the cache keeps only the exports of the window on screen', async () => {
    const { w } = await load('1h');
    await w._detailsData('192.168.1.10');               // the drill-down's export
    await w._load((NOW + 900) * 1000);                  // the next refresh, 15 minutes on
    assert.deepEqual(Object.keys(w.cache), [`FlowSourceAddrTotals/${S(8, 25)}/${NOW + 900}/300`]);
});

test('the device panel follows the rows on screen, not the scope control', async () => {
    const { w } = await load('1h', 'wan');
    w.state.scope = 'all';                              // changed, not reloaded yet
    const d = await w._detailsData('192.168.1.10');
    assert.deepEqual(d.peers, { '8.8.8.8': 1000, '1.1.1.1': 300 });
});

test('a failed scope change says so and closes the device panel', async () => {
    const { w } = await load('1h');
    w.state.selected = '192.168.1.10';
    reply = () => null;                                 // the firewall stops answering
    w.state.scope = 'wan';
    await w.render();
    assert.match(dom['.td-body'], /Unable to read NetFlow data/);
    assert.equal(dom['.td-window small'], '');
    assert.equal(w.state.selected, null);
});

// --- recent ranges from the raw flow log -----------------------------------------

// a flows/totals answer as flows.py gives it (spec §5.1)
function totalsAnswer(from, to, { L = from - 3600, hourlyUntil = null,
                                  devices = { '192.168.1.10': [1000, 357, 1000, 300], '192.168.20.5': [50, 0, 0, 0] } } = {}) {
    return { v: '0.2.0', now: to, log_from: L,
             all: { from, to, hourly_until: hourlyUntil }, inet: { from: Math.max(from, L), to },
             wan: ['em0'], devices };
}
const flowsOnly = (answer) => (url) => (url.startsWith(`${FLOWS_API}/`) ? answer(url) : null);

async function loadRaw(range, answer, now = NOW, scope = 'all') {
    const w = widget(range, scope);
    reply = flowsOnly(answer);
    const before = requests.length;
    await w._load(now * 1000);
    return { w, made: requests.slice(before) };
}

test('a range starting within the last day, and not in the future, is read from the raw log', () => {
    assert.equal(m.rawRange(NOW - 86400, NOW), true);
    assert.equal(m.rawRange(NOW - 86401, NOW), false);
    assert.equal(m.rawRange(NOW, NOW), false);
});

test('spans are told rounded down to the minute', () => {
    assert.equal(m.fmtSpan(22 * 3600 + 40 * 60 + 59), '22 h 40 min');
    assert.equal(m.fmtSpan(3600), '1 h 0 min');
    assert.equal(m.fmtSpan(59), '0 min');
});

test('Last hour reads the raw log for exactly the last hour', async () => {
    const { w, made } = await loadRaw('1h', () => totalsAnswer(NOW - 3600, NOW));
    assert.deepEqual(made, [`${FLOWS_API}/totals/${NOW - 3600}/${NOW}`]);
    assert.deepEqual(w.state.window, [NOW - 3600, NOW]);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
    assert.deepEqual(w._windowCaption(), {
        text: 'Wed Sep 23 18:08:54 AEST 2026  →  Wed Sep 23 19:08:54 AEST 2026 · all traffic', note: null });
});

test('Today and Last 24 hours read the raw log from local midnight and from a day back', async () => {
    let r = await loadRaw('today', () => totalsAnswer(S(14, 0, 22), NOW));
    assert.deepEqual(r.made, [`${FLOWS_API}/totals/${S(14, 0, 22)}/${NOW}`]);
    assert.match(r.w._windowCaption().text, /^Wed Sep 23 00:00:00 AEST 2026 {2}→ {2}Wed Sep 23 19:08:54 AEST 2026/);
    r = await loadRaw('24h', () => totalsAnswer(NOW - 86400, NOW));
    assert.deepEqual(r.made, [`${FLOWS_API}/totals/${NOW - 86400}/${NOW}`]);
});

test('Yesterday and Last 7 days still read NetFlow\'s records', async () => {
    for (const range of ['yesterday', '7d']) {
        const before = requests.length;
        await load(range);
        assert.ok(!requests.slice(before).some(u => u.startsWith(`${FLOWS_API}/`)), range);
    }
});

test('both scopes come from one answer: switching scope sends no request', async () => {
    const { w } = await loadRaw('1h', () => ({ ...totalsAnswer(NOW - 3600, NOW), wan: ['pppoe0'] }));   // the upstream the answer counted
    const before = requests.length;
    w.state.scope = 'wan';
    await w.render();
    assert.equal(requests.length, before);
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 300] });            // devices with internet traffic only
    assert.match(dom['.td-window small'], / · internet only \(via pppoe0\)$/);
});

test('internet only reaching past the log says how much it covers', async () => {
    const L = NOW - 3000;
    const { w } = await loadRaw('1h', () => totalsAnswer(NOW - 3600, NOW, { L }), NOW, 'wan');
    const c = w._windowCaption();
    assert.match(c.text, /^Wed Sep 23 18:18:54 AEST 2026 {2}→/);
    assert.equal(c.note, 'Internet only covers the last 50 min: older flows are no longer in the log');
});

test('internet only for a range wholly before the log says so', async () => {
    const w = widget('custom', 'wan');
    w.state.customFrom = '2026-09-23T17:00';
    w.state.customTo = '2026-09-23T17:30';
    // the log starts after the range: no internet-only span, so no internet bytes
    reply = flowsOnly(() => totalsAnswer(S(7, 0), S(7, 30), { L: S(8, 0), devices: { '192.168.1.10': [1000, 357, 0, 0] } }));
    await w._load(NOW * 1000);
    assert.deepEqual(byIp(w), {});
    assert.equal(w._windowCaption().note, 'Internet only: no flows in the log for this range');
});

test('all traffic filled from hourly records shows the whole range with no note', async () => {
    const { w } = await loadRaw('24h', () => totalsAnswer(NOW - 86400, NOW, { L: NOW - 80000, hourlyUntil: NOW - 79200 }));
    assert.deepEqual(w._windowCaption(), {
        text: 'Tue Sep 22 19:08:54 AEST 2026  →  Wed Sep 23 19:08:54 AEST 2026 · all traffic', note: null });
});

test('a failed or refused raw read falls back to NetFlow\'s records, and says so', async () => {
    for (const refused of [null, { error: 'the NetFlow flow log holds no flows yet' }]) {
        const w = widget('1h');
        reply = (url) => (url.startsWith(`${FLOWS_API}/`) ? refused : serve(url));
        await w._load(NOW * 1000);
        assert.equal(requests[requests.length - 1], `${EXPORT}/FlowSourceAddrTotals/${S(8, 10)}/${NOW}/300`);
        assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
        assert.equal(w._windowCaption().note, FALLBACK);
    }
});

test('a raw answer that lands after a newer load is dropped', async () => {
    const w = widget('1h');
    let release;
    reply = flowsOnly((url) => (url.includes(`/${NOW - 3600}/`)
        ? new Promise(r => { release = () => r(totalsAnswer(NOW - 3600, NOW)); })
        : totalsAnswer(S(14, 0, 22), NOW)));
    const first = w._load(NOW * 1000);
    await tick();
    w.state.range = 'today';
    await w._load(NOW * 1000);
    release();
    assert.equal(await first, false);
    assert.deepEqual(w.state.window, [S(14, 0, 22), NOW]);
});

test('a raw load drops the exports of earlier windows', async () => {
    const { w } = await load('7d');                     // NetFlow's records: the week's export is cached
    assert.ok(Object.keys(w.cache).length > 0);
    w.state.range = '1h';
    reply = flowsOnly(() => totalsAnswer(NOW - 3600, NOW));
    await w._load(NOW * 1000);
    assert.deepEqual(Object.keys(w.cache), []);
});

test('on the fallback path, a raw answer landing after the scope was switched back shows the scope on screen', async () => {
    const { w } = await load('1h');                     // the raw log refused: NetFlow's records, all traffic
    let release;
    reply = (url) => (url.startsWith(`${FLOWS_API}/`)
        ? new Promise(r => { release = () => r(totalsAnswer(NOW - 3600, NOW)); })   // the raw log answers again, late
        : serve(url));
    w.state.scope = 'wan';
    const toWan = w.render();                           // reloads for internet only: the raw retry is slow
    await tick();
    w.state.scope = 'all';
    await w.render();                                   // back before it arrives
    release();
    await toWan;
    assert.equal(w.state.request.scope, 'all');
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
    assert.match(dom['.td-window small'], / · all traffic$/);
});

test('a raw answer for a range just picked follows a scope switch and back', async () => {
    const { w } = await load('7d');                     // Last 7 days on screen: NetFlow's records
    const pending = [];
    reply = (url) => (url.startsWith(`${FLOWS_API}/`)
        ? new Promise(r => pending.push(() => r(totalsAnswer(NOW - 3600, NOW))))
        : serve(url));
    w.state.range = '1h';
    const picked = w._load(NOW * 1000);                 // the range change's load: slow
    await tick();
    w.state.scope = 'wan';
    const toWan = w.render();                           // reloads the table on screen for internet only: slow too
    await tick();
    w.state.scope = 'all';
    await w.render();                                   // back before either arrives
    pending.slice().reverse().forEach(f => f());        // the scope switch's answer lands first
    await toWan;
    assert.equal(await picked, false);                  // replaced by the scope switch's load
    assert.equal(w.state.request.scope, 'all');
    assert.deepEqual(byIp(w), { '192.168.1.10': [1000, 357], '192.168.20.5': [50, 0] });
});

test('leaving Live for a custom range brings back nothing from before Live', async () => {
    const { w } = await loadRaw('1h', () => totalsAnswer(NOW - 3600, NOW));
    w.state.range = 'live';
    w.state.scope = 'wan';                              // the scope changed during Live
    w.state.range = 'custom';                           // what the range control does on leaving Live
    w.state.rows = [];
    w.state.window = null;
    await w.render();
    assert.equal(w.state.window, null);
    assert.deepEqual(byIp(w), {});
});

// a flows/device answer as flows.py gives it (spec §5.1)
function deviceAnswer(from, to) {
    return { v: '0.2.0', ip: '192.168.1.10', from, to, log_from: from,
             peers: { all: [['8.8.8.8', 1000], ['1.1.1.1', 300], ['192.168.20.5', 50]], inet: [['8.8.8.8', 1000], ['1.1.1.1', 300]] },
             ports: { all: [['443', 1300], ['445', 50]], inet: [['443', 1300]] } };
}

test('the device panel reads the raw log for the table\'s window, and a scope switch reuses it', async () => {
    const { w } = await loadRaw('1h', (url) => (url.includes('/device/') ? deviceAnswer(NOW - 3600, NOW)
        : totalsAnswer(NOW - 3600, NOW)));
    const before = requests.length;
    let d = await w._detailsData('192.168.1.10');
    assert.deepEqual(requests.slice(before), [`${FLOWS_API}/device/192.168.1.10/${NOW - 3600}/${NOW}`]);
    assert.deepEqual(d, { peers: { '8.8.8.8': 1000, '1.1.1.1': 300, '192.168.20.5': 50 }, ports: { 443: 1300, 445: 50 },
                          down: 1000, up: 357, note: null });
    w.state.scope = 'wan';
    await w.render();
    d = await w._detailsData('192.168.1.10');
    assert.equal(requests.length, before + 1);
    assert.deepEqual([d.peers, d.down, d.up], [{ '8.8.8.8': 1000, '1.1.1.1': 300 }, 1000, 300]);
});

test('the device panel says when its lists cover less than the range', async () => {
    const { w } = await loadRaw('1h', (url) => (url.includes('/device/') ? deviceAnswer(NOW - 3000, NOW)
        : totalsAnswer(NOW - 3600, NOW)));
    assert.equal((await w._detailsData('192.168.1.10')).note, 'Peers and ports cover the last 50 min');
});

test('a refused panel read is an error, which the panel shows as unavailable', async () => {
    const { w } = await loadRaw('1h', (url) => (url.includes('/device/') ? { error: '192.168.1.99 is not a local device' }
        : totalsAnswer(NOW - 3600, NOW)));
    await assert.rejects(w._detailsData('192.168.1.99'));
});
