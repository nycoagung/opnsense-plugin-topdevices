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
    }
    async getWidgetConfig() { return (this.config && this.config.widget) || {}; }
    openEventSource(url, onMessage) {
        this.closeEventSource();
        if (this.eventSourceRetryCount >= this.retryLimit) return;
        this.eventSourceUrl = url;
        this.eventSourceOnData = onMessage;
        this.eventSource = new EventSource(url);
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
globalThis.Chart = class {
    constructor(el, cfg) { this.config = { type: cfg.type }; this.data = cfg.data; }
    update() {}
    destroy() {}
};

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

test('row order holds while hovering, new rows follow', () => {
    const rows = ['a', 'b', 'c', 'd'].map(ip => ({ ip }));
    assert.deepEqual(m.holdOrder(rows, ['c', 'a']).map(r => r.ip), ['c', 'a', 'b', 'd']);
});

test('fmtRate', () => {
    assert.equal(m.fmtRate(0), '0 b/s');
    assert.equal(m.fmtRate(999), '999 b/s');
    assert.equal(m.fmtRate(108000), '108 kb/s');
    assert.equal(m.fmtRate(5656000), '5.7 Mb/s');
    assert.equal(m.fmtRate(1500000000), '1.5 Gb/s');
});

