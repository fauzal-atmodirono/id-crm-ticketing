// Tracker tab: Settings. Read-only for account, inbox, team, automation,
// priority and webhook settings; the only writes are a [TEST] macro and a
// [TEST] canned response, both deleted in the test.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { INBOX, RUN_ID, TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, expectNoError, openApp, openConversation } from '../lib/ui';

trackerModule('Settings', {
  'SET-001': async ({ page, api, ev }) => {
    await openApp(page, 'settings/general');
    await expectNoError(page);
    const acc = Api.must(await api.cw(`/api/v1/accounts/1`), 'account');
    ev.add(`account "${acc.name}", locale ${acc.locale}, auto-resolve ${acc.auto_resolve_duration ?? 'not set'}`);
    await ev.shot(page, { fullPage: true });
    ev.handOff('changing account name, language and auto-resolve is a live settings change; do it by hand and restore');
  },

  'SET-002': manual('inviting a [TEST] agent, setting the password in super admin and logging in as them'),
  'SET-003': manual('creating a team is a live settings change (the account has no teams)'),

  'SET-004': async ({ page, api, ev }) => {
    await openApp(page, `settings/inboxes/${INBOX.whatsapp}`);
    for (const tab of ['Settings', 'Collaborators', 'Configuration']) {
      const t = page.getByRole('tab', { name: tab }).or(page.locator('a, button', { hasText: new RegExp(`^\\s*${tab}\\s*$`) })).first();
      await t.click();
      await expectNoError(page);
      ev.add(`${tab} tab opens`);
    }
    const members = Api.must(await api.cw(`inbox_members/${INBOX.whatsapp}`), 'members').payload as any[];
    ev.add(`${members.length} collaborator(s) on the WhatsApp inbox`);
    await ev.shot(page);
    ev.handOff('a test message from the WhatsApp handset to confirm it arrives');
  },

  'SET-005': async ({ page, api, ev }) => {
    const inbox = Api.must(await api.cw(`inboxes/${INBOX.email}`), 'email inbox');
    ev.add(`IMAP ${inbox.imap_enabled ? 'on' : 'off'}, SMTP ${inbox.smtp_enabled ? 'on' : 'off'}`);
    expect(inbox.imap_enabled && inbox.smtp_enabled, 'IMAP and SMTP enabled').toBeTruthy();
    await openApp(page, `settings/inboxes/${INBOX.email}`);
    const t = page.getByRole('tab', { name: 'Configuration' }).or(page.locator('a, button', { hasText: /^\s*Configuration\s*$/ })).first();
    if (await t.isVisible().catch(() => false)) await t.click();
    await expectNoError(page);
    await ev.shot(page, { fullPage: true });
    ev.handOff('both directions need a real email in (CONV-006) and a reply out (CONV-007)');
  },

  'SET-006': manual('changing business hours on the live WhatsApp inbox and messaging from the handset'),

  'SET-007': async ({ page, api, ev }) => {
    const titles = (Api.must(await api.cw('labels'), 'labels').payload as any[]).map(l => l.title as string);
    const dept = titles.filter(t => t.startsWith('dept_'));
    const dealer = titles.filter(t => t.startsWith('dealer_'));
    const missing = ['escalate', 'escalation_replied'].filter(t => !titles.includes(t));
    ev.add(`dept_* x${dept.length}, dealer_* x${dealer.length}; missing: ${missing.join(', ') || 'none'}`);
    expect(dept.length).toBeGreaterThan(0);
    expect(dealer.length).toBeGreaterThan(0);
    expect(missing).toEqual([]);
    await openApp(page, 'settings/labels/list');
    await ev.shot(page, { fullPage: true });
  },

  'SET-008': async ({ page, api, ev }) => {
    const defs = Api.must(await api.cw('custom_attribute_definitions?attribute_model=0'), 'definitions') as any[];
    const want: Record<string, string> = {
      case_type: 'list', case_category: 'list', case_subcategory: 'list', case_detail: 'list',
      vehicle_model: 'list', escalation_notified_at: 'date', dealer_escalated_at: 'date',
    };
    const report = Object.entries(want).map(([k, type]) => {
      const d = defs.find(x => x.attribute_key === k);
      return { k, type, got: d?.attribute_display_type ?? 'missing' };
    });
    ev.add(report.map(r => `${r.k}=${r.got}`).join(', '));
    for (const r of report) expect(r.got, `${r.k} type`).toBe(r.type);
    await openApp(page, 'settings/custom-attributes/list');
    await ev.shot(page, { fullPage: true });
  },

  'SET-009': manual('adding an automation rule is a live settings change (keep rule 2 disabled)'),

  'SET-010': async ({ page, api, ev }) => {
    await openApp(page, 'settings/agent-bots');
    await expectNoError(page);
    const inboxes = Api.must(await api.cw('inboxes'), 'inboxes').payload as any[];
    const map: string[] = [];
    for (const i of inboxes) {
      const r = await api.cw(`inboxes/${i.id}/agent_bot`);
      if (r.ok && r.data?.agent_bot) map.push(`${r.data.agent_bot.name} on inbox ${i.id}`);
    }
    ev.add(map.length ? map.join(', ') : 'no agent bot connected to any inbox');
    await ev.shot(page);
    ev.handOff('checking the bot replies only on its inbox needs messages on the WhatsApp inbox');
  },

  'SET-011': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} macro check`]);
    const label = (Api.must(await api.cw('labels'), 'labels').payload as any[]).map(l => l.title).find(t => /^category_/.test(t));
    const macro = Api.must(
      await api.cw('macros', {
        method: 'POST',
        body: {
          name: `${TEST_PREFIX} macro`,
          visibility: 'personal',
          actions: [
            { action_name: 'add_label', action_params: [label] },
            { action_name: 'add_private_note', action_params: [`[TEST] macro ran ${RUN_ID}`] },
          ],
        },
      }),
      'create macro',
    );
    const id = (macro.payload ?? macro).id;
    try {
      Api.must(await api.cw(`macros/${id}/execute`, { method: 'POST', body: { conversation_ids: [conv.id] } }), 'run macro');
      await expect.poll(async () => (Api.must(await api.cw(`conversations/${conv.id}/labels`), 'labels').payload as string[]).includes(label), { timeout: 30_000 }).toBeTruthy();
      const msgs = Api.must(await api.cw(`conversations/${conv.id}/messages`), 'messages').payload as any[];
      expect(msgs.some(m => m.private && String(m.content).includes(`macro ran ${RUN_ID}`)), 'private note action').toBeTruthy();
      await openConversation(page, conv.id);
      await expect(page.getByText(label).first()).toBeVisible();
      ev.add(`[TEST] macro ran: label ${label} added and private note posted (no teams exist, so no team action)`);
      await ev.shot(page);
    } finally {
      await api.cw(`macros/${id}`, { method: 'DELETE' });
    }
  },

  'SET-012': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} canned check`]);
    const contact = await data.testContact();
    const code = `e2e${RUN_ID}cr`;
    const cr = Api.must(await api.cw('canned_responses', { method: 'POST', body: { short_code: code, content: '[TEST] Hello {{contact.name}}' } }), 'create canned');
    try {
      await openConversation(page, conv.id);
      const editor = page.locator('.ProseMirror').first();
      await editor.click();
      await page.keyboard.type(`/${code}`);
      await expect(page.getByText(code).first(), 'found with /').toBeVisible({ timeout: 15_000 });
      await page.keyboard.press('Enter');
      await expect(editor).toContainText(`[TEST] Hello ${contact.name}`);
      ev.add(`/${code} found and inserted with the contact name filled in`);
      await ev.shot(page);
      await editor.fill('');
    } finally {
      await api.cw(`canned_responses/${cr.id}`, { method: 'DELETE' });
    }
  },

  'SET-013': manual('changing the channel priority order is a live routing change, and needs simultaneous WhatsApp and email'),

  'SET-014': async ({ page, api, ev }) => {
    const hooks = Api.must(await api.cw('webhooks'), 'webhooks');
    ev.add(`${(hooks.payload?.webhooks ?? hooks.payload ?? []).length} account webhook(s)`);
    await openApp(page, 'proton/integrations/dms');
    const text = await bodyText(page);
    ev.add(`DMS / TSP card: Base URL ${/Base URL/.test(text) ? 'field shown' : 'not shown'}, Auth type ${/Auth type/.test(text) ? 'shown' : 'not shown'} (credential never displayed)`);
    await ev.shot(page);
    ev.handOff('sending a test delivery fires real webhooks, so it is done by hand');
  },

  'SET-015': manual('a [TEST] case left overnight across closing time to watch the SLA timer pause'),
});
