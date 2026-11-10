// Tracker tab: RSA Incident Log. Creates only [TEST] incidents (vehicle number
// and remarks carry the run id) and deletes them in the test.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { RUN_ID, TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, c360Search, expectNoError, openApp } from '../lib/ui';

const PAGE = 'proton/rsa-incidents';
const today = () => new Date().toISOString().slice(0, 10);
const plate = (n: number) => `TEST ${RUN_ID.toUpperCase()}${n}`;

function full(n: number, extra: Record<string, unknown> = {}) {
  return {
    incident_date: today(),
    vehicle_no: plate(n),
    vehicle_model: 'Proton X50',
    cause: '[TEST] battery flat',
    breakdown_location: '[TEST] NKVE KM 23',
    arrived_location: '[TEST] Petaling Jaya outlet',
    total_km: 12,
    remarks: `${TEST_PREFIX} remarks`,
    ...extra,
  };
}

trackerModule('RSA Incident Log', {
  'RSA-001': async ({ page, api, ev }) => {
    const ok = Api.must(await api.be('/rsa/incidents', { method: 'POST', body: full(1) }), 'create full incident');
    const id = ok.id ?? ok.incident?.id;
    await openApp(page, PAGE);
    await expect(page.getByRole('heading', { name: /Incident Log/ })).toBeVisible();
    await expectNoError(page);
    await expect(page.getByText(plate(1)).first()).toBeVisible();
    ev.add('full [TEST] incident saved and listed');
    await ev.shot(page);
    const { vehicle_model: _drop, ...noModel } = full(2);
    const bad = await api.be('/rsa/incidents', { method: 'POST', body: noModel });
    if (bad.ok) await api.be(`/rsa/incidents/${bad.data.id ?? bad.data.incident?.id}`, { method: 'DELETE' });
    await api.be(`/rsa/incidents/${id}`, { method: 'DELETE' });
    ev.add(`incident without Vehicle model -> HTTP ${bad.status}`);
    expect(bad.status, 'incomplete incident (no Vehicle model) is refused').toBe(422);
    expect(JSON.stringify(bad.data)).toMatch(/vehicle_model/);
  },

  'RSA-002': async ({ page, api, ev }) => {
    const created = Api.must(await api.be('/rsa/incidents', { method: 'POST', body: full(3) }), 'create');
    const id = created.id ?? created.incident?.id;
    const edited = `${TEST_PREFIX} remarks EDITED`;
    Api.must(await api.be(`/rsa/incidents/${id}`, { method: 'PATCH', body: { remarks: edited } }), 'edit remarks');
    await openApp(page, PAGE);
    const search = page.getByPlaceholder(/search|filter/i).first();
    if (await search.isVisible().catch(() => false)) await search.fill(RUN_ID);
    await expect(page.getByText(edited).first()).toBeVisible();
    ev.add(`edited remarks saved; the list ${(await search.isVisible().catch(() => false)) ? 'search' : 'page'} finds it`);
    await ev.shot(page);
    Api.must(await api.be(`/rsa/incidents/${id}`, { method: 'DELETE' }), 'delete');
  },

  'RSA-003': async ({ page, api, data, ev }) => {
    const contact = await data.testContact();
    await data.conversation([`${TEST_PREFIX} RSA link check`]);
    const created = Api.must(await api.be('/rsa/incidents', { method: 'POST', body: full(4, { customer_phone: contact.phone }) }), 'create');
    const id = created.id ?? created.incident?.id;
    await c360Search(page, contact.phone.replace(/\D/g, ''));
    await expect(page.getByText(plate(4)).first(), 'incident listed under RSA incidents').toBeVisible();
    ev.add(`incident with the [TEST] contact's phone shows in Customer 360 for that contact`);
    expect(await bodyText(page)).toContain(contact.name);
    await ev.shot(page, { fullPage: true });
    Api.must(await api.be(`/rsa/incidents/${id}`, { method: 'DELETE' }), 'delete');
  },

  'RSA-004': manual('a login without sla.manage (agent) to check the menu and the direct URL'),
});
