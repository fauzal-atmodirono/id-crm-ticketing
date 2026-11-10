// [TEST] fixtures and cleanup.
//
// Prod safety rules (live production tenant) enforced here, not in each spec:
// - everything created is named with TEST_PREFIX ("[TEST] E2E <run id>") or a
//   key/slug carrying the run id, and conversations carry the audit_test label;
// - conversations are created on the API inbox (1) only, so nothing reaches a
//   real customer channel (no WhatsApp, no email);
// - cleanup matches only the FULL run-scoped forms (a name starting with
//   "[TEST] E2E <run id>", a key/slug/short code starting with "e2e<run id>")
//   and deletes only those, so it can never touch anything the run did not
//   create; afterwards it re-queries and prints a LEFTOVERS banner. It runs when each worker finishes
//   and once more in global teardown (a worker that died mid-test still gets
//   swept).
import { Api } from './api';
import { test } from '@playwright/test';
import { INBOX, KEY_PREFIX, RUN_ID, TEST_PREFIX } from './env';

export const AUDIT_LABEL = 'audit_test';

export interface TestConversation {
  id: number;
  contactId: number;
}

function digits(n: number): string {
  return String(Math.floor(Math.random() * 10 ** n)).padStart(n, '0');
}

export class TestData {
  /** Conversations this run created (the only ones a test may send in). */
  readonly createdConversations = new Set<number>();
  private contact: { id: number; sourceId: string; name: string; phone: string; email: string } | null = null;

  constructor(private readonly api: Api) {}

  /** The run's [TEST] contact on the API inbox, created on first use. */
  async testContact(): Promise<{ id: number; sourceId: string; name: string; phone: string; email: string }> {
    if (this.contact) return this.contact;
    this.contact = await this.newContact('contact');
    return this.contact;
  }

  async newContact(label: string, extra: { email?: string; phone?: string } = {}) {
    const name = `${TEST_PREFIX} ${label}`;
    const phone = extra.phone ?? `+60009${digits(6)}`;
    const email = extra.email ?? `${KEY_PREFIX}.${label.replace(/\W+/g, '').toLowerCase()}.${digits(4)}@example.com`;
    const r = Api.must(
      await this.api.cw('contacts', { method: 'POST', body: { inbox_id: INBOX.api, name, phone_number: phone, email } }),
      'create [TEST] contact',
    );
    const contact = r.payload?.contact ?? r.payload ?? r;
    const sourceId = r.payload?.contact_inbox?.source_id ?? contact.contact_inboxes?.[0]?.source_id;
    return { id: contact.id as number, sourceId: sourceId as string, name, phone, email };
  }

  /**
   * A new [TEST] conversation on the API inbox with incoming customer
   * messages, labelled audit_test (+ any extra labels).
   */
  async conversation(messages: string[], labels: string[] = []): Promise<TestConversation> {
    const c = await this.testContact();
    let sourceId = c.sourceId;
    if (!sourceId) {
      const ci = Api.must(
        await this.api.cw(`contacts/${c.id}/contact_inboxes`, { method: 'POST', body: { inbox_id: INBOX.api } }),
        'create contact inbox',
      );
      sourceId = ci.source_id;
    }
    const conv = Api.must(
      await this.api.cw('conversations', {
        method: 'POST',
        body: { source_id: sourceId, inbox_id: INBOX.api, contact_id: c.id, status: 'open' },
      }),
      'create [TEST] conversation',
    );
    this.createdConversations.add(conv.id);
    for (const content of messages) await this.incoming(conv.id, content);
    Api.must(
      await this.api.cw(`conversations/${conv.id}/labels`, { method: 'POST', body: { labels: [AUDIT_LABEL, ...labels] } }),
      'label [TEST] conversation',
    );
    return { id: conv.id, contactId: c.id };
  }

  /**
   * Guard before any composer Send / Add Note: the conversation must be one
   * this run created, on the API inbox. Anything else skips the test rather
   * than risk writing into a real customer's conversation.
   */
  async assertSendable(conversationId: number): Promise<void> {
    const c = await this.api.cw(`conversations/${conversationId}`);
    const ok = this.createdConversations.has(conversationId) && c.ok && c.data?.inbox_id === INBOX.api;
    if (!ok) test.skip(true, `Blocked: refusing to send in conversation ${conversationId} (not created by this run on the API inbox)`);
  }

  /** A customer (incoming) message. Only allowed by Chatwoot on API inboxes. */
  async incoming(conversationId: number, content: string): Promise<number> {
    const m = Api.must(
      await this.api.cw(`conversations/${conversationId}/messages`, {
        method: 'POST',
        body: { content, message_type: 'incoming' },
      }),
      'post incoming message',
    );
    return m.id;
  }
}

interface Sweeper {
  name: string;
  /** Run-created records of this kind still present. */
  find: (api: Api) => Promise<any[]>;
  remove: (api: Api, item: any) => Promise<boolean>;
  /** How a person finds leftovers by hand. */
  where: string;
}

/** "[TEST] E2E <run id>…" (names, questions, titles, remarks). */
const named = (s: unknown) => typeof s === 'string' && s.startsWith(TEST_PREFIX);
/** "e2e<run id>…" (keys, slugs, short codes, emails). */
const keyed = (s: unknown) => typeof s === 'string' && s.toLowerCase().startsWith(KEY_PREFIX);

async function del(api: Api, kind: 'cw' | 'be', path: string): Promise<boolean> {
  const r = kind === 'cw' ? await api.cw(path, { method: 'DELETE' }) : await api.be(path, { method: 'DELETE' });
  return r.ok || r.status === 404;
}

const SWEEPERS: Sweeper[] = [
  {
    name: 'contacts (and their conversations)',
    where: `Contacts, search "${RUN_ID}"`,
    find: async api => {
      const r = await api.cw(`contacts/search?q=${encodeURIComponent(RUN_ID)}&per_page=100`);
      return (r.data?.payload ?? []).filter((c: any) => named(c.name) || keyed(c.email));
    },
    remove: async (api, c) => {
      // Resolve first so the conversations close cleanly in reports, then
      // delete the contact (Chatwoot deletes its conversations with it).
      const convs = await api.cw(`contacts/${c.id}/conversations`);
      for (const conv of convs.data?.payload ?? []) {
        await api.cw(`conversations/${conv.id}/toggle_status`, { method: 'POST', body: { status: 'resolved' } });
      }
      return del(api, 'cw', `contacts/${c.id}`);
    },
  },
  {
    name: 'canned responses',
    where: `Settings > Canned responses, short code "${KEY_PREFIX}"`,
    find: async api => ((await api.cw('canned_responses')).data ?? []).filter((c: any) => keyed(c.short_code)),
    remove: (api, c) => del(api, 'cw', `canned_responses/${c.id}`),
  },
  {
    name: 'saved views and segments',
    where: `Conversations > Folders and Contacts > Segments, "${TEST_PREFIX}"`,
    find: async api => {
      const out: any[] = [];
      for (const type of ['conversation', 'contact']) out.push(...((await api.cw(`custom_filters?filter_type=${type}`)).data ?? []).filter((f: any) => named(f.name)));
      return out;
    },
    remove: (api, f) => del(api, 'cw', `custom_filters/${f.id}`),
  },
  {
    name: 'macros',
    where: `Settings > Macros, "${TEST_PREFIX}"`,
    find: async api => {
      const r = await api.cw('macros');
      return (r.data?.payload ?? r.data ?? []).filter((m: any) => named(m.name));
    },
    remove: (api, m) => del(api, 'cw', `macros/${m.id}`),
  },
  {
    name: 'help center portals',
    where: `Help Center > Portals, slug "e2e-${RUN_ID}"`,
    find: async api => ((await api.cw('portals')).data?.payload ?? []).filter((p: any) => p.slug === `e2e-${RUN_ID}`),
    remove: (api, p) => del(api, 'cw', `portals/${p.slug}`),
  },
  {
    name: 'FAQs',
    where: `Knowledge > FAQs, filter "${RUN_ID}"`,
    find: async api => ((await api.be('/kb/faq')).data?.entries ?? []).filter((f: any) => named(f.question)),
    remove: (api, f) => del(api, 'be', `/kb/faq/${encodeURIComponent(f.id)}`),
  },
  {
    name: 'assistants (and their scenarios)',
    where: `Knowledge > Assistants, "${TEST_PREFIX}"`,
    find: async api => ((await api.be('/kb/assistants')).data?.assistants ?? []).filter((a: any) => named(a.name)),
    remove: async (api, a) => {
      const s = await api.be(`/kb/scenarios?assistant_id=${encodeURIComponent(a.id)}`);
      for (const sc of s.data?.scenarios ?? []) await del(api, 'be', `/kb/scenarios/${encodeURIComponent(sc.id)}`);
      return del(api, 'be', `/kb/assistants/${encodeURIComponent(a.id)}`);
    },
  },
  {
    name: 'custom tools',
    where: `Knowledge > Tools > Custom tools, "${TEST_PREFIX}"`,
    find: async api => ((await api.be('/kb/tools')).data?.custom ?? []).filter((t: any) => named(t.title)),
    remove: (api, t) => del(api, 'be', `/kb/tools/${encodeURIComponent(t.slug)}`),
  },
  {
    name: 'knowledge documents',
    where: `Knowledge > Documents, "${TEST_PREFIX}"`,
    find: async api => ((await api.be('/kb/knowledge')).data?.documents ?? []).filter((d: any) => named(d.title)),
    remove: (api, d) => del(api, 'be', `/kb/knowledge/${encodeURIComponent(d.id)}`),
  },
  {
    name: 'RSA incidents',
    where: `RSA Incident Log, remarks "${TEST_PREFIX}"`,
    find: async api => ((await api.be('/rsa/incidents')).data?.incidents ?? []).filter((i: any) => named(i.remarks)),
    remove: (api, i) => del(api, 'be', `/rsa/incidents/${encodeURIComponent(i.id)}`),
  },
  {
    name: 'taxonomy nodes',
    where: `Case taxonomy, key "${KEY_PREFIX}_"`,
    find: async api => {
      const walk = (nodes: any[]): any[] => nodes.flatMap(x => [x, ...walk(x.children ?? [])]);
      return walk((await api.be('/admin/taxonomy/tree')).data?.tree ?? []).filter((n: any) => keyed(n.key));
    },
    remove: async (api, n) => (await api.be(`/admin/taxonomy/node/${encodeURIComponent(n.key)}/retire`, { method: 'POST' })).ok,
  },
];

function banner(lines: string[], log: (line: string) => void): void {
  log('');
  log('!'.repeat(78));
  for (const l of lines) log(`!! ${l}`);
  log('!'.repeat(78));
  log('');
}

/**
 * Delete everything this run created, then re-query and print a LEFTOVERS
 * banner for anything still there. Best effort; never throws.
 */
export async function sweep(api: Api, log: (line: string) => void = console.log): Promise<void> {
  const leftovers: string[] = [];
  for (const s of SWEEPERS) {
    try {
      let n = 0;
      for (const item of await s.find(api)) if (await s.remove(api, item)) n += 1;
      if (n) log(`[e2e cleanup] removed ${n} ${s.name} (run ${RUN_ID})`);
      const left = (await s.find(api)).length;
      if (left) leftovers.push(`${left} ${s.name}: ${s.where}`);
    } catch (e) {
      leftovers.push(`${s.name}: could not check (${(e as Error).message.slice(0, 120)}): ${s.where}`);
    }
  }
  if (leftovers.length) banner([`LEFTOVERS from e2e run ${RUN_ID}: remove these by hand`, ...leftovers], log);
}

/** Teardown could not even reach the CRM (e.g. the session expired). */
export function leftoversUnknown(reason: string, log: (line: string) => void = console.log): void {
  banner(
    [
      `LEFTOVERS from e2e run ${RUN_ID}: cleanup could not run (${reason.slice(0, 120)})`,
      'Sign in and remove anything carrying the run id by hand:',
      ...SWEEPERS.map(s => `${s.name}: ${s.where}`),
    ],
    log,
  );
}
