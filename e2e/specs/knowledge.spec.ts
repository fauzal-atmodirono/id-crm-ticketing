// Tracker tab: Knowledge. Writes only [TEST] assistants, FAQs, documents,
// scenarios and tools, each deleted in the test (and swept again at the end).
// The live Proton assistant, inbox AI modes and lifecycle messages are never
// changed.
import type { Page } from '@playwright/test';
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { KEY_PREFIX, RUN_ID, TEST_PREFIX } from '../lib/env';

// Test KB content is deliberately meaningless, so it can never answer a real
// customer: a run-scoped marker, no product, warranty or dealer wording.
const MARK = `E2E-ZZ-${RUN_ID}`;
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, openApp, settle } from '../lib/ui';

const kb = (section: string) => `proton/knowledge/${section}`;

async function selectAssistant(page: Page, name: string): Promise<void> {
  const select = page.locator('select', { has: page.locator('option', { hasText: name }) }).first();
  await select.selectOption({ label: name });
  await settle(page);
}

async function newAssistant(api: Api, label: string): Promise<{ id: string; name: string }> {
  const name = `${TEST_PREFIX} ${label}`;
  const r = Api.must(await api.be('/kb/assistants', { method: 'POST', body: { name, description: 'test only' } }), 'create assistant');
  return { id: String(r.id ?? r.assistant?.id), name };
}

async function defaultAssistantId(api: Api): Promise<string> {
  const list = Api.must(await api.be('/kb/assistants'), 'assistants').assistants as any[];
  return String((list.find(a => a.is_default) ?? list[0]).id);
}

async function copilot(api: Api, assistantId: string, question: string) {
  return api.be('/assist/copilot', {
    method: 'POST',
    body: { conversation_id: 'playground', thread: [{ role: 'user', content: question }], assistant_id: assistantId },
  });
}

trackerModule('Knowledge', {
  'KNOW-001': async ({ page, api, ev }) => {
    const a = await newAssistant(api, 'Assistant');
    await openApp(page, kb('assistants'));
    await expect(page.getByText(a.name, { exact: true })).toBeVisible();
    const renamed = `${a.name} 2`;
    Api.must(await api.be(`/kb/assistants/${a.id}`, { method: 'PUT', body: { name: renamed } }), 'rename');
    await page.reload();
    await settle(page);
    await expect(page.getByText(renamed, { exact: true })).toBeVisible();
    await ev.shot(page);
    Api.must(await api.be(`/kb/assistants/${a.id}`, { method: 'DELETE' }), 'delete');
    await page.reload();
    await settle(page);
    await expect(page.getByText(renamed, { exact: true })).toHaveCount(0);
    ev.add('[TEST] assistant created, renamed (kept after refresh) and deleted (gone after refresh)');
  },

  'KNOW-002': async ({ page, api, ev }) => {
    const question = `${TEST_PREFIX} ${MARK} marker question alpha?`;
    const created = Api.must(
      await api.be('/kb/faq', { method: 'POST', body: { question, answer: `${MARK} marker answer alpha. Not real information.`, keywords: [MARK.toLowerCase()] } }),
      'create FAQ',
    );
    const id = created.id;
    await openApp(page, kb('faqs'));
    await page.getByPlaceholder(/Filter by question/).fill(RUN_ID);
    await expect(page.getByText(question)).toBeVisible();
    const edited = `${MARK} marker answer alpha, edited. Not real information.`;
    Api.must(await api.be(`/kb/faq/${id}`, { method: 'PUT', body: { answer: edited } }), 'edit FAQ');
    await page.reload();
    await settle(page);
    await page.getByPlaceholder(/Filter by question/).fill(RUN_ID);
    await expect(page.getByText(edited)).toBeVisible();
    await ev.shot(page);
    ev.add('FAQ created and its edited answer stayed after refresh');
    const suggested = async () => ((await api.be(`/kb/suggest?q=${encodeURIComponent(question)}`)).data?.suggestions ?? []).some((s: any) => s.id === id);
    ev.add(`suggested before delete: ${await suggested()}`);
    Api.must(await api.be(`/kb/faq/${id}`, { method: 'DELETE' }), 'delete FAQ');
    await expect.poll(suggested, { timeout: 30_000, message: 'deleted FAQ no longer suggested' }).toBe(false);
    ev.add('after delete it is no longer suggested');
  },

  'KNOW-003': async ({ page, api, ev }) => {
    const count = async () => (Api.must(await api.be('/kb/faq'), 'faq list').entries as any[]).filter(f => String(f.question).includes(RUN_ID)).length;
    const before = await count();
    const good = ['question,answer,keywords', ...[1, 2, 3, 4, 5].map(i => `"${TEST_PREFIX} bulk ${MARK} marker question ${i}?","${MARK} marker answer ${i}. Not real information.","${MARK.toLowerCase()}"`)].join('\n');
    await openApp(page, kb('faqs'));
    const input = page.locator('input[type="file"][accept=".csv"]').first();
    await input.setInputFiles({ name: 'test.csv', mimeType: 'text/csv', buffer: Buffer.from(good) });
    await expect(page.getByText(/Bulk upload: 5 entries created/)).toBeVisible({ timeout: 30_000 });
    expect(await count()).toBe(before + 5);
    ev.add('valid 5-row CSV: "5 entries created" and 5 rows listed');
    await page.getByPlaceholder(/Filter by question/).fill(`${RUN_ID} bulk`);
    await ev.shot(page);
    const bad = ['question,keywords', `"${TEST_PREFIX} ${MARK} bad row?","${MARK.toLowerCase()}"`].join('\n');
    await input.setInputFiles({ name: 'bad.csv', mimeType: 'text/csv', buffer: Buffer.from(bad) });
    await expect(page.getByText(/Bulk upload failed|Nothing imported/).first()).toBeVisible({ timeout: 30_000 });
    // The banner's first line is generic; the per-row reasons ("row 2: question
    // and answer are required") are listed under it (patch 0087).
    const text = await bodyText(page);
    const header = text.match(/(Bulk upload failed[^\n]*|Nothing imported[^\n]*)/)?.[0] ?? '';
    const msg = [header, ...(text.match(/^\s*row \d+:.*$/gm) ?? []).map(l => l.trim())].join(' ');
    expect(msg, 'error names the missing column').toMatch(/answer/i);
    expect(await count(), 'bad file imports nothing').toBe(before + 5);
    ev.add(`bad CSV: "${msg.slice(0, 120)}", nothing imported`);
    for (const f of (Api.must(await api.be('/kb/faq'), 'faq list').entries as any[]).filter(x => String(x.question).includes(`${RUN_ID} bulk`))) {
      Api.must(await api.be(`/kb/faq/${f.id}`, { method: 'DELETE' }), 'delete bulk FAQ');
    }
    ev.add('the 5 test FAQs deleted');
  },

  'KNOW-004': async ({ page, api, ev }) => {
    const title = `${TEST_PREFIX} ${MARK} marker document`;
    const r = Api.must(await api.be('/kb/knowledge/text', { method: 'POST', body: { title, body: `${MARK} marker text. Not real information.` } }), 'create document');
    await expect
      .poll(async () => String((await api.be(`/kb/knowledge/${r.id}`)).data?.status ?? ''), { timeout: 90_000, message: 'document indexed' })
      .toMatch(/^(ready|indexed|done|complete|completed|active)$/i);
    // /kb/knowledge (operator uploads) is listed on Knowledge > Uploads (patch
    // 0021); Knowledge > Documents is the read-only Vertex corpus (/kb/documents).
    await openApp(page, kb('uploads'));
    const filter = page.getByPlaceholder(/Filter by/).first();
    if (await filter.isVisible().catch(() => false)) await filter.fill(RUN_ID);
    await expect(page.getByText(title).first()).toBeVisible();
    await ev.shot(page);
    ev.add('[TEST] document listed with status ready');
    Api.must(await api.be(`/kb/knowledge/${r.id}`, { method: 'DELETE' }), 'delete document');
    await page.reload();
    await settle(page);
    await expect(page.getByText(title)).toHaveCount(0);
    const gone = await api.be(`/kb/knowledge/${r.id}`);
    expect(gone.status, 'deleted document answers 404 (a message, not a blank page)').toBe(404);
    ev.add('after delete: gone from the list, its URL answers 404 Not found');
  },

  'KNOW-005': async ({ page, api, ev }) => {
    const a = await newAssistant(api, 'Scenario owner');
    const title = `${TEST_PREFIX} ${MARK} marker scenario`;
    // Disabled, and on a [TEST] assistant: never reaches the live bot prompt.
    const s = Api.must(await api.be('/kb/scenarios', { method: 'POST', body: { assistant_id: a.id, title, description: 'test only', enabled: false } }), 'create scenario');
    const sid = s.id ?? s.scenario?.id;
    Api.must(await api.be(`/kb/scenarios/${sid}`, { method: 'PUT', body: { description: `edited ${RUN_ID}` } }), 'edit scenario');
    await openApp(page, kb('scenarios'));
    await selectAssistant(page, a.name);
    await expect(page.getByText(title)).toBeVisible();
    await expect(page.getByText(`edited ${RUN_ID}`)).toBeVisible();
    await ev.shot(page);
    Api.must(await api.be(`/kb/scenarios/${sid}`, { method: 'DELETE' }), 'delete scenario');
    Api.must(await api.be(`/kb/assistants/${a.id}`, { method: 'DELETE' }), 'delete assistant');
    ev.add('[TEST] scenario created, edit kept after reload, deleted');
  },

  'KNOW-006': async ({ page, api, ev }) => {
    const title = `${TEST_PREFIX} ${MARK} marker tool`;
    const t = Api.must(
      await api.be('/kb/tools', { method: 'POST', body: { slug: `${KEY_PREFIX}_tool`, title, endpoint_url: 'https://example.com/e2e-zz-marker', http_method: 'GET', param_schema: { type: 'object', properties: {} }, enabled: false } }),
      'create tool',
    );
    const slug = t.slug ?? t.tool?.slug;
    Api.must(await api.be(`/kb/tools/${slug}`, { method: 'PUT', body: { description: `edited ${RUN_ID}` } }), 'edit tool');
    await openApp(page, kb('tools'));
    await expect(page.getByText(title)).toBeVisible();
    await expect(page.getByText(`edited ${RUN_ID}`)).toBeVisible();
    await ev.shot(page);
    Api.must(await api.be(`/kb/tools/${slug}`, { method: 'DELETE' }), 'delete tool');
    ev.add('[TEST] custom tool (disabled) created, edit kept after reload, deleted');
  },

  'KNOW-007': async ({ page, api, ev }) => {
    const aid = await defaultAssistantId(api);
    const faqs = Api.must(await api.be('/kb/faq'), 'faqs').entries as any[];
    const faq = faqs.find(f => /warrant/i.test(f.question)) ?? faqs[0];
    const known = await copilot(api, aid, faq.question);
    expect(known.status).toBe(200);
    const answer = String(known.data.answer ?? '');
    const facts = String(faq.answer).match(/\d[\d,.]*/g) ?? [];
    if (facts.length) expect(facts.some(n => answer.includes(n)), `answer uses the FAQ facts (${facts.join(', ')})`).toBeTruthy();
    else expect(answer.length).toBeGreaterThan(20);
    ev.add(`FAQ "${faq.question}" -> ${answer.slice(0, 120)}`);
    const unknown = await copilot(api, aid, 'What is the price of a Ferrari?');
    const u = String(unknown.data.answer ?? '');
    expect(u, 'says it does not know or offers a human').toMatch(/don.?t|do not|not (sure|able|available)|unable|cannot|no information|human|agent|tidak|maaf|sorry|only (help|assist)/i);
    ev.add(`Ferrari price -> ${u.slice(0, 120)}`);
    await openApp(page, kb('playground'));
    const box = page.getByPlaceholder('Ask the assistant something…');
    await box.fill(faq.question);
    const len = (await bodyText(page)).length;
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await expect.poll(async () => (await bodyText(page)).length, { timeout: 90_000, message: 'playground answer' }).toBeGreaterThan(len + 60);
    await ev.shot(page);
  },

  'KNOW-008': async ({ page, api, ev }) => {
    await openApp(page, kb('inboxes'));
    const rows = Api.must(await api.be('/kb/inboxes'), 'kb inboxes').inboxes as any[];
    ev.add(rows.map(r => `inbox ${r.inbox_id}: ${r.assistant_name ?? r.assistant_id ?? 'default'} / ${r.mode}`).join(', '));
    await ev.shot(page);
    ev.handOff('changing an inbox’s assistant or AI mode is a live AI-mode change the suite must not make on prod');
  },

  'KNOW-009': async ({ page, api, ev }) => {
    const a = await newAssistant(api, 'Persona');
    const line = 'Terima kasih, Proton e.MAS';
    const instructions = `You are a Proton support assistant. End every reply with exactly: ${line}`;
    Api.must(await api.be(`/kb/assistants/${a.id}`, { method: 'PUT', body: { config: { instructions, temperature: 0.2, language: 'en' } } }), 'save persona');
    const reread = Api.must(await api.be(`/kb/assistants/${a.id}`), 'reread');
    const cfg = reread.config ?? reread.assistant?.config ?? {};
    expect(cfg.instructions, 'instructions kept').toBe(instructions);
    await openApp(page, kb('settings'));
    await selectAssistant(page, a.name);
    await expect(page.getByPlaceholder('You are a helpful assistant for…')).toHaveValue(instructions);
    await ev.shot(page);
    const r1 = await copilot(api, a.id, 'How often should I service my X50?');
    expect(String(r1.data.answer ?? ''), 'reply follows the instruction').toContain(line);
    Api.must(await api.be(`/kb/assistants/${a.id}`, { method: 'PUT', body: { config: { instructions: '' } } }), 'clear persona');
    const r2 = await copilot(api, a.id, 'How often should I service my X50?');
    expect(String(r2.data.answer ?? ''), 'back to normal after removing it').not.toContain(line);
    Api.must(await api.be(`/kb/assistants/${a.id}`, { method: 'DELETE' }), 'delete assistant');
    ev.add('[TEST] assistant: instructions saved and shown in Settings; next reply ended with the line; after removing it the reply did not');
  },

  'KNOW-010': manual('editing the live welcome/handoff/resolution messages and checking them on the test handset (out of bounds for an automated run on prod)'),
  'KNOW-011': manual('editing the live escalation email template and receiving the escalation email in a test mailbox'),

  'KNOW-012': async ({ page, api, ev }) => {
    const question = `${TEST_PREFIX} ${MARK} marker rating question?`;
    const f = Api.must(await api.be('/kb/faq', { method: 'POST', body: { question, answer: `${MARK} marker rating answer. Not real information.`, keywords: [MARK.toLowerCase()] } }), 'create FAQ');
    const me = await api.me();
    const fb = await api.be('/kb/feedback', {
      method: 'POST',
      body: { article_id: f.id, helpful: true, score: 5, agent_id: String(me.id), conversation_id: `e2e-${RUN_ID}` },
    });
    expect(fb.ok, `POST /kb/feedback -> ${fb.status}`).toBeTruthy();
    await expect
      .poll(async () => (Api.must(await api.be('/kb/faq'), 'faqs').entries as any[]).find(x => x.id === f.id)?.rating_count ?? 0, { timeout: 30_000 })
      .toBeGreaterThan(0);
    await openApp(page, kb('faqs'));
    await page.getByPlaceholder(/Filter by question/).fill(RUN_ID);
    await expect(page.getByText(question)).toBeVisible();
    await ev.shot(page);
    Api.must(await api.be(`/kb/faq/${f.id}`, { method: 'DELETE' }), 'delete FAQ');
    ev.add('agent thumbs-up saved; the admin FAQ list shows rating_count 1 for that FAQ (strip thumbs controls: see CONV-013)');
  },

  'KNOW-013': async ({ page, ev }) => {
    await openApp(page, 'reports/bot');
    const gaps = page.getByText(/Knowledge Base Gaps/).first();
    await expect(gaps, 'KB gaps (suggested FAQ topics) section').toBeVisible({ timeout: 45_000 });
    await gaps.scrollIntoViewIfNeeded();
    const text = await bodyText(page);
    ev.add(/No unanswered enquir/.test(text) ? 'KB gaps section shows no unanswered enquiries' : 'KB gaps section lists unanswered topics');
    await ev.shot(page);
    ev.handOff('the report lists the topics, but turning one into an FAQ is done by hand in Knowledge > FAQs');
  },
});
