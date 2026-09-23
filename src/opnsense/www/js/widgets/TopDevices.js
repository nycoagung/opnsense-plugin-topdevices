/*
 * TopDevices.js - OPNsense dashboard widget
 *
 * Top local devices by traffic, split into download/upload, from the built-in
 * NetFlow/Insight aggregator. Preset and custom date ranges, filtering by
 * network / hostname / IP, sortable columns, per-device drill-down and charts.
 *
 * NOTHING IS HARDCODED: local networks are derived from the firewall's own
 * interface config; hostnames come from DHCP leases; row count, default range,
 * default chart and refresh interval are widget options. View selections are
 * remembered in localStorage.
 *
 * WHY THE DETAIL EXPORT RATHER THAN THE `top` ENDPOINT:
 * `top` returns one scalar per address and has no notion of direction, so it
 * cannot produce a download/upload split. It also ignores filter arguments
 * entirely (eight syntaxes tested, all byte-identical). The detail export is
 * the only source that carries direction, so it backs the table, the chart and
 * the drill-down alike - fetched once per range and cached, which is why the
 * default refresh is deliberately slow.
 *
 * ATTRIBUTION: totals are keyed on dst_addr. NetFlow records each flow once per
 * interface it crosses, with src/dst swapped between observations, so matching
 * "device is src OR dst" double-counts every byte and makes the direction split
 * meaningless (both halves come out identical). Keying on destination counts
 * each flow once; verified to within 0.1% against the `top` leaderboard.
 *
 * Figures are TOTAL traffic - internal plus internet. An NVR pulling camera
 * streams will dominate with traffic that never reaches the WAN.
 *
 * LIVE: the "Live" range shows current rates instead, streamed once per
 * interval by the plugin's own sampler (scripts/topdevices/live.py) through
 * /api/topdevices/live/stream/{interval}. The sampler reads the pf state
 * table; NetFlow is not involved. Design and measurements:
 * docs/superpowers/specs/2026-09-23-live-traffic-design.md
 */

/* ---------- live view: pure helpers (tests/live_view.test.mjs) ---------- */

export const LIVE_WINDOW_S = 3;       // rows and chart average this many seconds
export const LIVE_LINGER_MS = 10000;  // a device that goes quiet stays listed this long
export const LIVE_RETRY_MS = 30000;   // an unavailable stream retries by itself this often
export const LIVE_LINE_S = 60;        // the line graph shows this many seconds

// Tableau Classic 10, the palette core's Traffic Graph draws its lines in.
const LINE_COLOURS = ['#1F77B4', '#FF7F0E', '#2CA02C', '#D62728', '#9467BD',
                      '#8C564B', '#E377C2', '#7F7F7F', '#BCBD22', '#17BECF'];

// The Devices picker. bootstrap-select copies the <select>'s classes onto the
// wrapper <div> it builds around it, so '.td-livepick' alone matches both: an
// empty() then deletes the dropdown, and a delegated change handler also runs
// for the wrapper, whose val() is empty. Always address the <select> itself.
const PICKER = 'select.td-livepick';

// Fold one sampler event into the view: the events covering the last
// LIVE_WINDOW_S seconds, and when each device last moved traffic, per scope.
// A baseline event (dt 0) carries no rates and changes nothing.
export function mergeLive(view, event, nowMs) {
    const v = view
        ? { events: view.events.slice(), seen: { all: { ...view.seen.all }, inet: { ...view.seen.inet } } }
        : { events: [], seen: { all: {}, inet: {} } };
    if (!event || !(event.dt > 0)) return v;
    const devices = event.devices || {};
    v.events.push({ dt: event.dt, devices: devices });
    let covered = 0, keep = 0;
    for (let i = v.events.length - 1; i >= 0; i--) {
        keep++;
        covered += v.events[i].dt;
        if (covered >= LIVE_WINDOW_S) break;
    }
    v.events = v.events.slice(v.events.length - keep);
    for (const [ip, d] of Object.entries(devices)) {
        for (const scope of ['all', 'inet']) {
            const r = d[scope] || [0, 0];
            if (r[0] + r[1] > 0) v.seen[scope][ip] = nowMs;
        }
    }
    for (const scope of ['all', 'inet']) {
        for (const ip of Object.keys(v.seen[scope])) {
            if (nowMs - v.seen[scope][ip] > LIVE_LINGER_MS) delete v.seen[scope][ip];
        }
    }
    return v;
}

// Time-weighted average per device over the window: sum(rate * dt) / sum(dt).
// A device missing from an event moved nothing during it, so a lingering but
// idle device comes out at 0.
export function liveRates(view, scope) {
    const key = scope === 'inet' ? 'inet' : 'all';
    const span = view.events.reduce((s, e) => s + e.dt, 0);
    const out = {};
    for (const ip of Object.keys(view.seen[key])) {
        let down = 0, up = 0;
        for (const e of view.events) {
            const r = e.devices[ip] && e.devices[ip][key];
            if (r) { down += r[0] * e.dt; up += r[1] * e.dt; }
        }
        out[ip] = span > 0 ? { down: down / span, up: up / span } : { down: 0, up: 0 };
    }
    return out;
}

// The same averaging for one device's peers and ports. In the internet scope
// only internet peers are listed; ports come from the scope's own list.
export function liveDetail(view, ip, scope) {
    const key = scope === 'inet' ? 'inet' : 'all';
    const span = view.events.reduce((s, e) => s + e.dt, 0) || 1;
    const peers = {}, ports = {};
    for (const e of view.events) {
        const d = e.devices[ip];
        if (!d) continue;
        for (const [peer, down, up, inet] of d.peers || []) {
            if (key === 'inet' && !inet) continue;
            const p = peers[peer] || (peers[peer] = { down: 0, up: 0 });
            p.down += down * e.dt; p.up += up * e.dt;
        }
        for (const [port, down, up] of (d.ports && d.ports[key]) || []) {
            const p = ports[port] || (ports[port] = { down: 0, up: 0 });
            p.down += down * e.dt; p.up += up * e.dt;
        }
    }
    const rank = (o) => Object.entries(o)
        .map(([k, v]) => ({ key: k, down: v.down / span, up: v.up / span }))
        .sort((a, b) => (b.down + b.up) - (a.down + a.up));
    return { peers: rank(peers), ports: rank(ports) };
}

// Connection state from the age of the last event, scaled to the interval the
// sampler really uses: EventSource never surfaces the sampler's ': keepalive'
// comments, so fixed timeouts would call a throttled or 5 s stream dead.
export function liveStatusFor(ageMs, effectiveS) {
    const eff = Math.max(1, effectiveS || 1) * 1000;
    if (ageMs > Math.max(20000, 6 * eff)) return 'unavailable';
    if (ageMs > Math.max(6000, 3 * eff)) return 'reconnecting';
    return 'live';
}

// With nothing picked, the Live table keeps its rows where they are: each update
// only the busiest device moves, to the top - entering if it was not listed, the
// bottom row then dropping off - and a short list is topped up in `ranked` order.
// `ranked` holds every candidate, busiest first; `prev` is null to seed afresh.
// `hold` (the pointer is over the table) keeps every row still.
export function dynamicOrder(prev, ranked, n, hold = false) {
    const ips = ranked.map(r => r.ip);
    if (!prev) return ips.slice(0, n);
    let order = prev.filter(ip => ips.includes(ip));
    const top = ranked[0];
    if (!hold && top && top.total > 0 && order[0] !== top.ip) {
        order = [top.ip].concat(order.filter(ip => ip !== top.ip));
    }
    for (const ip of ips) {
        if (order.length >= n) break;
        if (!order.includes(ip)) order.push(ip);
    }
    return order.slice(0, n);
}

// The top of a chart axis: v rounded up to a round figure - 1, 1.2, 1.5, 2, 2.5,
// 3, 4, 5, 6 or 8 times a power of ten.
export function niceCeil(v) {
    if (!(v > 0)) return 0;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const s of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8]) {
        if (v <= s * p * (1 + 1e-12)) return Math.round(s * p);
    }
    return Math.round(10 * p);
}

// Picks, from the picker or back from localStorage: keep IPv4 addresses only,
// each once, in the order given. The live stream reports nothing else.
export function cleanPick(value) {
    if (!Array.isArray(value)) return [];
    const out = [];
    for (const v of value) {
        if (typeof v === 'string' && /^\d{1,3}(\.\d{1,3}){3}$/.test(v) && !out.includes(v)) out.push(v);
    }
    return out;
}

// A to Z by what the table shows (the name, else the IP), numbers compared as numbers.
const byLabel = (a, b) => String(a.name || a.ip).localeCompare(String(b.name || b.ip), undefined,
                                                               { numeric: true, sensitivity: 'base' });

// Which rows the Live table can list, in order. With devices picked: exactly
// those, idle ones included, A to Z - a fixed set, so the table keeps its size.
// With none: every candidate, busiest first, then the most recently seen (`seen`:
// ip -> ms), then A to Z; dynamicOrder() then decides what actually moves. Live
// never uses the column sort; the network filter and search apply either way.
export function liveRows(candidates, { picked = false, network = '', search = '', seen = {} } = {}) {
    const rows = candidates.filter(r => (!network || r.net === network) && (!search
        || r.ip.toLowerCase().includes(search) || (r.name && r.name.toLowerCase().includes(search))));
    if (picked) return rows.sort(byLabel);
    return rows.sort((a, b) => (b.total - a.total) || ((seen[b.ip] || 0) - (seen[a.ip] || 0)) || byLabel(a, b));
}

// Rows with the same key render the same cells, so the table can be updated in
// place; any other change rebuilds it.
export function liveRowsKey(rows) {
    return rows.map(r => `${r.ip}|${r.name}|${r.net}`).join('\n');
}

export function fmtRate(bps) {
    if (!bps || bps < 1) return '0 b/s';
    const units = ['b/s', 'kb/s', 'Mb/s', 'Gb/s'];
    let i = 0, n = bps;
    while (n >= 1000 && i < units.length - 1) { n /= 1000; i++; }
    return `${n.toFixed(i > 0 && n < 100 ? 1 : 0)} ${units[i]}`;
}

export default class TopDevices extends BaseWidget {

    constructor(config) {
        super(config);
        this.configurable = true;
        this.STORE = 'opnsense.topdevices.view';

        this.state = {
            range: '24h', chart: 'pie', liveChart: 'line', network: '', search: '',
            sortKey: 'total', sortDir: 'desc', rowsN: 0, detailN: 10, scope: 'all',
            customFrom: '', customTo: '', livePick: [],
            rows: [], window: null, selected: null
        };

        this.networks = [];
        this.ifaceNames = {};
        this.wanDevs = [];       // device names of the upstream interface(s)
        this.names = {};
        this.cache = {};          // cacheKey -> parsed export rows
        this.ptrCache = {};       // peer ip -> reverse-DNS name ('' = none)
        this.chartObj = null;
        this.loading = false;
        this.live = {
            interval: 1, view: null, last: null, lastAt: 0, retryAt: 0, status: 'off', token: 0,
            watchdog: null, hover: false, order: [], rowsShown: -1, ptrPending: new Set(), closed: false,
            known: new Set(),      // every device address this Live session has seen, for the picker
            picker: false,         // the Devices picker is up (bootstrap-select was available)
            rowsKey: null,         // liveRowsKey() of the rows on screen; null forces a rebuild
            dynOrder: null,        // with nothing picked: the listed IPs, in order (dynamicOrder)
            yMax: 0,               // the Live chart's axis top; only grows until a view change
            lastSeen: { all: {}, inet: {} },     // ip -> ms of the last traffic, per scope
            series: { all: {}, inet: {} },       // ip -> line graph points, per scope
            lineSets: {},          // ip -> line graph dataset, reused between updates
            colours: {}            // ip -> the line colour it last had
        };
    }

    getGridOptions() { return { sizeToContent: 1000 }; }

    async getWidgetOptions() {
        return {
            rowsToShow: {
                id: 'rowsToShow', title: 'Devices to show', type: 'select',
                options: ['10', '20', '50', '100'].map(v => ({ value: v, label: v })),
                default: '20', required: true
            },
            defaultRange: {
                id: 'defaultRange', title: 'Default range', type: 'select',
                options: this._ranges().map(r => ({ value: r.key, label: r.label })),
                default: '24h', required: true
            },
            defaultChart: {
                id: 'defaultChart', title: 'Default chart', type: 'select',
                options: [{ value: 'pie', label: 'Pie' }, { value: 'bar', label: 'Bar' }, { value: 'none', label: 'Off' }],
                default: 'pie', required: true
            },
            refreshInterval: {
                id: 'refreshInterval', title: 'Refresh interval', type: 'select',
                options: [
                    { value: '300', label: '5 minutes' },
                    { value: '900', label: '15 minutes' },
                    { value: '1800', label: '30 minutes' },
                    { value: '3600', label: '1 hour' }
                ],
                default: '900', required: true
            },
            liveInterval: {
                id: 'liveInterval', title: 'Live update interval', type: 'select',
                options: [
                    { value: '1', label: '1 second' },
                    { value: '2', label: '2 seconds' },
                    { value: '5', label: '5 seconds' }
                ],
                default: '1', required: true
            }
        };
    }

    /* ---------- view state persistence ---------- */

    _saveView() {
        try {
            const s = this.state;
            localStorage.setItem(this.STORE, JSON.stringify({
                range: s.range, chart: s.chart, liveChart: s.liveChart, network: s.network, search: s.search,
                sortKey: s.sortKey, sortDir: s.sortDir, rowsN: s.rowsN, detailN: s.detailN, scope: s.scope,
                customFrom: s.customFrom, customTo: s.customTo, livePick: s.livePick
            }));
        } catch (e) { /* private mode / storage disabled - not fatal */ }
    }

    _loadView() {
        try {
            const raw = localStorage.getItem(this.STORE);
            if (!raw) return null;
            const v = JSON.parse(raw);
            return (v && typeof v === 'object') ? v : null;
        } catch (e) { return null; }
    }

    /* ---------- ranges ---------- */

    _ranges() {
        return [
            { key: 'live',      label: 'Live' },
            { key: '1h',        label: 'Last hour' },
            { key: '24h',       label: 'Last 24 hours' },
            { key: 'today',     label: 'Today' },
            { key: 'yesterday', label: 'Yesterday' },
            { key: '7d',        label: 'Last 7 days' },
            { key: 'custom',    label: 'Custom range' }
        ];
    }

    _window(key) {
        const now = new Date();
        const mid = Math.floor(new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000);
        const s = Math.floor(now.getTime() / 1000);
        const DAY = 86400;
        switch (key) {
            case '1h':        return [s - 3600, s];
            case 'today':     return [mid, s];
            case 'yesterday': return [mid - DAY, mid];
            case '7d':        return [s - (7 * DAY), s];
            case 'custom': {
                const f = this._localToEpoch(this.state.customFrom);
                const t = this._localToEpoch(this.state.customTo);
                if (f && t && t > f) return [f, t];
                return [s - DAY, s];
            }
            case '24h':
            default:          return [s - DAY, s];
        }
    }

    _localToEpoch(v) {
        if (!v) return null;
        const d = new Date(v);                       // datetime-local is parsed as local time
        return isNaN(d.getTime()) ? null : Math.floor(d.getTime() / 1000);
    }

    _epochToLocal(ts) {
        const d = new Date(ts * 1000);
        const p = (x) => String(x).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    // OPNsense/Unix `date` format, e.g. "Tue Sep 22 12:08:17 AEST 2026"
    _dateStr(ts) {
        const d = new Date(ts * 1000);
        const p = (x) => String(x).padStart(2, '0');
        const dow = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()];
        const mon = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                     'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()];
        return `${dow} ${mon} ${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())} `
             + `${this._tzAbbr(d)} ${d.getFullYear()}`;
    }

    // "Australian Eastern Standard Time" -> "AEST". Browsers differ here, so try
    // the long name first, then Intl's short name, then the raw GMT offset.
    _tzAbbr(d) {
        const m = String(d.toTimeString()).match(/\(([^)]+)\)/);
        if (m && m[1]) {
            const words = m[1].split(/[\s-]+/).filter(w => /^[A-Za-z]/.test(w));
            if (words.length > 1) return words.map(w => w[0].toUpperCase()).join('');
            if (words.length === 1) return words[0];
        }
        try {
            const parts = new Intl.DateTimeFormat(undefined, { timeZoneName: 'short' }).formatToParts(d);
            const tz = parts.find(p => p.type === 'timeZoneName');
            if (tz) return tz.value;
        } catch (e) { /* fall through */ }
        const off = -d.getTimezoneOffset();
        const sg = off >= 0 ? '+' : '-';
        return `GMT${sg}${String(Math.floor(Math.abs(off) / 60)).padStart(2, '0')}`
             + String(Math.abs(off) % 60).padStart(2, '0');
    }

    /* ---------- helpers ---------- */

    _fmt(n) {
        if (!n || n < 1) return '-';
        const u = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return `${n.toFixed(1)} ${u[i]}`;
    }

    _esc(s) { return $('<div>').text(s === null || s === undefined ? '' : String(s)).html(); }

    _ip2int(ip) {
        const p = String(ip).split('.');
        if (p.length !== 4) return null;
        let v = 0;
        for (const o of p) {
            const n = parseInt(o, 10);
            if (isNaN(n) || n < 0 || n > 255) return null;
            v = (v * 256) + n;
        }
        return v;
    }

    _inNet(ip, e) { const v = this._ip2int(ip); return v !== null && (v & e.mask) === e.net; }
    _isLocal(ip)  { return this.networks.some(n => this._inNet(ip, n)); }
    // x.x.x.255 and friends carry broadcast/multicast chatter, not device traffic
    _isBroadcast(ip) {
        const v = this._ip2int(ip);
        return v !== null && this.networks.some(n => (v | 0) === n.bcast);
    }
    _netOf(ip)    { const h = this.networks.find(n => this._inNet(ip, n)); return h ? h.key : ''; }

    // A flow is recorded once per interface it crosses. Counting only the rows
    // seen on the upstream interface therefore yields internet traffic alone -
    // anything that never leaves the LAN (an NVR pulling camera streams, a NAS
    // copy) is simply absent from those rows. At the WAN both directions are
    // still keyed on dst_addr, so the down/up split is unchanged.
    _inScope(r) {
        if (this.state.scope !== 'wan') return true;
        return this.wanDevs.indexOf(r.iface) !== -1;
    }

    /* ---------- data ---------- */

    async _loadNetworks() {
        const rfc1918 = [
            { net: this._ip2int('10.0.0.0'),    mask: (0xff000000 | 0) },
            { net: this._ip2int('172.16.0.0'),  mask: (0xfff00000 | 0) },
            { net: this._ip2int('192.168.0.0'), mask: (0xffff0000 | 0) }
        ];
        const isPrivate = (v) => rfc1918.some(r => (v & r.mask) === (r.net & r.mask));
        // loopback and link-local are not upstream, and must not be mistaken for it
        const isSpecial = (v) => ((v & (0xff000000 | 0)) === (this._ip2int('127.0.0.0') & (0xff000000 | 0)))
                              || ((v & (0xffff0000 | 0)) === (this._ip2int('169.254.0.0') & (0xffff0000 | 0)));
        const ifaceNames = {};
        const wan = [];
        let data = [];
        try { data = await this.ajaxCall('/api/interfaces/overview/export'); } catch (e) { data = []; }
        const items = Array.isArray(data) ? data : (data.rows || []);
        const out = [];
        items.forEach((i) => {
            if (!i || typeof i !== 'object' || !i.identifier || !i.addr4) return;
            const c = String(i.addr4).split('/');
            const addr = this._ip2int(c[0]);
            const bits = parseInt(c[1], 10);
            if (addr === null || isNaN(bits) || bits < 8 || bits > 32) return;
            const mask = bits === 0 ? 0 : ((0xffffffff << (32 - bits)) | 0);
            const net = (addr & mask) | 0;
            if (!isPrivate(net)) {
                // anything not RFC1918 and not loopback/link-local is upstream:
                // its device name is what identifies WAN traffic in the export
                if (!isSpecial(net) && i.device) wan.push(i.device);
                return;
            }
            const label = i.description || i.identifier;
            out.push({ key: i.identifier, label: label, net: net, mask: mask,
                       bcast: (net | (~mask)) | 0 });
            // the firewall's own address on this segment is a real endpoint (DNS,
            // DHCP) but it is not one of the user's devices - label it as such
            ifaceNames[c[0]] = `${label} gateway`;
        });
        this.networks = out;
        this.ifaceNames = ifaceNames;
        this.wanDevs = wan;
    }

    // Names come from two sources. DHCP leases cover devices that are only ever
    // dynamic; static host records cover everything with a fixed address, which
    // never appears in the lease table at all (servers, the firewall itself).
    // Host records win where both exist: they are the curated name.
    async _loadNames() {
        const map = {};
        try {
            const r = await this.ajaxCall('/api/dnsmasq/leases/search', JSON.stringify({ rowCount: 1000 }), 'POST');
            // Dnsmasq writes '*' for a client that sent no hostname
            ((r && r.rows) || []).forEach(l => { if (l.address && l.hostname && l.hostname !== '*') map[l.address] = l.hostname; });
        } catch (e) { /* leases unavailable - fall back to host records alone */ }
        try {
            const r = await this.ajaxCall('/api/dnsmasq/settings/searchHost', JSON.stringify({ rowCount: 1000 }), 'POST');
            ((r && r.rows) || []).forEach(h => { if (h.ip && h.host) map[h.ip] = h.host; });
        } catch (e) { /* host records unavailable - leases alone still work */ }
        this.names = map;
    }

    async _export(from, to) {
        const key = `${from}-${to}`;
        if (this.cache[key]) return this.cache[key];
        const url = `/api/diagnostics/networkinsight/export/FlowSourceAddrDetails/${from}/${to}/86400/src_addr/octets`;
        const text = await new Promise((resolve, reject) => {
            $.ajax({ url: url, dataType: 'text', timeout: 180000 })
                .done(resolve).fail(() => reject(new Error('export failed')));
        });
        const lines = text.split('\n');
        const head = (lines.shift() || '').split(',').map(h => h.trim());
        const ix = {};
        head.forEach((h, i) => { ix[h] = i; });
        const rows = [];
        lines.forEach((line) => {
            if (!line) return;
            const c = line.split(',');
            rows.push({
                src: c[ix.src_addr], dst: c[ix.dst_addr],
                port: c[ix.service_port], dir: c[ix.direction],
                iface: c[ix['if']],
                octets: parseFloat(c[ix.octets]) || 0
            });
        });
        this.cache = {};                 // keep only the current range
        this.cache[key] = rows;
        return rows;
    }

    async _load() {
        const [from, to] = this._window(this.state.range);
        const flows = await this._export(from, to);
        const acc = {};
        flows.forEach((r) => {
            const ip = r.dst;            // dst-keyed: see ATTRIBUTION above
            if (!ip || !this._isLocal(ip) || this._isBroadcast(ip)) return;
            if (!this._inScope(r)) return;
            if (!acc[ip]) acc[ip] = { ip: ip, down: 0, up: 0 };
            if (r.dir === 'out') acc[ip].up += r.octets; else acc[ip].down += r.octets;
        });
        this.state.rows = Object.values(acc).map(d => ({
            ip: d.ip, name: this.names[d.ip] || (this.ifaceNames || {})[d.ip] || '', net: this._netOf(d.ip),
            down: d.down, up: d.up, total: d.down + d.up
        }));
        this.state.window = [from, to];
        this._scopeShown = this.state.scope;
    }

    // Reverse DNS for a peer address. The endpoint echoes the address back when
    // there is no PTR record, which is how "unresolved" is detected. Coverage is
    // genuinely partial - Cloudflare-fronted hosts publish no PTR at all - so the
    // raw IP remains the fallback rather than a guess.
    async _ptr(ip) {
        if (this.ptrCache[ip] !== undefined) return this.ptrCache[ip];
        let name = '';
        try {
            const r = await this.ajaxCall('/api/diagnostics/dns/reverse_lookup?address=' + encodeURIComponent(ip));
            const v = r && r[ip];
            if (v && v !== ip) name = String(v).replace(/\.$/, '');
        } catch (e) { /* unresolved - keep the IP */ }
        this.ptrCache[ip] = name;
        return name;
    }

    // Resolve many addresses with bounded concurrency. Promise.all over 100 peers
    // would queue behind the browser's ~6-per-host limit anyway and hammer the
    // firewall; six workers keeps it responsive without capping the result set.
    async _ptrMany(ips, limit) {
        const out = new Array(ips.length);
        let next = 0;
        const worker = async () => {
            while (next < ips.length) {
                const i = next++;
                out[i] = await this._ptr(ips[i]);
            }
        };
        await Promise.all(Array.from({ length: Math.min(limit || 6, ips.length) }, worker));
        return out;
    }

    // The dashboard grid sizes a widget to its content, but only recalculates on
    // its own events - filtering down to three rows leaves the cell at its old
    // height. gridstack exposes resizeToContent, so ask for it after a render.
    _fitHeight() {
        try {
            const item = $('.td-wrap').closest('.grid-stack-item')[0];
            if (!item) return;
            const gridEl = item.closest('.grid-stack');
            const grid = gridEl && gridEl.gridstack;
            if (grid && typeof grid.resizeToContent === 'function') grid.resizeToContent(item);
        } catch (e) { /* not on a gridstack dashboard - nothing to do */ }
    }

    _loading(on) {
        $('.td-loading').css('display', on ? 'flex' : 'none');
    }

    // Reference-counted so nested work (refresh -> render -> details) does not
    // hide the overlay early, and delayed by 150ms so operations that run off
    // the cached export - most filter changes - never flash it.
    _busy(on) {
        this._busyN = Math.max(0, (this._busyN || 0) + (on ? 1 : -1));
        if (this._busyN > 0) {
            if (!this._busyTimer) {
                this._busyTimer = setTimeout(() => { this._busyTimer = null; this._loading(true); }, 150);
            }
        } else {
            clearTimeout(this._busyTimer);
            this._busyTimer = null;
            this._loading(false);
        }
    }

    // The widget header is rendered by the dashboard, not by us, so the refresh
    // control is injected beside the header's link icon. No-ops if absent.
    _installRefreshButton() {
        const $item = $('.td-wrap').closest('.grid-stack-item');
        if (!$item.length || $item.find('.td-refresh').length) return;
        const $btn = $('<a href="#" class="td-refresh" title="Refresh"><i class="fa fa-refresh"></i></a>')
            .css({ marginRight: '8px', cursor: 'pointer' });
        const $link = $item.find('a[href*="networkinsight"]').first();
        if ($link.length) $link.before($btn);
        else $item.find('.widget-header, .panel-heading, .card-header').first().append($btn);
    }

    /* ---------- markup ---------- */

    getMarkup() {
        const ranges = this._ranges().map(r => `<option value="${r.key}">${this._esc(r.label)}</option>`).join('');
        // explicit widths: `width:auto` on a select inside a flex row collapses
        // and clips the label in the OPNsense theme
        const selCss = 'height:30px;padding:3px 24px 3px 8px;font-size:12px;'
                     + 'border:1px solid #ccc;border-radius:3px;background-color:#fff;flex:0 0 auto;';
        return $(`
        <div class="td-wrap" style="position:relative;">
            <div class="td-loading" style="display:none;position:absolute;top:0;left:0;right:0;bottom:0;
                 background:rgba(127,127,127,0.12);z-index:20;align-items:center;justify-content:center;">
                <i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.7;"></i>
            </div>
            <div class="td-controls" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;justify-content:center;margin-bottom:6px;">
                <select class="td-range"   style="${selCss}width:140px;">${ranges}</select>
                <select class="td-network" style="${selCss}width:140px;"></select>
                <span class="td-pickwrap" style="display:none;flex:0 0 auto;" title="Devices to show in Live">
                    <select class="td-livepick" multiple title="Busiest devices" data-width="170px"
                            data-style="btn-default btn-sm" data-container="body" data-live-search="true"
                            data-actions-box="true" data-size="12" data-selected-text-format="count > 1"
                            data-count-selected-text="{0} devices"></select>
                </span>
                <select class="td-scope" style="${selCss}width:130px;" title="Traffic scope">
                    <option value="all">All traffic</option>
                    <option value="wan">Internet only</option>
                </select>
                <input type="text" class="form-control input-sm td-search" placeholder="Filter name or IP"
                       style="height:30px;font-size:12px;flex:0 1 320px;min-width:110px;"/>
                <select class="td-rows" style="${selCss}width:96px;" title="Rows to show">
                    <option value="10">10 rows</option>
                    <option value="20">20 rows</option>
                    <option value="50">50 rows</option>
                    <option value="100">100 rows</option>
                </select>
                <div class="btn-group btn-group-sm td-chartbtns" style="flex:0 0 auto;">
                    <button type="button" class="btn btn-default" data-chart="line" style="display:none;">Line</button>
                    <button type="button" class="btn btn-default" data-chart="pie">Pie</button>
                    <button type="button" class="btn btn-default" data-chart="bar">Bar</button>
                    <button type="button" class="btn btn-default" data-chart="none">Off</button>
                </div>
            </div>
            <div class="td-custom" style="display:none;gap:6px;flex-wrap:wrap;align-items:center;justify-content:center;margin-bottom:6px;">
                <input type="datetime-local" step="1" class="form-control input-sm td-from"
                       style="height:30px;font-size:12px;width:210px;"/>
                <span class="text-muted">&rarr;</span>
                <input type="datetime-local" step="1" class="form-control input-sm td-to"
                       style="height:30px;font-size:12px;width:210px;"/>
                <button type="button" class="btn btn-primary btn-sm td-apply">Apply</button>
            </div>
            <div class="td-window" style="margin-bottom:4px;"><small class="text-muted"></small></div>
            <div class="td-chartbox" style="height:175px;margin:4px 0;"><canvas class="td-canvas"></canvas></div>
            <div class="td-main" style="display:flex;gap:12px;align-items:flex-start;">
                <div class="td-tablewrap" style="flex:1 1 0;min-width:0;max-height:420px;overflow-y:auto;">
                    <table class="table table-condensed table-hover" style="margin-bottom:4px;table-layout:fixed;width:100%;">
                        <thead style="position:sticky;top:0;z-index:2;background:inherit;box-shadow:inset 0 -1px 0 #ddd;"><tr>
                            <th class="td-sort" data-key="name"  style="cursor:pointer;text-align:left;width:38%;">Device</th>
                            <th class="td-sort" data-key="net"   style="cursor:pointer;text-align:left;width:16%;">Network</th>
                            <th class="td-sort" data-key="down"  style="cursor:pointer;text-align:right;width:15%;">Down</th>
                            <th class="td-sort" data-key="up"    style="cursor:pointer;text-align:right;width:15%;">Up</th>
                            <th class="td-sort" data-key="total" style="cursor:pointer;text-align:right;width:16%;">Total</th>
                        </tr></thead>
                        <tbody class="td-body"></tbody>
                    </table>
                </div>
                <div class="td-details" style="flex:1 1 0;min-width:0;position:sticky;top:0;"></div>
            </div>
        </div>`);
    }

    async onMarkupRendered() {
        const cfg = await this.getWidgetConfig() || {};
        this.state.range = cfg.defaultRange || '24h';
        this.state.chart = cfg.defaultChart || 'pie';
        this.tickTimeout = parseInt(cfg.refreshInterval, 10) || 900;

        const saved = this._loadView();
        if (saved) Object.assign(this.state, saved);
        this.state.livePick = cleanPick(this.state.livePick);
        if (!['line', 'bar', 'pie', 'none'].includes(this.state.liveChart)) this.state.liveChart = 'line';

        await this._loadNetworks();
        this._fillNetworkSelect();

        if (!this.state.rowsN) this.state.rowsN = parseInt(cfg.rowsToShow, 10) || 20;
        $('.td-rows').val(String(this.state.rowsN));
        $('.td-scope').val(this.state.scope || 'all');
        $('.td-range').val(this.state.range);
        $('.td-search').val(this.state.search);
        $('.td-network').val(this.state.network);
        const [f, t] = this._window(this.state.range);
        $('.td-from').val(this.state.customFrom || this._epochToLocal(f));
        $('.td-to').val(this.state.customTo || this._epochToLocal(t));
        $('.td-custom').css('display', this.state.range === 'custom' ? 'flex' : 'none');

        this._bind();
        this._initPicker();
        this._applyLayout();
        this._installRefreshButton();
        await this.refresh();
    }

    _fillNetworkSelect() {
        const $s = $('.td-network');
        $s.empty().append('<option value="">All networks</option>');
        this.networks.forEach(n => $s.append(`<option value="${this._esc(n.key)}">${this._esc(n.label)}</option>`));
        $s.val(this.state.network);
    }

    _bind() {
        const self = this;
        $(document).off('.topdevices');

        $(document).on('change.topdevices', '.td-range', function () {
            const was = self.state.range;
            self.state.range = $(this).val();
            self.state.selected = null;
            $('.td-custom').css('display', self.state.range === 'custom' ? 'flex' : 'none');
            self._saveView();
            // the toolbar follows the range at once, not only once its data has loaded
            self._renderChrome();
            if (self.state.range === 'live') { self._startLive(); return; }
            if (was === 'live') {
                // leave nothing of the live view behind for the NetFlow render
                self._stopLive();
                self.state.rows = [];
                self.state.window = null;
                $('.td-window small').text('');
            }
            if (self.state.range !== 'custom') self.refresh();
            else self.render();
        });
        $(document).on('mouseenter.topdevices', '.td-tablewrap', function () { self.live.hover = true; });
        $(document).on('mouseleave.topdevices', '.td-tablewrap', function () { self.live.hover = false; });
        $(document).on('click.topdevices', '.td-live-retry', function (e) {
            e.preventDefault();
            self._startLive();
        });
        $(document).on('click.topdevices', '.td-apply', function () {
            self.state.customFrom = $('.td-from').val();
            self.state.customTo = $('.td-to').val();
            self.state.selected = null;
            self._saveView();
            self.refresh();
        });
        $(document).on('click.topdevices', '.td-refresh', function (e) {
            e.preventDefault();
            self.refresh(true);
        });
        $(document).on('change.topdevices', '.td-scope', async function () {
            self.state.scope = $(this).val();
            self._saveView();
            self._liveViewChanged();
            // must await: render() drops a selection the new scope excludes, and
            // reading state.selected before that lands re-renders the stale device
            await self.render();                 // export is cached; only the filter changed
            if (self.state.selected) await self.renderDetails(self.state.selected);
        });
        $(document).on('change.topdevices', '.td-network', function () {
            self.state.network = $(this).val(); self._saveView(); self._liveViewChanged(); self.render();
        });
        $(document).on('input.topdevices', '.td-search', function () {
            self.state.search = $(this).val().toLowerCase().trim(); self._saveView(); self._liveViewChanged();
            self.render();
        });
        $(document).on('change.topdevices', '.td-detailrows', function () {
            self.state.detailN = parseInt($(this).val(), 10) || 10;
            self._saveView();
            if (self.state.selected) self.renderDetails(self.state.selected);
        });
        $(document).on('change.topdevices', '.td-rows', function () {
            self.state.rowsN = parseInt($(this).val(), 10) || 20;
            self._saveView(); self.render();
        });
        $(document).on('click.topdevices', '.td-chartbtns button', function () {
            if (self._setChart($(this).data('chart'))) { self._saveView(); self.render(); }
        });
        $(document).on('click.topdevices', '.td-sort', function () {
            if (self._sortBy($(this).data('key'))) self.render();
        });
        // bootstrap-select fires change for ticks and for select all / none alike
        $(document).on('change.topdevices', PICKER, function () {
            self.state.livePick = cleanPick($(this).val() || []);
            self._saveView();
            self._liveViewChanged();
            self.render();
        });
        // list what is known right now, just before the menu opens - never under the pointer
        $(document).on('show.bs.select.topdevices', PICKER, function () { self._fillPicker(); });
        $(document).on('click.topdevices', '.td-body tr', async function () {
            const ip = $(this).data('ip');
            self.state.selected = (self.state.selected === ip) ? null : ip;
            await self.render();
            if (self.state.selected) await self.renderDetails(self.state.selected);
        });
    }

    async refresh(force) {
        // Live restarts its stream: the header refresh button and an options change land here
        if (this.state.range === 'live') { await this._startLive(); return; }
        if (this.loading) return;
        this.loading = true;
        this._busy(true);
        if (force) this.cache = {};          // drop the cached export on an explicit refresh
        try {
            await this._loadNames();
            await this._load();
            await this.render();
            if (this.state.selected) await this.renderDetails(this.state.selected);
        } catch (e) {
            $('.td-body').html('<tr><td colspan="5" class="text-danger">Unable to read NetFlow data</td></tr>');
            $('.td-window small').text('');
        } finally { this.loading = false; this._busy(false); }
    }

    async onWidgetTick() {
        if (this.state.range === 'live') return;      // the stream refreshes itself
        await this.refresh();
    }

    async onWidgetOptionsChanged() { await this.onMarkupRendered(); }

    /* ---------- render ---------- */

    _visibleRows() {
        const s = this.state;
        let rows = s.rows.slice();
        if (s.network) rows = rows.filter(r => r.net === s.network);
        if (s.search) {
            rows = rows.filter(r => r.ip.toLowerCase().indexOf(s.search) !== -1 ||
                                    (r.name && r.name.toLowerCase().indexOf(s.search) !== -1));
        }
        const dir = s.sortDir === 'asc' ? 1 : -1;
        rows.sort((a, b) => {
            if (s.sortKey === 'name') return String(a.name || a.ip).localeCompare(String(b.name || b.ip)) * dir;
            if (s.sortKey === 'net')  return String(a.net).localeCompare(String(b.net)) * dir;
            return (a[s.sortKey] - b[s.sortKey]) * dir;
        });
        return rows;
    }

    async render() {
        this._busy(true);
        try { await this._render(); } finally { this._busy(false); }
    }

    async _render() {
        if (this.state.range === 'live') { this._renderLive(); return; }
        // _load() applies the scope filter while aggregating, so a scope change
        // needs a re-aggregate. The export itself is cached, so this is cheap.
        if (this._scopeShown !== this.state.scope && this.state.window) {
            this._scopeShown = this.state.scope;
            try { await this._load(); } catch (e) { /* keep previous rows */ }
        }
        const cfg = await this.getWidgetConfig() || {};
        const limit = this.state.rowsN || parseInt(cfg.rowsToShow, 10) || 20;
        const all = this._visibleRows();
        // A filter change can exclude the selected device. Drop the selection
        // rather than leaving its detail panel on screen next to a table that no
        // longer lists it - checked against the filtered set, not the truncated
        // page, so a device hidden only by the row limit keeps its panel.
        if (this.state.selected && !all.some(r => r.ip === this.state.selected)) {
            this.state.selected = null;
        }
        const rows = all.slice(0, limit);
        // A pie or bar of 100 devices is unreadable, so the chart always shows the
        // top 10 regardless of table length - labelled, never a silent truncation.
        const CHART_MAX = 10;

        if (this.state.window) {
            const scopeTxt = this.state.scope === 'wan'
                ? ` · internet only (via ${this.wanDevs.join(', ') || 'WAN'})`
                : ' · all traffic';
            $('.td-window small').text(
                `${this._dateStr(this.state.window[0])}  →  ${this._dateStr(this.state.window[1])}${scopeTxt}`);
        }
        this._renderChrome();
        $('.td-body').html(this._rowsHtml(rows, (v) => this._fmt(v), 'No matching devices'));
        this._renderChart(rows.slice(0, CHART_MAX));
        if (rows.length > CHART_MAX && this._chartKind() !== 'none') {
            $('.td-window small').append(
                `<span class="text-muted"> \u00b7 chart: top ${CHART_MAX} of ${rows.length}</span>`);
        }
        if (!this.state.selected) $('.td-details').empty();
        this._applyLayout();
        this._fitHeight();
    }

    _renderChrome() {
        const live = this.state.range === 'live';
        const picked = live && (this.state.livePick || []).length > 0;
        $('.td-chartbtns button').removeClass('btn-primary').addClass('btn-default');
        $(`.td-chartbtns button[data-chart="${this._chartKind()}"]`).removeClass('btn-default').addClass('btn-primary');
        $('.td-chartbtns button[data-chart="line"]').css('display', live && this._streamingOk() ? '' : 'none');
        $('.td-sort').each((i, el) => {
            $(el).find('.td-arrow').remove();
            $(el).css('cursor', live ? 'default' : 'pointer');
            if (!live && $(el).data('key') === this.state.sortKey) {
                $(el).append(`<span class="td-arrow"> ${this.state.sortDir === 'asc' ? '▲' : '▼'}</span>`);
            }
        });
        $('.td-pickwrap').css('display', live && this.live.picker ? '' : 'none');
        // every picked device is listed, so a row limit would only hide some of them
        $('.td-rows').css('display', picked ? 'none' : '');
    }

    // Column sorting belongs to the NetFlow ranges. Live keeps its own order (see
    // liveRows), so there a header click changes nothing. True when it sorted.
    _sortBy(key) {
        if (this.state.range === 'live') return false;
        const s = this.state;
        if (s.sortKey === key) s.sortDir = s.sortDir === 'asc' ? 'desc' : 'asc';
        else { s.sortKey = key; s.sortDir = (key === 'name' || key === 'net') ? 'asc' : 'desc'; }
        this._saveView();
        return true;
    }

    // One row per device; fmt formats the three figures (bytes for NetFlow
    // ranges, bits per second for Live).
    _rowsHtml(rows, fmt, empty) {
        if (rows.length === 0) return `<tr><td colspan="5" class="text-muted">${this._esc(empty)}</td></tr>`;
        return rows.map((r) => {
            const clip = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
            const label = r.name
                ? `<strong style="display:block;${clip}">${this._esc(r.name)}</strong>`
                  + `<small class="text-muted" style="display:block;${clip}">${this._esc(r.ip)}</small>`
                : `<strong style="display:block;${clip}">${this._esc(r.ip)}</strong>`;
            const net = this.networks.find(n => n.key === r.net);
            const sel = this.state.selected === r.ip ? ' class="info"' : '';
            return `<tr${sel} data-ip="${this._esc(r.ip)}" style="cursor:pointer;"
                    title="${this._esc((r.name ? r.name + ' ' : '') + r.ip)}">
                <td style="text-align:left;${clip}">${label}</td>
                <td style="text-align:left;${clip}"><small>${this._esc(net ? net.label : '')}</small></td>
                <td style="text-align:right;">${fmt(r.down)}</td>
                <td style="text-align:right;">${fmt(r.up)}</td>
                <td style="text-align:right;"><strong>${fmt(r.total)}</strong></td></tr>`;
        }).join('');
    }

    _renderChart(rows, fmt = (v) => this._fmt(v), live = false) {
        const kind = this._chartKind();
        if (live && kind === 'line' && rows.length) { this._renderLineChart(rows); return; }
        const isPie = kind === 'pie';
        const type = isPie ? 'doughnut' : 'bar';
        if (kind === 'none' || rows.length === 0) {
            if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
            $('.td-chartbox').hide();
            return;
        }
        $('.td-chartbox').show();
        const labels = rows.map(r => r.name || r.ip);
        const datasets = isPie
            ? [{ data: rows.map(r => r.total), borderWidth: 0 }]
            : [{ label: 'Down', data: rows.map(r => r.down) },
               { label: 'Up',   data: rows.map(r => r.up) }];
        // Live's bar axis holds its highest top until the view changes (see _resetLiveView)
        let yMax;
        if (live && !isPie) {
            this.live.yMax = Math.max(this.live.yMax, niceCeil(Math.max(0, ...rows.map(r => r.down + r.up))));
            yMax = this.live.yMax || undefined;
        }
        // Live redraws every interval: update the chart in place instead of
        // destroying and rebuilding it, which flickers.
        if (live && this.chartObj && this.chartObj.$live && this.chartObj.config.type === type) {
            this.chartObj.data.labels = labels;
            this.chartObj.data.datasets.forEach((ds, i) => { ds.data = datasets[i].data; });
            if (!isPie) this.chartObj.options.scales.y.max = yMax;
            this.chartObj.update('none');
            return;
        }
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
        const el = $('.td-canvas')[0];
        if (!el) return;
        this.chartObj = new Chart(el.getContext('2d'), {
            type: type,
            data: { labels: labels, datasets: datasets },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: isPie ? 'right' : 'top',
                              labels: { boxWidth: 10, font: { size: 10 } } },
                    tooltip: { callbacks: { label: (c) => `${c.dataset.label || c.label}: ${fmt(c.parsed.y ?? c.parsed)}` } }
                },
                scales: isPie ? {} : {
                    x: { stacked: true, ticks: { font: { size: 9 } } },
                    y: { stacked: true, max: yMax, ticks: { callback: (v) => fmt(v) } }
                }
            }
        });
        this.chartObj.$live = live;
    }

    // The line graph scrolls on chartjs-plugin-streaming's 'realtime' scale, which
    // the dashboard loads for core's Traffic Graph.
    _streamingOk() {
        try { return typeof Chart !== 'undefined' && !!Chart.registry.getScale('realtime'); } catch (e) { return false; }
    }

    // The chart this range draws. Live keeps its own choice, starting on the line
    // graph (the bar chart stands in without the streaming plugin); the NetFlow
    // ranges keep theirs and never draw a line graph.
    _chartKind() {
        if (this.state.range !== 'live') return this.state.chart === 'line' ? 'bar' : this.state.chart;
        const kind = this.state.liveChart || 'line';
        return kind === 'line' && !this._streamingOk() ? 'bar' : kind;
    }

    // A chart button: true when the choice was taken.
    _setChart(kind) {
        if (this.state.range === 'live') {
            if (kind === 'line' && !this._streamingOk()) return false;
            this.state.liveChart = kind;
        } else {
            if (kind === 'line') return false;
            this.state.chart = kind;
        }
        return true;
    }

    // A view change starts the Live axis top and the busiest-first list afresh:
    // entering Live or the refresh button (_startLive), another scope, network,
    // search or pick (_liveViewChanged).
    _resetLiveView() {
        this.live.yMax = 0;
        this.live.dynOrder = null;
        this.live.lineSets = {};
    }

    _liveViewChanged() {
        if (this.state.range === 'live') this._resetLiveView();
    }

    // One line graph point per device per interval, for both scopes: that
    // interval's own download (0 while idle), with its upload for the tooltip.
    // Kept for every device the table can list, a little longer than shown.
    _recordLine(e, now) {
        const l = this.live;
        const devices = e.devices || {};
        const keep = (LIVE_LINE_S + 10) * 1000;
        const ips = new Set([...Object.keys(devices), ...l.known, ...(this.state.livePick || []), ...l.order]);
        for (const scope of ['all', 'inet']) {
            for (const ip of ips) {
                const r = devices[ip] && devices[ip][scope];
                const s = l.series[scope][ip] || (l.series[scope][ip] = []);
                s.push({ x: now, y: r ? r[0] : 0, up: r ? r[1] : 0 });
                while (s.length && now - s[0].x > keep) s.shift();
            }
        }
    }

    // A line colour for each listed device: the one it had before, unless an
    // earlier listed device holds it, otherwise the first free one. At most ten
    // lines and ten colours, so no two listed devices ever share.
    _lineColours(ips) {
        const l = this.live;
        const taken = new Set(), out = {};
        for (const ip of ips) {
            const c = l.colours[ip];
            if (c && !taken.has(c)) { out[ip] = c; taken.add(c); }
        }
        for (const ip of ips) {
            if (out[ip]) continue;
            out[ip] = l.colours[ip] = LINE_COLOURS.find(c => !taken.has(c)) || LINE_COLOURS[0];
            taken.add(out[ip]);
        }
        return out;
    }

    // Live's line graph: each listed device's download over the last LIVE_LINE_S
    // seconds, filled and smoothed like core's Traffic Graph. The points come from
    // _recordLine(); the y axis only grows, like the bar chart's.
    _renderLineChart(rows) {
        const l = this.live;
        const scope = this.state.scope === 'wan' ? 'inet' : 'all';
        const colours = this._lineColours(rows.map(r => r.ip));
        const datasets = rows.map((r) => {
            const ds = l.lineSets[r.ip] || (l.lineSets[r.ip] = {
                ip: r.ip, data: l.series[scope][r.ip] || (l.series[scope][r.ip] = []),
                fill: true, cubicInterpolationMode: 'monotone', pointRadius: 0, borderWidth: 1.5
            });
            ds.label = r.name || r.ip;
            ds.borderColor = colours[r.ip];
            ds.backgroundColor = colours[r.ip] + '33';
            return ds;
        });
        const peak = Math.max(0, ...datasets.map(ds => Math.max(0, ...ds.data.map(p => p.y))));
        l.yMax = Math.max(l.yMax, niceCeil(peak));
        const top = l.yMax || undefined;
        $('.td-chartbox').show();
        if (this.chartObj && this.chartObj.$live && this.chartObj.config.type === 'line') {
            const joined = datasets.some(ds => !this.chartObj.data.datasets.includes(ds));
            this.chartObj.data.datasets = datasets;
            this.chartObj.options.scales.y.max = top;
            // chartjs-plugin-streaming's 'quiet' update patches every dataset's
            // controller first, and a line added since the last update has none
            // yet: that update must be a plain one
            this.chartObj.update(joined ? 'none' : 'quiet');
            return;
        }
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
        const el = $('.td-canvas')[0];
        if (!el) return;
        this.chartObj = new Chart(el.getContext('2d'), {
            type: 'line',
            data: { datasets: datasets },
            options: {
                responsive: true, maintainAspectRatio: false, normalized: true,
                interaction: { mode: 'nearest', intersect: false },
                scales: {
                    x: { type: 'realtime', display: false,
                         realtime: { duration: LIVE_LINE_S * 1000, delay: (l.interval + 1) * 1000 } },
                    y: { beginAtZero: true, max: top, ticks: { callback: (v) => fmtRate(v) } }
                },
                plugins: {
                    legend: { display: true, position: 'right', labels: { boxWidth: 10, font: { size: 10 } } },
                    tooltip: { mode: 'nearest', intersect: false, callbacks: {
                        label: (c) => `${c.dataset.label}: ↓ ${fmtRate(c.raw.y)} ↑ ${fmtRate(c.raw.up)}` } },
                    streaming: { frameRate: 30, ttl: (LIVE_LINE_S + 10) * 1000 }
                }
            }
        });
        this.chartObj.$live = true;
    }

    /* ---------- live ---------- */

    async _startLive() {
        this._stopLive();
        const token = this.live.token;
        const cfg = await this.getWidgetConfig() || {};
        const n = parseInt(cfg.liveInterval, 10);
        this.live.interval = [1, 2, 5].includes(n) ? n : 1;
        try { await this._loadNames(); } catch (e) { /* names are cosmetic */ }
        // the range may have changed while we waited
        if (token !== this.live.token || this.state.range !== 'live') return;
        this._fillPicker();                  // the names have just loaded
        this._resetLiveView();
        Object.assign(this.live, { view: null, last: null, lastAt: Date.now(), status: 'connecting', rowsShown: -1 });
        this.eventSourceRetryCount = 0;
        this.openEventSource(`/api/topdevices/live/stream/${this.live.interval}`, (ev) => this._onLiveEvent(ev));
        this.live.watchdog = setInterval(() => this._liveTick(), 1000);
        this._renderLive();
    }

    // BaseWidget.openEventSource arms a reconnect timer that only a successful
    // open clears, and it reopens with the URL it captured. So a stream left
    // before it connected - or one of a widget already removed - would come back
    // by itself and paint Live over a NetFlow range. Refuse any (re)open outside
    // Live; inside Live the timer still does its job.
    openEventSource(url, onMessage) {
        if (this.live.closed || this.state.range !== 'live') return;
        super.openEventSource(url, onMessage);
    }

    _stopLive() {
        this.live.token++;
        if (this.live.watchdog) { clearInterval(this.live.watchdog); this.live.watchdog = null; }
        this.closeEventSource();
        // BaseWidget.onVisibilityChanged reopens any remembered stream URL, and
        // closing does not forget it: forget it here, or a hidden-then-shown tab
        // would restart the stream while a NetFlow range is on screen.
        this.eventSourceUrl = null;
        this.eventSourceOnData = null;
        this.live.status = 'off';
        this.live.view = null;
        this.live.rowsKey = null;            // whatever renders next owns the table body
    }

    _onLiveEvent(ev) {
        let e;
        try { e = JSON.parse(ev.data); } catch (err) { return; }
        const now = Date.now();
        this.live.last = e;
        this.live.lastAt = now;
        this.live.view = mergeLive(this.live.view, e, now);
        Object.keys(e.devices || {}).forEach(ip => this.live.known.add(ip));
        if (e.dt > 0) {
            for (const [ip, d] of Object.entries(e.devices || {})) {
                for (const scope of ['all', 'inet']) {
                    if (d[scope] && d[scope][0] + d[scope][1] > 0) this.live.lastSeen[scope][ip] = now;
                }
            }
            this._recordLine(e, now);
        }
        this.live.status = e.dt > 0 ? 'live' : 'measuring';
        this._renderLive();
    }

    // Once a second: notice a stream that has gone quiet. Events clear it again.
    _liveTick() {
        if (this.state.range !== 'live') return;
        if (typeof document !== 'undefined' && document.hidden) return;
        const now = Date.now();
        if (this.live.status === 'unavailable') {
            // a laptop waking up, a firewall back from a reboot: try again by
            // ourselves rather than waiting for someone to click Retry
            if (now - this.live.retryAt >= LIVE_RETRY_MS) this._startLive();
            return;
        }
        const eff = (this.live.last && this.live.last.effective) || this.live.interval;
        const status = liveStatusFor(now - this.live.lastAt, eff);
        if (status === 'live' || status === this.live.status) return;
        this.live.status = status;
        if (status === 'unavailable') {
            this.closeEventSource();        // stop the browser's own retries; ours follow LIVE_RETRY_MS
            this.live.retryAt = now;
        }
        this._renderLive();
    }

    onVisibilityChanged(visible) {
        super.onVisibilityChanged(visible);
        if (visible && this.state.range === 'live' && this.eventSourceUrl !== null) {
            this.live.lastAt = Date.now();
            this.live.status = 'connecting';
            this._renderLive();
        }
    }

    _liveSummary() {
        const l = this.live, e = l.last;
        const parts = ['Live'];
        if (e && e.wan) {
            const via = (e.wan.devs || []).join(', ') || 'WAN';
            parts.push(`WAN \u2193 ${fmtRate(e.wan.down)} \u2191 ${fmtRate(e.wan.up)} (via ${via})`);
        }
        parts.push(this.state.scope === 'wan' ? 'internet only' : 'all traffic');
        parts.push(e && e.throttled ? `throttled to ${e.effective} s` : `${l.interval} s`);
        if (e && e.coverage && e.coverage.ok === false) parts.push('\u26a0 totals disagree with the WAN counters');
        // a gap in what was measured must not pass for a quiet network
        if (e && e.v6_skipped > 0) parts.push(`IPv6 not counted (${e.v6_skipped} connections)`);
        if (e && e.unparsed > 0) parts.push(`\u26a0 ${e.unparsed} unreadable state lines`);
        if (e && e.error) parts.push(`\u26a0 ${e.error}`);
        const status = { connecting: 'connecting\u2026', measuring: 'measuring\u2026',
                         reconnecting: 'reconnecting\u2026' }[l.status];
        if (status) parts.push(status);
        return parts.join(' \u00b7 ');
    }

    // What the Devices picker offers, grouped by network in the interfaces' order:
    // every named device on a local network (DHCP leases and host records), every
    // device this Live session has seen, and whatever is already picked - never the
    // firewall's own addresses or a broadcast address. A pick that is on no network
    // any more stays listed under Other, so it can still be unticked.
    _pickChoices() {
        const pick = this.state.livePick || [];
        const gateways = this.ifaceNames || {};
        const groups = this.networks.map(n => ({ key: n.key, label: n.label, devices: [] }));
        const other = { key: '', label: 'Other', devices: [] };
        for (const ip of new Set([...Object.keys(this.names), ...this.live.known, ...pick])) {
            if (gateways[ip] || this._isBroadcast(ip)) continue;
            const d = { ip: ip, name: this.names[ip] || '' };
            const g = groups.find(x => x.key === this._netOf(ip));
            if (g) g.devices.push(d);
            else if (pick.includes(ip)) other.devices.push(d);
        }
        return groups.concat(other).filter(g => g.devices.length)
                     .map(g => ({ label: g.label, devices: g.devices.sort(byLabel) }));
    }

    // (Re)build the picker's options, ticking the picks. Built as elements, not
    // markup, so a hostname can never inject HTML.
    _fillPicker() {
        const $s = $(PICKER);
        if (!$s.length) return;
        const pick = new Set(this.state.livePick || []);
        $s.empty();
        for (const g of this._pickChoices()) {
            const $g = $('<optgroup>').attr('label', g.label);
            for (const d of g.devices) {
                $g.append($('<option>').val(d.ip).text(d.name || d.ip)
                    .attr({ 'data-subtext': d.name ? d.ip : '', 'data-tokens': `${d.ip} ${d.name}` })
                    .prop('selected', pick.has(d.ip)));
            }
            $s.append($g);
        }
        if (this.live.picker) $s.selectpicker('refresh');
    }

    // The Devices picker is bootstrap-select, which the dashboard loads for its own
    // dialogs. Without it the picker stays hidden and Live shows the busiest devices.
    _initPicker() {
        const $s = $(PICKER);
        if (!$s.length || typeof $s.selectpicker !== 'function') return;
        this._fillPicker();
        $s.selectpicker();
        this.live.picker = true;
    }

    // The rows the Live table shows, in order: the picked devices (see liveRows),
    // or with none picked exactly the row limit's worth, where only the busiest
    // device moves (see dynamicOrder), topped up with recently seen devices and
    // then every other known one, at 0.
    _liveTableRows() {
        const l = this.live;
        const scope = this.state.scope === 'wan' ? 'inet' : 'all';
        const rates = l.view ? liveRates(l.view, scope) : {};
        const pick = this.state.livePick || [];
        const ips = pick.length ? pick
            : [...new Set([...Object.keys(rates), ...this._pickChoices().flatMap(g => g.devices.map(d => d.ip))])];
        this.state.rows = ips.map((ip) => {
            const r = rates[ip] || { down: 0, up: 0 };
            return { ip: ip, name: this.names[ip] || (this.ifaceNames || {})[ip] || '', net: this._netOf(ip),
                     down: r.down, up: r.up, total: r.down + r.up };
        });
        const ranked = liveRows(this.state.rows, { picked: pick.length > 0, network: this.state.network,
                                                   search: this.state.search, seen: l.lastSeen[scope] });
        let rows = ranked;
        if (!pick.length) {
            // seed only from a measured interval: before the first, every device
            // is at 0 and the seed would come out A to Z instead of busiest first
            const measured = !!(l.view && l.view.events.length);
            const order = dynamicOrder(measured ? l.dynOrder : null, ranked, this.state.rowsN || 20, l.hover);
            if (measured) l.dynOrder = order;
            const byIp = new Map(ranked.map(r => [r.ip, r]));
            rows = order.map(ip => byIp.get(ip));
        }
        // the selected device keeps its panel while it is listed, and loses it once gone
        if (this.state.selected && !rows.some(r => r.ip === this.state.selected)) this.state.selected = null;
        return rows;
    }

    // Rebuild the table only when its rows change; otherwise update the figures
    // in place. Rebuilding every interval replaced the row under the pointer, so
    // a click that straddled a redraw was lost.
    _paintLiveRows(rows, empty) {
        const l = this.live;
        const key = liveRowsKey(rows);
        const $trs = $('.td-body').children('tr[data-ip]');
        const onScreen = $trs.map((i, tr) => $(tr).attr('data-ip')).get().join('\n');
        if (rows.length && key === l.rowsKey && onScreen === rows.map(r => r.ip).join('\n')) {
            $trs.each((i, tr) => {
                const r = rows[i], $td = $(tr).children('td');
                $td.eq(2).text(fmtRate(r.down));
                $td.eq(3).text(fmtRate(r.up));
                $td.eq(4).children('strong').text(fmtRate(r.total));
                $(tr).toggleClass('info', this.state.selected === r.ip);
            });
            return;
        }
        $('.td-body').html(this._rowsHtml(rows, fmtRate, empty));
        l.rowsKey = rows.length ? key : null;
    }

    _renderLive() {
        const l = this.live;
        const CHART_MAX = 10;
        const picked = (this.state.livePick || []).length > 0;
        const rows = this._liveTableRows();
        l.order = rows.map(r => r.ip);

        $('.td-window small').text(this._liveSummary());
        this._renderChrome();
        if (l.status === 'unavailable') {
            l.rowsKey = null;
            $('.td-body').html('<tr><td colspan="5" class="text-muted">Live data unavailable. '
                             + '<a href="#" class="td-live-retry">Retry</a></td></tr>');
        } else {
            this._paintLiveRows(rows, picked ? 'No picked device matches the filter'
                                             : (l.view ? 'No active devices' : 'Measuring\u2026'));
        }
        this._renderChart(rows.slice(0, CHART_MAX), fmtRate, true);
        if (rows.length > CHART_MAX && this._chartKind() !== 'none') {
            $('.td-window small').append(`<span class="text-muted"> \u00b7 chart: ${picked ? 'first' : 'top'} `
                                         + `${CHART_MAX} of ${rows.length}</span>`);
        }
        if (this.state.selected) this._renderLiveDetails(this.state.selected);
        else $('.td-details').empty();
        this._applyLayout();
        if (rows.length !== l.rowsShown) { l.rowsShown = rows.length; this._fitHeight(); }
    }

    _renderLiveDetails(ip) {
        const $d = $('.td-details');
        const view = this.live.view;
        if (!view) { $d.html('<small class="text-muted">Measuring\u2026</small>'); return; }
        const scope = this.state.scope === 'wan' ? 'inet' : 'all';
        const rate = liveRates(view, scope)[ip] || { down: 0, up: 0 };
        const detail = liveDetail(view, ip, scope);
        // resolve peer names in the background; the next event re-renders with them
        const todo = detail.peers.map(p => p.key)
            .filter(k => !this.names[k] && this.ptrCache[k] === undefined && !this.live.ptrPending.has(k));
        if (todo.length) {
            todo.forEach(k => this.live.ptrPending.add(k));
            this._ptrMany(todo, 6).then(() => todo.forEach(k => this.live.ptrPending.delete(k)));
        }
        const row = (label, title, v) => `<tr title="${this._esc(title)}">`
            + `<td style="text-align:left;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:0;">`
            + `${this._esc(label)}</td>`
            + `<td style="text-align:right;white-space:nowrap;width:78px;">${fmtRate(v.down + v.up)}</td></tr>`;
        const none = '<tr><td colspan="2" class="text-muted">none</td></tr>';
        const peers = detail.peers.map((p) => {
            const name = this.names[p.key] || this.ptrCache[p.key] || p.key;
            return row(name, name === p.key ? p.key : `${name}  ${p.key}`, p);
        }).join('') || none;
        const ports = detail.ports.map(p => row(p.key === '0' ? 'other' : p.key, p.key, p)).join('') || none;
        $d.html(`
            <div style="border-top:1px solid #ddd;padding-top:6px;">
                <div title="${this._esc((this.names[ip] || ip) + ' ' + ip)}"
                     style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    <strong>${this._esc(this.names[ip] || ip)}</strong>
                    <small class="text-muted">${this._esc(ip)}</small>
                </div>
                <div style="margin-top:3px;"><small class="text-muted">down ${fmtRate(rate.down)}
                    &middot; up ${fmtRate(rate.up)} &middot; ${LIVE_WINDOW_S} s average</small></div>
                <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;">
                    <div style="flex:1 1 260px;min-width:0;">
                        <small class="text-muted">Top peers</small>
                        <table class="table table-condensed" style="margin:0;table-layout:fixed;width:100%;">${peers}</table>
                    </div>
                    <div style="flex:1 1 150px;min-width:0;">
                        <small class="text-muted">Top ports</small>
                        <table class="table table-condensed" style="margin:0;table-layout:fixed;width:100%;">${ports}</table>
                    </div>
                </div>
            </div>`);
    }

    async renderDetails(ip) {
        this._busy(true);
        try { await this._renderDetails(ip); } finally { this._busy(false); }
    }

    async _renderDetails(ip) {
        if (this.state.range === 'live') { this._renderLiveDetails(ip); return; }
        const $d = $('.td-details');
        // clicking a device must acknowledge immediately: the aggregate is cheap
        // but resolving peer names can take a second or more
        $d.html('<div style="padding:12px 0;text-align:center;">'
              + '<i class="fa fa-spinner fa-spin" style="opacity:0.6;"></i></div>');
        this._applyLayout();
        const [from, to] = this.state.window || this._window(this.state.range);
        let flows;
        try { flows = await this._export(from, to); }
        catch (e) { $d.html('<small class="text-danger">Detail unavailable for this range</small>'); return; }
        if (this.state.selected !== ip) return;

        const peers = {}, ports = {};
        let down = 0, up = 0;
        flows.forEach((r) => {
            if (r.dst !== ip) return;                 // dst-keyed, as above
            if (!this._inScope(r)) return;
            const peer = r.src;
            if (!peer) return;
            peers[peer] = (peers[peer] || 0) + r.octets;
            const p = (r.port && r.port !== '0') ? r.port : 'other';
            ports[p] = (ports[p] || 0) + r.octets;
            if (r.dir === 'out') up += r.octets; else down += r.octets;
        });
        const dn = this.state.detailN || 10;
        const topOf = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, dn);
        const topPeers = topOf(peers);
        // local peers already have a name; remote ones get a reverse-DNS lookup
        $d.find('.td-detailrows').prop('disabled', true);
        const ptrs = await this._ptrMany(topPeers.map(([k]) => k), 6);
        topPeers.forEach(([k], i) => { if (this.names[k]) ptrs[i] = ''; });
        if (this.state.selected !== ip) return;

        const list = (pairs, labels) => pairs.length
            ? pairs.map(([k, v], i) => {
                const label = (labels && labels[i]) || this.names[k] || k;
                return `<tr title="${this._esc(label === k ? k : label + '  ' + k)}">`
                     + `<td style="text-align:left;white-space:nowrap;overflow:hidden;`
                     + `text-overflow:ellipsis;max-width:0;">${this._esc(label)}</td>`
                     + `<td style="text-align:right;white-space:nowrap;width:78px;">${this._fmt(v)}</td></tr>`;
              }).join('')
            : '<tr><td colspan="2" class="text-muted">none</td></tr>';

        $d.html(`
            <div style="border-top:1px solid #ddd;padding-top:6px;">
                <div title="${this._esc((this.names[ip] || ip) + ' ' + ip)}"
                     style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                    <strong>${this._esc(this.names[ip] || ip)}</strong>
                    <small class="text-muted">${this._esc(ip)}</small>
                </div>
                <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:3px;">
                    <small class="text-muted">down ${this._fmt(down)} &middot; up ${this._fmt(up)}</small>
                    <select class="td-detailrows" title="Rows per list" style="width:68px !important;max-width:68px;min-width:68px;flex:0 0 68px;height:26px;font-size:11px;padding:1px 4px;border:1px solid #ccc;border-radius:3px;background-color:#fff;">
                        <option value="10">10</option>
                        <option value="50">50</option>
                        <option value="100">100</option>
                    </select>
                </div>
                <div style="display:flex;flex-wrap:wrap;gap:10px;margin-top:6px;">
                    <div style="flex:1 1 260px;min-width:0;">
                        <small class="text-muted">Top peers</small>
                        <table class="table table-condensed" style="margin:0;table-layout:fixed;width:100%;">${list(topPeers, ptrs)}</table>
                    </div>
                    <div style="flex:1 1 150px;min-width:0;">
                        <small class="text-muted">Top ports</small>
                        <table class="table table-condensed" style="margin:0;table-layout:fixed;width:100%;">${list(topOf(ports), null)}</table>
                    </div>
                </div>
            </div>`);
        // must run after the panel is rewritten, or the select always reads 10
        $d.find('.td-detailrows').val(String(dn));
        this._fitHeight();
    }

    // Side-by-side needs room. Below ~780px the details column would squeeze the
    // table into unreadability, so fall back to stacking it underneath.
    _applyLayout() {
        const w = $('.td-wrap').width() || 0;
        const narrow = w > 0 && w < 780;
        const show = !!this.state.selected;
        $('.td-main').css({ display: narrow ? 'block' : 'flex' });
        // hidden entirely when nothing is selected, so the table gets the full width
        $('.td-details').css({
            display: show ? 'block' : 'none',
            flex: narrow ? '' : '1 1 0',
            width: narrow ? '100%' : '',
            position: narrow ? 'static' : 'sticky',
            marginTop: narrow ? '8px' : '',
            maxHeight: narrow ? '' : '420px',
            overflowY: narrow ? '' : 'auto'
        });
        $('.td-tablewrap').css({ maxHeight: narrow ? '' : '420px' });

        // A sticky header inside a scrolling box needs an opaque background or the
        // rows scroll through it. 'inherit' resolves to transparent, so walk up to
        // the first ancestor that actually paints one - which keeps this working on
        // dark themes instead of hardcoding white.
        let bg = '', $p = $('.td-wrap');
        while ($p.length && (!bg || bg === 'transparent' || bg === 'rgba(0, 0, 0, 0)')) {
            bg = $p.css('background-color');
            $p = $p.parent();
        }
        $('.td-tablewrap thead').css('background-color',
            (bg && bg !== 'rgba(0, 0, 0, 0)') ? bg : '#fff');
    }

    onWidgetResize() {
        this._applyLayout();
        if (this.chartObj) this.chartObj.resize();
        return true;
    }

    onWidgetClose() {
        this.live.closed = true;            // before _stopLive, so no late timer can reopen
        this._stopLive();
        super.onWidgetClose();              // BaseWidget closes the EventSource here
        // its menu lives under <body> (data-container), outside the widget being removed
        if (this.live.picker) { try { $(PICKER).selectpicker('destroy'); } catch (e) { /* gone already */ } }
        $(document).off('.topdevices');
        $('.td-refresh').remove();
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
    }
}
