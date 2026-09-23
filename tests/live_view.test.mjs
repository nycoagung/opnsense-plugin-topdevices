// Live-view logic of TopDevices.js. Run:  node --test tests/live_view.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

// --- what the dashboard provides, reduced to what the widget touches ----------
// FakeBase mirrors BaseWidget.openEventSource / closeEventSource /
// onVisibilityChanged / onWidgetClose from opnsense/core 26.7.4
// (src/opnsense/www/js/widgets/BaseWidget.js). Keep it in step with upstream.
class FakeEventSource {
    constructor(url) { this.url = url; this.closed = false; FakeEventSource.opened.push(url); }
    close() { this.closed = true; }
}
FakeEventSource.opened = [];

globalThis.EventSource = FakeEventSource;
globalThis.BaseWidget = class {
    constructor(config) {
        this.config = config;
        this.eventSource = null;
        this.eventSourceUrl = null;
        this.eventSourceOnData = null;
        this.eventSourceRetryCount = 0;
        this.retryLimit = 3;
        this.timeoutPeriod = 5000;
    }
    async getWidgetConfig() { return (this.config && this.config.widget) || {}; }
    openEventSource(url, onMessage) {
        this.closeEventSource();
        if (this.eventSourceRetryCount >= this.retryLimit) return;
        this.eventSourceUrl = url;
        this.eventSourceOnData = onMessage;
        this.eventSource = new EventSource(url);
        // upstream: reconnect with the captured url if onopen has not fired in
        // time. Only onopen clears it - closeEventSource does not.
        const timer = setTimeout(() => {
            this.closeEventSource();
            this.eventSourceRetryCount++;
            this.openEventSource(url, onMessage);
        }, this.timeoutPeriod);
        if (timer.unref) timer.unref();          // node only: never hold the test process open
        this.eventSource.onopen = () => { clearTimeout(timer); this.eventSourceRetryCount = 0; };
        this.eventSource.onmessage = onMessage;
    }
    closeEventSource() {
        if (this.eventSource !== null) { this.eventSource.close(); this.eventSource = null; }
    }
    onVisibilityChanged(visible) {
        if (this.eventSourceUrl !== null) {
            if (visible) this.openEventSource(this.eventSourceUrl, this.eventSourceOnData);
            else if (this.eventSource !== null) this.closeEventSource();
        }
    }
    onWidgetClose() { this.closeEventSource(); }
};
// jQuery and Chart.js: chainable no-ops. `length` is 0 so "element not found" paths run.
const chain = new Proxy(function () {}, {
    get: (t, p) => (p === Symbol.toPrimitive ? () => '' : p === 'length' ? 0 : chain),
    apply: () => chain
});
globalThis.$ = chain;
globalThis.document = { hidden: false };
const store = new Map();                     // localStorage, for the remembered view
globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)); },
    removeItem: (k) => { store.delete(k); }
};
globalThis.Chart = class {
    constructor(el, cfg) {
        this.config = { type: cfg.type }; this.data = cfg.data; this.options = cfg.options;
        this._built = new Set(cfg.data.datasets);
    }
    update(mode) {
        // chartjs-plugin-streaming 3.0.2 (the dashboard's): a 'quiet' update first
        // patches every dataset's controller, and a dataset added since the last
        // update has none yet - the real page threw exactly this
        if (mode === 'quiet' && this.data.datasets.some(ds => !this._built.has(ds))) {
            throw new TypeError("Cannot set properties of null (setting '_setStyle')");
        }
        this._built = new Set(this.data.datasets);
    }
    destroy() {}
};
// the dashboard loads chartjs-plugin-streaming, which registers the 'realtime' scale
const withStreaming = { getScale(id) { if (id === 'realtime') return {}; throw new Error(`"${id}" is not a registered scale.`); } };
globalThis.Chart.registry = withStreaming;

const m = await import('../src/opnsense/www/js/widgets/TopDevices.js');
const TopDevices = m.default;

const ev = (dt, devices) => ({ dt, devices });
const dev = (all, inet = [0, 0], peers = [], ports = { all: [], inet: [] }) => ({ all, inet, peers, ports });

test('a baseline event changes nothing', () => {
    const v = m.mergeLive(null, ev(0, { a: dev([9, 9]) }), 0);
    assert.deepEqual(v, { events: [], seen: { all: {}, inet: {} } });
});

test('rates are time-weighted over the window', () => {
    let v = m.mergeLive(null, ev(1, { a: dev([1000, 100]) }), 1000);
    v = m.mergeLive(v, ev(2, { a: dev([4000, 400]) }), 3000);
    assert.deepEqual(m.liveRates(v, 'all'), { a: { down: 3000, up: 300 } });
});

test('the window keeps only the newest LIVE_WINDOW_S seconds', () => {
    let v = null;
    for (let i = 1; i <= 4; i++) v = m.mergeLive(v, ev(1, { a: dev([i * 100, 0]) }), i * 1000);
    assert.equal(v.events.length, 3);
    assert.deepEqual(m.liveRates(v, 'all').a, { down: 300, up: 0 });   // (200 + 300 + 400) / 3
    v = m.mergeLive(v, ev(5, { a: dev([50, 0]) }), 10000);           // one long event covers it alone
    assert.equal(v.events.length, 1);
});

test('a quiet device lingers at 0, then drops off', () => {
    let v = m.mergeLive(null, ev(1, { a: dev([800, 80]) }), 0);
    for (let t = 1000; t <= 3000; t += 1000) v = m.mergeLive(v, ev(1, {}), t);
    assert.deepEqual(m.liveRates(v, 'all'), { a: { down: 0, up: 0 } });
    v = m.mergeLive(v, ev(1, {}), 10001);
    assert.deepEqual(m.liveRates(v, 'all'), {});
});

test('each scope lists only devices with traffic in it', () => {
    const v = m.mergeLive(null, ev(1, { cam: dev([10, 5000]), phone: dev([900, 90], [900, 90]) }), 0);
    assert.deepEqual(Object.keys(m.liveRates(v, 'all')).sort(), ['cam', 'phone']);
    assert.deepEqual(Object.keys(m.liveRates(v, 'inet')), ['phone']);
});

test('detail averages peers and ports; internet scope hides local peers', () => {
    const peers = [['203.0.113.7', 800, 80, 1], ['192.168.1.1', 40, 4, 0]];
    const ports = { all: [[443, 800, 80], [53, 40, 4]], inet: [[443, 800, 80]] };
    const v = m.mergeLive(null, ev(1, { a: dev([840, 84], [800, 80], peers, ports) }), 0);
    assert.deepEqual(m.liveDetail(v, 'a', 'all'), {
        peers: [{ key: '203.0.113.7', down: 800, up: 80 }, { key: '192.168.1.1', down: 40, up: 4 }],
        ports: [{ key: '443', down: 800, up: 80 }, { key: '53', down: 40, up: 4 }]
    });
    assert.deepEqual(m.liveDetail(v, 'a', 'inet'), {
        peers: [{ key: '203.0.113.7', down: 800, up: 80 }],
        ports: [{ key: '443', down: 800, up: 80 }]
    });
});

test('status timeouts scale with the interval actually in use', () => {
    assert.equal(m.liveStatusFor(5000, 1), 'live');
    assert.equal(m.liveStatusFor(7000, 1), 'reconnecting');
    assert.equal(m.liveStatusFor(21000, 1), 'unavailable');
    assert.equal(m.liveStatusFor(7000, 5), 'live');           // 3 x 5 s
    assert.equal(m.liveStatusFor(16000, 5), 'reconnecting');
    assert.equal(m.liveStatusFor(31000, 5), 'unavailable');   // 6 x 5 s
    assert.equal(m.liveStatusFor(25000, 10), 'live');         // throttled to 10 s: 3 x 10 s
    assert.equal(m.liveStatusFor(31000, 10), 'reconnecting');
});

test('fmtRate', () => {
    assert.equal(m.fmtRate(0), '0 b/s');
    assert.equal(m.fmtRate(999), '999 b/s');
    assert.equal(m.fmtRate(108000), '108 kb/s');
    assert.equal(m.fmtRate(5656000), '5.7 Mb/s');
    assert.equal(m.fmtRate(1500000000), '1.5 Gb/s');
});

function widget(interval = '1') {
    const w = new TopDevices({ widget: { liveInterval: interval } });
    w.state.range = 'live';
    return w;
}

test('starting Live opens the stream at the configured interval', async (t) => {
    FakeEventSource.opened.length = 0;
    const w = widget('5');
    t.after(() => w._stopLive());
    await w._startLive();
    assert.deepEqual(FakeEventSource.opened, ['/api/topdevices/live/stream/5']);
    assert.ok(w.live.watchdog);
});

test('leaving Live forgets the stream, so a hidden/shown tab cannot reopen it', async () => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    await w._startLive();
    const es = w.eventSource;
    w.state.range = '24h';
    w._stopLive();
    assert.equal(es.closed, true);
    assert.equal(w.live.watchdog, null);
    w.onVisibilityChanged(false);
    w.onVisibilityChanged(true);
    assert.equal(FakeEventSource.opened.length, 1);
});

test('while Live, a hidden/shown tab reopens the stream', async (t) => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    t.after(() => w._stopLive());
    await w._startLive();
    w.onVisibilityChanged(false);
    w.onVisibilityChanged(true);
    assert.equal(FakeEventSource.opened.length, 2);
    assert.equal(w.live.status, 'connecting');
});

test('closing the widget closes the stream', async () => {
    const w = widget();
    await w._startLive();
    const es = w.eventSource;
    w.onWidgetClose();
    assert.equal(es.closed, true);
    assert.equal(w.eventSource, null);
    assert.equal(w.live.watchdog, null);
});

test('a range change during start-up does not leave a stream behind', async () => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    const starting = w._startLive();       // awaits config and names
    w.state.range = '24h';
    w._stopLive();
    await starting;
    assert.equal(FakeEventSource.opened.length, 0);
    assert.equal(w.live.watchdog, null);
});

test('events drive the status; the watchdog escalates and gives up', async (t) => {
    const w = widget();
    t.after(() => w._stopLive());
    await w._startLive();
    const send = (e) => w.eventSource.onmessage({ data: JSON.stringify(e) });
    send({ dt: 0, devices: {}, effective: 1, wan: { devs: ['em0'], down: 0, up: 0 } });
    assert.equal(w.live.status, 'measuring');
    send({ dt: 1, effective: 1, wan: { devs: ['em0'], down: 8, up: 8 },
           devices: { '192.168.1.10': dev([1000000, 8000], [1000000, 8000]) } });
    assert.equal(w.live.status, 'live');
    assert.deepEqual(w.state.rows.map(r => [r.ip, r.down]), [['192.168.1.10', 1000000]]);
    w.live.lastAt = Date.now() - 7000;
    w._liveTick();
    assert.equal(w.live.status, 'reconnecting');
    const es = w.eventSource;
    w.live.lastAt = Date.now() - 21000;
    w._liveTick();
    assert.equal(w.live.status, 'unavailable');
    assert.equal(es.closed, true);
});

async function running(t) {
    const w = widget();
    t.after(() => w._stopLive());
    await w._startLive();
    const send = (e) => w.eventSource.onmessage({ data: JSON.stringify(e) });
    return { w, send };
}
const wan = { devs: ['em0'], down: 0, up: 0 };

test('a recycled stream keeps its rows on screen through the new baseline', async (t) => {
    const { w, send } = await running(t);
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.10': dev([5000, 50], [5000, 50]) } });
    send({ dt: 0, effective: 1, wan, devices: {} });          // the hourly recycle's first event
    assert.equal(w.live.status, 'measuring');
    assert.deepEqual(w.state.rows.map(r => r.ip), ['192.168.1.10']);
});

test('an event carrying a warning still shows its rates, and the warning', async (t) => {
    const { w, send } = await running(t);
    send({ dt: 1, effective: 1, wan, error: 'routes: netstat exited with 1',
           devices: { '192.168.1.10': dev([5000, 50], [5000, 50]) } });
    assert.deepEqual(w.state.rows.map(r => [r.ip, r.down]), [['192.168.1.10', 5000]]);
    assert.match(w._liveSummary(), /routes: netstat exited with 1/);
});

test('an unavailable stream retries by itself', async (t) => {
    FakeEventSource.opened.length = 0;
    const { w } = await running(t);
    w.live.lastAt = Date.now() - 21000;
    w._liveTick();
    assert.equal(w.live.status, 'unavailable');
    w._liveTick();                                          // too soon: no retry yet
    assert.equal(FakeEventSource.opened.length, 1);
    w.live.retryAt = Date.now() - m.LIVE_RETRY_MS;
    w._liveTick();
    await new Promise((r) => setTimeout(r, 0));             // _startLive awaits config and names
    assert.equal(FakeEventSource.opened.length, 2);
    assert.equal(w.live.status, 'connecting');
});

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

test('a connect timer that fires after leaving Live does not reopen the stream', async () => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    w.timeoutPeriod = 5;                    // the stream is still connecting: onopen never fires here
    await w._startLive();
    w.state.range = '24h';
    w._stopLive();
    await pause(30);
    assert.deepEqual(FakeEventSource.opened, ['/api/topdevices/live/stream/1']);
    assert.equal(w.eventSource, null);
    assert.equal(w.eventSourceUrl, null);
});

test('a connect timer that fires after the widget is removed does not reopen the stream', async () => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    w.timeoutPeriod = 5;
    await w._startLive();
    w.onWidgetClose();
    await pause(30);
    assert.equal(FakeEventSource.opened.length, 1);
    assert.equal(w.eventSource, null);
});

test('within Live, the connect timer still reconnects a stream that never opened', async (t) => {
    FakeEventSource.opened.length = 0;
    const w = widget();
    t.after(() => w.onWidgetClose());
    w.timeoutPeriod = 5;
    await w._startLive();
    await pause(12);
    assert.ok(FakeEventSource.opened.length >= 2, FakeEventSource.opened.length);
});

test('the summary says what Live could not count, and stays quiet when nothing was missed', async (t) => {
    const { w, send } = await running(t);
    send({ dt: 1, effective: 1, wan, v6_skipped: 7, unparsed: 2, devices: {} });
    const missed = w._liveSummary();
    assert.match(missed, /IPv6/);
    assert.match(missed, /\b7\b/);
    assert.match(missed, /\b2\b/);
    send({ dt: 1, effective: 1, wan, v6_skipped: 0, unparsed: 0, devices: {} });
    assert.doesNotMatch(w._liveSummary(), /IPv6|unreadable/);
});

/* ---------- picking devices, fixed order (Live only) ---------- */

// Two local networks as _loadNetworks() derives them, and their gateways.
function networks(w) {
    const net = (addr, bits, key, label) => {
        const mask = (0xffffffff << (32 - bits)) | 0, n = (w._ip2int(addr) & mask) | 0;
        return { key, label, net: n, mask, bcast: (n | ~mask) | 0 };
    };
    w.networks = [net('192.168.1.0', 24, 'lan', 'LAN'), net('192.168.20.0', 24, 'opt1', 'IOT')];
    w.ifaceNames = { '192.168.1.254': 'LAN gateway', '192.168.20.1': 'IOT gateway' };
}

test('picked devices are always listed, idle ones at 0, in A to Z order', async (t) => {
    const { w, send } = await running(t);
    w.names = { '192.168.1.10': 'nas', '192.168.1.9': 'Camera', '192.168.1.30': 'tv' };
    w.state.livePick = ['192.168.1.30', '192.168.1.10', '192.168.1.9'];
    send({ dt: 1, effective: 1, wan, devices: {
        '192.168.1.10': dev([8000, 800], [8000, 800]), '192.168.1.77': dev([90000, 9]) } });
    assert.deepEqual(w._liveTableRows().map(r => [r.ip, r.down, r.up]),
                     [['192.168.1.9', 0, 0], ['192.168.1.10', 8000, 800], ['192.168.1.30', 0, 0]]);
});

test('with nothing picked, Live lists the busiest first whatever the column sort', async (t) => {
    const { w, send } = await running(t);
    w.names = { '192.168.1.10': 'alpha', '192.168.1.11': 'bravo', '192.168.1.12': 'charlie' };
    w.state.sortKey = 'name';
    w.state.sortDir = 'asc';
    w.state.rowsN = 2;
    send({ dt: 1, effective: 1, wan, devices: {
        '192.168.1.10': dev([100, 0]), '192.168.1.11': dev([900, 0]), '192.168.1.12': dev([500, 0]) } });
    assert.deepEqual(w._liveTableRows().map(r => r.ip), ['192.168.1.11', '192.168.1.12']);
});

test('the network filter and search still narrow a picked table', async (t) => {
    const { w, send } = await running(t);
    networks(w);
    w.names = { '192.168.20.5': 'camera-1' };
    w.state.livePick = ['192.168.1.10', '192.168.20.5'];
    send({ dt: 1, effective: 1, wan, devices: {} });
    w.state.network = 'opt1';
    assert.deepEqual(w._liveTableRows().map(r => r.ip), ['192.168.20.5']);
    w.state.network = '';
    w.state.search = '1.10';
    assert.deepEqual(w._liveTableRows().map(r => r.ip), ['192.168.1.10']);
});

test('a header click sorts the NetFlow ranges but changes nothing in Live', () => {
    const w = widget();
    w.state.sortKey = 'total';
    w.state.sortDir = 'desc';
    assert.equal(w._sortBy('name'), false);
    assert.deepEqual([w.state.sortKey, w.state.sortDir], ['total', 'desc']);
    w.state.range = '24h';
    assert.equal(w._sortBy('name'), true);
    assert.deepEqual([w.state.sortKey, w.state.sortDir], ['name', 'asc']);
    w._sortBy('name');
    assert.deepEqual([w.state.sortKey, w.state.sortDir], ['name', 'desc']);
});

test('picks are remembered across a reload, without anything that is not a device address', async (t) => {
    store.clear();
    store.set('opnsense.topdevices.view', JSON.stringify(
        { range: 'live', livePick: ['192.168.1.20', 'x', 5, '192.168.1.20', '192.168.1.3', '<b>'] }));
    const w = new TopDevices({ widget: {} });
    t.after(() => { w._stopLive(); store.clear(); });
    await w.onMarkupRendered();
    assert.deepEqual(w.state.livePick, ['192.168.1.20', '192.168.1.3']);
    w.state.livePick = ['192.168.1.7'];
    w._saveView();
    assert.deepEqual(JSON.parse(store.get('opnsense.topdevices.view')).livePick, ['192.168.1.7']);
});

test('the picker offers named devices and anything Live saw, never the firewall or a broadcast address', async (t) => {
    const { w, send } = await running(t);
    networks(w);
    w.names = { '192.168.1.10': 'nas', '192.168.20.5': 'camera', '192.168.1.254': 'opnsense', '8.8.8.8': 'dns.google' };
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.77': dev([5, 5]), '192.168.20.255': dev([1, 1]) } });
    send({ dt: 1, effective: 1, wan, devices: {} });
    w.state.livePick = ['192.168.1.99', '10.9.9.9'];     // picked on another day; 10.9.9.9 is on no network now
    assert.deepEqual(w._pickChoices(), [
        { label: 'LAN', devices: [{ ip: '192.168.1.77', name: '' }, { ip: '192.168.1.99', name: '' },
                                  { ip: '192.168.1.10', name: 'nas' }] },
        { label: 'IOT', devices: [{ ip: '192.168.20.5', name: 'camera' }] },
        { label: 'Other', devices: [{ ip: '10.9.9.9', name: '' }] }
    ]);
});

test('the table is rebuilt when its rows change, and only patched when their rates do', () => {
    const nas = { ip: '192.168.1.10', name: 'nas', net: 'lan', down: 1, up: 2, total: 3 };
    const tv = { ip: '192.168.1.11', name: 'tv', net: 'lan', down: 0, up: 0, total: 0 };
    assert.equal(m.liveRowsKey([nas, tv]), m.liveRowsKey([{ ...nas, down: 9, up: 9, total: 18 }, tv]));
    assert.notEqual(m.liveRowsKey([nas, tv]), m.liveRowsKey([tv, nas]));
    assert.notEqual(m.liveRowsKey([nas, tv]), m.liveRowsKey([{ ...nas, name: 'nas-2' }, tv]));
    assert.notEqual(m.liveRowsKey([nas]), m.liveRowsKey([nas, tv]));
});

/* ---------- line graph, a bar axis that only grows, only the busiest moves (Live) ---------- */

test('with nothing picked, only the busiest device moves, to the top', () => {
    const r = (ip, total) => ({ ip, name: '', net: 'lan', down: total, up: 0, total });
    // seeded busiest first
    assert.deepEqual(m.dynamicOrder(null, [r('a', 9), r('b', 5), r('c', 1), r('d', 0)], 3), ['a', 'b', 'c']);
    // c is the busiest now: it moves to the top and nothing else moves
    assert.deepEqual(m.dynamicOrder(['a', 'b', 'c'], [r('c', 9), r('a', 5), r('b', 1)], 3), ['c', 'a', 'b']);
    // a new busiest enters at the top and the bottom row drops off
    assert.deepEqual(m.dynamicOrder(['a', 'b', 'c'], [r('x', 9), r('a', 5), r('b', 4), r('c', 3)], 3), ['x', 'a', 'b']);
    // the busiest already on top, or nobody busy at all: no change
    assert.deepEqual(m.dynamicOrder(['a', 'b', 'c'], [r('a', 9), r('c', 8), r('b', 7)], 3), ['a', 'b', 'c']);
    assert.deepEqual(m.dynamicOrder(['b', 'a', 'c'], [r('a', 0), r('b', 0), r('c', 0)], 3), ['b', 'a', 'c']);
    // a row whose device is gone is dropped, and a short list is topped up in the order given
    assert.deepEqual(m.dynamicOrder(['a', 'gone', 'c'], [r('a', 9), r('c', 0), r('d', 0)], 3), ['a', 'c', 'd']);
    assert.deepEqual(m.dynamicOrder(['a'], [r('a', 9), r('b', 0), r('c', 0)], 3), ['a', 'b', 'c']);
    // under the pointer nothing moves at all
    assert.deepEqual(m.dynamicOrder(['a', 'b', 'c'], [r('c', 9), r('a', 5), r('b', 1)], 3, true), ['a', 'b', 'c']);
});

test('with nothing picked, Live lists the rows setting exactly, topped up with known devices', async (t) => {
    const { w, send } = await running(t);
    networks(w);
    w.names = { '192.168.1.10': 'alpha', '192.168.1.11': 'bravo', '192.168.1.12': 'charlie', '192.168.1.13': 'delta' };
    w.state.rowsN = 3;
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.12': dev([900, 0]), '192.168.1.13': dev([300, 0]) } });
    assert.deepEqual(w._liveTableRows().map(r => r.ip), ['192.168.1.12', '192.168.1.13', '192.168.1.10']);
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.13': dev([9000, 0]) } });
    assert.deepEqual(w._liveTableRows().map(r => r.ip), ['192.168.1.13', '192.168.1.12', '192.168.1.10']);
});

test('axis tops round up to a round figure', () => {
    const want = [[0, 0], [950, 1000], [1000000, 1000000], [2600000, 3000000], [7400000, 8000000],
                  [12000000, 12000000], [12500000, 15000000], [334700000, 400000000]];
    for (const [v, top] of want) assert.equal(m.niceCeil(v), top, `niceCeil(${v})`);
});

test('the Live bar axis only grows, and starts fresh when Live restarts', async (t) => {
    const { w, send } = await running(t);
    w.state.liveChart = 'bar';
    const top = () => w.chartObj.options.scales.y.max;
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.10': dev([7000000, 400000]) } });
    assert.equal(top(), 8000000);                        // 7.4 Mb/s down + up, rounded up
    for (let i = 0; i < 3; i++) send({ dt: 1, effective: 1, wan, devices: { '192.168.1.10': dev([100000, 0]) } });
    assert.equal(top(), 8000000);                        // the 3 s average is down to 0.1 Mb/s; the axis holds
    await w._startLive();                                // the refresh button, a scope or filter change
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.10': dev([100000, 0]) } });
    assert.equal(top(), 100000);
});

test('the Live line graph plots each interval: download per device, upload in the point, 0 while idle', async (t) => {
    const { w, send } = await running(t);
    w.state.livePick = ['192.168.1.10', '192.168.1.11'];
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.10': dev([5000, 50]) } });
    send({ dt: 1, effective: 1, wan, devices: { '192.168.1.11': dev([7000, 70]) } });
    assert.equal(w.chartObj.config.type, 'line');
    const byIp = Object.fromEntries(w.chartObj.data.datasets.map(d => [d.ip, d]));
    assert.deepEqual(byIp['192.168.1.10'].data.map(p => [p.y, p.up]), [[5000, 50], [0, 0]]);
    assert.deepEqual(byIp['192.168.1.11'].data.map(p => [p.y, p.up]), [[0, 0], [7000, 70]]);
});

test('a device keeps its line colour when the listed devices change', async (t) => {
    const { w, send } = await running(t);
    const colours = () => Object.fromEntries(w.chartObj.data.datasets.map(d => [d.ip, d.borderColor]));
    w.state.livePick = ['192.168.1.10', '192.168.1.11'];
    send({ dt: 1, effective: 1, wan, devices: {} });
    const before = colours();
    assert.notEqual(before['192.168.1.10'], before['192.168.1.11']);
    w.state.livePick = ['192.168.1.11'];
    send({ dt: 1, effective: 1, wan, devices: {} });
    w.state.livePick = ['192.168.1.9', '192.168.1.10', '192.168.1.11'];
    send({ dt: 1, effective: 1, wan, devices: {} });
    const after = colours();
    assert.equal(after['192.168.1.10'], before['192.168.1.10']);
    assert.equal(after['192.168.1.11'], before['192.168.1.11']);
    assert.ok(![before['192.168.1.10'], before['192.168.1.11']].includes(after['192.168.1.9']));
});

test('Live keeps its own chart choice, and only Live offers the line graph', () => {
    const w = widget();
    assert.equal(w._chartKind(), 'line');                // Live starts on the line graph
    assert.equal(w._setChart('bar'), true);
    assert.equal(w._chartKind(), 'bar');
    w.state.range = '24h';
    assert.equal(w._chartKind(), 'pie');                 // the NetFlow ranges keep their own choice
    assert.equal(w._setChart('line'), false);            // and are never offered the line graph
    assert.equal(w._chartKind(), 'pie');
    w._setChart('none');
    w.state.range = 'live';
    assert.equal(w._chartKind(), 'bar');
});

test('without the streaming plugin, Live draws the bar chart instead of the line graph', (t) => {
    Chart.registry = { getScale(id) { throw new Error(`"${id}" is not a registered scale.`); } };
    t.after(() => { Chart.registry = withStreaming; });
    const w = widget();
    assert.equal(w.state.liveChart, 'line');
    assert.equal(w._chartKind(), 'bar');
});

test('a device joining the line graph does not break its streaming update', async (t) => {
    const { w, send } = await running(t);
    w.state.livePick = ['192.168.1.10'];
    send({ dt: 1, effective: 1, wan, devices: {} });
    w.state.livePick = ['192.168.1.10', '192.168.1.11'];
    send({ dt: 1, effective: 1, wan, devices: {} });       // threw inside chartjs-plugin-streaming
    assert.deepEqual(w.chartObj.data.datasets.map(d => d.ip), ['192.168.1.10', '192.168.1.11']);
});

test('listed devices never share a line colour, even after more than ten have been drawn', async (t) => {
    const { w, send } = await running(t);
    const ips = (from, n) => Array.from({ length: n }, (_, i) => `192.168.1.${from + i}`);
    for (const pick of [ips(10, 10), ips(30, 5), ips(10, 5).concat(ips(30, 5))]) {
        w.state.livePick = pick;
        send({ dt: 1, effective: 1, wan, devices: {} });
        const colours = w.chartObj.data.datasets.map(d => d.borderColor);
        assert.equal(new Set(colours).size, colours.length, colours.join(' '));
    }
});

test("a lease hostname of '*' (Dnsmasq's 'none') is no name", async () => {
    const w = widget();
    w.ajaxCall = async (url) => (url.includes('leases')
        ? { rows: [{ address: '192.168.20.136', hostname: '*' }, { address: '192.168.1.5', hostname: 'mac' }] }
        : { rows: [] });
    await w._loadNames();
    assert.deepEqual(w.names, { '192.168.1.5': 'mac' });
});

test('with nothing picked, the list is seeded busiest first from the first measured interval', async (t) => {
    const w = widget();
    t.after(() => w._stopLive());
    networks(w);                                      // as on the dashboard: known before Live starts
    const leases = [['192.168.1.10', 'alpha'], ['192.168.1.11', 'bravo'], ['192.168.1.12', 'charlie']];
    w.ajaxCall = async (url) => ({ rows: url.includes('leases') ? leases.map(([address, hostname]) => ({ address, hostname })) : [] });
    w.state.rowsN = 3;
    await w._startLive();                             // renders once before any data
    const send = (e) => w.eventSource.onmessage({ data: JSON.stringify(e) });
    send({ dt: 0, effective: 1, wan, devices: {} });  // the sampler's baseline
    send({ dt: 1, effective: 1, wan, devices: {
        '192.168.1.12': dev([900, 0]), '192.168.1.11': dev([500, 0]), '192.168.1.10': dev([100, 0]) } });
    assert.deepEqual(w._liveTableRows().map(r => r.ip), ['192.168.1.12', '192.168.1.11', '192.168.1.10']);
});
