// Tracker tab: Voice & Phone, plus the new call-recording players and the
// test console's persona features (extra tests VOICE-X.., not sheet rows).
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { CALL_ARCHIVE_CONVERSATION, FLAGS } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, chatTurn, consoleTab, openApp, openConsole, openConversation } from '../lib/ui';

const PHONE_PERSONAS = [/^Ahmad/, /^Nurul/, /^Lim/, /^Farid/, /^New caller/, /^Unknown caller/];

trackerModule(
  'Voice & Phone',
  {
    'VOICE-001': manual('a real call to the Proton number from the test handset, speaking to the IVR'),
    'VOICE-002': manual('real calls in Bahasa Malaysia, English, Chinese and Tamil'),
    'VOICE-003': manual('a live call handed to an agent while someone speaks'),
    'VOICE-004': manual('a live call on the softphone to mute, hold and transfer'),

    'VOICE-005': async ({ page, ev }) => {
      // In-note call player (patch 0095) on the conversation with a
      // "[Call archived…]" note.
      await openConversation(page, CALL_ARCHIVE_CONVERSATION);
      const note = page.locator('[data-proton-call-note]').first();
      await expect(note, 'call archive note renders as a player').toBeVisible({ timeout: 30_000 });
      const play = note.getByRole('button', { name: /Play recording/ });
      await expect(play).toBeVisible();
      expect(await note.innerText(), 'no gs:// text in the note bubble').not.toContain('gs://');
      expect(await bodyText(page), 'no gs:// text in the timeline or the list preview').not.toContain('gs://');
      ev.add(`conv ${CALL_ARCHIVE_CONVERSATION}: note shows "▶ Play recording", no gs:// text in bubble or list`);
      await play.click();
      const audio = note.locator('audio');
      await expect(audio, 'audio element gets a src').toHaveAttribute('src', /\S+/, { timeout: 20_000 });
      await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime), { timeout: 30_000, message: 'playback advances' }).toBeGreaterThan(0.5);
      const t = await audio.evaluate((a: HTMLAudioElement) => a.currentTime);
      await audio.evaluate((a: HTMLAudioElement) => a.pause());
      ev.add(`playing: currentTime ${t.toFixed(1)} s`);
      const view = note.getByRole('button', { name: /View transcript/ });
      if (await view.isVisible().catch(() => false)) {
        await view.click();
        const pre = note.locator('pre');
        await expect(pre).toBeVisible({ timeout: 20_000 });
        const transcript = (await pre.innerText()).trim();
        expect(transcript.length, 'transcript text').toBeGreaterThan(0);
        ev.add(`transcript opened (${transcript.length} chars)`);
      } else {
        ev.add('no transcript line on this note');
      }
      await ev.shot(page);
    },

    'VOICE-006': manual('a live call where the caller asks the AI for a person'),
    'VOICE-007': manual('a call outside business hours and a voicemail'),

    'VOICE-008': async ({ page, api, ev }) => {
      const r = Api.must(await api.cw('conversations?assignee_type=all&status=all&labels[]=csat_5'), 'csat_5 list');
      ev.add(`${r.data.meta.all_count} conversation(s) carry a saved 5/5 call rating (csat_5)`);
      await openApp(page, 'label/csat_5');
      await ev.shot(page);
      ev.handOff('a real call where the caller rates the agent on the keypad or by voice');
    },

    'VOICE-009': manual('a real call dropped while every agent is busy'),
  },
  [
    {
      id: 'VOICE-X01',
      title: 'Sidebar call recording player plays the latest call',
      fn: async ({ page, ev }) => {
        await openConversation(page, CALL_ARCHIVE_CONVERSATION);
        const panel = page.locator('[data-proton-call-recording]').first();
        await expect(panel, 'sidebar "Call recording" panel').toBeVisible({ timeout: 30_000 });
        const audio = panel.locator('audio');
        await expect(audio).toHaveAttribute('src', /\S+/);
        expect(await panel.innerText()).not.toContain('gs://');
        await audio.evaluate((a: HTMLAudioElement) => a.play());
        await expect.poll(() => audio.evaluate((a: HTMLAudioElement) => a.currentTime), { timeout: 30_000 }).toBeGreaterThan(0.5);
        const t = await audio.evaluate((a: HTMLAudioElement) => a.currentTime);
        await audio.evaluate((a: HTMLAudioElement) => a.pause());
        ev.add(`sidebar player on conv ${CALL_ARCHIVE_CONVERSATION}: src set, played to ${t.toFixed(1)} s`);
        await ev.shot(page);
      },
    },
    {
      id: 'VOICE-X02',
      title: 'Test console Chat tab: talking as Ahmad, the reply knows his name',
      fn: async ({ page, ev }) => {
        await openConsole(page);
        await consoleTab(page, 'Chat');
        const r = await chatTurn(page, 'Hi, do you know who I am and which car I drive?', /^Ahmad/);
        ev.add(`as Ahmad -> ${r.role}: ${r.text.slice(0, 160)}`);
        expect(r.text, 'reply mentions Ahmad').toMatch(/Ahmad/);
        await ev.shot(page);
      },
    },
    {
      id: 'VOICE-X03',
      title: 'Test console Chat tab: Anonymous gets no CRM name',
      fn: async ({ page, ev }) => {
        await openConsole(page);
        await consoleTab(page, 'Chat');
        const r = await chatTurn(page, 'Hi, do you know who I am and which car I drive?', /^Anonymous/);
        ev.add(`anonymous -> ${r.role}: ${r.text.slice(0, 160)}`);
        expect(r.text, 'reply does not mention Ahmad').not.toMatch(/Ahmad/);
        await ev.shot(page);
      },
    },
    {
      id: 'VOICE-X04',
      title: 'Test console Phone tab lists the demo personas',
      fn: async ({ page, ev }) => {
        await openConsole(page);
        await consoleTab(page, 'Phone');
        const options = await page.locator('select.persona__select option').allInnerTexts();
        for (const re of PHONE_PERSONAS) expect(options.some(o => re.test(o)), `persona ${re}`).toBeTruthy();
        ev.add(`Call as: ${options.join(' | ')}`);
        await expect(page.getByRole('button', { name: /Call support/ })).toBeVisible();
        await ev.shot(page);
        // Never press "Call support": that starts a real voice call.
      },
    },
    {
      id: 'VOICE-X05',
      title: 'Test console Demo tab shows persona seed status',
      fn: async ({ page, ev }) => {
        await openConsole(page);
        await consoleTab(page, 'Demo');
        const cards = page.locator('article.card');
        await expect(cards.first()).toBeVisible({ timeout: 30_000 });
        if (FLAGS.allowDemoReset) {
          page.on('dialog', d => d.accept());
          await page.getByRole('button', { name: 'Reset', exact: true }).click();
          await expect(page.getByRole('button', { name: 'Reset', exact: true })).toBeEnabled({ timeout: 120_000 });
          await page.getByRole('button', { name: 'Seed', exact: true }).click();
          await expect(page.getByRole('button', { name: 'Seed', exact: true })).toBeEnabled({ timeout: 180_000 });
          ev.add('Reset then Seed pressed (--allow-demo-reset)');
        }
        const rows: string[] = [];
        for (let i = 0; i < (await cards.count()); i++) {
          const card = cards.nth(i);
          rows.push(`${(await card.locator('strong').first().innerText()).trim()}: ${(await card.locator('.chip').first().innerText()).trim()}`);
        }
        ev.add(rows.join(', '));
        expect(rows.length, 'persona cards').toBeGreaterThanOrEqual(5);
        const seedable = rows.filter(r => !/no CRM record/.test(r));
        expect(seedable.every(r => /: seeded$/.test(r)), 'every seedable persona is seeded').toBeTruthy();
        await ev.shot(page, { fullPage: true });
      },
    },
  ],
);
