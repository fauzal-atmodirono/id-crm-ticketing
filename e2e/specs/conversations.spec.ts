// Tracker tab: Conversations.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { FLAGS, INBOX, KEY_PREFIX, RUN_ID, TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, chatTurn, consoleTab, insertCanned, openApp, openContactPanel, openConsole, openConversation, revealInPanel, settle } from '../lib/ui';

const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);
const TINY_PDF = Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n');

/**
 * Top-of-list tab ("Mine", "Unassigned", "All") as rendered by woot-tabs: the
 * label plus its count badge, outside the sidebar. A bare /^All\b/ also
 * matched the sidebar's "All Conversations" link, which keeps the current tab.
 */
const listTab = (page: import('@playwright/test').Page, name: string) =>
  page.locator('main li a', { hasText: new RegExp(`^\\s*${name}\\s*\\d*\\s*$`) }).first();

/**
 * How many conversations the open list tab holds, after scrolling it to its
 * end. Read from the SPA's store (the list renders the store filtered by tab),
 * because the virtualised list keeps only the visible rows in the DOM.
 */
async function listedConversations(page: import('@playwright/test').Page, assignee: string, meId: number, expected: number): Promise<number> {
  const list = page.locator('.conversations-list').first();
  const eof = page.getByText('All conversations loaded', { exact: false });
  const inStore = () =>
    page.evaluate(
      ([tab, me]) => {
        const store = (document.querySelector('#app') as any)?.__vue_app__?.config?.globalProperties?.$store;
        const all: any[] | undefined = store?.getters?.getAllConversations;
        if (!Array.isArray(all)) return -1;
        if (tab === 'me') return all.filter(c => c.meta?.assignee?.id === me).length;
        if (tab === 'unassigned') return all.filter(c => !c.meta?.assignee).length;
        return all.length;
      },
      [assignee, meId] as [string, number],
    );
  // The previous tab's "All conversations loaded" line can still be on screen
  // right after switching, so EOF alone is not "done": stop when the list holds
  // the expected number, or when it is at EOF and stopped growing.
  let n = await inStore();
  let still = 0;
  for (let i = 0; i < 80 && n >= 0 && n < expected; i++) {
    await list.evaluate(el => el.scrollTo(0, el.scrollHeight)).catch(() => {});
    await page.waitForTimeout(700);
    const next = await inStore();
    still = next === n ? still + 1 : 0;
    n = next;
    if (still >= 4 && (await eof.isVisible().catch(() => false))) break;
  }
  if (n < 0) throw new Error('could not read the conversation list from the SPA store; count the rows by hand');
  return n;
}

async function messages(api: Api, convId: number): Promise<any[]> {
  return Api.must(await api.cw(`conversations/${convId}/messages`), 'list messages').payload ?? [];
}

trackerModule('Conversations', {
  'CONV-001': async ({ page, api, ev }) => {
    const tabs: [string, string, string][] = [
      ['Mine', 'me', 'mine_count'],
      ['Unassigned', 'unassigned', 'unassigned_count'],
      ['All', 'all', 'all_count'],
    ];
    const me = await api.me();
    await openApp(page, 'conversations');
    for (const [name, assignee, key] of tabs) {
      const r = await api.cw(`conversations?assignee_type=${assignee}&status=all&page=1`);
      expect(r.ok, `${name} list API`).toBeTruthy();
      expect(r.ms, `${name} list loads in under 3 s`).toBeLessThan(3000);
      const count = Number(r.data.data.meta[key]);
      const t0 = Date.now();
      await listTab(page, name).click();
      await settle(page);
      await expect(page.locator('.conversation').first(), `${name} list shows rows`).toBeVisible({ timeout: count ? 15_000 : 1 }).catch(e => {
        if (count) throw e;
      });
      const uiMs = Date.now() - t0;
      // The list is virtualised (virtua): only the rows on screen exist in the
      // DOM, and it loads 25 at a time as you scroll. So scroll it to the end
      // ("All conversations loaded") and count what the list holds.
      const rows = await listedConversations(page, assignee, me.id, count);
      expect(rows, `${name}: conversations in the list vs tab count ${count}`).toBe(count);
      ev.add(`${name}: tab ${count}, rows ${rows}, API ${r.ms} ms, page ${uiMs} ms`);
    }
    for (const p of ['mentions/conversations', 'unattended/conversations']) {
      const t0 = Date.now();
      await openApp(page, p);
      ev.add(`${p.split('/')[0]} opened in ${Date.now() - t0} ms with ${await page.locator('.conversation').count()} rows`);
    }
    await openApp(page, 'conversations');
    await page.reload();
    await settle(page);
    await expect(listTab(page, 'All'), 'All is the default tab').toHaveClass(/text-n-blue-11/);
    ev.add('after refresh the All tab is selected');
    await ev.shot(page);
  },

  'CONV-002': async ({ page, api, data, ev }) => {
    // Membership is checked from the API list; in the UI only the run's own
    // [TEST] conversation is clicked (never a real customer's).
    const conv = await data.conversation([`${TEST_PREFIX} inbox and label view check`]);
    const contact = await data.testContact();
    const inboxList = Api.must(await api.cw(`conversations?assignee_type=all&status=all&inbox_id=${INBOX.api}`), 'inbox list').data.payload as any[];
    expect(inboxList.every(c => c.inbox_id === INBOX.api), `every row is inbox ${INBOX.api}`).toBeTruthy();
    await openApp(page, `inbox/${INBOX.api}`);
    const mine = page.locator('.conversation', { hasText: contact.name }).first();
    await expect(mine, 'the [TEST] conversation is in the inbox view').toBeVisible();
    await mine.click();
    await expect(page).toHaveURL(new RegExp(`/inbox/${INBOX.api}/conversations/${conv.id}`));
    ev.add(`inbox ${INBOX.api} view: ${inboxList.length} rows from the API all inbox ${INBOX.api}; the [TEST] conversation opens from it`);
    const labelList = Api.must(await api.cw('conversations?assignee_type=all&status=all&labels[]=audit_test'), 'label list').data.payload as any[];
    expect(labelList.every(c => (c.labels ?? []).includes('audit_test')), 'every row labelled audit_test').toBeTruthy();
    await openApp(page, 'label/audit_test');
    await expect(page.locator('.conversation', { hasText: contact.name }).first(), 'the [TEST] conversation is in the label view').toBeVisible();
    ev.add(`label audit_test view: ${labelList.length} rows all labelled; the [TEST] conversation is listed`);
    const teams = Api.must(await api.cw('teams'), 'teams') as any[];
    if (teams.length) {
      await openApp(page, `team/${teams[0].id}`);
      ev.add(`team view opened (${await page.locator('.conversation').count()} rows)`);
    } else {
      ev.add('no teams exist on the account, so there is no team view to check');
    }
    await ev.shot(page);
  },

  'CONV-003': async ({ page, api, data, ev }) => {
    await data.conversation([`${TEST_PREFIX} filter check`]);
    const since = new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10);
    const payload = [
      { attribute_key: 'status', filter_operator: 'equal_to', values: ['open'], query_operator: 'and' },
      { attribute_key: 'priority', filter_operator: 'equal_to', values: ['high'], query_operator: 'and' },
      { attribute_key: 'labels', filter_operator: 'equal_to', values: ['escalate'], query_operator: 'and' },
      { attribute_key: 'created_at', filter_operator: 'is_greater_than', values: [since], query_operator: null },
    ];
    const r = Api.must(await api.cw('conversations/filter?page=1', { method: 'POST', body: { payload } }), 'filter');
    const rows = r.payload as any[];
    for (const c of rows) {
      expect(c.status).toBe('open');
      expect(c.priority).toBe('high');
      expect(c.labels).toContain('escalate');
      expect(c.created_at * 1000).toBeGreaterThan(Date.parse(since));
    }
    ev.add(`4-filter query returned ${rows.length} rows, all matching`);
    const name = `${TEST_PREFIX} Escalated high`;
    const view = Api.must(
      await api.cw('custom_filters', { method: 'POST', body: { custom_filter: { name, filter_type: 'conversation', query: { payload } } } }),
      'save view',
    );
    await openApp(page, 'conversations');
    const item = page.locator('aside').first().getByText(name).first();
    await expect(item, 'saved view in the sidebar').toBeVisible();
    await item.click();
    await expect(page).toHaveURL(new RegExp(`custom_view/${view.id}`));
    await settle(page);
    ev.add(`saved view "${name}" listed under Folders and reopens (custom_view/${view.id})`);
    await ev.shot(page);
  },

  'CONV-004': async ({ page, api, data, ev }) => {
    const token = `servis${RUN_ID}`;
    const conv = await data.conversation([`${TEST_PREFIX} nak ${token} kereta X50`]);
    const contact = await data.testContact();
    // Search indexes asynchronously; give it a moment.
    await expect.poll(async () => (await api.cw(`search/messages?q=${token}`)).data?.payload?.messages?.length ?? 0, { timeout: 60_000 }).toBeGreaterThan(0);
    for (const q of [contact.name, contact.phone.replace('+', '')]) {
      const r = Api.must(await api.cw(`search/contacts?q=${encodeURIComponent(q)}`), 'contact search');
      expect(r.payload.contacts.length, `contact search "${q}"`).toBeGreaterThan(0);
    }
    ev.add('message, contact-name and phone searches return results');
    await openApp(page, `search?q=${token}`);
    // The message result is a link to the conversation; a bare text match
    // also hits "No contacts found for query '<token>'".
    const hit = page.locator(`a[href*="/conversations/${conv.id}"]`).filter({ hasText: token }).first();
    await expect(hit, 'message result for the [TEST] conversation').toBeVisible();
    await ev.shot(page);
    await hit.click();
    await expect(page).toHaveURL(new RegExp(`conversations/${conv.id}`));
    ev.add(`clicking the message result opens conversation ${conv.id}`);
  },

  'CONV-005': manual('a real WhatsApp message from the test handset, a stopwatch and the toast sound'),
  'CONV-006': manual('sending a real email with an attachment from the external test mailbox to inbox 5'),
  'CONV-007': manual('the reply must reach the test handset and the test mailbox thread (the suite never sends WhatsApp or email)'),

  'CONV-008': async ({ page, api, data, ev }) => {
    // The @mention targets ONLY the signed-in person, so no colleague is
    // notified by a test.
    const me = await api.me();
    const conv = await data.conversation([`${TEST_PREFIX} please help with my booking`]);
    await data.assertSendable(conv.id);
    await openConversation(page, conv.id);
    await page.getByText('Private Note', { exact: true }).click();
    const editor = page.locator('.ProseMirror').first();
    await editor.click();
    await page.keyboard.type(`@${me.name.split(' ')[0]}`);
    await page.waitForTimeout(1200);
    await page.keyboard.press('Enter');
    await page.keyboard.type(' please check this case [TEST]');
    const draft = await editor.innerText();
    if (!draft.includes(`@${me.name}`)) {
      await editor.fill('');
      throw new Error('the mention picker did not pick the signed-in user; nothing was sent');
    }
    await ev.shot(page);
    await page.getByRole('button', { name: /Add Note/i }).click();
    await expect.poll(async () => (await messages(api, conv.id)).filter(m => m.private).length, { timeout: 20_000 }).toBeGreaterThan(0);
    const note = (await messages(api, conv.id)).filter(m => m.private).pop();
    expect(note.content).toContain(`mention://user/${me.id}/`);
    // A Chatwoot private note is an outgoing message (message_type 1) with
    // private=true; private is what keeps it from the customer.
    expect(note.private, 'note is private (not sent to the customer)').toBe(true);
    ev.add(`private note ${note.id} with an @mention of the signed-in user saved (private, not sent; API inbox)`);
    ev.handOff('a colleague’s bell notification and Mentions list need a second agent mentioned and logged in (Window B)');
  },

  'CONV-009': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} can you send the brochure?`]);
    const contact = await data.testContact();
    const code = KEY_PREFIX;
    Api.must(
      await api.cw('canned_responses', { method: 'POST', body: { short_code: code, content: '[TEST] Hello {{contact.name}}' } }),
      'create canned response',
    );
    await openConversation(page, conv.id);
    const editor = page.locator('.ProseMirror').first();
    await editor.click();
    await insertCanned(page, code);
    // Chatwoot capitalises each word of {{contact.name}} when it fills it in.
    await expect(editor).toContainText(new RegExp(contact.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'i'));
    ev.add('canned response inserted with {{contact.name}} filled in');
    // The page has more than one file input (the first takes images only, one
    // at a time); use the composer's, which accepts PDFs, and attach the files
    // one by one unless it allows several at once.
    const files = [
      { name: 'test-image.png', mimeType: 'image/png', buffer: PNG_1PX },
      { name: 'test-doc.pdf', mimeType: 'application/pdf', buffer: TINY_PDF },
    ];
    const input = page.locator('input[type="file"]:not([accept]), input[type="file"][accept*="pdf"]').first();
    if ((await input.getAttribute('multiple')) !== null) await input.setInputFiles(files);
    else for (const f of files) await input.setInputFiles(f);
    await page.waitForTimeout(1500);
    await ev.shot(page);
    await data.assertSendable(conv.id);
    await page.getByRole('button', { name: /^Send/ }).first().click();
    await expect
      .poll(async () => (await messages(api, conv.id)).filter(m => m.message_type === 1 && (m.attachments?.length ?? 0) > 0).length, { timeout: 30_000 })
      .toBeGreaterThan(0);
    const sent = (await messages(api, conv.id)).filter(m => m.message_type === 1 && m.attachments?.length).pop();
    ev.add(`outgoing message ${sent.id} with ${sent.attachments.length} attachment(s), status ${sent.status}`);
    expect(sent.status).not.toBe('failed');
    ev.handOff('delivery and preview on the real test handset (the suite only uses the API inbox)');
  },

  'CONV-010': async ({ page, api, ev }) => {
    await openApp(page, 'proton/knowledge/inboxes');
    const r = Api.must(await api.be('/kb/inboxes'), 'kb inboxes');
    const rows = (r.inboxes ?? r) as any[];
    ev.add(`AI mode per inbox: ${rows.map((i: any) => `${i.inbox_id ?? i.id}=${i.mode ?? i.agent_mode ?? '?'}`).join(', ')}`);
    await ev.shot(page);
    ev.handOff('needs a new chat from the WhatsApp test handset to see the bot draft (the suite never sends WhatsApp)');
  },

  'CONV-011': async ({ page, api, data, ev }) => {
    const q = '[TEST] What is the warranty period for the X50 battery?';
    const conv = await data.conversation([q]);
    const base = { conversation_id: String(conv.id), messages: [`Customer: ${q}`] };
    const s = await api.be('/assist/suggest', { method: 'POST', body: base });
    expect(s.status, 'suggest reply').toBe(200);
    expect(String(s.data.draft ?? '').length).toBeGreaterThan(10);
    ev.add(`suggest 200 in ${s.ms} ms`);
    for (const mode of ['tone_professional', 'improve']) {
      const r = await api.be('/assist/rewrite', { method: 'POST', body: { ...base, content: s.data.draft, mode } });
      expect(r.status, `rewrite ${mode}`).toBe(200);
      expect(String(r.data.draft ?? '').length).toBeGreaterThan(10);
      ev.add(`rewrite ${mode} 200 in ${r.ms} ms`);
    }
    const a = await api.be('/assist/ask', { method: 'POST', body: { ...base, question: 'what is the customer asking?' } });
    expect(a.status, 'ask copilot').toBe(200);
    expect(String(a.data.answer ?? '')).toMatch(/warrant|battery|X50/i);
    ev.add(`ask 200 in ${a.ms} ms, answer mentions the warranty question`);
    await openConversation(page, conv.id);
    const copilot = page.getByRole('button', { name: /copilot/i }).first();
    ev.add(`Copilot button ${(await copilot.isVisible().catch(() => false)) ? 'visible' : 'not found by name'} in the conversation`);
    await ev.shot(page);
  },

  'CONV-012': async ({ page, api, data, ev }) => {
    const bm = '[TEST] Saya nak tahu bila kereta saya boleh siap servis, terima kasih.';
    const conv = await data.conversation([bm]);
    await openConversation(page, conv.id);
    await expect(page.locator('[title^="Translate the draft"]').first(), 'Translate control').toBeVisible();
    const tr = async (text: string, target: string) => {
      const r = await api.be('/assist/translate', { method: 'POST', body: { conversation_id: String(conv.id), text, target_language: target } });
      return r;
    };
    // r.ms is the browser-side round trip of the translate call (what the
    // agent waits for). All four are measured and recorded first, then each is
    // held to the DoD's 5 s, so one slow call still leaves full evidence.
    const timed: { what: string; status: number; ms: number }[] = [];
    for (const target of ['en', 'zh']) {
      const r = await tr(bm, target);
      timed.push({ what: `inbound BM -> ${target}`, status: r.status, ms: r.ms });
      ev.add(`BM->${target} ${r.ms} ms`);
    }
    const draft = 'Your car will be ready by 5 pm today. Thank you.';
    for (const target of ['ms', 'zh']) {
      const r = await tr(draft, target);
      timed.push({ what: `draft EN -> ${target}`, status: r.status, ms: r.ms });
      ev.add(`draft EN->${target} ${r.ms} ms`);
    }
    for (const t of timed) {
      expect(t.status, t.what).toBe(200);
      expect(t.ms, `${t.what} within 5 s`).toBeLessThan(5000);
    }
    const ta = await tr(bm, 'ta');
    ev.add(ta.ok ? `BM->ta ${ta.ms} ms` : `Tamil target refused (${ta.status}; outbound Tamil is off by design)`);
    const msgs = await messages(api, conv.id);
    expect(msgs.some(m => m.content === bm), 'original message kept').toBeTruthy();
    await ev.shot(page);
  },

  'CONV-013': async ({ page, api, data, ev }) => {
    const faqs = Api.must(await api.be('/kb/faq'), 'faq list').entries as any[];
    const booking = faqs.find(f => /book/i.test(f.question) && /servic/i.test(f.question));
    expect(booking, 'an FAQ about service booking exists').toBeTruthy();
    ev.add(`booking FAQ: "${booking.question}"`);
    const conv = await data.conversation(['[TEST] How do I book a service?']);
    await openConversation(page, conv.id);
    const features: string[] = await page.evaluate(() => (window as any).__PROTON_CONFIG__?.features ?? []);
    if (!features.includes('faq_suggestion_popup')) ev.blocked('the FAQ strip feature (faq_suggestion_popup) is off on this tenant');
    // The strip shows only the top /kb/suggest hit and only when its score is
    // >= 0.65 (patch 0056). Record what the backend returns for this message so
    // a missing strip can be told apart: low match vs. strip not rendering.
    const sug = await api.be(`/kb/suggest?q=${encodeURIComponent('[TEST] How do I book a service?')}&limit=1`);
    const top = (sug.data?.suggestions ?? [])[0];
    ev.add(`/kb/suggest -> HTTP ${sug.status}; top: ${top ? `"${String(top.question ?? top.title ?? '').slice(0, 60)}" score ${top.score}` : 'none'} (strip threshold 0.65)`);
    const apply = page.getByRole('button', { name: 'Apply', exact: true });
    await expect(apply, 'FAQ strip with Apply').toBeVisible({ timeout: 30_000 });
    await ev.shot(page);
    await apply.click();
    await expect(page.locator('.ProseMirror').first()).not.toBeEmpty();
    const draft = await page.locator('.ProseMirror').first().innerText();
    const before = (await messages(api, conv.id)).length;
    ev.add(`Apply put ${draft.length} chars in the composer (not sent: ${before} messages unchanged)`);
  },

  'CONV-014': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} assignment check`]);
    const me = await api.me();
    Api.must(await api.cw(`conversations/${conv.id}/assignments`, { method: 'POST', body: { assignee_id: me.id } }), 'assign');
    Api.must(await api.cw(`conversations/${conv.id}/toggle_priority`, { method: 'POST', body: { priority: 'high' } }), 'priority');
    await openConversation(page, conv.id);
    // Assigning yourself logs "<name> self-assigned this conversation", not
    // "Assigned to <name> by <name>" (Chatwoot activity text).
    await expect(page.getByText(/Assigned to|self-assigned this conversation/i).first(), 'assignment activity line').toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(me.name).first()).toBeVisible();
    ev.add(`assigned to ${me.name}, priority high; activity line shown`);
    const teams = Api.must(await api.cw('teams'), 'teams') as any[];
    ev.add(teams.length ? `${teams.length} team(s) exist` : 'no teams exist, team assignment not possible');
    await ev.shot(page);
    ev.handOff('the assignee’s notification needs a second agent logged in (self-assignment does not notify)');
  },

  'CONV-015': manual('three test agents with Online/Busy/Offline status and new WhatsApp messages from test handsets'),

  'CONV-016': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} label check`]);
    const all = (Api.must(await api.cw('labels'), 'labels').payload as any[]).map(l => l.title as string);
    const pick = all.filter(l => /^category_/.test(l)).slice(0, 2);
    expect(pick.length, 'two category_* labels exist').toBe(2);
    Api.must(await api.cw(`conversations/${conv.id}/labels`, { method: 'POST', body: { labels: ['audit_test', ...pick] } }), 'add');
    Api.must(await api.cw(`conversations/${conv.id}/labels`, { method: 'POST', body: { labels: ['audit_test', pick[0]] } }), 'remove');
    await openConversation(page, conv.id);
    await expect(page.getByText(pick[0]).first()).toBeVisible();
    await openApp(page, `label/${pick[0]}`);
    const contact = await data.testContact();
    await expect(page.locator('.conversation', { hasText: contact.name }).first(), 'conversation listed under the label').toBeVisible();
    ev.add(`added ${pick.join(' + ')}, removed ${pick[1]}; ${pick[0]} shows and its label view lists the conversation`);
    await ev.shot(page);
  },

  'CONV-017': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} snooze check`]);
    const tomorrow = Math.floor(Date.now() / 1000) + 86400;
    const inList = async (status: string) =>
      ((Api.must(await api.cw(`conversations?assignee_type=all&status=${status}&inbox_id=${INBOX.api}`), 'list').data.payload as any[]).some(c => c.id === conv.id));
    Api.must(await api.cw(`conversations/${conv.id}/toggle_status`, { method: 'POST', body: { status: 'snoozed', snoozed_until: tomorrow } }), 'snooze');
    expect(await inList('snoozed'), 'in Snoozed').toBeTruthy();
    expect(await inList('open'), 'not in Open').toBeFalsy();
    ev.add('snoozed until tomorrow: in Snoozed, not in Open');
    Api.must(await api.cw(`conversations/${conv.id}/toggle_status`, { method: 'POST', body: { status: 'resolved' } }), 'resolve');
    expect(await inList('resolved'), 'in Resolved').toBeTruthy();
    ev.add('resolved: in Resolved');
    await data.incoming(conv.id, `${TEST_PREFIX} one more question`);
    await expect.poll(async () => Api.must(await api.cw(`conversations/${conv.id}`), 'get').status, { timeout: 20_000 }).toBe('open');
    ev.add('new customer message reopened it');
    await openConversation(page, conv.id);
    await ev.shot(page);
  },

  'CONV-018': async ({ page, api, data, ev }) => {
    // How many conversations the AI has filled is read from the API (counts
    // only); the UI part runs on the run's own [TEST] conversation.
    const filled = Api.must(
      await api.cw('conversations/filter?page=1', {
        method: 'POST',
        body: { payload: [{ attribute_key: 'case_type', filter_operator: 'is_present', values: [], query_operator: null, custom_attribute_type: 'conversation_attribute' }] },
      }),
      'filter case_type present',
    );
    const rows = filled.payload as any[];
    expect(rows.length, 'conversations with AI-filled case_type').toBeGreaterThan(0);
    const keys = ['case_type', 'case_category', 'case_subcategory', 'case_detail', 'vehicle_model'];
    ev.add(`${filled.meta?.all_count ?? rows.length} conversations have case_type; on page 1: ${keys.map(k => `${k} ${rows.filter(r => r.custom_attributes?.[k]).length}`).join(', ')}`);
    const defs = Api.must(await api.cw('custom_attribute_definitions?attribute_model=0'), 'attribute definitions') as any[];
    const cat = defs.find(d => d.attribute_key === 'case_category');
    const value = cat?.attribute_values?.[1] ?? cat?.attribute_values?.[0];
    expect(value, 'case_category has list values').toBeTruthy();
    const conv = await data.conversation([`${TEST_PREFIX} taxonomy correction`]);
    Api.must(await api.cw(`conversations/${conv.id}/custom_attributes`, { method: 'POST', body: { custom_attributes: { case_category: value } } }), 'set case_category');
    await openConversation(page, conv.id);
    await page.reload();
    await revealInPanel(page, String(value)); // changed case_category kept after refresh
    ev.add(`[TEST] conv ${conv.id}: case_category=${value} kept after refresh`);
    await ev.shot(page);
  },

  'CONV-019': async ({ page, api, ev }) => {
    const c = Api.must(await api.cw('search/contacts?q=60000000101'), 'find Ahmad').payload.contacts as any[];
    expect(c.length, '[DEMO] Ahmad contact').toBeGreaterThan(0);
    expect(c[0].name, 'only the [DEMO] persona contact is opened').toMatch(/\[DEMO\]/);
    const convs = Api.must(await api.cw(`contacts/${c[0].id}/conversations`), 'Ahmad conversations').payload as any[];
    expect(convs.length).toBeGreaterThan(0);
    await openConversation(page, convs[0].id);
    await openContactPanel(page);
    const text = await bodyText(page);
    expect(text, 'DMS card loads or says not connected').toMatch(/Not connected|No DMS\/TSP record|Vehicles|Service history|DMS/);
    expect(text).toMatch(/Previous Conversations/i);
    ev.add(`[DEMO] Ahmad conv ${convs[0].id}: DMS card ${/Not connected/.test(text) ? 'shows Not connected' : 'loaded'}; Previous Conversations listed (${convs.length} conversations)`);
    await ev.shot(page);
  },

  'CONV-020': async ({ page, api, ev }) => {
    const list = Api.must(await api.cw(`conversations?assignee_type=all&status=all&inbox_id=${INBOX.email}`), 'email convs').data.payload as any[];
    let found = 0;
    for (const c of list.slice(0, 10)) {
      if ((await messages(api, c.id)).some(m => m.private && /suggest.*department/i.test(m.content ?? ''))) found += 1;
    }
    ev.add(`${found} of the latest ${Math.min(10, list.length)} email conversations carry an AI department-suggestion note`);
    await openApp(page, `inbox/${INBOX.email}`);
    await ev.shot(page);
    ev.handOff('a fresh email from the external test mailbox is needed to watch a new suggestion appear');
  },

  'CONV-021': manual('needs the email inbox to send the acknowledgement and PIC forward to real test mailboxes'),
  'CONV-022': manual('the PIC must reply from the PIC test mailbox'),
  'CONV-023': manual('dealer ladder emails go to real test mailboxes and a CRE reply is needed'),
  'CONV-024': manual('a live call with audio both ways on the softphone and the test handset'),

  'CONV-025': async ({ page, api, data, ev }) => {
    const conv = await data.conversation(['[TEST] hello']);
    await page.waitForTimeout(15_000);
    const out = (await messages(api, conv.id)).filter(m => m.message_type === 1 || m.message_type === 3);
    ev.add(`within 15 s of creation: ${out.length} automatic message(s): ${out.map(m => String(m.content ?? '').slice(0, 40)).join(' | ')}`);
    await openConversation(page, conv.id);
    await ev.shot(page);
    ev.handOff('the inactivity message needs a wait past the inbox inactivity time (about 10 min) on a real channel');
  },

  'CONV-026': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} thanks, all sorted`]);
    Api.must(await api.cw(`conversations/${conv.id}/toggle_status`, { method: 'POST', body: { status: 'resolved' } }), 'resolve');
    await expect.poll(async () => (await messages(api, conv.id)).some(m => m.content_type === 'input_csat'), { timeout: 30_000 }).toBeTruthy();
    ev.add('resolving posted the CSAT survey message');
    await openConversation(page, conv.id);
    await ev.shot(page);
    ev.handOff('the 5-star rating is given by the customer on the handset survey link');
  },

  'CONV-027': async ({ page, api, ev }) => {
    const inboxes = Api.must(await api.cw('inboxes'), 'inboxes').payload as any[];
    const social = inboxes.filter(i => /FacebookPage|Instagram/.test(i.channel_type));
    await openApp(page, 'settings/inboxes/list');
    await ev.shot(page);
    if (!social.length) ev.blocked('no Messenger or Instagram inbox is connected on proton (needs Meta page access from Proton)');
    ev.add(`social inboxes: ${social.map(i => `${i.name} (${i.channel_type})`).join(', ')}`);
    ev.handOff('messages from test Facebook and Instagram accounts are needed');
  },

  'CONV-028': manual('15-20 MB files sent from the test handset and test mailbox'),
  'CONV-029': manual('a voice note, photo and video sent from the test handset'),

  'CONV-030': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([
      '[TEST] My X70 broke down on the NKVE, engine light came on and it stopped.',
      '[TEST] I am at KM 23 heading to Damansara.',
      '[TEST] The tow truck just arrived, thanks.',
    ]);
    for (const content of ['We are sending roadside assistance now.', 'The tow truck is 20 minutes away.', 'Glad it arrived. We will close this case.']) {
      Api.must(await api.cw(`conversations/${conv.id}/messages`, { method: 'POST', body: { content, message_type: 'outgoing' } }), 'agent reply');
    }
    const thread = (await messages(api, conv.id))
      .filter(m => m.message_type === 0 || m.message_type === 1)
      .map(m => `${m.message_type === 0 ? 'Customer' : 'Agent'}: ${m.content}`);
    expect(thread.length).toBeGreaterThanOrEqual(6);
    const r = await api.be('/assist/summarize', { method: 'POST', body: { conversation_id: String(conv.id), messages: thread } });
    expect(r.status).toBe(200);
    const summary = String(r.data.summary ?? '');
    expect(summary, 'covers issue, action and outcome').toMatch(/break|tow|roadside/i);
    expect(summary).toMatch(/\b(the|customer|was)\b/i);
    ev.add(`summary (${r.ms} ms): ${summary.slice(0, 160)}`);
    await openConversation(page, conv.id);
    await ev.shot(page);
  },

  'CONV-031': manual('needs a reply mailed from inbox 5 to an invalid address (the suite never sends email)'),

  'CONV-032': async ({ page, api, ev }) => {
    const inbox = Api.must(await api.cw(`inboxes/${INBOX.whatsapp}`), 'whatsapp inbox');
    ev.add(`WhatsApp inbox business hours ${inbox.working_hours_enabled ? 'on' : 'off'}, tz ${inbox.timezone}, out-of-office ${inbox.out_of_office_message ? 'set' : 'empty'}`);
    await openApp(page, `settings/inboxes/${INBOX.whatsapp}`);
    await ev.shot(page);
    ev.handOff('a message from the test handset outside business hours');
  },

  'CONV-033': !FLAGS.allowConsoleHandoff
    ? manual('would open a real human handoff from the test console (re-run with --allow-console-handoff to automate it)')
    : async ({ page, ev }) => {
    await openConsole(page);
    await consoleTab(page, 'Chat');
    const first = await chatTurn(page, 'brp lama siap? nk service', /^Anonymous/);
    ev.add(`"brp lama siap? nk service" -> ${first.role}: ${first.text.slice(0, 120)}`);
    expect(first.text, 'reads it as a service-time question').toMatch(/servis|service|jam|hour|minit|minute|masa|time|tempah|book/i);
    const second = await chatTurn(page, 'kete sy rosak kat highway, tlg');
    ev.add(`"kete sy rosak kat highway, tlg" -> ${second.role}: ${second.text.slice(0, 120)} ${second.meta}`);
    expect(`${second.text} ${second.meta}`, 'reads it as a breakdown').toMatch(/tunda|tow|RSA|roadside|selamat|safe|bantuan|kecemasan|emergency|rosak|breakdown|agent|ejen/i);
    await ev.shot(page);
  },

  'CONV-034': async ({ page, api, data, ev }) => {
    // Flag count from the API; the UI check uses two [TEST] conversations of
    // the run's own contact (never a real customer's).
    const flagged = Api.must(await api.cw('conversations?assignee_type=all&status=all&labels[]=possible_duplicate'), 'possible_duplicate list').data.meta.all_count;
    ev.add(`${flagged} conversation(s) flagged possible_duplicate by the AI (count only)`);
    const first = await data.conversation([`${TEST_PREFIX} brake noise after service`]);
    Api.must(await api.cw(`conversations/${first.id}/toggle_status`, { method: 'POST', body: { status: 'resolved' } }), 'resolve first');
    const second = await data.conversation([`${TEST_PREFIX} brake noise is back`]);
    // A Previous Conversations card shows no case id, and its preview (the last
    // message) keeps changing after resolve as automatic notes arrive, and the
    // run's [TEST] contact has many look-alike cases. So: the earlier case must
    // be among the contact's other conversations, and the panel must list all
    // of them.
    const others = async () =>
      ((Api.must(await api.cw(`contacts/${first.contactId}/conversations`), 'contact conversations').payload ?? []) as any[])
        .map(c => c.id as number)
        .filter(id => id !== second.id);
    expect(await others(), `earlier case #${first.id} is one of the contact's other conversations`).toContain(first.id);
    await openConversation(page, second.id);
    await openContactPanel(page);
    // Scoped to the panel's list: the left conversation list previews the same
    // contact's conversations too.
    const prev = page.locator('.contact-conversation--list');
    // Open the accordion only if it is closed (its header button carries
    // rounded-bl-none when open). Clicking it while the list was still
    // fetching closed an open accordion.
    const header = page.locator('button', { hasText: /^\s*Previous Conversations\s*$/ }).filter({ visible: true }).first();
    await header.scrollIntoViewIfNeeded();
    if (!/rounded-bl-none/.test((await header.getAttribute('class')) ?? '')) await header.click();
    await expect(header, 'Previous Conversations open').toHaveClass(/rounded-bl-none/);
    await expect(page.getByText('There are no previous conversations associated to this contact.'), 'panel does not say "no previous conversations"').toHaveCount(0);
    await expect(prev.locator('.conversation').first(), 'Previous Conversations lists cards').toBeVisible({ timeout: 20_000 });
    const want = (await others()).length;
    await expect
      .poll(() => prev.locator('.conversation').count(), { timeout: 20_000, message: `Previous Conversations lists all ${want} earlier cases (incl. #${first.id})` })
      .toBe(want);
    ev.add(`[TEST] conv ${second.id}: Previous Conversations lists all ${want} earlier cases of the contact, including #${first.id}; no automatic merge`);
    await ev.shot(page);
  },
});
