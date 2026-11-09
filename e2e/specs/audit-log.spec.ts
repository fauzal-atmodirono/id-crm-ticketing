// Tracker tab: Audit Log. Read-only.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { manual, trackerModule } from '../lib/tracker';
import { expectNoError, openApp, settle } from '../lib/ui';

trackerModule('Audit Log', {
  'AUD-001': async ({ page, api, ev }) => {
    await openApp(page, 'proton/audit-log');
    await expect(page.getByRole('heading', { name: 'Audit Log' })).toBeVisible();
    for (const col of ['Actor', 'Changes']) await expect(page.getByText(col, { exact: true }).first()).toBeVisible();
    await expectNoError(page);
    const rows = Api.must(await api.be('/admin/audit?limit=200'), 'audit').audit as any[];
    const full = rows.filter(r => r.actor && r.at && r.before != null && r.after != null);
    ev.add(`${rows.length} recent entries, ${full.length} with actor and before/after values`);
    const types = [...new Set(rows.map(r => r.event_type).filter(Boolean))];
    ev.add(`event types present: ${types.join(', ')}`);
    expect(full.length, 'entries carry who/what/when/before/after').toBeGreaterThan(0);
    await ev.shot(page);
    ev.handOff('the three admin changes (role, SLA policy, dealer email) are live configuration changes the suite must not make on prod');
  },

  'AUD-002': async ({ page, api, ev }) => {
    const all = Api.must(await api.be('/admin/audit?limit=200'), 'audit').audit as any[];
    const actor = all.find(r => r.actor_user_id)?.actor_user_id;
    if (actor) {
      const mine = Api.must(await api.be(`/admin/audit?limit=200&actor_user_id=${actor}`), 'by actor').audit as any[];
      expect(mine.every(r => r.actor_user_id === actor), 'only that user’s entries').toBeTruthy();
      ev.add(`actor ${actor}: ${mine.length} entries, all theirs`);
    }
    const type = all.find(r => r.event_type)?.event_type;
    expect(type, 'entries with an event type').toBeTruthy();
    const byType = Api.must(await api.be(`/admin/audit?limit=200&event_type=${type}`), 'by type').audit as any[];
    expect(byType.every(r => r.event_type === type)).toBeTruthy();
    expect(byType.length).toBeLessThanOrEqual(all.length);
    ev.add(`event_type ${type}: ${byType.length} of ${all.length}`);
    const today = new Date().toISOString().slice(0, 10);
    const fromToday = Api.must(await api.be(`/admin/audit?limit=200&from_ts=${today}`), 'from today').audit as any[];
    ev.add(`from ${today}: ${fromToday.length} entries`);
    await openApp(page, 'proton/audit-log');
    const select = page.locator('select', { has: page.locator('option', { hasText: 'Status change' }) }).first();
    await select.selectOption({ index: 1 });
    await page.getByRole('button', { name: 'Filter' }).click();
    await settle(page);
    await ev.shot(page);
  },

  'AUD-003': async ({ page, api, ev }) => {
    await openApp(page, 'proton/audit-log');
    await expect(page.getByRole('heading', { name: 'Audit Log' })).toBeVisible();
    const table = page.locator('table').first();
    await expect(table.getByRole('button', { name: /edit|delete|remove/i })).toHaveCount(0);
    for (const method of ['PUT', 'DELETE']) {
      const r = await api.be('/admin/audit/1', { method, body: method === 'PUT' ? {} : undefined });
      expect([404, 405], `${method} /admin/audit/1`).toContain(r.status);
      ev.add(`${method} -> ${r.status}`);
    }
    ev.add('no edit/delete controls on the page');
    await ev.shot(page);
  },

  'AUD-004': manual('a login without audit.view (agent) to check the menu and the direct URL'),
});
