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
];

let survivors = 0;
for (const [name, from, to] of mutants) {
    const count = source.split(from).length - 1;
    if (count !== 1) { console.log(`BROKEN MUTANT ${name}: anchor found ${count} times`); process.exit(2); }
    const dir = mkdtempSync(join(tmpdir(), 'tdmut-'));
    mkdirSync(join(dir, 'src/opnsense/www/js/widgets'), { recursive: true });
    mkdirSync(join(dir, 'tests'));
    writeFileSync(join(dir, widget), source.replace(from, to));
    copyFileSync(join(root, 'tests/live_view.test.mjs'), join(dir, 'tests/live_view.test.mjs'));
    // a mutant that leaves a timer running keeps node alive: a hang counts as caught
    const run = spawnSync(process.execPath, ['--test', 'tests/live_view.test.mjs'],
                          { cwd: dir, encoding: 'utf8', timeout: 20000 });
    const killed = run.status !== 0;
    console.log(`${killed ? 'killed  ' : 'SURVIVED'} ${name}${run.status === null ? ' (hung)' : ''}`);
    if (!killed) survivors++;
}
process.exit(survivors ? 1 : 0);
