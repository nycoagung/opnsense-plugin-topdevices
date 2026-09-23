// Prove the widget tests catch the lifecycle mistakes that matter.
// Run:  node tests/mutate_widget.mjs      (exit status 1 if any mutant survives)
import { readFileSync, writeFileSync, mkdtempSync, copyFileSync, mkdirSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const widget = 'src/opnsense/www/js/widgets/TopDevices.js';
const source = readFileSync(join(root, widget), 'utf8');
const TESTS = ['tests/live_view.test.mjs', 'tests/netflow_ranges.test.mjs'];

const mutants = [
    ['stream URL not forgotten when leaving Live', '        this.eventSourceUrl = null;\n        this.eventSourceOnData = null;\n', ''],
    ['no guard against a range change during start-up', "        if (token !== this.live.token || this.state.range !== 'live') return;\n", ''],
    ['watchdog not cleared', '        if (this.live.watchdog) { clearInterval(this.live.watchdog); this.live.watchdog = null; }\n', ''],
    ['fixed timeouts instead of scaling with the interval', "    const eff = Math.max(1, effectiveS || 1) * 1000;", '    const eff = 1000;'],
    ['no automatic retry once unavailable', '            if (now - this.live.retryAt >= LIVE_RETRY_MS) this._startLive();\n', ''],
    ['a stale connect timer may reopen the stream outside Live', "        if (this.live.closed || this.state.range !== 'live') return;\n", ''],
    ['baseline events averaged in', '    if (!event || !(event.dt > 0)) return v;', '    if (!event) return v;'],
    ['picked devices vanish while idle', '        const ips = pick.length ? pick\n',
     '        const ips = pick.length ? Object.keys(rates)\n'],
    ['with nothing picked, Live is not seeded busiest first',
     '    return rows.sort((a, b) => (b.total - a.total) || ((seen[b.ip] || 0) - (seen[a.ip] || 0)) || byLabel(a, b));',
     '    return rows.sort(byLabel);'],
    ['filters ignored for picked devices', '    if (picked) return rows.sort(byLabel);',
     '    if (picked) return candidates.slice().sort(byLabel);'],
    ['a header click still sorts Live', "        if (this.state.range === 'live') return false;\n", ''],
    ['saved picks restored uncleaned', '        this.state.livePick = cleanPick(this.state.livePick);\n', ''],
    ['the picker offers the firewall itself', '            if (gateways[ip] || this._isBroadcast(ip)) continue;',
     '            if (this._isBroadcast(ip)) continue;'],
    ['a pick not seen today drops out of the picker', '...this.live.known, ...pick])) {', '...this.live.known])) {'],
    ['a renamed device is patched, not rebuilt', "    return rows.map(r => `${r.ip}|${r.name}|${r.net}`).join('\\n');",
     "    return rows.map(r => `${r.ip}|${r.net}`).join('\\n');"],
    ['every row re-sorted each update', '    if (!prev) {\n        order = ips.slice();', '    if (true) {\n        order = ips.slice();'],
    ['a selected device pushed off the bottom', '    if (n > 0 && at >= n) { order.splice(at, 1); order.splice(n - 1, 0, keep); }\n', ''],
    ['a new busiest does not push the bottom row off', '    return order.slice(0, n);\n}', '    return order;\n}'],
    ['a short list is not topped up', '        if (!order.includes(ip)) order.push(ip);', '        if (!order.includes(ip)) continue;'],
    ['the pointer does not hold the rows', 'if (!hold && top && top.total > 0 && order[0] !== top.ip) {',
     'if (top && top.total > 0 && order[0] !== top.ip) {'],
    ['an idle device moves to the top', 'if (!hold && top && top.total > 0 && order[0] !== top.ip) {',
     'if (!hold && top && order[0] !== top.ip) {'],
    ['the Live bar axis shrinks', '            this.live.yMax = Math.max(this.live.yMax, niceCeil(',
     '            this.live.yMax = Math.max(0, niceCeil('],
    ['a restart keeps the old axis top', '        this.live.yMax = 0;\n        this.live.dynOrder = null;\n',
     '        this.live.dynOrder = null;\n'],
    ['the axis top is not rounded', '    if (!(v > 0)) return 0;', '    return v;'],
    ['line colours follow the row, not the device',
     '            const c = l.colours[ip];\n            if (c && !taken.has(c)) { out[ip] = c; taken.add(c); }\n', ''],
    ['listed devices can share a line colour', 'LINE_COLOURS.find(c => !taken.has(c)) || LINE_COLOURS[0]',
     'LINE_COLOURS[Object.keys(l.colours).length % LINE_COLOURS.length]'],
    ['a line that just joined gets a quiet update', "this.chartObj.update(joined ? 'none' : 'quiet');",
     "this.chartObj.update('quiet');"],
    ["Dnsmasq's '*' shown as a name", " && l.hostname !== '*')", ')'],
    ['Down and Up swapped when patching a row', '                $td.eq(2).text(fmtRate(r.down));\n                $td.eq(3).text(fmtRate(r.up));\n',
     '                $td.eq(2).text(fmtRate(r.up));\n                $td.eq(3).text(fmtRate(r.down));\n'],
    ['rows rebuilt every interval instead of patched', 'if (rows.length && key === l.rowsKey && onScreen',
     'if (false && rows.length && key === l.rowsKey && onScreen'],
    ['the highlight not moved when patching', "                $(tr).toggleClass('info', this.state.selected === r.ip);\n", ''],
    ['a stale visit sizes the line axis', '                while (s.length && s[0].x < cutoff) s.shift();\n', ''],
    ['the line graph survives leaving Live', '        if (this.chartObj) { this.chartObj.destroy(); this.chartObj = null; }\n        $(\'.td-chartbox\').hide();\n',
     "        $('.td-chartbox').hide();\n"],
    ['a seen device on an unknown network cannot be picked', '            else if (pick.includes(ip) || this.live.known.has(ip)) other.devices.push(d);',
     '            else if (pick.includes(ip)) other.devices.push(d);'],
    ['switching the Live chart keeps the old axis top', '            if (kind !== this.state.liveChart) this.live.yMax = 0;', ''],
    ['the list seeded before the first measured interval', '            if (measured) l.dynOrder = order;\n',
     '            l.dynOrder = order;\n'],
    ['an idle device gets no line point',
     '        const ips = new Set([...Object.keys(devices), ...l.known, ...(this.state.livePick || []), ...l.order]);',
     '        const ips = new Set(Object.keys(devices));'],
    ["Live's chart choice lands on the NetFlow ranges", '            this.state.liveChart = kind;', '            this.state.chart = kind;'],
    ['three failed connects keep a shown tab from reconnecting',
     '        if (visible && this.eventSourceUrl !== null) this.eventSourceRetryCount = 0;\n', ''],
    ['quotes left unescaped in title attributes', ".html().replace(/\"/g, '&quot;'); }", '.html(); }'],
    ['the line graph offered outside Live', "            if (kind === 'line') return false;\n", ''],
    ['no stand-in without the streaming plugin', "        return kind === 'line' && !this._streamingOk() ? 'bar' : kind;",
     '        return kind;'],
    // NetFlow ranges
    ['all traffic read from the daily details, as before 0.1.2',
     '    for (const [res, kept] of TOTALS_KEPT) {', '    return detailsPlan(from, to, now);\n    for (const [res, kept] of TOTALS_KEPT) {'],
    ['no 5-minute or hourly buckets', 'const TOTALS_KEPT = [[300, 3600], [3600, 86400], [86400, Infinity]];',
     'const TOTALS_KEPT = [[86400, Infinity]];'],
    ['buckets used past their retention', '        if (start >= Math.floor(now / res) * res - kept) return',
     '        if (true) return'],
    ['internet only read from the totals, which have no interface',
     "    if (scope === 'wan') return detailsPlan(from, to, now);\n", ''],
    ['window start rounded down, as the export does', '    const near = (t) => Math.round(t / res) * res;',
     '    const near = (t) => Math.floor(t / res) * res;'],
    ['a bucket still in progress claimed to its end', '    let end = to >= now ? now : Math.min(near(to), now);',
     '    let end = to >= now ? now : near(to);'],
    ['totals rows read with the details convention', "        const sent = totals ? r.dir !== 'out' : r.dir === 'out';",
     "        const sent = r.dir === 'out';"],
    ['totals rows keyed on a destination they do not have', '        const ip = totals ? r.src : r.dst;',
     '        const ip = r.dst;'],
    ['the caption shows the window asked for', '        this.state.window = [plan.start, plan.end];',
     '        this.state.window = [from, to];'],
    ['no note when the buckets are coarser than the range',
     '        if (p.res === 86400 && (Math.abs(p.start - q.from) >= 3600 || Math.abs(p.end - q.to) >= 3600)) {',
     '        if (false) {'],
    ["the panel shows the day's totals, not the row's", '        if (row) { down = row.down; up = row.up; }\n', ''],
    ['the caption follows the scope control rather than the rows shown',
     "        const scopeTxt = q.scope === 'wan'", "        const scopeTxt = this.state.scope === 'wan'"],
    ['the panel never says its lists cover more', "        const note = same ? null : `Peers and ports cover",
     "        const note = true ? null : `Peers and ports cover"],
];

let survivors = 0;
for (const [name, from, to] of mutants) {
    const count = source.split(from).length - 1;
    if (count !== 1) { console.log(`BROKEN MUTANT ${name}: anchor found ${count} times`); process.exit(2); }
    const dir = mkdtempSync(join(tmpdir(), 'tdmut-'));
    mkdirSync(join(dir, 'src/opnsense/www/js/widgets'), { recursive: true });
    mkdirSync(join(dir, 'tests'));
    writeFileSync(join(dir, widget), source.replace(from, to));
    for (const t of TESTS) copyFileSync(join(root, t), join(dir, t));
    // a mutant that leaves a timer running keeps node alive: a hang counts as caught
    const run = spawnSync(process.execPath, ['--test', ...TESTS],
                          { cwd: dir, encoding: 'utf8', timeout: 20000 });
    const killed = run.status !== 0;
    console.log(`${killed ? 'killed  ' : 'SURVIVED'} ${name}${run.status === null ? ' (hung)' : ''}`);
    if (!killed) survivors++;
}
process.exit(survivors ? 1 : 0);
