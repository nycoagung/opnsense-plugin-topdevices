// The Connected devices widget (ConnectedDevices.js). Run:  node --test tests/connected_view.test.mjs
import test from 'node:test';
import assert from 'node:assert/strict';

// --- what the dashboard provides, reduced to what the widget touches ----------
globalThis.BaseWidget = class {
    constructor(config) { this.config = config; }
};
// jQuery: each selector records what the widget wrote to it. Elements the widget looks
// up but the test page lacks (the grid item, the header) have length 0.
let page = {};
const node = (selector) => {
    const r = page[selector] || (page[selector] = { css: {}, attr: {} });
    const api = {
        length: 0,
        html(v) { if (v === undefined) return r.html; r.html = v; return api; },
        text(v) { if (v === undefined) return r.text; r.text = v; return api; },
        css(k, v) { if (typeof k === 'object') Object.assign(r.css, k); else r.css[k] = v; return api; },
        attr(k, v) { r.attr[k] = v; return api; },
        empty() { r.html = ''; return api; },
        on() { return api; }, first() { return api; }, before() { return api; }, prepend() { return api; },
        removeClass() { return api; }, addClass(c) { r.added = c; return api; },
        find() { return node('(none)'); }, closest() { return node('(none)'); },
    };
    return api;
};
globalThis.$ = (s) => node(typeof s === 'string' && s.trim().startsWith('<') ? '(markup)' : s);
const storage = {};
globalThis.localStorage = { getItem: (k) => (k in storage ? storage[k] : null), setItem: (k, v) => { storage[k] = String(v); } };

const mod = await import('../src/opnsense/www/js/widgets/ConnectedDevices.js');
const { escapeHtml, zoneOf, clockText, seenText, statusHtml, statusTitle, summaryText, noteTexts, bodyHtml } = mod;
const ConnectedDevices = mod.default;

const T0 = 1793000000;                    // Mon 26 Oct 2026 07:33:20 UTC = 16:33:20 in Tokyo
const dev = (name, ip, mac, more) => ({ name, ip, mac, vendor: '', since: null, before: null, for: '',
    first_seen: null, last_seen: null, ...more });
const answer = (more) => ({
    v: '0.5.0', status: 'ok', now: T0, zone: 'UTC', discovery: { state: 'on', last: T0 - 30 }, unread: [],
    tracking: { active: true, started: T0 - 7200, run: T0 - 30 },
    groups: [
        { name: 'LAN', vpn: false, devices: [
            dev('Laptop-Example', '192.0.2.10', '00:00:5e:00:53:01', { since: T0 - 8040, for: '2 h 14 min' }),
            dev('', '192.0.2.50', '00:00:5e:00:53:05', { before: T0 - 7200 })] },
        { name: 'VPN', vpn: true, devices: [dev('Phone-Example', '198.51.100.3', '', { since: T0 - 2700, for: '45 min' })] }],
    recent: [
        { name: 'Phone-Example-2', ip: '198.51.100.4', mac: '', vendor: '', vpn: true, first_seen: null, last_seen: T0 - 900 },
        { name: 'Tablet-Example', ip: '192.0.2.40', mac: '00:00:5e:00:53:04', vendor: '', vpn: false, first_seen: null,
          last_seen: T0 - 86400 }],
    ...more });
const rows = (html) => html.split('</tr>').filter((r) => r.includes('<tr'));

// --- the pure helpers -----------------------------------------------------------

test('escapeHtml escapes markup and quotes, and treats nothing as empty', () => {
    assert.equal(escapeHtml('<a href="x">&\'</a>'), '&lt;a href=&quot;x&quot;&gt;&amp;&#39;&lt;/a&gt;');
    assert.deepEqual([escapeHtml(null), escapeHtml(undefined), escapeHtml(5)], ['', '', '5']);
});

test('zoneOf keeps a zone the browser knows and drops any other', () => {
    assert.deepEqual([zoneOf('UTC'), zoneOf('Asia/Tokyo'), zoneOf('Not/AZone'), zoneOf(''), zoneOf(undefined)],
                     ['UTC', 'Asia/Tokyo', undefined, undefined, undefined]);
});

test('clockText is 24-hour, in the zone given', () => {
    assert.deepEqual([clockText(T0, 'UTC'), clockText(T0, 'Asia/Tokyo'), clockText(T0 - 7 * 3600, 'UTC')],
                     ['07:33', '16:33', '00:33']);
});

test('seenText: minutes, then hours, earlier the same day', () => {
    assert.deepEqual([seenText(T0 - 25 * 60, T0, 'UTC'), seenText(T0 - 3 * 3600 - 120, T0, 'UTC'), seenText(T0 - 59, T0, 'UTC')],
                     ['25 min ago', '3 h ago', '0 min ago']);
});

test('seenText: an earlier day shows the weekday and the time', () => {
    assert.equal(seenText(T0 - 86400, T0, 'UTC'), 'Sun 07:33');
    assert.equal(seenText(T0 - 6 * 86400, T0, 'UTC'), 'Tue 07:33');
});

test("seenText: the day is the zone's", () => {
    const now = Date.UTC(2026, 9, 26, 20, 0, 0) / 1000;      // Mon 20:00 UTC = Tue 05:00 in Tokyo
    const at = now - 6 * 3600;                               // Mon 14:00 UTC = Mon 23:00 in Tokyo
    assert.deepEqual([seenText(at, now, 'UTC'), seenText(at, now, 'Asia/Tokyo')], ['6 h ago', 'Mon 23:00']);
});

test('statusHtml: how long, before when, or just connected; when last seen', () => {
    const [laptop, unnamed] = answer().groups[0].devices;
    assert.deepEqual([statusHtml(laptop, true, T0, 'UTC'), statusHtml(unnamed, true, T0, 'UTC'),
                      statusHtml(dev('X', '192.0.2.9', '00:00:5e:00:53:09'), true, T0, 'UTC'),
                      statusHtml({ last_seen: T0 - 1500 }, false, T0, 'UTC')],
                     ['<span class="text-success">Connected</span> 2 h 14 min',
                      '<span class="text-success">Connected</span> before 05:33',
                      '<span class="text-success">Connected</span>',
                      'Last seen 25 min ago']);
});

test('statusTitle: the exact times, the vendor and the first sighting', () => {
    const laptop = dev('Laptop-Example', '192.0.2.10', '00:00:5e:00:53:01',
        { since: T0 - 8040, vendor: 'Example Inc', first_seen: Date.UTC(2026, 8, 19, 1, 0, 0) / 1000 });
    assert.equal(statusTitle(laptop, false, true, 'UTC'),
                 'Connected at Mon 26 Oct 2026 05:19\nVendor: Example Inc\nFirst seen on the network: 19 Sep 2026');
    assert.equal(statusTitle(dev('', '192.0.2.50', 'm', { before: T0 - 7200 }), false, true, 'UTC'),
                 'Connected before Mon 26 Oct 2026 05:33');
    assert.deepEqual([statusTitle(answer().recent[0], true, false, 'UTC'), statusTitle(answer().recent[1], false, false, 'UTC')],
                     ['Latest handshake: Mon 26 Oct 2026 07:18', 'Last heard from: Sun 25 Oct 2026 07:33']);
});

test('summaryText counts both lists, the second only when there is one', () => {
    assert.deepEqual([summaryText(answer()), summaryText(answer({ recent: [] }))],
                     ['3 connected · 2 seen this week', '3 connected']);
});

test('noteTexts: each condition, in order', () => {
    assert.deepEqual(noteTexts(answer()), []);
    assert.deepEqual(noteTexts(answer({ tracking: { active: false, started: null, run: null },
                                        discovery: { state: 'idle', last: T0 - 1500 }, unread: ['arp', 'wireguard'] })), [
        "How long devices have been connected comes from a job that runs every minute, and it hasn't run for over 3 minutes.",
        'Host discovery has heard nothing for 25 min: devices come from the ARP table, and last-seen times may be earlier than the devices really left.',
        "The ARP table couldn't be read: devices come from Host discovery alone.",
        "WireGuard's status couldn't be read: VPN clients aren't listed."]);
    assert.deepEqual(noteTexts(answer({ discovery: { state: 'idle', last: null } })),
        ['Host discovery has heard nothing on these networks: devices come from the ARP table, and last-seen times may be earlier than the devices really left.']);
    assert.deepEqual(noteTexts(answer({ discovery: { state: 'off', last: null } })),
        ["Host discovery is off: devices come from the ARP table, without first-seen times, and the ones not connected can't be listed."]);
    assert.deepEqual(noteTexts(answer({ discovery: { state: 'off', last: null }, unread: ['hostdiscovery'] })),
        ["Host discovery couldn't be read: devices come from the ARP table, without first-seen times, and the ones not connected can't be listed."]);
});

test('bodyHtml: groups, dots, names, MACs', () => {
    const html = bodyHtml(answer(), false, 'UTC');
    assert.ok(html.includes('<strong>LAN · 2 connected</strong>') && html.includes('<strong>VPN · 1 connected</strong>'));
    const lan = rows(html)[1];
    assert.ok(lan.includes('fa fa-circle text-success') && lan.includes('Laptop-Example') && lan.includes('00:00:5e:00:53:01'));
    assert.ok(rows(html)[2].includes('<i>(unnamed)</i>') && rows(html)[2].includes('title="No DHCP reservation and no name from DHCP"'));
    assert.ok(rows(html)[4].includes('>—</td>'), 'a VPN client shows no MAC');
});

test('bodyHtml: the not-connected section, closed and open', () => {
    const closed = rows(bodyHtml(answer(), false, 'UTC'));
    const head = closed.find((r) => r.includes('cd-recent-head'));
    assert.ok(head.includes('aria-expanded="false"') && head.includes('fa-chevron-right')
              && head.includes('Not connected · seen in the last 7 days (2)'));
    const recent = closed.filter((r) => /class="cd-recent[ "]/.test(r));    // not the header's cd-recent-head
    assert.equal(recent.length, 3);                                   // two devices and the note
    assert.ok(recent.every((r) => r.includes('display:none')));
    assert.ok(recent[0].includes('fa fa-circle-o text-muted') && recent[0].includes('Last seen 15 min ago') && recent[0].includes('>—</td>'));
    assert.ok(recent[1].includes('Last seen Sun 07:33') && recent[1].includes('00:00:5e:00:53:04'));
    assert.ok(recent[2].includes('Devices not seen for 7 days drop off the widget. They stay under Interfaces → Neighbors.'));
    const open = rows(bodyHtml(answer(), true, 'UTC'));
    assert.ok(open.find((r) => r.includes('cd-recent-head')).includes('aria-expanded="true"'));
    assert.ok(open.every((r) => !r.includes('display:none')));
});

test('bodyHtml: no section without recent devices, and a line when nobody is connected', () => {
    assert.ok(!bodyHtml(answer({ recent: [] }), true, 'UTC').includes('cd-recent'));
    const nobody = bodyHtml(answer({ groups: [] }), false, 'UTC');
    assert.ok(nobody.includes('No devices connected') && nobody.includes('cd-recent-head'));
});

// --- the widget ------------------------------------------------------------------

const widget = (stored) => {
    page = {};
    for (const k of Object.keys(storage)) delete storage[k];
    if (stored !== undefined) storage['topdevices.connected.recent'] = stored;
    return new ConnectedDevices({});
};

test('a refresh while a read is under way starts no second read', async () => {
    const w = widget();
    let calls = 0, release;
    // the first read waits for release(); any later one answers at once
    w.ajaxCall = () => { calls++; return calls === 1 ? new Promise((r) => { release = r; }) : Promise.resolve(answer()); };
    const first = w.refresh();
    const second = w.refresh();
    release(answer());
    await Promise.all([first, second]);
    assert.equal(calls, 1);
    await w.refresh();
    assert.equal(calls, 2);
});

test('a failed answer empties the list and the summary', async () => {
    const w = widget();
    w.ajaxCall = async () => answer();
    await w.refresh();
    w.ajaxCall = async () => ({ status: 'failed', message: 'could not read the host list' });
    await w.refresh();
    assert.deepEqual([page['.cd-body'].html, page['.cd-summary'].text], ['', '']);
    assert.ok(page['.cd-msg'].html.includes('Cannot read the host list.'));
});

test('the section opens closed, and a toggle is saved and outlives a refresh', async () => {
    const w = widget();
    w.ajaxCall = async () => answer();
    await w.refresh();
    assert.ok(page['.cd-body'].html.includes('display:none'));
    w._toggleRecent();
    assert.equal(storage['topdevices.connected.recent'], '1');
    assert.equal(page['.cd-recent'].css.display, '');
    await w.refresh();
    assert.ok(!page['.cd-body'].html.includes('display:none'));
    w._toggleRecent();
    assert.deepEqual([storage['topdevices.connected.recent'], page['.cd-recent'].css.display], ['0', 'none']);
    assert.ok(widget('1')._recentOpen && !widget('0')._recentOpen && !widget()._recentOpen);
});

test('unknown zone falls back to the browser', async () => {
    const w = widget();
    w.ajaxCall = async () => answer({ zone: 'Not/AZone' });
    await w.refresh();
    const odd = page['.cd-body'].html;
    w.ajaxCall = async () => answer({ zone: undefined });
    await w.refresh();
    assert.equal(odd, page['.cd-body'].html);
});

test('section state survives an empty list', async () => {
    const w = widget('1');
    w.ajaxCall = async () => answer({ recent: [] });
    await w.refresh();
    assert.ok(!page['.cd-body'].html.includes('cd-recent-head'));
    w.ajaxCall = async () => answer();
    await w.refresh();
    assert.ok(page['.cd-body'].html.includes('aria-expanded="true"') && !page['.cd-body'].html.includes('display:none'));
});
