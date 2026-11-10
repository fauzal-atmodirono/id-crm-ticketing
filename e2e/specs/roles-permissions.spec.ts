// Tracker tab: Roles & Permissions. Read-only: the suite never creates,
// edits, assigns or deletes a role on prod.
import { expect } from '../lib/fixtures';
import { Api } from '../lib/api';
import { manual, trackerModule } from '../lib/tracker';
import { expectNoError, openApp, sidebarItem } from '../lib/ui';

trackerModule('Roles & Permissions', {
  'ROLE-001': manual('creating a [TEST] role is a role change the suite must not make on prod'),
  'ROLE-002': manual('assigning a role and logging in as that test agent in a second window'),
  'ROLE-003': manual('editing a role’s permissions and checking from the test agent’s login'),

  'ROLE-004': async ({ page, api, ev }) => {
    await openApp(page, 'proton/roles-permissions');
    await expect(page.getByRole('heading', { name: 'Roles & Permissions' })).toBeVisible();
    await expectNoError(page);
    const list = Api.must(await api.be('/authz/roles'), 'roles').roles as any[];
    const withChatwoot: any[] = [];
    for (const r of list) {
      const perms = Api.must(await api.be(`/authz/roles/${r.id}/permissions`), `role ${r.name} permissions`);
      const names: string[] = (perms.permissions ?? perms).map((p: any) => (typeof p === 'string' ? p : p.name ?? p.key));
      if (names.some(n => String(n).startsWith('chatwoot.'))) withChatwoot.push(r);
    }
    ev.add(`${list.length} roles; ${withChatwoot.length} with a chatwoot.* (Chatwoot access) permission`);
    expect(withChatwoot.map(r => r.name), 'no role ticks Chatwoot access').toEqual([]);
    await expect(sidebarItem(page, 'Settings'), 'admin still sees Settings').toBeVisible();
    ev.add('signed-in admin still sees Settings in the sidebar');
    await ev.shot(page);
  },

  'ROLE-005': manual('deleting a role and logging in again as its test agent'),
});
