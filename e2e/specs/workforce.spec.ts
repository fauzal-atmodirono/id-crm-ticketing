// Tracker tab: Workforce & My status. The only write is the signed-in
// person's OWN status, set to non-routable statuses (never Available, so no
// conversation is routed to them) and restored at the end of each test.
import type { Page } from '@playwright/test';
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, expectNoError, openApp, settle } from '../lib/ui';

const NAMED = ['Available', 'Busy', 'Lunch', 'Break', 'Coaching', 'Training', 'Toilet', 'Prayer'];

async function currentStatus(page: Page): Promise<string> {
  const t = await bodyText(page);
  return (t.match(/Currently:\s*([^\n—]+)/)?.[1] ?? '').trim();
}

async function choose(page: Page, label: RegExp): Promise<void> {
  await page.getByRole('button', { name: label }).first().click();
  await expect.poll(() => currentStatus(page), { timeout: 20_000 }).toMatch(label);
}

/** Put the person's own status back to what it was before the test. */
async function restore(page: Page, original: string): Promise<void> {
  if (!original) return;
  const btn = page.getByRole('button', { name: new RegExp(`^\\s*${original.replace(/[()]/g, '.')}`) }).first();
  if (await btn.isVisible().catch(() => false)) await btn.click();
}

trackerModule('Workforce & My status', {
  'WF-001': async ({ page, api, ev }) => {
    await openApp(page, 'proton/workforce');
    await expect(page.getByRole('heading', { name: /Workforce dashboard/ })).toBeVisible();
    await expectNoError(page);
    for (const col of ['Current status', 'Cases closed today', 'Availability history']) {
      await expect(page.getByText(new RegExp(col.slice(0, 12))).first(), col).toBeVisible();
    }
    const wf = Api.must(await api.be('/admin/workforce'), 'workforce');
    const me = await api.me();
    const mine = (wf.agents as any[]).find(a => a.agent_id === me.id);
    expect(mine, 'signed-in agent listed').toBeTruthy();
    const open = Api.must(await api.cw('conversations?assignee_type=me&status=open'), 'my open').data.meta.mine_count;
    ev.add(`${wf.agents.length} agents listed; ${me.name}: status ${JSON.stringify(mine.current_status?.label ?? mine.current_status?.key ?? mine.current_status)}, open ${mine.open_case_count} (Conversations says ${open})`);
    if (mine.open_case_count !== null) expect(mine.open_case_count, 'open count matches Conversations').toBe(open);
    await ev.shot(page);
  },

  'WF-002': async ({ page, api, ev }) => {
    // Window A: workforce dashboard. Window B (same person): My status.
    const me = await api.me();
    await openApp(page, 'proton/workforce');
    const b = await page.context().newPage();
    try {
      await openApp(b, 'proton/my-status');
      const original = await currentStatus(b);
      const t0 = Date.now();
      await choose(b, /^\s*Busy/);
      const row = page.locator('tr', { hasText: me.name }).first();
      await expect.poll(async () => (await row.innerText().catch(() => '')).includes('Busy'), { timeout: 35_000, message: 'dashboard shows Busy within ~30 s' }).toBeTruthy();
      ev.add(`dashboard row for ${me.name} showed Busy ${((Date.now() - t0) / 1000).toFixed(0)} s after the change (was ${original || 'unknown'})`);
      await ev.shot(page);
      await restore(b, original);
    } finally {
      await b.close();
    }
  },

  'WF-003': async ({ page, ev }) => {
    await openApp(page, 'proton/my-status');
    const original = await currentStatus(page);
    ev.add(`started as ${original || 'unknown'}`);
    for (const label of [/^\s*Busy/, /^\s*Break/]) {
      await choose(page, label);
      ev.add(`picked ${label.source.replace(/\^\\s\*/, '')}: shown as current`);
    }
    await page.reload();
    await settle(page);
    expect(await currentStatus(page), 'kept after refresh').toMatch(/Break/);
    ev.add('last status kept after refresh (Available/Online skipped on purpose: it would route live conversations)');
    await ev.shot(page);
    await restore(page, original);
  },

  'WF-004': manual('same as CONV-015: needs Busy test agents and new WhatsApp messages'),
  'WF-005': manual('a login without workforce.view (agent) to check the menu and the direct URL'),

  'WF-006': async ({ page, ev }) => {
    await openApp(page, 'proton/my-status');
    const buttons = (await page.locator('button').allInnerTexts()).map(s => s.replace(/\s+/g, ' ').trim());
    for (const n of NAMED) expect(buttons.some(b => b.startsWith(n)), `status ${n} listed`).toBeTruthy();
    ev.add(`statuses offered: ${buttons.filter(b => NAMED.some(n => b.startsWith(n))).join(', ')}`);
    await ev.shot(page);
    ev.handOff('picking each status while a test message arrives (only Available should get it) needs WhatsApp traffic');
  },

  'WF-007': manual('a test agent on Break for 10 minutes and both notifications checked'),
  'WF-008': manual('a test agent on Break with 2 open cases for 1 hour'),
  'WF-009': manual('follow-up reminders over 5 minutes plus 1 hour with sound and desktop alerts'),
  'WF-010': manual('every WhatsApp-priority agent Busy and a new WhatsApp message'),

  'WF-011': async ({ page, ev }) => {
    await openApp(page, 'proton/workforce');
    await expect(page.getByText(/Availability history/).first()).toBeVisible();
    ev.add('Availability history column shown');
    await ev.shot(page);
    ev.handOff('a test agent must log in and out twice in a second window');
  },
});
