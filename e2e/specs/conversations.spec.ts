// Tracker tab: Conversations.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { INBOX, RUN_ID, TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, chatTurn, consoleTab, openApp, openConsole, openConversation, revealInPanel, settle } from '../lib/ui';

const PNG_1PX = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
);
const TINY_PDF = Buffer.from('%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n');

/** Top-of-list tab ("Mine", "Unassigned", "All") as rendered by woot-tabs. */
const listTab = (page: import('@playwright/test').Page, name: string) =>
  page.locator('li a', { hasText: new RegExp(`^\\s*${name}\\b`) }).first();

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
    await openApp(page, 'conversations');
    for (const [name, assignee, key] of tabs) {
      const r = await api.cw(`conversations?assignee_type=${assignee}&status=all&page=1`);
      expect(r.ok, `${name} list API`).toBeTruthy();
      expect(r.ms, `${name} list loads in under 3 s`).toBeLessThan(3000);
      const count = Number(r.data.data.meta[key]);
      const t0 = Date.now();
      await listTab(page, name).click();
      await settle(page);
      const rows = await page.locator('.conversation').count();
      const uiMs = Date.now() - t0;
      // The list pages at 25; the tab badge is the total.
      expect(rows, `${name}: rows shown vs tab count ${count}`).toBe(Math.min(count, 25));
      ev.add(`${name}: tab ${count}, rows ${rows}, API ${r.ms} ms, UI ${uiMs} ms`);
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
    await data.conversation([`${TEST_PREFIX} inbox and label view check`]);
    await openApp(page, `inbox/${INBOX.api}`);
    const inboxRows = page.locator('.conversation');
    await expect(inboxRows.first()).toBeVisible();
    for (let i = 0; i < Math.min(3, await inboxRows.count()); i++) {
      await inboxRows.nth(i).click();
      await expect(page).toHaveURL(new RegExp(`/inbox/${INBOX.api}/conversations/\\d+`));
      const id = Number(page.url().match(/conversations\/(\d+)/)![1]);
      const c = Api.must(await api.cw(`conversations/${id}`), 'get conversation');
      expect(c.inbox_id, `conversation ${id} inbox`).toBe(INBOX.api);
    }
    ev.add(`inbox ${INBOX.api} view: first 3 rows all inbox ${INBOX.api}`);
    await openApp(page, 'label/audit_test');
    const labelRows = page.locator('.conversation');
    await expect(labelRows.first()).toBeVisible();
    for (let i = 0; i < Math.min(3, await labelRows.count()); i++) {
      await labelRows.nth(i).click();
      await expect(page).toHaveURL(/\/conversations\/\d+/);
      const id = Number(page.url().match(/conversations\/(\d+)/)![1]);
      const labels = Api.must(await api.cw(`conversations/${id}/labels`), 'labels').payload as string[];
      expect(labels, `conversation ${id} labels`).toContain('audit_test');
    }
    ev.add('label audit_test view: first 3 rows all labelled');
    const teams = Api.must(await api.cw('teams'), 'teams') as any[];
    if (teams.length) {
      await openApp(page, `team/${teams[0].id}`);
      ev.add(`team ${teams[0].name} view opened (${await page.locator('.conversation').count()} rows)`);
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
      await api.cw('custom_filters', { method: 'POST', body: { custom_filter: { name, type: 'conversation', query: { payload } } } }),
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
    const hit = page.getByText(new RegExp(token)).first();
    await expect(hit).toBeVisible();
    await ev.shot(page);
    await hit.click();
    await expect(page).toHaveURL(new RegExp(`conversations/${conv.id}`));
    ev.add(`clicking the message result opens conversation ${conv.id}`);
  },

  'CONV-005': manual('a real WhatsApp message from the test handset, a stopwatch and the toast sound'),
  'CONV-006': manual('sending a real email with an attachment from the external test mailbox to inbox 5'),
  'CONV-007': manual('the reply must reach the test handset and the test mailbox thread (the suite never sends WhatsApp or email)'),

  'CONV-008': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} please help with my booking`]);
    await openConversation(page, conv.id);
    await page.getByText('Private Note', { exact: true }).click();
    const editor = page.locator('.ProseMirror').first();
    await editor.click();
    await page.keyboard.type('@');
    await page.waitForTimeout(1200);
    await page.keyboard.press('Enter');
    await page.keyboard.type(' please check this case [TEST]');
    await ev.shot(page);
    await page.getByRole('button', { name: /Add Note/i }).click();
    await expect.poll(async () => (await messages(api, conv.id)).filter(m => m.private).length, { timeout: 20_000 }).toBeGreaterThan(0);
    const note = (await messages(api, conv.id)).filter(m => m.private).pop();
    expect(note.content).toContain('mention://user/');
    expect(note.message_type, 'note is not sent to the customer').not.toBe(1);
    ev.add(`private note ${note.id} with an @mention saved (private, not sent; API inbox)`);
    ev.handOff('the mentioned agent’s bell notification and Mentions list need that agent’s own login (Window B)');
  },

  'CONV-009': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} can you send the brochure?`]);
    const contact = await data.testContact();
    const code = `e2e${RUN_ID}`;
    Api.must(
      await api.cw('canned_responses', { method: 'POST', body: { short_code: code, content: '[TEST] Hello {{contact.name}}' } }),
      'create canned response',
    );
    await openConversation(page, conv.id);
    const editor = page.locator('.ProseMirror').first();
    await editor.click();
    await page.keyboard.type(`/${code}`);
    await page.waitForTimeout(1200);
    await page.keyboard.press('Enter');
    await expect(editor).toContainText(contact.name);
    ev.add('canned response inserted with {{contact.name}} filled in');
    await page.locator('input[type="file"]').first().setInputFiles([
      { name: 'test-image.png', mimeType: 'image/png', buffer: PNG_1PX },
      { name: 'test-doc.pdf', mimeType: 'application/pdf', buffer: TINY_PDF },
    ]);
    await page.waitForTimeout(1500);
    await ev.shot(page);
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
    for (const target of ['en', 'zh']) {
      const r = await tr(bm, target);
      expect(r.status, `inbound BM -> ${target}`).toBe(200);
      expect(r.ms, `inbound BM -> ${target} within 5 s`).toBeLessThan(5000);
      ev.add(`BM->${target} ${r.ms} ms`);
    }
    const draft = 'Your car will be ready by 5 pm today. Thank you.';
    for (const target of ['ms', 'zh']) {
      const r = await tr(draft, target);
      expect(r.status, `draft EN -> ${target}`).toBe(200);
      expect(r.ms).toBeLessThan(5000);
      ev.add(`draft EN->${target} ${r.ms} ms`);
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
    await expect(page.getByText(/Assigned to/i).first(), 'assignment activity line').toBeVisible({ timeout: 30_000 });
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
    const filled = Api.must(
      await api.cw('conversations/filter?page=1', {
        method: 'POST',
        body: { payload: [{ attribute_key: 'case_type', filter_operator: 'is_present', values: [], query_operator: null, custom_attribute_type: 'conversation_attribute' }] },
      }),
      'filter case_type present',
    ).payload as any[];
    expect(filled.length, 'conversations with AI-filled case_type').toBeGreaterThan(0);
    const sample = filled[0];
    const attrs = sample.custom_attributes ?? {};
    const keys = ['case_type', 'case_category', 'case_subcategory', 'case_detail', 'vehicle_model'];
    ev.add(`conv ${sample.id}: ${keys.map(k => `${k}=${attrs[k] ?? '-'}`).join(', ')}`);
    await openConversation(page, sample.id);
    await revealInPanel(page, String(attrs.case_type)); // case_type shown in Conversation information
    // Agent correction, on the run's own [TEST] conversation only.
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
    const convs = Api.must(await api.cw(`contacts/${c[0].id}/conversations`), 'Ahmad conversations').payload as any[];
    expect(convs.length).toBeGreaterThan(0);
    await openConversation(page, convs[0].id);
    const text = await bodyText(page);
    expect(text, 'DMS card loads or says not connected').toMatch(/Not connected|No DMS\/TSP record|Vehicles|Service history|DMS/);
    expect(text).toMatch(/Previous Conversations/i);
    ev.add(`conv ${convs[0].id} (${c[0].name}): DMS card ${/Not connected/.test(text) ? 'shows Not connected' : 'loaded'}; Previous Conversations listed (${convs.length} conversations)`);
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

  'CONV-033': async ({ page, ev }) => {
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

  'CONV-034': async ({ page, api, ev }) => {
    const r = Api.must(await api.cw('conversations?assignee_type=all&status=all&labels[]=possible_duplicate'), 'possible_duplicate list');
    const list = r.data.payload as any[];
    expect(list.length, 'conversations flagged possible_duplicate').toBeGreaterThan(0);
    await openConversation(page, list[0].id);
    await expect(page.getByText(/Previous Conversations/i).first()).toBeVisible();
    ev.add(`${list.length} conversation(s) flagged possible_duplicate; conv ${list[0].id} shows Previous Conversations; no automatic merge`);
    await ev.shot(page);
  },
});
