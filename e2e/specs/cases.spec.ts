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
/** Case rows only (each has a #id link), not an empty-state row. */
const caseRows = (page: Page) => page.locator('table tbody tr').filter({ has: page.locator('td a') });

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

  'CASE-002': async ({ page, api, ev }) => {
    await openApp(page, 'proton/cases');
    await expect(rows(page).first()).toBeVisible({ timeout: 45_000 });
    await filter(page, 'Status').selectOption('open');
    await settle(page);
    // A filter that returns nothing proves nothing: pick the first case type
    // with at least 2 open cases, else fall back to Status = Open alone.
    const typeSelect = filter(page, 'Case type');
    const types = (await typeSelect.locator('option').allInnerTexts()).map(t => t.trim()).filter(t => t && !/^all\b/i.test(t));
    let type: string | null = null;
    for (const t of types) {
      await typeSelect.selectOption({ label: t });
      await settle(page);
      if ((await caseRows(page).count()) >= 2) {
        type = t;
        break;
      }
    }
    if (!type) {
      await typeSelect.selectOption({ index: 0 }); // "All case types"
      await settle(page);
    }
    const n = await caseRows(page).count();
    ev.add(`Status=open${type ? ` + Case type=${type}` : ' (no case type has 2+ open cases)'}: ${n} rows`);
    if (n < 2) ev.handOff('fewer than 2 open cases, so neither the filter nor the sort order can be checked');
    const heads = (await page.locator('table thead th').allInnerTexts()).map(h => h.trim());
    const statusIdx = heads.findIndex(h => /^Status$/.test(h));
    for (let i = 0; i < n; i++) {
      const status = await caseRows(page).nth(i).locator('td').nth(statusIdx).innerText();
      expect(status.toLowerCase(), `row ${i} status`).toContain('open');
    }
    ev.add('every row is Open');

    // Sort (patch 0098): "Sort by" Newest first (default) / Oldest first, by
    // creation time. The Created column is minute-precision local text, so the
    // order is checked against each listed case's created_at from the API.
    expect(heads, 'Created column').toContain('Created');
    const sortSelect = page.locator('#proton-cases-sort');
    await expect(sortSelect, 'Sort by control').toBeVisible();
    expect(await sortSelect.inputValue(), 'Newest first is the default').toBe('created_at_desc');
    const createdAt = new Map<number, number>();
    const orderOk = async (dir: 1 | -1): Promise<string> => {
      const ids = (await caseRows(page).locator('td:first-child').allInnerTexts()).map(t => Number(t.replace(/\D/g, ''))).slice(0, 30);
      for (const id of ids) {
        if (!createdAt.has(id)) createdAt.set(id, Number(Api.must(await api.cw(`conversations/${id}`), `case ${id}`).created_at));
      }
      const ts = ids.map(id => createdAt.get(id)!);
      const bad = ts.findIndex((t, i) => i > 0 && dir * (t - ts[i - 1]) < 0);
      return bad < 0 ? 'ok' : `#${ids[bad - 1]} then #${ids[bad]}`;
    };
    const newest = await orderOk(-1);
    ev.add(`Newest first: ${newest === 'ok' ? 'created_at descending' : `out of order at ${newest}`}`);
    expect(newest, 'Newest first sorts by created date, newest first').toBe('ok');
    await sortSelect.selectOption('created_at_asc');
    await expect(page.getByText('Sorting…')).toHaveCount(0, { timeout: 45_000 });
    await settle(page);
    expect(await caseRows(page).count(), 'the filter is kept after sorting').toBe(n);
    const oldest = await orderOk(1);
    ev.add(`Oldest first: ${oldest === 'ok' ? 'created_at ascending' : `out of order at ${oldest}`}`);
    expect(oldest, 'Oldest first sorts by created date, oldest first').toBe('ok');
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
