// Tracker tab: AI capabilities. Customer-facing bot turns go through the test
// console's Chat tab (Anonymous), never through a real customer channel.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { FLAGS, TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, chatTurn, consoleTab, newChatSession, openApp, openConsole, openConversation } from '../lib/ui';

const HANDOFF = /human agent|Escalated|Connected to/i;
const NO_HANDOFF = 'would open a real human handoff from the test console (routed to live agents, may notify PICs); re-run with --allow-console-handoff to automate it';

trackerModule('AI capabilities', {
  'AI-001': !FLAGS.allowConsoleHandoff
    ? manual(NO_HANDOFF)
    : async ({ page, ev }) => {
    await openConsole(page);
    await consoleTab(page, 'Chat');
    const faq = await chatTurn(page, 'How often should I service my Proton X50?', /^Anonymous/);
    expect(faq.role, 'FAQ question answered by the bot').toBe('assistant');
    expect(faq.text).toMatch(/km|month|bulan|servis|service/i);
    ev.add(`FAQ -> ${faq.text.slice(0, 120)}`);
    await newChatSession(page);
    const complex = await chatTurn(page, 'My car was repaired 3 times and still fails, I want compensation.');
    ev.add(`complaint -> ${complex.role}: ${complex.text.slice(0, 120)} ${complex.meta}`);
    expect(`${complex.text}`, 'complex complaint handed to a human').toMatch(HANDOFF);
    await ev.shot(page);
  },

  'AI-002': !FLAGS.allowConsoleHandoff
    ? manual(NO_HANDOFF)
    : async ({ page, ev }) => {
    await openConsole(page);
    await consoleTab(page, 'Chat');
    const angry = await chatTurn(page, 'This is the third time! Nobody helps me, I am very angry.', /^Anonymous/);
    ev.add(`angry -> ${angry.role}: ${angry.text.slice(0, 80)} [${angry.meta}]`);
    expect(`${angry.text} ${angry.meta}`, 'flagged dissatisfied (sentiment or handoff)').toMatch(/sentiment=(negative|dissatisfied|angry|urgent)|urgency=|human agent|Escalated/i);
    await newChatSession(page);
    const urgent = await chatTurn(page, 'Kereta mati tengah jalan, anak saya dalam kereta.');
    ev.add(`urgent -> ${urgent.role}: ${urgent.text.slice(0, 80)} [${urgent.meta}]`);
    expect(`${urgent.text} ${urgent.meta}`, 'flagged urgent').toMatch(/sentiment=urgent|urgency=(high|urgent|critical)|human agent|Escalated|kecemasan|emergency|999|selamat/i);
    await ev.shot(page);
  },

  'AI-003': manual('a photo of a dashboard warning light sent from the test handset (the test console has no image upload)'),

  'AI-004': async ({ page, api, data, ev }) => {
    const q = '[TEST] My X50 battery died after 2 years, is it still under warranty?';
    const conv = await data.conversation([q]);
    const r = await api.be('/assist/suggest', { method: 'POST', body: { conversation_id: String(conv.id), messages: [`Customer: ${q}`] } });
    expect(r.status).toBe(200);
    const sources = (r.data.sources ?? []) as any[];
    expect(sources.length, 'draft cites knowledge sources').toBeGreaterThan(0);
    ev.add(`draft (${r.ms} ms) cites ${sources.length} source(s): ${sources.map(s => s.title ?? s.source ?? s.kind ?? '?').slice(0, 3).join(' | ')}`);
    const resolvedCase = sources.some(s => /case|resolved/i.test(JSON.stringify(s)));
    ev.add(resolvedCase ? 'includes a past resolved case' : 'no past resolved case cited for this question');
    await openConversation(page, conv.id);
    await ev.shot(page);
  },

  'AI-005': async ({ page, api, ev }) => {
    await openApp(page, 'proton/ai-activity');
    await expect(page.getByRole('heading', { name: 'AI activity' })).toBeVisible();
    const r = Api.must(await api.be('/admin/ai-activity'), 'ai activity');
    ev.add(`AI activity page loads; summary ${JSON.stringify(r.summary ?? {}).slice(0, 160)}`);
    expect(await bodyText(page)).not.toMatch(/Could not load AI activity/);
    await ev.shot(page);
  },

  'AI-006': async ({ page, api, data, ev }) => {
    const conv = await data.conversation([`${TEST_PREFIX} Tamil reply check`]);
    const r = await api.be('/assist/translate', {
      method: 'POST',
      body: { conversation_id: String(conv.id), text: 'Your car is ready for collection.', target_language: 'ta' },
    });
    await openConversation(page, conv.id);
    await ev.shot(page);
    if (r.status === 403) ev.blocked('outbound Tamil is switched off on proton (TRANSLATION_OUTBOUND_TAMIL_ENABLED); translate to ta answers 403');
    expect(r.status).toBe(200);
    const ta = String(r.data.translation ?? '');
    Api.must(await api.cw(`conversations/${conv.id}/messages`, { method: 'POST', body: { content: ta, message_type: 'outgoing' } }), 'send Tamil reply');
    ev.add(`Tamil reply sent on the [TEST] API conversation: ${ta.slice(0, 60)}`);
  },

  'AI-007': async ({ page, ev }) => {
    await openApp(page, 'reports/bot');
    const text = await bodyText(page);
    await ev.shot(page, { fullPage: true });
    if (/token|AI cost|usage/i.test(text)) {
      ev.add('Bot report shows AI usage/cost figures');
      return;
    }
    ev.handOff('AI token/cost usage per day and conversation lives in the BigQuery views (v_ai_cost, v_ai_token_usage), not on a CRM screen; check it in Power BI');
  },

  'AI-008': manual('a supervisor judges 10 AI categorisations against their own'),
});
