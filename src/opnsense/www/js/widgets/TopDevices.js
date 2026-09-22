/*
 * TopDevices.js - OPNsense dashboard widget
 *
 * Top local devices by traffic, from the built-in NetFlow/Insight aggregator.
 * Date-range picker, network/name/IP filtering, sortable columns, per-device
 * drill-down and pie/bar charts.
 *
 * NOTHING IS HARDCODED:
 *   - local networks are derived from the firewall's own interface config
 *   - hostnames come from DHCP leases
 *   - row count, default range, default chart and refresh interval are
 *     widget configuration options
 *
 * TWO LIMITATIONS OF THE UPSTREAM API, BOTH MEASURED, NOT ASSUMED:
 *
 * 1. The Insight `top` endpoint ignores filter arguments entirely (eight
 *    different syntaxes tested, all byte-identical). Per-device drill-down
 *    therefore has to pull the full detail export and filter in the browser,
 *    which is why details load on demand and are cached per range.
 *
 * 2. Windows wider than a few hours snap to whole day buckets aligned to UTC
 *    midnight - a 6h and a 24h query return identical totals. "Today" and
 *    "Yesterday" are therefore approximate. The widget always prints the
 *    window it actually asked for so the figure is never silently wrong.
 *
 * The figures are TOTAL traffic per device, internal plus internet. An NVR
 * pulling camera streams will dominate; that traffic never reaches the WAN.
 */

export default class TopDevices extends BaseWidget {

    constructor(config) {
        super(config);
        this.configurable = true;

        this.state = {
            range: '24h',
            chart: 'pie',
            network: '',
            search: '',
            sortKey: 'total',
            sortDir: 'desc',
            rows: [],
            selected: null
        };

        this.networks = [];      // [{key, label, net, mask}] derived from the firewall
        this.names = {};         // ip -> hostname, from DHCP leases
        this.detailCache = {};   // range -> parsed export rows
        this.chartObj = null;
        this.loading = false;
    }

    getGridOptions() {
        return { sizeToContent: 900 };
    }

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
                options: [
                    { value: 'pie', label: 'Pie' },
                    { value: 'bar', label: 'Bar' },
                    { value: 'none', label: 'None' }
                ],
                default: 'pie', required: true
            },
            refreshInterval: {
                id: 'refreshInterval', title: 'Refresh interval', type: 'select',
                options: [
                    { value: '60', label: '1 minute' },
                    { value: '300', label: '5 minutes' },
                    { value: '900', label: '15 minutes' },
                    { value: '3600', label: '1 hour' }
                ],
                default: '300', required: true
            }
        };
    }

    /* ---------- ranges (computed, never hardcoded timestamps) ---------- */

    _ranges() {
        return [
            { key: '1h',        label: 'Last hour' },
            { key: '24h',       label: 'Last 24 hours' },
            { key: 'today',     label: 'Today' },
            { key: 'yesterday', label: 'Yesterday' },
            { key: '7d',        label: 'Last 7 days' }
        ];
    }

    _window(key) {
        const now = new Date();
        const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
        const s = Math.floor(now.getTime() / 1000);
        const DAY = 86400;
        switch (key) {
            case '1h':        return [s - 3600, s];
            case 'today':     return [Math.floor(midnight), s];
            case 'yesterday': return [Math.floor(midnight) - DAY, Math.floor(midnight)];
            case '7d':        return [s - (7 * DAY), s];
            case '24h':
            default:          return [s - DAY, s];
        }
    }

    /* ---------- helpers ---------- */

    _fmt(n) {
        if (!n || n < 1) return '-';
        const u = ['B', 'KB', 'MB', 'GB', 'TB'];
        let i = 0;
        while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
        return `${n.toFixed(1)} ${u[i]}`;
    }

    _esc(s) {
        return $('<div>').text(s === null || s === undefined ? '' : String(s)).html();
    }

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

    _inNet(ip, entry) {
        const v = this._ip2int(ip);
        return v !== null && (v & entry.mask) === entry.net;
    }

    _isLocal(ip) {
        return this.networks.some(n => this._inNet(ip, n));
    }

    _netOf(ip) {
        const hit = this.networks.find(n => this._inNet(ip, n));
        return hit ? hit.key : '';
    }

    _stamp(ts) {
        const d = new Date(ts * 1000);
        const p = (x) => String(x).padStart(2, '0');
        return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
    }

    /* ---------- data ---------- */

    // Local networks come from the firewall's interface config. Anything
    // outside RFC1918 is treated as upstream (a public WAN subnet is not a
    // set of local devices), so this adapts to any installation.
    async _loadNetworks() {
        const rfc1918 = [
            { net: this._ip2int('10.0.0.0'),    mask: (0xff000000 | 0) },
            { net: this._ip2int('172.16.0.0'),  mask: (0xfff00000 | 0) },
            { net: this._ip2int('192.168.0.0'), mask: (0xffff0000 | 0) }
        ];
        const isPrivate = (v) => rfc1918.some(r => (v & r.mask) === (r.net & r.mask));

        let data = [];
        try {
            data = await this.ajaxCall('/api/interfaces/overview/export');
        } catch (e) {
            data = [];
        }
        const items = Array.isArray(data) ? data : (data.rows || []);
        const out = [];
        items.forEach((i) => {
            if (!i || typeof i !== 'object' || !i.identifier || !i.addr4) return;
            const cidr = String(i.addr4).split('/');
            const addr = this._ip2int(cidr[0]);
            const bits = parseInt(cidr[1], 10);
            if (addr === null || isNaN(bits) || bits < 8 || bits > 32) return;
            const mask = bits === 0 ? 0 : ((0xffffffff << (32 - bits)) | 0);
            const net = (addr & mask) | 0;
            if (!isPrivate(net)) return;                       // drops WAN / loopback
            out.push({ key: i.identifier, label: i.description || i.identifier, net: net, mask: mask });
        });
        this.networks = out;
    }

    async _loadNames() {
        try {
            const r = await this.ajaxCall('/api/dnsmasq/leases/search',
                JSON.stringify({ rowCount: 1000 }), 'POST');
            const map = {};
            ((r && r.rows) || []).forEach(l => { if (l.address && l.hostname) map[l.address] = l.hostname; });
            this.names = map;
        } catch (e) {
            this.names = {};
        }
    }

    async _loadTop() {
        const cfg = await this.getWidgetConfig() || {};
        const limit = Math.max(50, (parseInt(cfg.rowsToShow, 10) || 10) * 6);
        const [from, to] = this._window(this.state.range);
        const raw = await this.ajaxCall(
            `/api/diagnostics/networkinsight/top/FlowSourceAddrDetails/${from}/${to}/dst_addr/octets/${limit}`
        );
        const rows = [];
        (Array.isArray(raw) ? raw : []).forEach((r) => {
            const ip = r.dst_addr;
            // the API appends a summary row with an empty address
            if (!ip || !this._isLocal(ip)) return;
            rows.push({
                ip: ip,
                name: this.names[ip] || '',
                net: this._netOf(ip),
                total: parseFloat(r.total) || 0
            });
        });
        this.state.rows = rows;
        this.state.window = [from, to];
    }

    // Detail export is large (megabytes) and unfilterable server-side, so it is
    // fetched only when a device is opened, and cached per range.
    async _loadDetail(range) {
        if (this.detailCache[range]) return this.detailCache[range];
        const [from, to] = this._window(range);
        const url = `/api/diagnostics/networkinsight/export/FlowSourceAddrDetails/${from}/${to}/86400/src_addr/octets`;
        const text = await new Promise((resolve, reject) => {
            $.ajax({ url: url, dataType: 'text', timeout: 120000 })
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
                port: c[ix.service_port], proto: c[ix.protocol],
                dir: c[ix.direction], iface: c[ix['if']],
                octets: parseFloat(c[ix.octets]) || 0
            });
        });
        this.detailCache[range] = rows;
        return rows;
    }

    /* ---------- markup ---------- */

    getMarkup() {
        const ranges = this._ranges()
            .map(r => `<option value="${r.key}">${this._esc(r.label)}</option>`).join('');
        return $(`
        <div class="td-wrap">
            <div class="td-controls" style="display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;">
                <select class="form-control input-sm td-range" style="width:auto;flex:0 0 auto;">${ranges}</select>
                <select class="form-control input-sm td-network" style="width:auto;flex:0 0 auto;"></select>
                <input type="text" class="form-control input-sm td-search" placeholder="Filter name or IP"
                       style="width:auto;flex:1 1 120px;min-width:100px;"/>
                <div class="btn-group btn-group-sm td-chartbtns" style="flex:0 0 auto;">
                    <button type="button" class="btn btn-default" data-chart="pie">Pie</button>
                    <button type="button" class="btn btn-default" data-chart="bar">Bar</button>
                    <button type="button" class="btn btn-default" data-chart="none">Off</button>
                </div>
            </div>
            <div class="td-window"><small class="text-muted"></small></div>
            <div class="td-chartbox" style="height:170px;margin:4px 0;"><canvas class="td-canvas"></canvas></div>
            <table class="table table-condensed table-hover" style="margin-bottom:4px;">
                <thead><tr>
                    <th class="td-sort" data-key="name"  style="cursor:pointer;">Device</th>
                    <th class="td-sort" data-key="net"   style="cursor:pointer;">Network</th>
                    <th class="td-sort" data-key="total" style="cursor:pointer;text-align:right;">Traffic</th>
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
        this.tickTimeout = parseInt(cfg.refreshInterval, 10) || 300;

        $('.td-range', this.$widget || document).val(this.state.range);

        await this._loadNetworks();
        this._fillNetworkSelect();
        this._bind();
        await this.refresh();
    }

    _fillNetworkSelect() {
        const $sel = $('.td-network');
        $sel.empty().append('<option value="">All networks</option>');
        this.networks.forEach(n => {
            $sel.append(`<option value="${this._esc(n.key)}">${this._esc(n.label)}</option>`);
        });
        $sel.val(this.state.network);
    }

    _bind() {
        const self = this;
        $(document).off('.topdevices');

        $(document).on('change.topdevices', '.td-range', function () {
            self.state.range = $(this).val();
            self.state.selected = null;
            self.refresh();
        });
        $(document).on('change.topdevices', '.td-network', function () {
            self.state.network = $(this).val();
            self.render();
        });
        $(document).on('input.topdevices', '.td-search', function () {
            self.state.search = $(this).val().toLowerCase().trim();
            self.render();
        });
        $(document).on('click.topdevices', '.td-chartbtns button', function () {
            self.state.chart = $(this).data('chart');
            self.render();
        });
        $(document).on('click.topdevices', '.td-sort', function () {
            const key = $(this).data('key');
            if (self.state.sortKey === key) {
                self.state.sortDir = self.state.sortDir === 'asc' ? 'desc' : 'asc';
            } else {
                self.state.sortKey = key;
                self.state.sortDir = key === 'total' ? 'desc' : 'asc';
            }
            self.render();
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
        try {
            await this._loadNames();
            await this._loadTop();
            this.render();
            if (this.state.selected) this.renderDetails(this.state.selected);
        } catch (e) {
            $('.td-body').html('<tr><td colspan="3" class="text-danger">Unable to read NetFlow data</td></tr>');
        } finally {
            this.loading = false;
        }
    }

    async onWidgetTick() {
        await this.refresh();
    }

    async onWidgetOptionsChanged() {
        await this.onMarkupRendered();
    }

    /* ---------- render ---------- */

    _visibleRows() {
        const s = this.state;
        let rows = s.rows.slice();
        if (s.network) rows = rows.filter(r => r.net === s.network);
        if (s.search) {
            rows = rows.filter(r =>
                r.ip.toLowerCase().indexOf(s.search) !== -1 ||
                (r.name && r.name.toLowerCase().indexOf(s.search) !== -1));
        }
        const dir = s.sortDir === 'asc' ? 1 : -1;
        rows.sort((a, b) => {
            let av = a[s.sortKey], bv = b[s.sortKey];
            if (s.sortKey === 'name') { av = (a.name || a.ip); bv = (b.name || b.ip); }
            if (typeof av === 'string') return av.localeCompare(bv) * dir;
            return (av - bv) * dir;
        });
        return rows;
    }

    async render() {
        const cfg = await this.getWidgetConfig() || {};
        const limit = parseInt(cfg.rowsToShow, 10) || 10;
        const rows = this._visibleRows().slice(0, limit);

        if (this.state.window) {
            $('.td-window small').text(
                `${this._stamp(this.state.window[0])} → ${this._stamp(this.state.window[1])}`);
        }
        $('.td-chartbtns button').removeClass('btn-primary').addClass('btn-default');
        $(`.td-chartbtns button[data-chart="${this.state.chart}"]`).removeClass('btn-default').addClass('btn-primary');
        $('.td-sort').each((i, el) => {
            const k = $(el).data('key');
            $(el).find('.td-arrow').remove();
            if (k === this.state.sortKey) {
                $(el).append(`<span class="td-arrow"> ${this.state.sortDir === 'asc' ? '▲' : '▼'}</span>`);
            }
        });

        if (rows.length === 0) {
            $('.td-body').html('<tr><td colspan="3" class="text-muted">No matching devices</td></tr>');
        } else {
            $('.td-body').html(rows.map((r) => {
                const label = r.name
                    ? `<strong>${this._esc(r.name)}</strong><br/><small class="text-muted">${this._esc(r.ip)}</small>`
                    : `<strong>${this._esc(r.ip)}</strong>`;
                const net = this.networks.find(n => n.key === r.net);
                const sel = this.state.selected === r.ip ? ' class="info"' : '';
                return `<tr${sel} data-ip="${this._esc(r.ip)}" style="cursor:pointer;">
                    <td>${label}</td>
                    <td><small>${this._esc(net ? net.label : '')}</small></td>
                    <td style="text-align:right;">${this._fmt(r.total)}</td></tr>`;
            }).join(''));
        }
        this._renderChart(rows);
        if (!this.state.selected) $('.td-details').empty();
    }

    _renderChart(rows) {
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
        if (this.state.chart === 'none' || rows.length === 0) {
            $('.td-chartbox').hide();
            return;
        }
        $('.td-chartbox').show();
        const ctx = $('.td-canvas')[0];
        if (!ctx) return;
        const labels = rows.map(r => r.name || r.ip);
        const values = rows.map(r => r.total);
        const isPie = this.state.chart === 'pie';
        this.chartObj = new Chart(ctx.getContext('2d'), {
            type: isPie ? 'doughnut' : 'bar',
            data: {
                labels: labels,
                datasets: [{ data: values, borderWidth: isPie ? 0 : 1 }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: isPie, position: 'right', labels: { boxWidth: 10, font: { size: 10 } } },
                    tooltip: { callbacks: { label: (c) => `${c.label}: ${this._fmt(c.parsed.y ?? c.parsed)}` } }
                },
                scales: isPie ? {} : {
                    y: { ticks: { callback: (v) => this._fmt(v) } },
                    x: { ticks: { font: { size: 9 } } }
                }
            }
        });
    }

    async renderDetails(ip) {
        const $d = $('.td-details');
        $d.html('<small class="text-muted">Loading detail (large export, filtered in-browser)…</small>');
        let rows;
        try {
            rows = await this._loadDetail(this.state.range);
        } catch (e) {
            $d.html('<small class="text-danger">Detail export unavailable for this range</small>');
            return;
        }
        if (this.state.selected !== ip) return;   // selection changed while loading

        const peers = {}, ports = {};
        let sent = 0, recv = 0;
        // Count ONLY rows where the device is dst_addr. NetFlow records each flow
        // once per interface it crosses, with src/dst swapped between the two
        // observations, so matching "src OR dst" double-counts every byte and
        // makes the in/out split meaningless (both halves come out identical).
        // dst-keyed also matches how the leaderboard is built, so the detail
        // figures add up to the row total.
        rows.forEach((r) => {
            if (r.dst !== ip) return;
            const peer = r.src;
            if (!peer) return;
            peers[peer] = (peers[peer] || 0) + r.octets;
            const p = r.port && r.port !== '0' ? r.port : 'other';
            ports[p] = (ports[p] || 0) + r.octets;
            if (r.dir === 'out') sent += r.octets; else recv += r.octets;
        });

        const topOf = (o) => Object.entries(o).sort((a, b) => b[1] - a[1]).slice(0, 5);
        const name = this.names[ip] || ip;
        const list = (pairs, resolve) => pairs.length
            ? pairs.map(([k, v]) =>
                `<tr><td>${this._esc(resolve ? (this.names[k] || k) : k)}</td>
                     <td style="text-align:right;">${this._fmt(v)}</td></tr>`).join('')
            : '<tr><td colspan="2" class="text-muted">none</td></tr>';

        $d.html(`
            <div style="border-top:1px solid #ddd;padding-top:6px;">
                <strong>${this._esc(name)}</strong>
                <small class="text-muted">${this._esc(ip)}</small>
                <div><small>in ${this._fmt(recv)} &middot; out ${this._fmt(sent)}</small></div>
                <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:4px;">
                    <div style="flex:1 1 140px;">
                        <small class="text-muted">Top peers</small>
                        <table class="table table-condensed" style="margin:0;">${list(topOf(peers), true)}</table>
                    </div>
                    <div style="flex:1 1 100px;">
                        <small class="text-muted">Top ports</small>
                        <table class="table table-condensed" style="margin:0;">${list(topOf(ports), false)}</table>
                    </div>
                </div>
            </div>`);
    }

    onWidgetResize() {
        if (this.chartObj) this.chartObj.resize();
        return true;
    }

    onWidgetClose() {
        $(document).off('.topdevices');
        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }
    }
}
