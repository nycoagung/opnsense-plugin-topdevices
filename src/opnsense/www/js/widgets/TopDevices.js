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
 */

export default class TopDevices extends BaseWidget {

    constructor(config) {
        super(config);
        this.configurable = true;
        this.STORE = 'opnsense.topdevices.view';

        this.state = {
            range: '24h', chart: 'pie', network: '', search: '',
            sortKey: 'total', sortDir: 'desc',
            customFrom: '', customTo: '',
            rows: [], window: null, selected: null
        };

        this.networks = [];
        this.ifaceNames = {};
        this.names = {};
        this.cache = {};          // cacheKey -> parsed export rows
        this.ptrCache = {};       // peer ip -> reverse-DNS name ('' = none)
        this.chartObj = null;
        this.loading = false;
    }

    getGridOptions() { return { sizeToContent: 1000 }; }

    async getWidgetOptions() {
        return {
            rowsToShow: {
                id: 'rowsToShow', title: 'Devices to show', type: 'select',
                options: ['5', '10', '15', '20', '30', '50'].map(v => ({ value: v, label: v })),
                default: '10', required: true
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
            }
        };
    }

    /* ---------- view state persistence ---------- */

    _saveView() {
        try {
            const s = this.state;
            localStorage.setItem(this.STORE, JSON.stringify({
                range: s.range, chart: s.chart, network: s.network, search: s.search,
                sortKey: s.sortKey, sortDir: s.sortDir,
                customFrom: s.customFrom, customTo: s.customTo
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

    /* ---------- data ---------- */

    async _loadNetworks() {
        const rfc1918 = [
            { net: this._ip2int('10.0.0.0'),    mask: (0xff000000 | 0) },
            { net: this._ip2int('172.16.0.0'),  mask: (0xfff00000 | 0) },
            { net: this._ip2int('192.168.0.0'), mask: (0xffff0000 | 0) }
        ];
        const isPrivate = (v) => rfc1918.some(r => (v & r.mask) === (r.net & r.mask));
        const ifaceNames = {};
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
            if (!isPrivate(net)) return;                 // drops WAN and loopback
            const label = i.description || i.identifier;
            out.push({ key: i.identifier, label: label, net: net, mask: mask,
                       bcast: (net | (~mask)) | 0 });
            // the firewall's own address on this segment is a real endpoint (DNS,
            // DHCP) but it is not one of the user's devices - label it as such
            ifaceNames[c[0]] = `${label} gateway`;
        });
        this.networks = out;
        this.ifaceNames = ifaceNames;
    }

    // Names come from two sources. DHCP leases cover devices that are only ever
    // dynamic; static host records cover everything with a fixed address, which
    // never appears in the lease table at all (servers, the firewall itself).
    // Host records win where both exist: they are the curated name.
    async _loadNames() {
        const map = {};
        try {
            const r = await this.ajaxCall('/api/dnsmasq/leases/search', JSON.stringify({ rowCount: 1000 }), 'POST');
            ((r && r.rows) || []).forEach(l => { if (l.address && l.hostname) map[l.address] = l.hostname; });
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
            if (!acc[ip]) acc[ip] = { ip: ip, down: 0, up: 0 };
            if (r.dir === 'out') acc[ip].up += r.octets; else acc[ip].down += r.octets;
        });
        this.state.rows = Object.values(acc).map(d => ({
            ip: d.ip, name: this.names[d.ip] || (this.ifaceNames || {})[d.ip] || '', net: this._netOf(d.ip),
            down: d.down, up: d.up, total: d.down + d.up
        }));
        this.state.window = [from, to];
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

    /* ---------- markup ---------- */

    getMarkup() {
        const ranges = this._ranges().map(r => `<option value="${r.key}">${this._esc(r.label)}</option>`).join('');
        // explicit widths: `width:auto` on a select inside a flex row collapses
        // and clips the label in the OPNsense theme
        const selCss = 'height:30px;padding:3px 24px 3px 8px;font-size:12px;'
                     + 'border:1px solid #ccc;border-radius:3px;background-color:#fff;flex:0 0 auto;';
        return $(`
        <div class="td-wrap">
            <div class="td-controls" style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:6px;">
                <select class="td-range"   style="${selCss}width:140px;">${ranges}</select>
                <select class="td-network" style="${selCss}width:140px;"></select>
                <input type="text" class="form-control input-sm td-search" placeholder="Filter name or IP"
                       style="height:30px;font-size:12px;flex:1 1 140px;min-width:110px;"/>
                <div class="btn-group btn-group-sm td-chartbtns" style="flex:0 0 auto;">
                    <button type="button" class="btn btn-default" data-chart="pie">Pie</button>
                    <button type="button" class="btn btn-default" data-chart="bar">Bar</button>
                    <button type="button" class="btn btn-default" data-chart="none">Off</button>
                </div>
            </div>
            <div class="td-custom" style="display:none;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px;">
                <input type="datetime-local" step="1" class="form-control input-sm td-from"
                       style="height:30px;font-size:12px;width:210px;"/>
                <span class="text-muted">&rarr;</span>
                <input type="datetime-local" step="1" class="form-control input-sm td-to"
                       style="height:30px;font-size:12px;width:210px;"/>
                <button type="button" class="btn btn-primary btn-sm td-apply">Apply</button>
            </div>
            <div class="td-window" style="margin-bottom:4px;"><small class="text-muted"></small></div>
            <div class="td-chartbox" style="height:175px;margin:4px 0;"><canvas class="td-canvas"></canvas></div>
            <table class="table table-condensed table-hover" style="margin-bottom:4px;">
                <thead><tr>
                    <th class="td-sort" data-key="name"  style="cursor:pointer;text-align:left;">Device</th>
                    <th class="td-sort" data-key="net"   style="cursor:pointer;text-align:left;">Network</th>
                    <th class="td-sort" data-key="down"  style="cursor:pointer;text-align:right;">Down</th>
                    <th class="td-sort" data-key="up"    style="cursor:pointer;text-align:right;">Up</th>
                    <th class="td-sort" data-key="total" style="cursor:pointer;text-align:right;">Total</th>
                </tr></thead>
                <tbody class="td-body"></tbody>
            </table>
            <div class="td-details"></div>
        </div>`);
    }

    async onMarkupRendered() {
        const cfg = await this.getWidgetConfig() || {};
        this.state.range = cfg.defaultRange || '24h';
        this.state.chart = cfg.defaultChart || 'pie';
        this.tickTimeout = parseInt(cfg.refreshInterval, 10) || 900;

        const saved = this._loadView();
        if (saved) Object.assign(this.state, saved);

        await this._loadNetworks();
        this._fillNetworkSelect();

        $('.td-range').val(this.state.range);
        $('.td-search').val(this.state.search);
        $('.td-network').val(this.state.network);
        const [f, t] = this._window(this.state.range);
        $('.td-from').val(this.state.customFrom || this._epochToLocal(f));
        $('.td-to').val(this.state.customTo || this._epochToLocal(t));
        $('.td-custom').css('display', this.state.range === 'custom' ? 'flex' : 'none');

        this._bind();
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
            self.state.range = $(this).val();
            self.state.selected = null;
            $('.td-custom').css('display', self.state.range === 'custom' ? 'flex' : 'none');
            self._saveView();
            if (self.state.range !== 'custom') self.refresh();
        });
        $(document).on('click.topdevices', '.td-apply', function () {
            self.state.customFrom = $('.td-from').val();
            self.state.customTo = $('.td-to').val();
            self.state.selected = null;
            self._saveView();
            self.refresh();
        });
        $(document).on('change.topdevices', '.td-network', function () {
            self.state.network = $(this).val(); self._saveView(); self.render();
        });
        $(document).on('input.topdevices', '.td-search', function () {
            self.state.search = $(this).val().toLowerCase().trim(); self._saveView(); self.render();
        });
        $(document).on('click.topdevices', '.td-chartbtns button', function () {
            self.state.chart = $(this).data('chart'); self._saveView(); self.render();
        });
        $(document).on('click.topdevices', '.td-sort', function () {
            const k = $(this).data('key');
            if (self.state.sortKey === k) self.state.sortDir = self.state.sortDir === 'asc' ? 'desc' : 'asc';
            else { self.state.sortKey = k; self.state.sortDir = (k === 'name' || k === 'net') ? 'asc' : 'desc'; }
            self._saveView(); self.render();
        });
        $(document).on('click.topdevices', '.td-body tr', function () {
            const ip = $(this).data('ip');
            self.state.selected = (self.state.selected === ip) ? null : ip;
            self.render();
            if (self.state.selected) self.renderDetails(self.state.selected);
        });
    }

    async refresh() {
        if (this.loading) return;
        this.loading = true;
        $('.td-window small').text('Loading…');
        try {
            await this._loadNames();
            await this._load();
            this.render();
            if (this.state.selected) this.renderDetails(this.state.selected);
        } catch (e) {
            $('.td-body').html('<tr><td colspan="5" class="text-danger">Unable to read NetFlow data</td></tr>');
            $('.td-window small').text('');
        } finally { this.loading = false; }
    }

    async onWidgetTick() { await this.refresh(); }

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
        const cfg = await this.getWidgetConfig() || {};
        const limit = parseInt(cfg.rowsToShow, 10) || 10;
        const rows = this._visibleRows().slice(0, limit);

        if (this.state.window) {
            $('.td-window small').text(
                `${this._dateStr(this.state.window[0])}  →  ${this._dateStr(this.state.window[1])}`);
        }
        $('.td-chartbtns button').removeClass('btn-primary').addClass('btn-default');
        $(`.td-chartbtns button[data-chart="${this.state.chart}"]`).removeClass('btn-default').addClass('btn-primary');
        $('.td-sort').each((i, el) => {
            $(el).find('.td-arrow').remove();
            if ($(el).data('key') === this.state.sortKey) {
                $(el).append(`<span class="td-arrow"> ${this.state.sortDir === 'asc' ? '▲' : '▼'}</span>`);
            }
        });

        if (rows.length === 0) {
            $('.td-body').html('<tr><td colspan="5" class="text-muted">No matching devices</td></tr>');
        } else {
            $('.td-body').html(rows.map((r) => {
                const label = r.name
                    ? `<strong>${this._esc(r.name)}</strong><br/><small class="text-muted">${this._esc(r.ip)}</small>`
                    : `<strong>${this._esc(r.ip)}</strong>`;
                const net = this.networks.find(n => n.key === r.net);
                const sel = this.state.selected === r.ip ? ' class="info"' : '';
                return `<tr${sel} data-ip="${this._esc(r.ip)}" style="cursor:pointer;">
                    <td style="text-align:left;">${label}</td>
                    <td style="text-align:left;"><small>${this._esc(net ? net.label : '')}</small></td>
                    <td style="text-align:right;">${this._fmt(r.down)}</td>
                    <td style="text-align:right;">${this._fmt(r.up)}</td>
                    <td style="text-align:right;"><strong>${this._fmt(r.total)}</strong></td></tr>`;
            }).join(''));
        }
        this._renderChart(rows);
        if (!this.state.selected) $('.td-details').empty();
    }

    _renderChart(rows) {
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
        if (this.state.chart === 'none' || rows.length === 0) { $('.td-chartbox').hide(); return; }
        $('.td-chartbox').show();
        const el = $('.td-canvas')[0];
        if (!el) return;
        const labels = rows.map(r => r.name || r.ip);
        const isPie = this.state.chart === 'pie';
        const fmt = (v) => this._fmt(v);
        this.chartObj = new Chart(el.getContext('2d'), {
            type: isPie ? 'doughnut' : 'bar',
            data: {
                labels: labels,
                datasets: isPie
                    ? [{ data: rows.map(r => r.total), borderWidth: 0 }]
                    : [{ label: 'Down', data: rows.map(r => r.down) },
                       { label: 'Up',   data: rows.map(r => r.up) }]
            },
            options: {
                responsive: true, maintainAspectRatio: false,
                plugins: {
                    legend: { display: true, position: isPie ? 'right' : 'top',
                              labels: { boxWidth: 10, font: { size: 10 } } },
                    tooltip: { callbacks: { label: (c) => `${c.dataset.label || c.label}: ${fmt(c.parsed.y ?? c.parsed)}` } }
                },
                scales: isPie ? {} : {
                    x: { stacked: true, ticks: { font: { size: 9 } } },
                    y: { stacked: true, ticks: { callback: (v) => fmt(v) } }
                }
            }
        });
    }

    async renderDetails(ip) {
        const $d = $('.td-details');
        const [from, to] = this.state.window || this._window(this.state.range);
        let flows;
        try { flows = await this._export(from, to); }
        catch (e) { $d.html('<small class="text-danger">Detail unavailable for this range</small>'); return; }
        if (this.state.selected !== ip) return;

        const peers = {}, ports = {};
        let down = 0, up = 0;
        flows.forEach((r) => {
            if (r.dst !== ip) return;                 // dst-keyed, as above
            const peer = r.src;
            if (!peer) return;
            peers[peer] = (peers[peer] || 0) + r.octets;
            const p = (r.port && r.port !== '0') ? r.port : 'other';
            ports[p] = (ports[p] || 0) + r.octets;
            if (r.dir === 'out') up += r.octets; else down += r.octets;
        });
        const topOf = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 5);
        const topPeers = topOf(peers);
        // local peers already have a name; remote ones get a reverse-DNS lookup
        const ptrs = await Promise.all(topPeers.map(([k]) => this.names[k] ? '' : this._ptr(k)));
        if (this.state.selected !== ip) return;

        const list = (pairs, labels) => pairs.length
            ? pairs.map(([k, v], i) => {
                const label = (labels && labels[i]) || this.names[k] || k;
                return `<tr><td style="text-align:left;word-break:break-all;" title="${this._esc(k)}">`
                     + `${this._esc(label)}</td>`
                     + `<td style="text-align:right;white-space:nowrap;">${this._fmt(v)}</td></tr>`;
              }).join('')
            : '<tr><td colspan="2" class="text-muted">none</td></tr>';

        $d.html(`
            <div style="border-top:1px solid #ddd;padding-top:6px;">
                <strong>${this._esc(this.names[ip] || ip)}</strong>
                <small class="text-muted">${this._esc(ip)}</small>
                <div><small>down ${this._fmt(down)} &middot; up ${this._fmt(up)}</small></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;">
                    <div style="flex:1 1 150px;">
                        <small class="text-muted">Top peers</small>
                        <table class="table table-condensed" style="margin:0;">${list(topPeers, ptrs)}</table>
                    </div>
                    <div style="flex:1 1 110px;">
                        <small class="text-muted">Top ports</small>
                        <table class="table table-condensed" style="margin:0;">${list(topOf(ports), null)}</table>
                    </div>
                </div>
            </div>`);
    }

    onWidgetResize() { if (this.chartObj) this.chartObj.resize(); return true; }

    onWidgetClose() {
        $(document).off('.topdevices');
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
    }
}
