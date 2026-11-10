// UI helpers shared by the specs. Selectors come from the Chatwoot fork
// patches (deploy/chatwoot-fork/patches) and the test console source
// (backend/apps/frontend); they prefer visible text and roles over classes.
import { expect, type Page } from '@playwright/test';
import { CONSOLE_URL, appUrl } from './env';

const LOADING = /^\s*(Loading…|Loading\.\.\.)\s*$/;
export const ERROR_TEXT = /Could not load|Something went wrong|not authori[sz]ed|You do not have permission|Permission denied|Internal Server Error|Failed to (load|fetch)/i;

/** Navigate inside the CRM and wait for the sidebar (= signed in, SPA booted). */
export async function openApp(page: Page, p: string): Promise<void> {
  await page.goto(appUrl(p), { waitUntil: 'domcontentloaded' });
  await expect(page.locator('aside').first(), 'CRM sidebar (still signed in?)').toBeVisible({ timeout: 45_000 });
  await settle(page);
}

/** Wait until no "Loading…" placeholder is visible (max 30 s). */
export async function settle(page: Page, timeout = 30_000): Promise<void> {
  await page.waitForLoadState('load').catch(() => {});
  const deadline = Date.now() + timeout;
  await page.waitForTimeout(600);
  while (Date.now() < deadline) {
    const loading = await page.getByText(LOADING).first().isVisible().catch(() => false);
    if (!loading) return;
    await page.waitForTimeout(500);
  }
}

export async function bodyText(page: Page): Promise<string> {
  return (await page.locator('body').innerText().catch(() => '')) || '';
}

/** The page shows no load/permission error banner. */
export async function expectNoError(page: Page): Promise<void> {
  const text = await bodyText(page);
  const m = text.match(ERROR_TEXT);
  expect(m ? `error shown: "${m[0]}"` : 'no error', 'page error text').toBe('no error');
}

export async function expectHeading(page: Page, name: RegExp | string): Promise<void> {
  await expect(page.getByRole('heading', { name }).first()).toBeVisible({ timeout: 30_000 });
}

/** Open one conversation and wait for its reply box. */
export async function openConversation(page: Page, id: number): Promise<void> {
  await openApp(page, `conversations/${id}`);
  await expect(page.locator('.ProseMirror, [contenteditable="true"]').first(), 'reply box').toBeVisible({ timeout: 45_000 });
}

/**
 * Open the conversation's contact panel if it is closed. Whether it is open is
 * a per-user UI setting (is_contact_sidebar_open), so it can be closed from an
 * earlier session; the round "contact" switch (i-ph-user-bold) only ever opens
 * it, never toggles it shut.
 */
export async function openContactPanel(page: Page): Promise<void> {
  const panel = page.getByText(/Conversation Actions/i).first();
  if (await panel.isVisible({ timeout: 3000 }).catch(() => false)) return;
  await page.locator('button:has(.i-ph-user-bold)').first().click();
  await expect(panel, 'contact panel open').toBeVisible({ timeout: 15_000 });
}

/**
 * Make a value in the conversation's right panel visible: its accordion
 * ("Conversation Information" etc.) may be collapsed for this user.
 */
export async function revealInPanel(page: Page, value: string | RegExp, section = /Conversation Information/i): Promise<void> {
  const target = page.getByText(value).first();
  if (await target.isVisible({ timeout: 3000 }).catch(() => false)) return;
  await openContactPanel(page);
  const header = page.getByText(section).first();
  if (await header.isVisible().catch(() => false)) await header.click();
  await expect(target).toBeVisible({ timeout: 20_000 });
}

/** Sidebar entry by its visible label. */
export function sidebarItem(page: Page, label: string | RegExp) {
  // Visible only: sub-items of collapsed groups (e.g. Knowledge > Settings)
  // carry the same label but are hidden, and must not shadow the top-level one.
  return page.locator('aside').first().getByText(label, { exact: typeof label === 'string' }).filter({ visible: true }).first();
}

/**
 * Table column header by its text. Plain <th> without scope="col" has the
 * ARIA role "cell" in Playwright, not "columnheader", so match the element.
 */
export function columnHeader(page: Page, name: string) {
  return page.locator('th').filter({ hasText: new RegExp(`^\\s*${name}\\s*$`) }).first();
}

/**
 * Composer: type "/<short code>" and pick that canned response from the
 * mention box. The box first lists every canned response and only narrows
 * after its search request returns, so pressing Enter early inserts whichever
 * response happens to be first; click the matching item instead.
 */
export async function insertCanned(page: Page, code: string): Promise<void> {
  await page.keyboard.type(`/${code}`);
  const item = page.locator('.mention--box button').filter({ hasText: code }).first();
  await expect(item, `canned response "${code}" offered after /`).toBeVisible({ timeout: 15_000 });
  await item.click();
}

/** Seconds between two Date.now() values, 1 decimal. */
export function secs(fromMs: number, toMs = Date.now()): string {
  return ((toMs - fromMs) / 1000).toFixed(1);
}

// ---- Test console (backend/apps/frontend) ----------------------------------

export async function openConsole(page: Page): Promise<void> {
  await page.goto(CONSOLE_URL, { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('tab', { name: 'Chat' }), 'test console (basic auth cached?)').toBeVisible({ timeout: 30_000 });
}

export async function consoleTab(page: Page, name: 'Chat' | 'Voice' | 'Phone' | 'Demo'): Promise<void> {
  await page.getByRole('tab', { name }).click();
}

/**
 * Chat tab: pick the "Talk as" persona (which starts a new session), send one
 * message and return what came back: the bot's reply (role "assistant"), or a
 * system line (handoff marker, empty reply, error) with its meta
 * ("urgency=… · lang=…", "sentiment=…").
 */
export async function chatTurn(page: Page, message: string, persona?: RegExp): Promise<{ role: string; text: string; meta: string }> {
  if (persona) {
    const select = page.locator('select.talk-as__select');
    const options = await select.locator('option').allInnerTexts();
    const label = options.find(o => persona.test(o));
    if (!label) throw new Error(`No "Talk as" persona matching ${persona}: ${options.join(' | ')}`);
    await select.selectOption({ label });
    await page.waitForTimeout(400); // the new-session line renders first
  }
  const msgs = page.locator('.log .msg');
  const before = await msgs.count();
  await page.getByPlaceholder('Type a message...').fill(message);
  await page.getByRole('button', { name: 'Send', exact: true }).click();
  // The user's line plus one response line.
  await expect.poll(() => msgs.count(), { timeout: 120_000, message: 'bot response' }).toBeGreaterThanOrEqual(before + 2);
  const last = msgs.last();
  const cls = (await last.getAttribute('class')) ?? '';
  const role = ['assistant', 'agent', 'system'].find(r => cls.split(/\s+/).includes(r)) ?? 'unknown';
  const text = ((await last.locator('.text').innerText().catch(() => '')) || '').trim();
  const meta = ((await last.locator('.meta').innerText().catch(() => '')) || '').trim();
  return { role, text, meta };
}

/** Chat tab: start a fresh session (the persona stays as picked). */
export async function newChatSession(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'New session' }).click();
  await expect(page.locator('.msg.user')).toHaveCount(0);
}

// ---- Customer 360 -------------------------------------------------------------

/** Search Customer 360 and return how long the result took to render (ms). */
export async function c360Search(page: Page, q: string): Promise<number> {
  await openApp(page, 'proton/customer360'); // fresh page, so the timing below is this search only
  // The placeholder ends in the tenant's term for the asset id (automotive:
  // "vehicle no.", patch 0076), so only its fixed start is matched.
  await page.getByPlaceholder(/^Phone number or /).fill(q);
  const t0 = Date.now();
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Contact', exact: true })).toBeVisible({ timeout: 30_000 });
  const ms = Date.now() - t0;
  await expect(page.getByText('Could not complete the search.')).toHaveCount(0);
  return ms;
}
