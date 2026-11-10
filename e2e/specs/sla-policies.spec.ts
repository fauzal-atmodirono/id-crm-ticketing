// Tracker tab: SLA Policies. Read-only: the suite never creates or edits an
// SLA policy on prod (that changes live timers and alerts).
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { manual, trackerModule } from '../lib/tracker';
import { bodyText, expectNoError, openApp } from '../lib/ui';

trackerModule('SLA Policies', {
  'SLA-001': async ({ page, api, ev }) => {
    await openApp(page, 'proton/sla-policies');
    await expect(page.getByRole('heading', { name: 'SLA Policies' })).toBeVisible();
    await expect(page.getByText('Response window (hours)').first()).toBeVisible();
    await expect(page.getByText('Resolution window (hours)').first()).toBeVisible();
    await expectNoError(page);
    const p = Api.must(await api.be('/admin/sla-policy/default'), 'default policy');
    ev.add(`tenant default: response ${p.response_hours ?? p.response_window_hours ?? '?'} h, resolution ${p.resolution_hours ?? p.resolution_window_hours ?? '?'} h`);
    await ev.shot(page);
    ev.handOff('creating and editing a [TEST] policy changes live SLA timers on prod; do it by hand and restore');
  },

  'SLA-002': async ({ page, ev }) => {
    await openApp(page, 'proton/sla-policies');
    await expect(page.getByText('Scope').first()).toBeVisible();
    const text = await bodyText(page);
    const scope = text.slice(text.indexOf('Scope'), text.indexOf('Scope') + 200).replace(/\s+/g, ' ');
    ev.add(`scope section: ${scope}`);
    await ev.shot(page);
    ev.handOff('needs a new message from the test handset to watch the SLA timer appear');
  },

  'SLA-003': manual('waiting until the warning threshold on an unanswered conversation'),
  'SLA-004': manual('waiting past the breach time and checking the PIC test mailbox'),
  'SLA-005': manual('waiting past the Tier-2 threshold and checking the Tier-2 test mailbox'),
  'SLA-006': manual('a login without sla.manage (agent) to check the menu and the direct URL'),
});
