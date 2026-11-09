// Tracker tab: Escalation Routing. Strictly read-only: the suite never saves
// a PIC, dealer, group, contact or ladder setting and never touches dry_run.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, expectNoError, openApp } from '../lib/ui';

const PAGE = 'proton/escalation-routing';
const NO_SAVE = 'saving it is a live escalation-routing change the suite must not make on prod; edit, refresh and restore by hand';

trackerModule('Escalation Routing', {
  'ESC-001': async ({ page, api, ev }) => {
    await openApp(page, PAGE);
    await expect(page.getByRole('heading', { name: 'Escalation Routing' })).toBeVisible();
    await expect(page.getByText(/Department PICs?/).first()).toBeVisible();
    await expectNoError(page);
    const pics = Api.must(await api.be('/admin/escalation/pics'), 'pics');
    const list = (pics.pics ?? pics) as any[];
    const missing = list.filter(p => !p.pic_email && !p.email).map(p => p.department);
    ev.add(`${list.length} department PICs; without a PIC email: ${missing.length ? missing.join(', ') : 'none'}`);
    await ev.shot(page);
    ev.handOff(NO_SAVE);
  },

  'ESC-002': async ({ page, api, ev }) => {
    await openApp(page, PAGE);
    await expect(page.getByText('Dealers', { exact: true }).first()).toBeVisible();
    const d = Api.must(await api.be('/admin/escalation/dealers'), 'dealers');
    const list = (d.dealers ?? d) as any[];
    ev.add(`${list.length} dealer(s): ${list.map(x => x.dealer ?? x.slug).join(', ')}`);
    await ev.shot(page);
    ev.handOff(NO_SAVE);
  },

  'ESC-003': async ({ page, ev }) => {
    await openApp(page, PAGE);
    await expect(page.getByText('Dealer groups').first()).toBeVisible();
    await expect(page.getByText(/Every member receives the escalation email/).first()).toBeVisible();
    ev.add('Dealer groups section with Members (CC) shown');
    await ev.shot(page);
    ev.handOff(`creating a [TEST] group: ${NO_SAVE}`);
  },

  'ESC-004': async ({ page, ev }) => {
    await openApp(page, PAGE);
    await expect(page.getByText(/Tier-2 manager/).first()).toBeVisible();
    ev.add('escalation manager (Tier-2) contact field shown');
    await ev.shot(page);
    ev.handOff(NO_SAVE);
  },

  'ESC-005': async ({ page, ev }) => {
    await openApp(page, PAGE);
    await expect(page.getByText('Ladder contacts').first()).toBeVisible();
    const text = await bodyText(page);
    for (const f of ['Dealer CRE', 'Sales / Aftersales Mgr', 'Dealer Principal', 'Dealer Owner', 'PRO-NET region']) {
      ev.add(`${f}: ${text.includes(f) ? 'shown' : 'not found'}`);
    }
    await ev.shot(page);
    ev.handOff(NO_SAVE);
  },

  'ESC-006': async ({ page, api, ev }) => {
    await openApp(page, PAGE);
    const ladder = page.getByText('Escalation ladder').first();
    await expect(ladder).toBeVisible();
    await ladder.scrollIntoViewIfNeeded();
    const l = Api.must(await api.be('/admin/escalation/ladder'), 'ladder');
    const eff = l.effective ?? l;
    const hours = Object.entries(eff).filter(([k, v]) => /step|hours/i.test(k) && typeof v === 'number').map(([k, v]) => `${k}=${v}`);
    ev.add(`ladder step settings: ${hours.join(', ') || 'see screenshot'}; dry run ${eff.dry_run === undefined ? 'unknown' : eff.dry_run ? 'on' : 'off'} (left as is)`);
    for (const col of ['Case', 'Dealer', 'Rung', 'Next step', 'Due in']) {
      await expect(page.getByText(col, { exact: true }).first(), `in-flight column ${col}`).toBeVisible();
    }
    const inflight = (await bodyText(page)).match(/In flight \((\d+)\)/)?.[1];
    ev.add(`in-flight view lists ${inflight ?? '?'} open dealer case(s) with Case/Dealer/Rung/Next step/Due in (dry run left as is)`);
    await ev.shot(page, { fullPage: true });
  },

  'ESC-007': manual('a login without escalation.manage (agent) to check the menu and the direct URL'),
  'ESC-008': manual('escalating a [TEST] email case and checking three test mailboxes'),
  'ESC-009': manual('a PIC WhatsApp alert received on a test handset'),
  'ESC-010': manual('a real case email with photos sent into inbox 5 and the forward checked in the PIC mailbox'),

  'ESC-011': async ({ page, ev }) => {
    await openApp(page, PAGE);
    await expect(page.getByText(/Step 6 — 48 h unresolved/).first(), '48-hour unresolved step configured').toBeVisible();
    ev.add('ladder has "Step 6 — 48 h unresolved"');
    await ev.shot(page);
    ev.handOff('the alert itself needs a [TEST] dealer case left open for 48 hours');
  },

  'ESC-012': async ({ page, api, ev }) => {
    const rows = Api.must(await api.be('/admin/audit?limit=200&event_type=escalation_notify'), 'escalation audit').audit as any[];
    expect(rows.length, 'escalation notifications recorded').toBeGreaterThan(0);
    const complete = rows.filter(r => (r.recipients?.length ?? 0) > 0 && r.at && r.actor);
    expect(complete.length, 'entries name recipient, actor and time').toBeGreaterThan(0);
    const transports = [...new Set(rows.map(r => r.transport).filter(Boolean))].join('/');
    ev.add(`${rows.length} escalation_notify entries, ${complete.length} with recipients, actor and time (transport ${transports || '?'}, ${rows.filter(r => r.sla_status).length} with SLA status)`);
    await openApp(page, 'proton/audit-log');
    const select = page.locator('select', { has: page.locator('option', { hasText: 'Escalation notified' }) }).first();
    await select.selectOption({ label: 'Escalation notified' });
    await page.getByRole('button', { name: 'Filter' }).click();
    await expect(page.locator('table tbody tr').first()).toBeVisible();
    await ev.shot(page);
  },

  'ESC-013': manual('every agent of the target team set Offline, then an escalation (would change real agents’ status)'),
});
