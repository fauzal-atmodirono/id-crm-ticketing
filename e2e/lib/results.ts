// Pure mapping from a Playwright JSON report to tracker-sheet results.
// No imports, so `node --test unit/*.test.ts` runs it without Playwright.
//
//   passed                          -> Pass
//   skipped "Needs manual: <why>"   -> Needs manual
//   skipped "Blocked: <why>"        -> Blocked
//   failed / timedOut               -> Fail (first line of the error)
//   interrupted / not run           -> not written
//
// Extra tests for new features (IDs like VOICE-X01) are kept apart: they have
// no sheet row.

// 'Retest' only comes from a triage file (publish --triage): the row failed
// because of the test script, the script is fixed, and a rerun is owed.
export type AutoResult = 'Pass' | 'Fail' | 'Needs manual' | 'Blocked' | 'Retest';

export interface TriageEntry {
  id: string;
  class: 'A' | 'B' | 'C' | 'D';
  summary: string;
}

/** Apply a failure triage to mapped results: A (test bug, fixed) and D
 * (unsure) become Retest, B (product finding) stays Fail with the finding as
 * evidence, C becomes Blocked. Only rows that FAILED in this run change. */
export function applyTriage(mapped: Mapped[], triage: TriageEntry[]): Mapped[] {
  const byId = new Map(triage.map(t => [t.id, t]));
  return mapped.map(m => {
    const t = byId.get(m.id);
    if (!t || m.result !== 'Fail') return m;
    const s = oneLine(t.summary);
    if (t.class === 'B') return { ...m, result: 'Fail', evidence: s };
    if (t.class === 'C') return { ...m, result: 'Blocked', evidence: s };
    const why = t.class === 'A' ? 'Test script fixed after this run; rerun owed' : 'Unclear; rerun owed';
    return { ...m, result: 'Retest', evidence: oneLine(`${why}: ${t.summary}`) };
  });
}

export interface Mapped {
  id: string;
  tab: string;
  title: string;
  result: AutoResult;
  evidence: string;
  extra: boolean;
}

interface Annotation {
  type: string;
  description?: string;
}

const ANSI = /\u001b\[[0-9;]*m/g;
export const EVIDENCE_MAX = 450;

// Personal data never goes into evidence or the sheet: emails, phone numbers
// (8+ digits, optional +, spaces or dashes; ISO dates are kept) and Malaysian-style vehicle plates
// ("DMO 1105", "WXY1234A") are masked. Customer names are kept out at source
// (specs never put them in evidence).
const EMAIL = /[\w.+-]+@[\w-]+(\.[\w-]+)+/g;
const PHONE = /(?<![\w-])(?!\d{4}-\d{2}-\d{2})\+?\d(?:[\s-]?\d){7,}(?![\w-])/g; // 8+ digits, not an ISO date
const PLATE = /\b(?!HTTP|API|SLA|CSV|PDF|PIC|NPS|AHT|ACW|KB|AI|UI|X\d{2}\b)[A-Z]{1,3} ?\d{1,4}[A-Z]?\b/g;

export function redact(s: string): string {
  return s.replace(EMAIL, '[email]').replace(PHONE, '[phone]').replace(PLATE, '[plate]');
}

export function oneLine(s: string, max = EVIDENCE_MAX): string {
  const t = redact(s.replace(ANSI, '')).replace(/\s+/g, ' ').trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

export function isExtraId(id: string): boolean {
  return /-X\d+$/.test(id);
}

/** Test- and result-level annotations (the JSON report repeats them), deduped. */
function annotationsOf(test: any): Annotation[] {
  const all: Annotation[] = [...(test.annotations ?? [])];
  for (const r of test.results ?? []) all.push(...(r.annotations ?? []));
  const seen = new Set<string>();
  return all.filter(a => {
    const k = `${a.type}\u0000${a.description ?? ''}`;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}

function mapOne(spec: any, tab: string): Mapped | null {
  const test = spec.tests?.[0];
  if (!test) return null;
  const last = (test.results ?? [])[test.results.length - 1];
  const status: string = last?.status ?? (test.status === 'skipped' ? 'skipped' : 'unknown');
  const anns = annotationsOf(test);
  const evidence = anns.filter(a => a.type === 'evidence').map(a => a.description ?? '').join('; ');
  const id = String(spec.title).split(' ')[0];
  const base = { id, tab, title: spec.title as string, extra: isExtraId(id) };

  if (status === 'passed') return { ...base, result: 'Pass', evidence: oneLine(evidence || 'Passed') };
  if (status === 'skipped') {
    const reason = anns.filter(a => a.type === 'skip').map(a => a.description ?? '').find(Boolean) ?? '';
    if (/^Blocked:/i.test(reason)) return { ...base, result: 'Blocked', evidence: oneLine(reason.replace(/^Blocked:\s*/i, '')) };
    return { ...base, result: 'Needs manual', evidence: oneLine(reason.replace(/^Needs manual:\s*/i, 'Needs ') || 'Needs a manual check') };
  }
  if (status === 'failed' || status === 'timedOut') {
    const err = String(last?.error?.message ?? last?.errors?.[0]?.message ?? status).split('\n').find(l => l.trim()) ?? status;
    const msg = status === 'timedOut' ? `Timed out. ${err}` : err;
    return { ...base, result: 'Fail', evidence: oneLine(`${msg}${evidence ? ` | ${evidence}` : ''}`) };
  }
  return null; // interrupted or never ran: leave the sheet alone
}

/** Walk the JSON report: file suite -> describe (tracker tab) -> specs. */
export function mapResults(report: any): Mapped[] {
  const out: Mapped[] = [];
  const walk = (suite: any, tab: string) => {
    for (const spec of suite.specs ?? []) {
      const m = mapOne(spec, tab);
      if (m) out.push(m);
    }
    for (const child of suite.suites ?? []) walk(child, child.title ?? tab);
  };
  for (const s of report.suites ?? []) walk(s, s.title);
  return out;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "DD Mon YYYY (Playwright, rc20)" in Malaysia time (the tenant's zone). */
export function runAtLabel(startIso: string, label = 'rc20'): string {
  const d = new Date(new Date(startIso).getTime() + 8 * 3600_000); // UTC+8, no DST
  const dd = String(d.getUTCDate()).padStart(2, '0');
  return `${dd} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} (Playwright, ${label})`;
}

export interface SheetWrite {
  range: string;
  values: string[][];
}

/**
 * Build K:M writes for one tab, locating each ID in the tab's LIVE column A
 * (rows may have moved since the snapshot), so a result can only land on the
 * row that carries its own ID.
 */
export function buildWrites(tab: string, columnA: string[], mapped: Mapped[], runAt: string): { writes: SheetWrite[]; unmatched: string[] } {
  const writes: SheetWrite[] = [];
  const unmatched: string[] = [];
  for (const m of mapped.filter(x => x.tab === tab && !x.extra)) {
    const idx = columnA.findIndex(v => String(v ?? '').trim() === m.id);
    if (idx < 0) {
      unmatched.push(m.id);
      continue;
    }
    const row = idx + 1;
    const quoted = `'${tab.replace(/'/g, "''")}'`;
    writes.push({ range: `${quoted}!K${row}:M${row}`, values: [[m.result, m.evidence, runAt]] });
  }
  return { writes, unmatched };
}
