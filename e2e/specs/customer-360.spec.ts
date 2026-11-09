// Tracker tab: Customer 360 (read-only lookups).
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, c360Search } from '../lib/ui';

const AHMAD = '60000000101';

trackerModule('Customer 360', {
  'C360-001': async ({ page, ev }) => {
    const ms = await c360Search(page, AHMAD);
    const text = await bodyText(page);
    expect(text, 'contact found').toMatch(/Ahmad/);
    await expect(page.getByRole('columnheader', { name: 'Inbox' }).first()).toBeVisible();
    await expect(page.getByRole('heading', { name: 'RSA incidents' })).toBeVisible();
    const convs = await page.locator('table').first().locator('tbody tr').count();
    ev.add(`[DEMO] Ahmad by phone in ${ms} ms: contact, ${convs} conversation(s) with Inbox and Status, RSA incidents section ${/No RSA incidents found/.test(text) ? 'empty' : 'listed'}`);
    await ev.shot(page, { fullPage: true });
  },

  'C360-002': async ({ page, api, ev }) => {
    const incidents = Api.must(await api.be('/rsa/incidents'), 'incidents').incidents as any[];
    const plate = (incidents.find(i => /DMO 1105/i.test(i.vehicle_no)) ?? incidents.find(i => i.customer_phone) ?? incidents[0])?.vehicle_no;
    expect(plate, 'an RSA incident with a vehicle number').toBeTruthy();
    const ms = await c360Search(page, plate);
    const text = await bodyText(page);
    await ev.shot(page, { fullPage: true });
    ev.add(`plate ${plate} in ${ms} ms: contact ${/No matching contact found/.test(text) ? 'not found' : 'found'}, conversations ${/No conversations found/.test(text) ? 'none' : 'listed'}, RSA incidents ${/No RSA incidents found/.test(text) ? 'none' : 'listed'}`);
    expect(text, 'matching contact returned for the vehicle number').not.toMatch(/No matching contact found/);
    expect(text, 'matching cases returned for the vehicle number').not.toMatch(/No conversations found/);
  },

  'C360-003': async ({ page, ev }) => {
    const ms = await c360Search(page, '0000000000');
    for (const t of ['No matching contact found.', 'No conversations found.', 'No RSA incidents found.']) {
      await expect(page.getByText(t)).toBeVisible();
    }
    ev.add(`unknown number: clear no-results messages, no error (${ms} ms)`);
    await ev.shot(page);
  },

  'C360-004': async ({ page, ev }) => {
    await c360Search(page, AHMAD);
    const link = page.locator('table').first().locator('tbody tr a').first();
    const label = (await link.innerText()).trim();
    await link.click();
    await expect(page).toHaveURL(new RegExp(`conversations/${label.replace('#', '')}`));
    await expect(page.locator('.ProseMirror, [contenteditable="true"]').first()).toBeVisible({ timeout: 45_000 });
    ev.add(`clicked ${label}: the conversation opened`);
    await ev.shot(page);
  },

  'C360-005': manual('a login with a role without customer360.view'),

  'C360-006': async ({ page, ev }) => {
    await c360Search(page, AHMAD);
    const text = await bodyText(page);
    await ev.shot(page, { fullPage: true });
    if (/Not connected/.test(text)) ev.blocked('DMS and TSP are not connected (no Proton API access yet): the section says Not connected');
    expect(text, 'a DMS / TSP section with vehicle details and service history').toMatch(/Service history|Vehicles/);
    ev.add('DMS / TSP vehicle and service history shown');
  },

  'C360-007': async ({ page, ev }) => {
    const times: number[] = [];
    for (let i = 0; i < 3; i++) times.push(await c360Search(page, AHMAD));
    ev.add(`phone search x3: ${times.join(', ')} ms`);
    for (const t of times) expect(t, 'each result under 3 s').toBeLessThan(3000);
    await ev.shot(page);
  },

  'C360-008': manual('a real call to the Proton number answered on the softphone'),
});
