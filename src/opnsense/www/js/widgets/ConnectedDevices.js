/*
 * ConnectedDevices.js - OPNsense dashboard widget (Top Devices plugin)
 *
 * The devices connected now, grouped by network, each with its host name, IP
 * address, MAC address and how long it has been connected this time; then the
 * devices seen in the last 7 days that are not connected now, with when each was
 * last seen.
 *
 * Nothing is worked out here. scripts/topdevices/connected.py merges the
 * firewall's ARP table and Host discovery, the Dnsmasq names and the WireGuard
 * handshakes, and keeps when each visit began; this only draws its answer, with
 * times in the firewall's zone like the Top Devices widget.
 * Design: docs/superpowers/specs/2026-10-27-connected-devices-widget-design.md
 */

/* whether the "Not connected" section is open, remembered in this browser */
const RECENT_KEY = 'topdevices.connected.recent';
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const FORMATS = new Map();                 // one formatter per zone; the browser's is made afresh

// Values land inside title="..." too, so quotes are escaped as well.
export function escapeHtml(s) {
    return (s === null || s === undefined ? '' : String(s)).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

// The firewall's zone if this browser knows it, else undefined: the browser's own.
export function zoneOf(zone) {
    if (typeof zone !== 'string' || zone === '') return undefined;
    try {
        new Intl.DateTimeFormat('en-US', { timeZone: zone });
        return zone;
    } catch (e) {
        return undefined;
    }
}

// [year, month 1-12, day, hours, minutes, weekday 0 = Sunday] of an instant (epoch seconds) in zone,
// the Top Devices widget's way: English parts on a 24-hour clock, whatever the browser's language
function wall(at, zone) {
    const make = () => new Intl.DateTimeFormat('en-US', { timeZone: zone, hourCycle: 'h23', weekday: 'short',
        year: 'numeric', month: 'numeric', day: 'numeric', hour: 'numeric', minute: 'numeric' });
    if (zone !== undefined && !FORMATS.has(zone)) FORMATS.set(zone, make());
    const p = {};
    (zone === undefined ? make() : FORMATS.get(zone)).formatToParts(new Date(at * 1000)).forEach((x) => { p[x.type] = x.value; });
    return [+p.year, +p.month, +p.day, +p.hour, +p.minute, WEEKDAYS.indexOf(p.weekday)];
}

const two = (n) => String(n).padStart(2, '0');

export function clockText(at, zone) {
    const [, , , h, mi] = wall(at, zone);
    return `${two(h)}:${two(mi)}`;
}

function dateText(at, zone) {
    const [y, mo, d] = wall(at, zone);
    return `${d} ${MONTHS[mo - 1]} ${y}`;
}

function dateTimeText(at, zone) {
    return `${WEEKDAYS[wall(at, zone)[5]]} ${dateText(at, zone)} ${clockText(at, zone)}`;
}

// "25 min ago" or "3 h ago" earlier the same day in zone, else the day and time: "Sat 09:29".
export function seenText(at, now, zone) {
    const [y, mo, d, , , dow] = wall(at, zone);
    const [ny, nmo, nd] = wall(now, zone);
    if (y === ny && mo === nmo && d === nd) {
        const minutes = Math.max(0, Math.floor((now - at) / 60));
        return minutes < 60 ? `${minutes} min ago` : `${Math.floor(minutes / 60)} h ago`;
    }
    return `${WEEKDAYS[dow]} ${clockText(at, zone)}`;
}

// A filled dot with how long this visit has lasted ("2 h 14 min"), or alone when that isn't
// known (there before tracking began, or tracking off), "Connected" in words for screen
// readers; a hollow dot and "Last seen …" for a device not connected now.
export function statusHtml(d, connected, now, zone) {
    const dot = (look) => `<i class="fa ${look}" style="font-size:11px;margin-right:4px;" aria-hidden="true"></i>`;
    if (!connected) return `${dot('fa-circle-o text-muted')} Last seen ${escapeHtml(seenText(d.last_seen, now, zone))}`;
    return `${dot('fa-circle text-success')}<span class="sr-only">Connected</span>${d.for ? ` ${escapeHtml(d.for)}` : ''}`;
}

// The exact times behind the status, then the vendor and when Host discovery first saw it.
export function statusTitle(d, vpn, connected, zone) {
    const lines = [];
    if (connected) {
        lines.push(d.since ? `Connected at ${dateTimeText(d.since, zone)}`
            : d.before ? `Connected since before ${dateTimeText(d.before, zone)} (when tracking began)` : 'Connected');
    } else {
        lines.push(`${vpn ? 'Latest handshake' : 'Last heard from'}: ${dateTimeText(d.last_seen, zone)}`);
    }
    if (d.vendor) lines.push(`Vendor: ${d.vendor}`);
    if (d.first_seen) lines.push(`First seen on the network: ${dateText(d.first_seen, zone)}`);
    return lines.join('\n');
}

export function summaryText(answer) {
    const connected = answer.groups.reduce((n, g) => n + g.devices.length, 0);
    const recent = (answer.recent || []).length;
    return `${connected} connected` + (recent ? ` · ${recent} seen this week` : '');
}

// The lines under the table, in this order: tracking; Host discovery; the ARP table; WireGuard.
export function noteTexts(answer) {
    const notes = [];
    const unread = answer.unread || [];
    const discovery = answer.discovery || { state: 'on', last: null };
    const arpOnly = 'devices come from the ARP table, without first-seen times, and the ones not connected can\'t be listed.';
    const idle = 'devices come from the ARP table, and last-seen times may be earlier than the devices really left.';
    if (!answer.tracking || !answer.tracking.active) {
        notes.push("How long devices have been connected comes from a job that runs every minute, and it hasn't run for over 3 minutes.");
    }
    if (unread.includes('hostdiscovery')) {
        notes.push(`Host discovery couldn't be read: ${arpOnly}`);
    } else if (discovery.state === 'off') {
        notes.push(`Host discovery is off: ${arpOnly}`);
    } else if (discovery.state === 'idle') {
        notes.push(discovery.last === null
            ? `Host discovery has heard nothing on these networks: ${idle}`
            : `Host discovery has heard nothing for ${Math.floor((answer.now - discovery.last) / 60)} min: ${idle}`);
    }
    if (unread.includes('arp')) notes.push("The ARP table couldn't be read: devices come from Host discovery alone.");
    if (unread.includes('wireguard')) notes.push("WireGuard's status couldn't be read: VPN clients aren't listed.");
    return notes;
}

// One device: its name, address and MAC, and its status, whose dots differ in shape so the
// state never rests on colour alone.
function rowHtml(d, vpn, connected, hidden, now, zone) {
    const clip = 'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;';
    const name = d.name !== '' ? escapeHtml(d.name) : '<i>(unnamed)</i>';
    const nameTitle = d.name !== '' ? escapeHtml(d.name) : 'No DHCP reservation and no name from DHCP';
    const mac = vpn ? '—' : escapeHtml(d.mac);
    const attrs = connected ? '' : ` class="cd-recent text-muted"${hidden ? ' style="display:none"' : ''}`;
    return `<tr${attrs}>`
        + `<td style="text-align:left;${clip}" title="${nameTitle}">${name}</td>`
        + `<td style="text-align:left;${clip}" title="${escapeHtml(d.ip)}">${escapeHtml(d.ip)}</td>`
        + `<td style="text-align:left;${clip}" title="${mac}">${mac}</td>`
        + `<td style="text-align:left;${clip}" title="${escapeHtml(statusTitle(d, vpn, connected, zone))}">${statusHtml(d, connected, now, zone)}</td></tr>`;
}

// The table's body: the connected groups, then the not-connected section, open or closed.
export function bodyHtml(answer, open, zone) {
    const groups = answer.groups.map((g) =>
        `<tr><td colspan="4" style="text-align:left;"><strong>${escapeHtml(g.name)} · ${g.devices.length} connected</strong></td></tr>`
        + g.devices.map((d) => rowHtml(d, g.vpn, true, false, answer.now, zone)).join('')).join('');
    const recent = answer.recent || [];
    const section = recent.length === 0 ? '' :
        `<tr class="cd-recent-head" style="cursor:pointer;"><td colspan="4" style="text-align:left;">`
        + `<span class="cd-recent-toggle" role="button" tabindex="0" aria-expanded="${open}">`
        + `<i class="fa ${open ? 'fa-chevron-down' : 'fa-chevron-right'} cd-chevron" aria-hidden="true" style="width:12px;"></i>`
        + `<strong>Not connected · seen in the last 7 days (${recent.length})</strong></span></td></tr>`
        + recent.map((d) => rowHtml(d, d.vpn, false, !open, answer.now, zone)).join('')
        + `<tr class="cd-recent"${open ? '' : ' style="display:none"'}><td colspan="4" class="text-muted" style="text-align:left;">`
        + '<small>Devices not seen for 7 days drop off the widget. They stay under Interfaces → Neighbors.</small></td></tr>';
    return (groups !== '' ? groups : '<tr><td colspan="4" class="text-muted">No devices connected</td></tr>') + section;
}

export default class ConnectedDevices extends BaseWidget {

    constructor(config) {
        super(config);
        this.tickTimeout = 60;
        try {
            this._recentOpen = localStorage.getItem(RECENT_KEY) === '1';
        } catch (e) {
            this._recentOpen = false;   // storage blocked: closed, as on a first visit
        }
    }

    getGridOptions() { return { sizeToContent: 650 }; }

    getMarkup() {
        const $markup = $(`
        <div class="cd-wrap" style="position:relative;">
            <div class="cd-loading" style="display:none;position:absolute;top:0;left:0;right:0;bottom:0;
                 background:rgba(127,127,127,0.12);z-index:20;align-items:center;justify-content:center;">
                <i class="fa fa-spinner fa-spin fa-2x" style="opacity:0.7;"></i>
            </div>
            <div class="cd-summary text-muted" style="margin:0 0 2px 5px;font-size:12px;"></div>
            <table class="table table-condensed table-hover" style="margin-bottom:4px;table-layout:fixed;width:100%;">
                <thead><tr>
                    <th style="text-align:left;width:34%;">Host name</th>
                    <th style="text-align:left;width:18%;">IP address</th>
                    <th style="text-align:left;width:21%;">MAC address</th>
                    <th style="text-align:left;width:27%;">Status</th>
                </tr></thead>
                <tbody class="cd-body"></tbody>
            </table>
            <div class="cd-msg"></div>
        </div>`);
        // the rows are redrawn on every refresh, so the section's toggle is listened for here
        $markup.on('click', '.cd-recent-head', () => this._toggleRecent());
        $markup.on('keydown', '.cd-recent-toggle', (e) => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                this._toggleRecent();
            }
        });
        return $markup;
    }

    _busy(on) {
        this._n = Math.max(0, (this._n || 0) + (on ? 1 : -1));
        if (this._n > 0) {
            if (!this._t) { this._t = setTimeout(() => { this._t = null; $('.cd-loading').css('display', 'flex'); }, 150); }
        } else {
            clearTimeout(this._t); this._t = null; $('.cd-loading').css('display', 'none');
        }
    }

    _fitHeight() {
        try {
            const item = $('.cd-wrap').closest('.grid-stack-item')[0];
            if (!item) return;
            const gridEl = item.closest('.grid-stack');
            const grid = gridEl && gridEl.gridstack;
            if (grid && typeof grid.resizeToContent === 'function') grid.resizeToContent(item);
        } catch (e) { /* not on a gridstack dashboard */ }
    }

    async onMarkupRendered() {
        this._installRefreshButton();
        await this.refresh();
    }

    async onWidgetTick() { await this.refresh(); }

    // The dashboard draws the header, so the refresh control is put in before its link
    // icon, where it hides with that icon while the dashboard is edited.
    _installRefreshButton() {
        const $item = $('.cd-wrap').closest('.grid-stack-item');
        if (!$item.length || $item.find('.cd-refresh').length) return;
        const $btn = $('<a href="#" class="cd-refresh" title="Refresh"><i class="fa fa-refresh"></i></a>')
            .css({ marginRight: '8px', cursor: 'pointer' })
            .on('click', (e) => { e.preventDefault(); this.refresh(); });
        const $link = $item.find('.link-handle a').first();
        if ($link.length) $link.before($btn);
        else $item.find('.widget-command-container').first().prepend($btn);
    }

    async refresh() {
        if (this._reading) return;  // a click during a read: that read is the fresh one
        this._reading = true;
        this._busy(true);
        try {
            this._render(await this.ajaxCall('/api/topdevices/connected/list'));
        } catch (e) {
            this._render(null);
        } finally { this._reading = false; this._busy(false); }
    }

    _render(data) {
        let notes;
        if (!data || data.status !== 'ok' || !Array.isArray(data.groups)) {
            // never an old list left standing as if it were current
            $('.cd-body').empty();
            $('.cd-summary').text('');
            notes = ['Cannot read the host list.'];
        } else {
            $('.cd-body').html(bodyHtml(data, this._recentOpen, zoneOf(data.zone)));
            $('.cd-summary').text(summaryText(data));
            notes = noteTexts(data);
        }
        $('.cd-msg').html(notes.map((n) => `<small class="text-muted">${escapeHtml(n)}</small>`).join('<br/>'));
        this._fitHeight();
    }

    _toggleRecent() {
        this._recentOpen = !this._recentOpen;
        try {
            localStorage.setItem(RECENT_KEY, this._recentOpen ? '1' : '0');
        } catch (e) { /* storage blocked: remembered until the page closes */ }
        this._showRecent();
        this._fitHeight();
    }

    // In place, so the toggle keeps the keyboard focus.
    _showRecent() {
        $('.cd-recent').css('display', this._recentOpen ? '' : 'none');
        $('.cd-recent-toggle').attr('aria-expanded', String(this._recentOpen));
        $('.cd-chevron').removeClass('fa-chevron-right fa-chevron-down')
            .addClass(this._recentOpen ? 'fa-chevron-down' : 'fa-chevron-right');
    }
}
