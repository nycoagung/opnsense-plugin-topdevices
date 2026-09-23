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
    ['picked devices vanish while idle', '        this.state.rows = (pick.length ? pick : Object.keys(rates)).map((ip) => {',
     '        this.state.rows = Object.keys(rates).map((ip) => {'],
    ['with nothing picked, Live is not busiest first', '    rows.sort((a, b) => (b.total - a.total) || byLabel(a, b));',
     '    rows.sort(byLabel);'],
    ['filters ignored for picked devices', '    if (picked) return rows.sort(byLabel);',
     '    if (picked) return candidates.slice().sort(byLabel);'],
    ['a header click still sorts Live', "        if (this.state.range === 'live') return false;\n", ''],
    ['saved picks restored uncleaned', '        this.state.livePick = cleanPick(this.state.livePick);\n', ''],
    ['the picker offers the firewall itself', '            if (gateways[ip] || this._isBroadcast(ip)) continue;',
     '            if (this._isBroadcast(ip)) continue;'],
    ['a pick not seen today drops out of the picker', '...this.live.known, ...pick])) {', '...this.live.known])) {'],
    ['a renamed device is patched, not rebuilt', "    return rows.map(r => `${r.ip}|${r.name}|${r.net}`).join('\\n');",
     "    return rows.map(r => `${r.ip}|${r.net}`).join('\\n');"],
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
