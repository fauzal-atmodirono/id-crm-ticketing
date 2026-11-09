// One Playwright test per tracker-sheet row.
//
// Each spec calls `trackerModule(<tab name>, handlers)`. Rows come from the
// committed snapshot data/tracker.json (refresh: `npm run sync-tracker`), in
// sheet order, titled "<ID> <Test>" so `--grep CONV` runs one module and the
// publisher can map every result back to its row.
//
// A handler is either a function (automated) or `manual(why)`: a row that
// genuinely needs something the suite cannot do. Those are declared skipped
// with the reason "Needs manual: <why>" and reported as Needs manual, not Fail.
// A row with no handler at all fails loudly, so a new sheet row can never be
// silently left out.
import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import type { Api } from './api';
import type { TestData } from './data';
import { E2E_ROOT } from './env';
import { test, type Evidence } from './fixtures';

export interface TrackerRow {
  id: string;
  row: number;
  subModule: string;
  test: string;
  steps: string;
  dod: string;
  role: string;
  priority: string;
  knownIssue: string;
}

export interface Ctx {
  page: Page;
  api: Api;
  ev: Evidence;
  data: TestData;
  row: TrackerRow;
}

type Fn = (ctx: Ctx) => Promise<void>;
interface Manual {
  manual: string;
}
export type Handler = Fn | Manual;

export function manual(why: string): Manual {
  return { manual: why };
}

export function trackerRows(tab: string): TrackerRow[] {
  const file = path.join(E2E_ROOT, 'data', 'tracker.json');
  const snap = JSON.parse(fs.readFileSync(file, 'utf8')) as { tabs: { tab: string; rows: TrackerRow[] }[] };
  const found = snap.tabs.find(t => t.tab === tab);
  if (!found) throw new Error(`Tracker tab "${tab}" is not in data/tracker.json (run npm run sync-tracker).`);
  return found.rows;
}

const KEEPALIVE = 'proton_last_activity_ms';

/**
 * Chatwoot's idle monitor (patch 0079) signs out after 15 min without input,
 * judged by a localStorage stamp shared by every tab. Tests navigate and click
 * continuously, which already refreshes it; stamping it before each test also
 * covers long AI waits and manual-skip stretches.
 */
async function keepAlive(api: Api): Promise<void> {
  await api.page
    .evaluate(k => {
      try {
        localStorage.setItem(k, String(Date.now()));
      } catch {
        /* storage blocked */
      }
    }, KEEPALIVE)
    .catch(() => {});
}

export interface Extra {
  id: string;
  title: string;
  fn: Fn;
}

/**
 * @param extras tests for new features with no tracker row of their own
 *   (IDs like VOICE-X01). They run and are reported, but the publisher never
 *   writes them to the sheet.
 */
export function trackerModule(tab: string, handlers: Record<string, Handler>, extras: Extra[] = []): void {
  const rows = trackerRows(tab);
  const unknown = Object.keys(handlers).filter(id => !rows.some(r => r.id === id));
  if (unknown.length) throw new Error(`${tab}: handlers for IDs not in the tracker snapshot: ${unknown.join(', ')}`);

  test.describe(tab, () => {
    test.beforeEach(async ({ api }) => keepAlive(api));

    for (const row of rows) {
      const title = `${row.id} ${row.test}`;
      const annotation = [{ type: 'tracker', description: `${tab}!${row.row}` }];
      const h = handlers[row.id];
      if (!h) {
        test(title, { annotation }, async () => {
          throw new Error('No automation or manual reason written for this tracker row.');
        });
      } else if (typeof h !== 'function') {
        test(title, { annotation, tag: '@manual' }, async () => {
          test.skip(true, `Needs manual: ${h.manual}`);
        });
      } else {
        test(title, { annotation, tag: '@auto' }, async ({ page, api, ev, data }) => {
          await h({ page, api, ev, data, row });
        });
      }
    }

    for (const x of extras) {
      test(`${x.id} ${x.title}`, { annotation: [{ type: 'extra', description: tab }], tag: '@auto' }, async ({ page, api, ev, data }) => {
        await x.fn({ page, api, ev, data, row: { id: x.id, row: 0, subModule: 'new feature', test: x.title, steps: '', dod: '', role: '', priority: '', knownIssue: '' } });
      });
    }
  });
}
