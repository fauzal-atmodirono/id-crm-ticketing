// Write a run's results to the tracker sheet, columns K:M (Auto result, Auto
// evidence, Auto run at).
//
//   npm run publish                       # dry run: prints what it would write
//   npm run publish -- --write            # writes via `gws ... values batchUpdate`
//   npm run publish -- --label rc21 --report results/results.json
//   npm run publish -- --triage results/triage-run1.json   # failures → Retest/Fail/Blocked
//
// Dry run is the default on purpose: look at the table first. Rows are found
// by ID in each tab's live column A, never by position, and extra tests
// (VOICE-X..) are never written. Evidence is the run's own one-line summary;
// it never contains a credential (the suite never handles one).
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyTriage, buildWrites, mapResults, runAtLabel, type SheetWrite, type TriageEntry } from '../lib/results.ts';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SNAPSHOT = JSON.parse(fs.readFileSync(path.join(ROOT, 'data', 'tracker.json'), 'utf8')) as { spreadsheetId: string };

function arg(name: string, fallback: string): string {
  const i = process.argv.indexOf(name);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}

function gws(args: string[]): any {
  const out = execFileSync('gws', args, { encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  const start = out.indexOf('{'); // gws prints a "Using keyring" line first
  return start >= 0 ? JSON.parse(out.slice(start)) : {};
}

// Columns A (ID) and K (existing Auto result) of a tab, by sheet row (index 0 = row 1).
function columnsAK(spreadsheetId: string, tab: string): { a: string[]; k: string[] } {
  const params = JSON.stringify({ spreadsheetId, range: `'${tab.replace(/'/g, "''")}'!A1:K400` });
  const r = gws(['sheets', 'spreadsheets', 'values', 'get', '--params', params]);
  const rows: string[][] = r.values ?? [];
  return { a: rows.map(row => row[0] ?? ''), k: rows.map(row => row[10] ?? '') };
}

function main(): number {
  const write = process.argv.includes('--write');
  const reportPath = path.resolve(ROOT, arg('--report', 'results/results.json'));
  const label = arg('--label', 'rc20');
  const spreadsheetId = arg('--sheet', SNAPSHOT.spreadsheetId);
  if (!fs.existsSync(reportPath)) {
    console.error(`No report at ${reportPath}. Run the suite first (npm run e2e).`);
    return 1;
  }
  const report = JSON.parse(fs.readFileSync(reportPath, 'utf8'));
  const triagePath = arg('--triage', '');
  const triage: TriageEntry[] = triagePath ? JSON.parse(fs.readFileSync(path.resolve(ROOT, triagePath), 'utf8')) : [];
  const mapped = applyTriage(mapResults(report), triage);
  const runAt = runAtLabel(report.stats?.startTime ?? new Date().toISOString(), label);

  const tabs = [...new Set(mapped.filter(m => !m.extra).map(m => m.tab))];
  const writes: SheetWrite[] = [];
  const unmatched: string[] = [];
  // A "Needs manual" or "Retest" from this run says nothing new about a row that already
  // holds a result (e.g. the 9 Nov API-level re-test): keep that one.
  const kept: string[] = [];
  for (const tab of tabs) {
    const cols = columnsAK(spreadsheetId, tab);
    const r = buildWrites(tab, cols.a, mapped, runAt);
    for (const w of r.writes) {
      const row = Number(/K(\d+):/.exec(w.range)?.[1] ?? 0);
      const existing = (cols.k[row - 1] ?? '').trim();
      if ((w.values[0][0] === 'Needs manual' || w.values[0][0] === 'Retest') && existing && existing !== 'Needs manual') {
        kept.push(`${w.range} (${existing})`);
        continue;
      }
      writes.push(w);
    }
    unmatched.push(...r.unmatched.map(id => `${tab}:${id}`));
  }
  if (kept.length) console.log(`Kept ${kept.length} existing result(s) where this run only says Needs manual or Retest.`);

  const counts: Record<string, number> = {};
  for (const m of mapped.filter(x => !x.extra)) counts[m.result] = (counts[m.result] ?? 0) + 1;
  for (const w of writes) console.log(`${w.range.padEnd(34)} ${w.values[0][0].padEnd(13)} ${w.values[0][1].slice(0, 110)}`);
  for (const m of mapped.filter(x => x.extra)) console.log(`(extra, not written) ${m.id.padEnd(10)} ${m.result.padEnd(13)} ${m.evidence.slice(0, 110)}`);
  console.log(`\n${writes.length} row(s) to write, run at "${runAt}". ${Object.entries(counts).map(([k, v]) => `${k} ${v}`).join(', ')}`);
  if (unmatched.length) console.log(`IDs not found in the live sheet (skipped): ${unmatched.join(', ')}`);

  if (!write) {
    console.log('\nDry run: nothing written. Re-run with --write to update the sheet.');
    return 0;
  }
  // Sheets accepts many ranges per call; chunk to stay well under limits.
  for (let i = 0; i < writes.length; i += 100) {
    const body = { valueInputOption: 'RAW', data: writes.slice(i, i + 100) };
    gws(['sheets', 'spreadsheets', 'values', 'batchUpdate', '--params', JSON.stringify({ spreadsheetId }), '--json', JSON.stringify(body)]);
  }
  console.log(`Wrote ${writes.length} row(s) to the tracker sheet.`);
  return 0;
}

process.exit(main());
