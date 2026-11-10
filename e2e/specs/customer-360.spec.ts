// Tracker tab: Customer 360 (read-only lookups).
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { RUN_ID, TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, c360Search, columnHeader } from '../lib/ui';

const AHMAD = '60000000101';

trackerModule('Customer 360', {
  'C360-001': async ({ page, ev }) => {
    const ms = await c360Search(page, AHMAD);
    const text = await bodyText(page);
    expect(text, 'contact found').toMatch(/Ahmad/);
    await expect(columnHeader(page, 'Inbox'), 'conversations table with an Inbox column').toBeVisible();
    await expect(page.getByRole('heading', { name: 'RSA incidents' })).toBeVisible();
    const convs = await page.locator('table').first().locator('tbody tr').count();
    ev.add(`[DEMO] Ahmad (demo persona) by phone in ${ms} ms: contact, ${convs} conversation(s) with Inbox and Status, RSA incidents section ${/No RSA incidents found/.test(text) ? 'empty' : 'listed'}`);
    await ev.shot(page, { fullPage: true });
  },

  'C360-002': async ({ page, api, data, ev }) => {
    // A run-created [TEST] incident for the run's [TEST] contact, so the
    // search never surfaces a real customer.
    const contact = await data.testContact();
    await data.conversation([`${TEST_PREFIX} vehicle search check`]);
    // A plate the backend treats as plate-shaped: 4-12 letters and digits once
    // normalised (customer360_router._plate_shaped). "TEST <run id>V" is 14 and
    // never reached the incident-phone lookup. Still unique to the run.
    const plate = `ZT ${RUN_ID.slice(-8).toUpperCase()} 9`;
    const created = Api.must(
      await api.be('/rsa/incidents', {
        method: 'POST',
        body: { incident_date: new Date().toISOString().slice(0, 10), vehicle_no: plate, vehicle_model: '[TEST] X50', cause: '[TEST] marker', customer_phone: contact.phone, remarks: `${TEST_PREFIX} C360 plate` },
      }),
      'create [TEST] incident',
    );
    try {
      const ms = await c360Search(page, plate);
      const text = await bodyText(page);
      await ev.shot(page, { fullPage: true });
      ev.add(`[TEST] vehicle number in ${ms} ms: contact ${/No matching contact found/.test(text) ? 'not found' : 'found'}, conversations ${/No conversations found/.test(text) ? 'none' : 'listed'}, RSA incidents ${/No RSA incidents found/.test(text) ? 'none' : 'listed'}`);
      expect(text, 'matching contact returned for the vehicle number').not.toMatch(/No matching contact found/);
      expect(text, 'matching cases returned for the vehicle number').not.toMatch(/No conversations found/);
    } finally {
      await api.be(`/rsa/incidents/${created.id}`, { method: 'DELETE' });
    }
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
    expect(await bodyText(page), 'only the [DEMO] persona is opened').toMatch(/\[DEMO\]/);
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
    // With the integration switched off the backend sends no DMS block and the
    // page leaves the DMS/TSP section out entirely (patch 0045).
    if (!/DMS ?\/ ?TSP/.test(text)) ev.blocked('DMS and TSP are not connected (no Proton API access yet): the integration is off, so Customer 360 has no DMS/TSP section');
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
