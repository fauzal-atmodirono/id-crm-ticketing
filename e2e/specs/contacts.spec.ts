// Tracker tab: Contacts. Creates, merges, imports and deletes only [TEST]
// contacts carrying the run id.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { KEY_PREFIX, RUN_ID, TEST_PREFIX } from '../lib/env';
import { trackerModule } from '../lib/tracker';
import { openApp, settle } from '../lib/ui';

async function findRun(api: Api, extra = ''): Promise<any[]> {
  const r = Api.must(await api.cw(`contacts/search?q=${encodeURIComponent(RUN_ID)}&per_page=100`), 'contact search');
  return (r.payload as any[]).filter(c => String(c.name).startsWith(TEST_PREFIX) && String(c.name).includes(extra));
}

trackerModule('Contacts', {
  'CONT-001': async ({ page, api, data, ev }) => {
    const c = await data.newContact('Ahmad');
    await openApp(page, `contacts/${c.id}`);
    await expect(page.getByText(c.name).first()).toBeVisible();
    const renamed = `${TEST_PREFIX} Ahmad B`;
    Api.must(await api.cw(`contacts/${c.id}`, { method: 'PUT', body: { name: renamed } }), 'rename');
    await page.reload();
    await settle(page);
    await expect(page.getByText(renamed).first(), 'edit kept after refresh').toBeVisible();
    ev.add(`[TEST] contact ${c.id} created with phone and email, renamed, kept after refresh`);
    await ev.shot(page);
  },

  'CONT-002': async ({ page, api, data, ev }) => {
    const c = await data.newContact('Search');
    await openApp(page, 'contacts');
    const search = page.getByPlaceholder(/Search/).first();
    await search.fill(RUN_ID);
    await search.press('Enter');
    await expect(page.getByText(c.name).first(), 'search by [TEST] name').toBeVisible({ timeout: 30_000 });
    const byPhone = Api.must(await api.cw(`contacts/search?q=${encodeURIComponent(c.phone.replace('+', ''))}`), 'phone search').payload as any[];
    expect(byPhone.some(x => x.id === c.id), 'search by phone').toBeTruthy();
    ev.add('search by [TEST] name (UI) and by phone (API) find the contact');
    const payload = [{ attribute_key: 'name', filter_operator: 'contains', values: [RUN_ID], query_operator: null }];
    const seg = Api.must(
      await api.cw('custom_filters', { method: 'POST', body: { custom_filter: { name: `${TEST_PREFIX} segment`, filter_type: 'contact', query: { payload } } } }),
      'save segment',
    );
    // Reopen it the way a person does: from Contacts, then the segment's link.
    // A cold deep link renders before the segment list is loaded (empty title,
    // empty list), so it is only the fallback.
    await openApp(page, 'contacts');
    const link = page.locator(`aside a[href*="contacts/segments/${seg.id}"]`).first();
    if (await link.isVisible({ timeout: 10_000 }).catch(() => false)) await link.click();
    else await openApp(page, `contacts/segments/${seg.id}`);
    await expect(page).toHaveURL(new RegExp(`contacts/segments/${seg.id}`));
    await expect(page.locator('main').getByText(`${TEST_PREFIX} segment`).filter({ visible: true }).first(), 'segment title shown').toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(c.name).first(), 'segment reopens with the same filter').toBeVisible({ timeout: 30_000 });
    ev.add(`segment "${TEST_PREFIX} segment" reopens and lists the [TEST] contacts`);
    await ev.shot(page);
  },

  'CONT-003': async ({ page, api, data, ev }) => {
    const base = await data.newContact('Merge base');
    const dup = await data.newContact('Merge dup');
    // Chatwoot keeps emails unique, so the duplicate differs in email; each
    // gets a conversation so the merge has something to carry over.
    for (const c of [base, dup]) {
      Api.must(await api.cw('conversations', { method: 'POST', body: { source_id: c.sourceId, inbox_id: 1, contact_id: c.id } }), 'conversation');
    }
    Api.must(await api.cw('actions/contact_merge', { method: 'POST', body: { base_contact_id: base.id, mergee_contact_id: dup.id } }), 'merge');
    const convs = Api.must(await api.cw(`contacts/${base.id}/conversations`), 'base conversations').payload as any[];
    expect(convs.length, 'one contact with both conversations').toBe(2);
    expect((await api.cw(`contacts/${dup.id}`)).status, 'duplicate gone').toBe(404);
    await openApp(page, `contacts/${base.id}`);
    await expect(page.getByText(base.name).first()).toBeVisible();
    ev.add(`merged ${dup.id} into ${base.id}: ${convs.length} conversations on the remaining contact, duplicate 404`);
    await ev.shot(page);
  },

  'CONT-004': async ({ page, api, ev }) => {
    const rows = [1, 2, 3].map(i => `${TEST_PREFIX} import ${i},${KEY_PREFIX}.import${i}@example.com,+6000877${String(Date.now()).slice(-4)}${i}`);
    const csv = ['name,email,phone_number', ...rows].join('\n');
    Api.must(await api.upload('cw', 'contacts/import', { field: 'import_file', filename: 'contacts.csv', content: csv, type: 'text/csv' }), 'import');
    await expect.poll(async () => (await findRun(api, 'import')).length, { timeout: 180_000, intervals: [5000], message: 'imported contacts appear (async)' }).toBe(3);
    const found = await findRun(api, 'import');
    for (const c of found) {
      expect(c.email).toContain(`${RUN_ID}.import`);
      expect(c.phone_number).toBeTruthy();
    }
    await openApp(page, 'contacts');
    const search = page.getByPlaceholder(/Search/).first();
    await search.fill(`${RUN_ID} import`);
    await search.press('Enter');
    await expect(page.getByText(`${TEST_PREFIX} import 1`).first()).toBeVisible({ timeout: 30_000 });
    ev.add('3-row CSV imported: 3 contacts with name, email and phone');
    await ev.shot(page);
  },

  'CONT-005': async ({ page, api, ev }) => {
    const mine = (await findRun(api)).filter(c => !/ contact$/.test(c.name)); // keep the shared [TEST] contact for later modules
    for (const c of mine) Api.must(await api.cw(`contacts/${c.id}`, { method: 'DELETE' }), `delete ${c.id}`);
    const left = (await findRun(api)).filter(c => !/ contact$/.test(c.name));
    expect(left.length, 'none of the deleted [TEST] contacts remain').toBe(0);
    await openApp(page, 'contacts');
    const search = page.getByPlaceholder(/Search/).first();
    await search.fill(`${RUN_ID} import`);
    await search.press('Enter');
    await settle(page);
    await expect(page.getByText(`${TEST_PREFIX} import 1`)).toHaveCount(0);
    ev.add(`${mine.length} [TEST] contacts deleted; search no longer finds them`);
    await ev.shot(page);
  },

  'CONT-006': async ({ page, api, ev }) => {
    const c = (Api.must(await api.cw('search/contacts?q=60000000101'), 'Ahmad').payload.contacts as any[])[0];
    expect(c, '[DEMO] Ahmad').toBeTruthy();
    expect(c.name, 'only the [DEMO] persona contact is opened').toMatch(/\[DEMO\]/);
    const convs = Api.must(await api.cw(`contacts/${c.id}/conversations`), 'conversations').payload as any[];
    await openApp(page, `contacts/${c.id}`);
    await expect(page.getByText(c.name).first()).toBeVisible();
    const historyTab = page.getByRole('button', { name: 'History', exact: true }).or(page.getByText('History', { exact: true })).first();
    if (await historyTab.isVisible().catch(() => false)) await historyTab.click();
    const cards = page.locator('div.divide-y').last().locator(':scope > *');
    await expect.poll(() => cards.count(), { timeout: 30_000, message: 'conversation history listed' }).toBe(convs.length);
    await cards.first().click();
    await expect(page).toHaveURL(/conversations\/\d+/);
    ev.add(`[DEMO] Ahmad: ${convs.length} conversation(s) listed on the contact`);
    await ev.shot(page);
  },
});
