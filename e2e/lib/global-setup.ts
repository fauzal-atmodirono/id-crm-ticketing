// Global setup: ONE headed, persistent Chromium for the whole run, and the
// hand-off where the person running the suite signs in.
//
// Why the browser is launched here and not in a worker fixture: Playwright
// throws away a worker (and anything it launched) after every failing test.
// A browser owned by a worker would die on the first failure, taking the
// basic-auth cache for the test console with it, and the person would be asked
// to type it again. Launched here, in the runner process, the browser lives
// for the whole run; each worker only CONNECTS to it over CDP (fixtures.ts),
// so a worker restart costs nothing and nobody has to log in twice.
//
// HARD RULE: this file never types a password, a TOTP code or any credential.
// It opens the pages and waits for the human to do it in the headed window.
import fs from 'node:fs';
import { chromium, type BrowserContext, type Page } from '@playwright/test';
import { Api } from './api';
import { leftoversUnknown, sweep } from './data';
import {
  BASE_URL,
  CDP_PORT,
  CONSOLE_URL,
  CONSOLE_WAIT_MS,
  LOGIN_WAIT_MS,
  PROFILE_DIR,
  RUN_ID,
  SCREENSHOT_DIR,
  appUrl,
} from './env';

const LOGGED_IN = /\/app\/accounts\/\d+\//;

async function waitForCrmLogin(page: Page): Promise<void> {
  const deadline = Date.now() + LOGIN_WAIT_MS;
  let said = false;
  while (Date.now() < deadline) {
    if (LOGGED_IN.test(page.url()) && (await page.locator('aside').first().isVisible().catch(() => false))) return;
    if (!said) {
      console.log('\nWaiting for you to log in… (sign in to the CRM in the browser window; up to 15 min)');
      said = true;
    }
    await page.waitForTimeout(2000);
  }
  throw new Error('Timed out after 15 min waiting for the CRM login.');
}

async function waitForConsole(page: Page): Promise<void> {
  const deadline = Date.now() + CONSOLE_WAIT_MS;
  console.log('Opening the test console… pass the browser’s sign-in prompt there (up to 10 min).');
  // The navigation stays pending while the native basic-auth prompt is up, so
  // do not await it; poll for the console's own UI instead.
  page.goto(CONSOLE_URL, { waitUntil: 'commit', timeout: CONSOLE_WAIT_MS }).catch(() => {});
  while (Date.now() < deadline) {
    if (await page.getByRole('tab', { name: 'Chat' }).isVisible().catch(() => false)) return;
    await page.waitForTimeout(2000);
    // A cancelled prompt leaves a 401 page: offer the prompt again.
    const body = (await page.locator('body').innerText().catch(() => '')) || '';
    if (/401|Unauthorized|Authorization Required/i.test(body)) {
      console.log('Test console says 401: reloading so the sign-in prompt shows again…');
      page.goto(CONSOLE_URL, { waitUntil: 'commit', timeout: CONSOLE_WAIT_MS }).catch(() => {});
      await page.waitForTimeout(5000);
    }
  }
  throw new Error('Timed out after 10 min waiting for the test console sign-in.');
}

export default async function globalSetup(): Promise<() => Promise<void>> {
  process.env.E2E_RUN_ID = RUN_ID;
  fs.rmSync(SCREENSHOT_DIR, { recursive: true, force: true });
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
  fs.mkdirSync(PROFILE_DIR, { recursive: true });

  const context: BrowserContext = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: null,
    acceptDownloads: true,
    args: [`--remote-debugging-port=${CDP_PORT}`, '--window-size=1500,950', '--autoplay-policy=no-user-gesture-required'],
  });
  process.env.E2E_CDP_URL = `http://127.0.0.1:${CDP_PORT}`;
  console.log(`[e2e] run ${RUN_ID} against ${BASE_URL} (profile ${PROFILE_DIR})`);

  const crm = context.pages()[0] ?? (await context.newPage());
  await crm.goto(`${BASE_URL}/app/login`, { waitUntil: 'domcontentloaded' });
  await waitForCrmLogin(crm);
  console.log('CRM login detected.');

  const consolePage = await context.newPage();
  await waitForConsole(consolePage);
  console.log('Test console reachable. Starting the tests in this same browser.\n');
  await consolePage.close();

  // Leave the CRM tab on the dashboard: it is the anchor that keeps the
  // browser window open between tests.
  await crm.goto(appUrl('dashboard'), { waitUntil: 'domcontentloaded' });

  return async () => {
    try {
      const api = new Api(await context.newPage());
      await api.ready();
      await sweep(api);
    } catch (e) {
      leftoversUnknown((e as Error).message);
    }
    await context.close();
  };
}
