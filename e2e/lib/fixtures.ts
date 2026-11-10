// Test fixtures: the shared logged-in browser, the in-browser API, [TEST] data
// and the per-row evidence recorder.
//
// `crm` (worker scope) connects to the single headed browser global setup
// launched, so every test runs in the SAME signed-in context (CRM session and
// the test console's basic-auth cache). `page` is a fresh tab in that context
// per test, closed afterwards. `api` is a long-lived CRM tab used only for
// in-browser API calls and the idle keep-alive.
import fs from 'node:fs';
import path from 'node:path';
import { chromium, test as base, type Browser, type BrowserContext, type Locator, type Page } from '@playwright/test';
import { Api } from './api';
import { TestData, leftoversUnknown, sweep } from './data';
import { SCREENSHOT_DIR, appUrl } from './env';
import { redact } from './results';

export class Evidence {
  readonly lines: string[] = [];
  shotTaken = false;

  constructor(readonly id: string) {}

  add(line: string): void {
    // One line per fact; no secrets, and personal data is masked here too.
    this.lines.push(redact(line).replace(/\s+/g, ' ').trim());
  }

  summary(): string {
    return this.lines.join('; ');
  }

  /** Save the key screen for this row as results/screenshots/<ID>.png. */
  async shot(target: Page | Locator, opts: { fullPage?: boolean } = {}): Promise<void> {
    fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
    const file = path.join(SCREENSHOT_DIR, `${this.id}.png`);
    if ('goto' in target) await target.screenshot({ path: file, fullPage: opts.fullPage ?? false });
    else await target.screenshot({ path: file });
    this.shotTaken = true;
    await base.info().attach(`${this.id}.png`, { path: file, contentType: 'image/png' });
  }

  /**
   * The automatable part of the row is done and passed; what is left needs a
   * person (a handset, a mailbox, a second login, a long wait). Reported as
   * "Needs manual" with what was automated, never as Pass.
   */
  handOff(why: string): never {
    base.skip(true, `Needs manual: ${why}${this.lines.length ? `. Automated part OK: ${this.summary()}` : ''}`);
    throw new Error('unreachable');
  }

  /** A precondition outside the suite's control is missing (reported Blocked). */
  blocked(why: string): never {
    base.skip(true, `Blocked: ${why}`);
    throw new Error('unreachable');
  }
}

type WorkerFixtures = {
  crm: { browser: Browser; context: BrowserContext };
  api: Api;
  data: TestData;
};

type TestFixtures = {
  ev: Evidence;
  page: Page;
};

export const test = base.extend<TestFixtures, WorkerFixtures>({
  crm: [
    async ({}, use) => {
      const url = process.env.E2E_CDP_URL;
      if (!url) throw new Error('E2E_CDP_URL is not set: run the suite with `npm run e2e` (global setup launches the browser).');
      const browser = await chromium.connectOverCDP(url);
      const context = browser.contexts()[0];
      if (!context) throw new Error('The shared browser has no context.');
      await use({ browser, context });
      // Do not close: that would end the shared browser. The CDP connection
      // drops with the worker process.
    },
    { scope: 'worker' },
  ],

  api: [
    async ({ crm }, use) => {
      const page = await crm.context.newPage();
      await page.goto(appUrl('dashboard'), { waitUntil: 'domcontentloaded' });
      const api = new Api(page);
      await api.ready();
      await use(api);
      await page.close().catch(() => {});
    },
    { scope: 'worker' },
  ],

  data: [
    async ({ api }, use) => {
      await use(new TestData(api));
      try {
        await api.ready();
        await sweep(api);
      } catch (e) {
        leftoversUnknown((e as Error).message);
      }
    },
    { scope: 'worker' },
  ],

  ev: async ({}, use, testInfo) => {
    const ev = new Evidence(testInfo.title.split(' ')[0]);
    await use(ev);
    if (ev.lines.length) testInfo.annotations.push({ type: 'evidence', description: ev.summary().slice(0, 1500) });
  },

  page: async ({ crm, ev }, use) => {
    const page = await crm.context.newPage();
    await use(page);
    // Every row gets a screenshot, even a failing one: the screen as it was.
    if (!ev.shotTaken) await ev.shot(page).catch(() => {});
    await page.close().catch(() => {});
  },
});

export { expect } from '@playwright/test';
