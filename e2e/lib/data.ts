// [TEST] fixtures and cleanup.
//
// Prod safety rules (live production tenant) enforced here, not in each spec:
// - everything created is named with TEST_PREFIX ("[TEST] E2E <run id>") or a
//   key/slug carrying the run id, and conversations carry the audit_test label;
// - conversations are created on the API inbox (1) only, so nothing reaches a
//   real customer channel (no WhatsApp, no email);
// - cleanup finds records BY THE RUN ID and deletes only those, so it can never
//   touch anything the run did not create. It runs when each worker finishes
//   and once more in global teardown (a worker that died mid-test still gets
//   swept).
import { Api } from './api';
import { INBOX, RUN_ID, TEST_PREFIX } from './env';

export const AUDIT_LABEL = 'audit_test';

export interface TestConversation {
  id: number;
  contactId: number;
}

function digits(n: number): string {
  return String(Math.floor(Math.random() * 10 ** n)).padStart(n, '0');
}

export class TestData {
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
    const email = extra.email ?? `e2e.${RUN_ID}.${label.replace(/\W+/g, '')}.${digits(4)}@example.com`;
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
    for (const content of messages) await this.incoming(conv.id, content);
    Api.must(
      await this.api.cw(`conversations/${conv.id}/labels`, { method: 'POST', body: { labels: [AUDIT_LABEL, ...labels] } }),
      'label [TEST] conversation',
    );
    return { id: conv.id, contactId: c.id };
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

type Sweeper = { name: string; run: (api: Api) => Promise<number> };

const has = (s: unknown) => typeof s === 'string' && s.includes(RUN_ID);

async function del(api: Api, kind: 'cw' | 'be', path: string): Promise<boolean> {
  const r = kind === 'cw' ? await api.cw(path, { method: 'DELETE' }) : await api.be(path, { method: 'DELETE' });
  return r.ok || r.status === 404;
}

const SWEEPERS: Sweeper[] = [
  {
    name: 'contacts (and their conversations)',
    run: async api => {
      let n = 0;
      const r = await api.cw(`contacts/search?q=${encodeURIComponent(RUN_ID)}&per_page=100`);
      for (const c of r.data?.payload ?? []) {
        if (!has(c.name) && !has(c.email)) continue;
        // Resolve first so the conversation closes cleanly in reports, then
        // delete the contact (Chatwoot deletes its conversations with it).
        const convs = await api.cw(`contacts/${c.id}/conversations`);
        for (const conv of convs.data?.payload ?? []) {
          await api.cw(`conversations/${conv.id}/toggle_status`, { method: 'POST', body: { status: 'resolved' } });
        }
        if (await del(api, 'cw', `contacts/${c.id}`)) n += 1;
      }
      return n;
    },
  },
  {
    name: 'canned responses',
    run: async api => {
      let n = 0;
      const r = await api.cw('canned_responses');
      for (const c of r.data ?? []) if (has(c.short_code) && (await del(api, 'cw', `canned_responses/${c.id}`))) n += 1;
      return n;
    },
  },
  {
    name: 'saved views and segments',
    run: async api => {
      let n = 0;
      for (const type of ['conversation', 'contact']) {
        const r = await api.cw(`custom_filters?filter_type=${type}`);
        for (const f of r.data ?? []) if (has(f.name) && (await del(api, 'cw', `custom_filters/${f.id}`))) n += 1;
      }
      return n;
    },
  },
  {
    name: 'macros',
    run: async api => {
      let n = 0;
      const r = await api.cw('macros');
      for (const m of r.data?.payload ?? r.data ?? []) if (has(m.name) && (await del(api, 'cw', `macros/${m.id}`))) n += 1;
      return n;
    },
  },
  {
    name: 'help center portals',
    run: async api => {
      let n = 0;
      const r = await api.cw('portals');
      for (const p of r.data?.payload ?? []) if (has(p.slug) && (await del(api, 'cw', `portals/${p.slug}`))) n += 1;
      return n;
    },
  },
  {
    name: 'FAQs',
    run: async api => {
      let n = 0;
      const r = await api.be('/kb/faq');
      const list = r.data?.entries ?? [];
      for (const f of list) if (has(f.question) && (await del(api, 'be', `/kb/faq/${encodeURIComponent(f.id)}`))) n += 1;
      return n;
    },
  },
  {
    name: 'assistants, scenarios',
    run: async api => {
      let n = 0;
      const r = await api.be('/kb/assistants');
      const list = r.data?.assistants ?? [];
      for (const a of list) {
        if (!has(a.name)) continue;
        const s = await api.be(`/kb/scenarios?assistant_id=${encodeURIComponent(a.id)}`);
        for (const sc of s.data?.scenarios ?? []) {
          await del(api, 'be', `/kb/scenarios/${encodeURIComponent(sc.id)}`);
        }
        if (await del(api, 'be', `/kb/assistants/${encodeURIComponent(a.id)}`)) n += 1;
      }
      return n;
    },
  },
  {
    name: 'custom tools',
    run: async api => {
      let n = 0;
      const r = await api.be('/kb/tools');
      const list = r.data?.custom ?? [];
      for (const t of list) if ((has(t.title) || has(t.slug)) && (await del(api, 'be', `/kb/tools/${encodeURIComponent(t.slug)}`))) n += 1;
      return n;
    },
  },
  {
    name: 'knowledge documents',
    run: async api => {
      let n = 0;
      const r = await api.be('/kb/knowledge');
      const list = r.data?.documents ?? [];
      for (const d of list) if (has(d.title) && (await del(api, 'be', `/kb/knowledge/${encodeURIComponent(d.id)}`))) n += 1;
      return n;
    },
  },
  {
    name: 'RSA incidents',
    run: async api => {
      let n = 0;
      const r = await api.be('/rsa/incidents');
      const list = r.data?.incidents ?? [];
      for (const i of list) if ((has(i.remarks) || has(i.vehicle_no)) && (await del(api, 'be', `/rsa/incidents/${encodeURIComponent(i.id)}`))) n += 1;
      return n;
    },
  },
  {
    name: 'taxonomy nodes',
    run: async api => {
      let n = 0;
      const r = await api.be('/admin/taxonomy/tree');
      const walk = (nodes: any[]): any[] => nodes.flatMap(x => [x, ...walk(x.children ?? [])]);
      const nodes = walk(r.data?.tree ?? []);
      for (const node of nodes) {
        if (!has(node.key)) continue;
        const res = await api.be(`/admin/taxonomy/node/${encodeURIComponent(node.key)}/retire`, { method: 'POST' });
        if (res.ok) n += 1;
      }
      return n;
    },
  },
];

/** Delete everything carrying this run's id. Best effort; never throws. */
export async function sweep(api: Api, log: (line: string) => void = console.log): Promise<void> {
  for (const s of SWEEPERS) {
    try {
      const n = await s.run(api);
      if (n) log(`[e2e cleanup] removed ${n} ${s.name} (run ${RUN_ID})`);
    } catch (e) {
      log(`[e2e cleanup] ${s.name}: ${(e as Error).message.slice(0, 160)}`);
    }
  }
}
