// Tracker tab: Cases (read-only).
import type { Page } from '@playwright/test';
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { TEST_PREFIX } from '../lib/env';
import { trackerModule } from '../lib/tracker';
import { bodyText, expectNoError, openApp, settle } from '../lib/ui';

const filter = (page: Page, label: string) =>
  page.locator('label', { hasText: new RegExp(`^\\s*${label}\\s*$`) }).locator('xpath=following-sibling::select[1]').first();

const rows = (page: Page) => page.locator('table tbody tr');

trackerModule('Cases', {
  'CASE-001': async ({ page, ev }) => {
    await openApp(page, 'proton/cases');
    await expect(page.getByRole('heading', { name: 'Cases' })).toBeVisible();
    await expectNoError(page);
    await expect(rows(page).first()).toBeVisible({ timeout: 45_000 });
    const headers = (await page.locator('table thead th').allInnerTexts()).map(h => h.trim());
    const wanted = ['Case ID', 'Channel', 'Case type', 'Concern', 'Car Plate', 'Dealer', 'Division', 'Status'];
    const missing = wanted.filter(w => !headers.some(h => h.toLowerCase().includes(w.toLowerCase())));
    ev.add(`columns: ${headers.join(' | ')}${missing.length ? `; not found: ${missing.join(', ')}` : ''}`);
    for (const must of ['Case ID', 'Division', 'Status']) expect(missing, `${must} column`).not.toContain(must);
    ev.add(`${await rows(page).count()} cases listed`);
    await ev.shot(page);
  },

  'CASE-002': async ({ page, ev }) => {
    await openApp(page, 'proton/cases');
    await expect(rows(page).first()).toBeVisible({ timeout: 45_000 });
    await filter(page, 'Status').selectOption('open');
    const types = await filter(page, 'Case type').locator('option').allInnerTexts();
    const type = types.map(t => t.trim()).find(t => t && !/^all\b/i.test(t)); // skip the "All case types" option
    if (type) await filter(page, 'Case type').selectOption({ label: type });
    await settle(page);
    const n = await rows(page).count();
    const statusIdx = (await page.locator('table thead th').allInnerTexts()).findIndex(h => /^\s*Status\s*$/.test(h));
    for (let i = 0; i < n; i++) {
      const status = await rows(page).nth(i).locator('td').nth(statusIdx).innerText();
      expect(status.toLowerCase(), `row ${i} status`).toContain('open');
    }
    ev.add(`Status=open + Case type=${type ?? '(none)'}: ${n} rows, all Open`);
    const ids = (await rows(page).locator('td:first-child').allInnerTexts()).map(t => Number(t.replace(/\D/g, '')));
    const desc = ids.every((v, i) => i === 0 || ids[i - 1] >= v);
    ev.add(`rows ordered newest first by case id: ${desc}`);
    expect(desc, 'sorted by date (newest first)').toBeTruthy();
    await ev.shot(page);
  },

  'CASE-003': async ({ page, data, ev }) => {
    // Opens the run's own [TEST] case, never a real customer's.
    const conv = await data.conversation([`${TEST_PREFIX} case open check`]);
    await openApp(page, 'proton/cases');
    const link = page.locator('table tbody tr a', { hasText: new RegExp(`^\\s*#${conv.id}\\s*$`) }).first();
    await expect(link, 'the [TEST] case is listed').toBeVisible({ timeout: 45_000 });
    await link.click();
    await expect(page).toHaveURL(new RegExp(`conversations/${conv.id}`));
    ev.add(`clicked [TEST] case #${conv.id}: the linked conversation opened`);
    await ev.shot(page);
  },

  'CASE-004': async ({ page, api, ev }) => {
    await openApp(page, 'proton/cases');
    await expect(rows(page).first()).toBeVisible({ timeout: 45_000 });
    await filter(page, 'Status').selectOption('open');
    await settle(page);
    const shown = await rows(page).count();
    const counter = (await bodyText(page)).match(/(\d+) of (\d+) cases/);
    const meta = Api.must(await api.cw('conversations?assignee_type=all&status=open'), 'open conversations').data.meta;
    ev.add(`Cases open: ${shown} (${counter?.[0] ?? 'no counter'}); Conversations All/Open: ${meta.all_count}`);
    expect(shown, 'open cases = open conversations').toBe(Number(meta.all_count));
    await ev.shot(page);
  },
});
