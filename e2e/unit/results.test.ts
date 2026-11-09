// Unit tests for the report -> sheet mapping (no browser, no network).
//   npm run test:unit
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { buildWrites, mapResults, oneLine, redact, runAtLabel } from '../lib/results.ts';

function spec(title: string, status: string, extra: { annotations?: any[]; error?: string } = {}) {
  return {
    title,
    tests: [
      {
        annotations: extra.annotations ?? [],
        status: status === 'passed' ? 'expected' : status === 'skipped' ? 'skipped' : 'unexpected',
        results: [{ status, error: extra.error ? { message: extra.error } : undefined, annotations: [] }],
      },
    ],
  };
}

const report = {
  stats: { startTime: '2026-11-09T17:30:00.000Z' },
  suites: [
    {
      title: '../lib/tracker.ts',
      specs: [],
      suites: [
        {
          title: 'Conversations',
          specs: [
            spec('CONV-001 Lists', 'passed', { annotations: [{ type: 'evidence', description: 'All: tab 12, rows 12' }] }),
            spec('CONV-005 WhatsApp', 'skipped', { annotations: [{ type: 'skip', description: 'Needs manual: a real WhatsApp handset' }] }),
            spec('CONV-027 Social', 'skipped', { annotations: [{ type: 'skip', description: 'Blocked: no Messenger inbox' }] }),
            spec('CONV-030 Summary', 'failed', { error: '\u001b[31mError: expect(received).toMatch(expected)\u001b[39m\n\nExpected pattern' }),
            spec('CONV-031 Bounce', 'interrupted'),
          ],
        },
        { title: 'Voice & Phone', specs: [spec('VOICE-X01 Sidebar player', 'passed')] },
      ],
    },
  ],
};

test('maps statuses to sheet results', () => {
  const m = mapResults(report);
  const by = Object.fromEntries(m.map(x => [x.id, x]));
  assert.equal(by['CONV-001'].result, 'Pass');
  assert.equal(by['CONV-001'].evidence, 'All: tab 12, rows 12');
  assert.equal(by['CONV-005'].result, 'Needs manual');
  assert.equal(by['CONV-005'].evidence, 'Needs a real WhatsApp handset');
  assert.equal(by['CONV-027'].result, 'Blocked');
  assert.equal(by['CONV-027'].evidence, 'no Messenger inbox');
  assert.equal(by['CONV-030'].result, 'Fail');
  assert.equal(by['CONV-030'].evidence, 'Error: expect(received).toMatch(expected)');
  assert.equal(by['CONV-031'], undefined, 'interrupted rows are not written');
  assert.equal(by['CONV-001'].tab, 'Conversations');
  assert.equal(by['VOICE-X01'].extra, true);
});

test('writes go to the row carrying the ID, never extras', () => {
  const m = mapResults(report);
  const colA = ['ID', 'CONV-005', 'CONV-001', '', 'CONV-030'];
  const { writes, unmatched } = buildWrites('Conversations', colA, m, 'X');
  assert.deepEqual(
    writes.map(w => [w.range, w.values[0][0]]),
    [
      ["'Conversations'!K3:M3", 'Pass'],
      ["'Conversations'!K2:M2", 'Needs manual'],
      ["'Conversations'!K5:M5", 'Fail'],
    ],
  );
  assert.deepEqual(unmatched, ['CONV-027']);
  assert.equal(buildWrites('Voice & Phone', ['ID', 'VOICE-X01'], m, 'X').writes.length, 0);
});

test('quotes tab names with apostrophes and ampersands', () => {
  const m = [{ id: 'A-1', tab: "Workforce & Bob's", title: 'A-1 t', result: 'Pass' as const, evidence: 'ok', extra: false }];
  assert.equal(buildWrites("Workforce & Bob's", ['ID', 'A-1'], m, 'X').writes[0].range, "'Workforce & Bob''s'!K2:M2");
});

test('run-at label is DD Mon YYYY in Malaysia time', () => {
  assert.equal(runAtLabel('2026-11-09T17:30:00.000Z'), '10 Nov 2026 (Playwright, rc20)');
  assert.equal(runAtLabel('2026-11-09T01:00:00.000Z', 'rc21'), '09 Nov 2026 (Playwright, rc21)');
});

test('annotations repeated at test and result level are not doubled', () => {
  const r = { suites: [{ title: 'f', suites: [{ title: 'T', specs: [{ title: 'A-1 x', tests: [{ annotations: [{ type: 'evidence', description: 'e' }], results: [{ status: 'passed', annotations: [{ type: 'evidence', description: 'e' }] }] }] }] }] }] };
  assert.equal(mapResults(r)[0].evidence, 'e');
});

test('evidence is one line and capped', () => {
  assert.equal(oneLine('a\n  b\tc'), 'a b c');
  assert.equal(oneLine('x'.repeat(500)).length, 450);
});

test('personal data is masked, ordinary numbers are not', () => {
  assert.equal(redact('call +60 12-345 6789 or e2e.x@example.com'), 'call [phone] or [email]');
  assert.equal(redact('plate DMO 1105 and WXY1234A'), 'plate [plate] and [plate]');
  assert.equal(redact('HTTP 422 on 2026-11-09, 113 of 113, X50 in 1200 ms'), 'HTTP 422 on 2026-11-09, 113 of 113, X50 in 1200 ms');
  assert.equal(mapResults({ suites: [{ title: 'f', suites: [{ title: 'T', specs: [{ title: 'A-1 x', tests: [{ results: [{ status: 'failed', error: { message: 'Error: 60000000101 not found' } }] }] }] }] }] })[0].evidence, 'Error: [phone] not found');
});
