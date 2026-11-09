// Tracker tab: Alert preferences. Changes only the signed-in person's OWN
// rule for "A customer sends a new message" and puts it back afterwards; the
// account defaults are never changed.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { TEST_PREFIX } from '../lib/env';
import { manual, trackerModule } from '../lib/tracker';
import { openApp } from '../lib/ui';

const EVENT = 'new_inbound';

trackerModule('Alert preferences', {
  'ALRT-001': async ({ page, api, data, ev }) => {
    // Read-only checks first: nothing is changed until the original rule is known.
    const mine = Api.must(await api.be('/alerts/rules/mine'), 'my rules');
    if (mine.disabled) ev.blocked(`alert rules are not enabled on this tenant (${mine.reason ?? 'disabled'})`);
    const original = mine.rules?.[EVENT];
    let def: any;
    try {
      def = Api.must(await api.be('/alerts/rules/defaults'), 'defaults').defaults?.[EVENT];
    } catch {
      def = undefined;
    }
    try {
      await openApp(page, 'proton/alert-preferences');
      await expect(page.getByRole('heading', { name: 'Alert preferences' })).toBeVisible();
      const card = page.locator('div.p-4', { hasText: 'A customer sends a new message' }).first();
      const sound = card.locator('label', { hasText: 'Sound' }).locator('input[type="checkbox"]');
      const toast = card.locator('label', { hasText: 'On-screen' }).locator('input[type="checkbox"]');
      if (await sound.isChecked()) await sound.click();
      if (!(await toast.isChecked())) await toast.click();
      await card.getByRole('button', { name: 'Save', exact: true }).click();
      await expect(page.getByText('Saved. This applies to you only.')).toBeVisible();
      const after = Api.must(await api.be('/alerts/rules/mine'), 'my rules').rules[EVENT];
      expect(after.modalities).not.toContain('sound');
      expect(after.modalities).toContain('toast');
      ev.add('own rule saved: On-screen on, Sound off ("Saved. This applies to you only.")');
      await ev.shot(page);
      // An inbound message on the run's [TEST] conversation: the toast should show.
      await data.conversation([`${TEST_PREFIX} alert toast check`]);
      ev.add('inbound [TEST] message posted to check the toast');
    } finally {
      // No known original, or it equals the account default -> drop the own
      // rule; otherwise put the original back exactly.
      const sameAsDefault =
        original && def &&
        JSON.stringify([...original.modalities].sort()) === JSON.stringify([...def.modalities].sort()) &&
        original.scope === def.scope && original.enabled === def.enabled;
      const r = !original || sameAsDefault
        ? await api.be(`/alerts/rules/mine/${EVENT}`, { method: 'DELETE' })
        : await api.be(`/alerts/rules/mine/${EVENT}`, { method: 'PUT', body: { scope: original.scope, modalities: original.modalities, enabled: original.enabled } });
      if (!r.ok && r.status !== 404) {
        console.error(`\n!! YOUR "new message" ALERT RULE WAS NOT RESTORED (HTTP ${r.status}): reset it on Alert preferences by hand\n`);
        throw new Error(`own alert rule not restored (HTTP ${r.status})`);
      }
    }
    ev.handOff('that the next inbound shows a toast WITHOUT sound has to be seen and heard by the tester');
  },

  'ALRT-002': manual('changing an account default is a live settings change the suite must not make on prod'),
  'ALRT-003': manual('an agent login to check Account defaults is read-only'),
});
