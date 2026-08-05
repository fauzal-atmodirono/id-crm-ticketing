# Knowledge

The **Knowledge** area is where administrators and agents manage everything
that shapes how the AI assistant answers customers: the facts it can quote
(FAQs, Documents), the personas it can speak as (Assistants), the playbooks
and tools it can use (Scenarios, Tools), which inbox it is switched on for
(Inboxes), and how it behaves (Settings). It sits in its own section of the
left-hand navigation, separate from Conversations and Contacts, because it is
configuration rather than day-to-day case work — most of it is used by
administrators, though any agent with access can browse it to understand why
the assistant answered the way it did.

## FAQs

### What it is

FAQs is the live, editable question-and-answer knowledge base the AI
assistant is grounded on. Each entry pairs a customer question with the exact
answer to give, plus optional keywords and tags to help matching. Unlike the
Documents corpus (see the next section), FAQ entries are edited directly in
the CRM and take effect immediately — there is no separate publishing step.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **FAQs**.

### How to use it

1. Open **Knowledge → FAQs**. The page lists every entry with its question,
   answer, keywords, tags, and whether it is active.
2. To add an entry, click **+ New entry**, fill in **Question** and
   **Answer** (both required), and optionally **Keywords** and **Tags** as
   comma-separated lists. Leave **Active** checked so the entry is usable for
   grounding, then save.
3. To change an entry, click **Edit** on its row, update the fields, and
   save.
4. To remove an entry, click **Delete** on its row and confirm.
5. Use the filter box at the top to search by question, keyword, or tag
   across all entries.
6. To add many entries at once, click **Bulk upload (CSV)**, choose a `.csv`
   file, and wait for the confirmation message. It reports how many entries
   were created and, if any rows were skipped, which row numbers and why.

The CSV file must be UTF-8 text with a header row using exactly these column
names:

| Column | Required | Format | Notes |
|---|---|---|---|
| `question` | Yes | text | Row is skipped if empty. |
| `answer` | Yes | text | Row is skipped if empty. |
| `keywords` | No | multiple values separated by `;` | Left blank for no keywords. |
| `tags` | No | multiple values separated by `;` | Left blank for no tags. |

> Entries created through the bulk upload are always saved as active — if you
> need an imported entry to start out inactive, edit it afterwards and
> uncheck **Active**.

[[SCREENSHOT: ch04-faqs | The FAQs list under Knowledge]]

[[SCREENSHOT: ch04-faq-bulk-upload | Bulk-uploading FAQs from a CSV file]]

### Example scenario

Proton's service team compiles a spreadsheet of 40 frequently asked
questions about the e.MAS 7 warranty ahead of a launch event. Instead of
typing each one in by hand, the administrator exports the spreadsheet as a
CSV with `question`/`answer`/`keywords`/`tags` columns and uses **Bulk
upload (CSV)** to import all 40 entries in one go, then spot-checks a few of
them in Playground before the launch.

### Integrations & automation

FAQ entries are part of the knowledge base the assistant draws on for
Suggest-a-reply, the Ask Copilot panel, and AI auto-drafted replies (see
Conversations and AI Behaviour). Toggling an entry's **Active** flag off
removes it from grounding immediately, without deleting it.

## Documents

### What it is

Documents covers the larger, bulk knowledge corpus that also grounds the
assistant's answers, alongside FAQs. It has two related views in the
left-hand navigation:

- **Documents** — a read-only listing of everything indexed in the tenant's
  bulk search corpus (product manuals, policies, and similar material loaded
  outside the CRM), so administrators and agents can see what the assistant
  can quote from.
- **Uploads** — where operators add their own text or files directly from
  the CRM; these are queued for indexing and become searchable once
  processing finishes.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Documents** for
the read-only corpus listing, or **Uploads** to add new material.

### How to use it

#### Browsing the indexed corpus (Documents)

1. Open **Knowledge → Documents**. Each row shows a document's title, a link
   to its source (if available), and a short snippet.
2. Use the filter box to search by title, link, or snippet.
3. Click **Refresh** to reload the list after new material has finished
   indexing elsewhere.

This view is read-only from the CRM — there is no upload, edit, or delete
here; use **Uploads** instead to add operator-authored material.

#### Adding operator-authored material (Uploads)

1. Open **Knowledge → Uploads**.
2. To paste text directly, click **+ Add text**, give it a **Title**, paste
   the content into **Body**, and submit.
3. To upload a file instead, click **Upload file** and choose a
   `.pdf`, `.docx`, `.md`, `.txt`, or `.markdown` file.
4. Watch the **Status** column: new documents start as **pending** while
   they are processed, then move to **indexed** once searchable, or
   **failed** if something went wrong (hover the badge for the reason).
5. To remove a document you added, click **Delete** on its row and confirm.

[[SCREENSHOT: ch04-documents | Uploading a document to the knowledge base]]

### Example scenario

An administrator wants the assistant to be able to answer detailed questions
about the Proton X50 owner's manual. Rather than writing FAQ entries for
every possible question, they upload the manual as a PDF under **Uploads**
and wait for it to reach **indexed** status, after which the assistant can
draw on it directly.

### Integrations & automation

Both the read-only corpus and the operator-authored uploads feed the same
grounding the assistant uses for Suggest-a-reply, Ask Copilot, and Playground
answers (see Conversations). Uploads that finish indexing become available
to every assistant persona automatically — there is no separate step to
attach a document to a specific assistant.

## Assistants

### What it is

An Assistant is a named AI persona: its own name, description, and product
context, with its own instructions, temperature, guardrails, and tool
access, configured on the Settings and Tools pages. Most tenants only need
one, and a **Default Assistant** always exists so nothing has to be
configured before the AI works. Larger tenants can create additional
assistants — for example, one per product line or department — and pick
which one answers on which inbox (see Inboxes).

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Assistants**.

### How to use it

1. Open **Knowledge → Assistants** to see every assistant, with a **default**
   badge on the one used when no other assistant is assigned.
2. To create one, click **+ New assistant**, enter a **Name** (required),
   optional **Description** and **Product name**, and save.
3. To change one, click **Edit** on its row, update the fields, and save.
4. To remove one, click **Delete** on its row and confirm — the default
   assistant cannot be deleted; make another assistant the default first if
   you need to retire it.
5. Once an assistant exists, pick it from the **Assistant** selector shown
   at the top of the Scenarios, Playground, Tools, and Settings pages to
   configure or test that specific persona.

[[SCREENSHOT: ch04-assistants | The Assistants list under Knowledge]]

### Example scenario

Proton's dealership arm wants the same CRM tenant to answer both new-car
sales questions and after-sales service questions with a different tone for
each. An administrator creates a second assistant named "Proton Service
Assistant" alongside the default sales-focused one, then assigns it to the
after-sales WhatsApp inbox under Inboxes.

### Integrations & automation

The assistant selected in the header of Scenarios, Playground, Tools, and
Settings is shared across those pages during a session, so switching it in
one place carries over to the others. Which assistant actually answers on a
given conversation is controlled from Inboxes.

## Scenarios

### What it is

A Scenario is a named playbook — a block of instructions, scoped to one
assistant, that gets added to that assistant's behaviour when the scenario
is turned on. Use it to give the assistant extra guidance for a specific
situation (for example, how to handle a recall notice or a warranty claim)
without rewriting its whole persona. A scenario can also be tied to specific
tools so the assistant knows which ones to reach for in that situation.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Scenarios**.

### How to use it

1. Open **Knowledge → Scenarios** and pick the assistant you want to manage
   scenarios for, using the **Assistant** selector at the top.
2. To create one, click **+ New scenario**, enter a **Title** (required),
   an optional short **Description**, and the **Instruction** text the
   assistant should follow when this scenario applies.
3. Optionally tick which **Tools** the assistant should have available for
   this scenario, from the built-in and custom tools already registered
   under Tools.
4. Leave **Enabled** checked so the scenario is active, then save.
5. To turn a scenario on or off without editing it, use the toggle switch
   in its row.
6. To change or remove a scenario, click **Edit** or **Delete** on its row.

Each assistant can hold a limited number of scenarios, and the instruction
text has a maximum length — the page shows a character counter and will not
let you save past the limit.

[[SCREENSHOT: ch04-scenarios | The Scenarios list under Knowledge]]

### Example scenario

Proton issues a service campaign for a steering component on the X70. The
administrator creates a "Steering component recall" scenario on the Service
Assistant with instructions to ask for the vehicle number, check it against
the recall list using the relevant tool, and direct affected customers to
book a service appointment.

### Integrations & automation

Enabled scenarios are folded into the assistant's behaviour wherever that
assistant answers — in Playground testing and in real conversations alike.
Disabling a scenario removes its instructions immediately without deleting
it, so it can be re-enabled later.

## Playground

### What it is

Playground is a sandbox for trying out an assistant outside any real
conversation. It behaves like the Ask Copilot panel in a conversation — you
type a question, the assistant answers using its configured knowledge and
tools, and the reply shows which tools it used and any sources it drew on —
but nothing here reaches a customer.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Playground**.

### How to use it

1. Open **Knowledge → Playground** and pick the assistant to test with the
   **Assistant** selector at the top. Switching assistants starts a fresh
   conversation.
2. Type a question in the box at the bottom and press **Enter** (use
   **Shift+Enter** for a new line) or click **Send**.
3. Read the reply. If the assistant used a tool, a **Looked at:** line names
   it; if it cited sources, they appear as clickable links underneath.
4. Continue the back-and-forth as needed — each new message includes the
   full thread so far.
5. Click **Reset** to clear the conversation and start over.
6. Optionally expand **Advanced** to supply a real conversation ID, which
   lets you exercise tools that look up customer or ticket context as if you
   were inside that conversation.

[[SCREENSHOT: ch04-playground | Testing a question in the Playground before it goes live]]

### Example scenario

After bulk-importing new e.MAS 7 warranty FAQ entries, an administrator opens
Playground, selects the Sales Assistant, and asks a few of the same
questions a customer might ask (for example, "Berapa lama garansi baterai
e.MAS 7?") to confirm the assistant answers correctly before customers start
seeing it live.

### Integrations & automation

Playground uses the same assistant configuration, knowledge base, and tools
as real conversations, so a question that works in Playground will behave
the same way when a customer asks it for real. Nothing typed in Playground
is saved to a real conversation or visible to customers.

## Tools

### What it is

Tools are the actions an assistant is allowed to take beyond answering from
text — looking things up or calling out to another system. There are two
kinds:

- **Built-in tools** — capabilities that ship with the platform; you can
  only turn them on or off and adjust their description.
- **Custom tools** — webhook-based tools an administrator defines to call an
  external HTTPS endpoint (for example, a dealer's own booking or
  parts-lookup system).

Which of the registered tools an assistant may actually use is then set
per-assistant, further down the same page.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Tools**.

### How to use it

1. Open **Knowledge → Tools**. The **Built-in tools** table lists every
   built-in tool with an editable description and an **Enabled** checkbox;
   click **Save** on a row after changing it.
2. To add a custom tool, click **+ New custom tool** in the **Custom tools**
   section and fill in:
   - **Title** and **Description** (shown to the assistant).
   - **Endpoint URL** — must start with `https://`.
   - **HTTP method** — GET or POST.
   - **Auth type** — none, bearer token, basic (username/password), or an
     API key header; the corresponding secret fields appear once you pick
     one, and are never shown again after saving (leave them blank on an
     edit to keep the existing secret).
   - Optionally a **param_schema** (a JSON description of the tool's
     inputs), a **request template**, and a **response template**.
   - **Enabled**, so the tool is available to be assigned to an assistant.
3. Save. Custom tools are capped at a fixed number per tenant, shown as a
   count next to the section heading.
4. To change or remove a custom tool, click **Edit** or **Delete** on its
   row.
5. Scroll to **Per-assistant enablement**, pick an assistant with the
   **Assistant** selector, tick which built-in and custom tools that
   assistant may call, and click **Save enablement**.

[[SCREENSHOT: ch04-tools | The Tools list under Knowledge]]

### Example scenario

A dealer network wants its WhatsApp assistant to check real-time service-bay
availability instead of just telling customers to call in. An administrator
registers a custom tool pointing at the dealer's booking system endpoint,
enables it for the after-sales assistant under **Per-assistant enablement**,
and references it from a "Book a service" scenario.

### Integrations & automation

Tools enabled for an assistant are available to it everywhere it answers —
Playground, Ask Copilot, and live conversations — and can be scoped further
by attaching them to specific Scenarios.

## Inboxes (assignment)

### What it is

The Inboxes page controls which assistant answers for each Chatwoot inbox,
and in what mode. Every inbox that is not explicitly assigned here falls
back to the tenant's default assistant and default mode, shown with a
**default** badge; an inbox with its own assignment shows an **override**
badge instead.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Inboxes**.

### How to use it

1. Open **Knowledge → Inboxes** to see every Chatwoot inbox with its
   channel type, currently assigned assistant, mode, and source badge.
2. To assign an assistant to an inbox, use the **Assistant** dropdown on its
   row and pick one — choosing **— default —** clears the override and goes
   back to inheriting the tenant default.
3. To change how the assistant behaves on that inbox, use the **Mode**
   dropdown: **Off** (the assistant does not answer on this inbox),
   **Suggest** (it drafts privately for a human to review and send), or
   **Auto** (it can reply directly).
4. Changes save automatically as soon as you pick a new value — there is no
   separate save button, and the row's badge switches to **override** once
   you have set anything explicitly.

[[SCREENSHOT: ch04-inboxes | Assigning an assistant to an inbox]]

### Example scenario

Proton adds a new WhatsApp inbox for e.MAS 7 pre-order enquiries and wants it
handled by the sales-focused assistant in **Auto** mode, while the general
support WhatsApp inbox stays on the default assistant in **Suggest** mode so
agents can review replies first. The administrator opens **Knowledge →
Inboxes**, finds the new inbox, sets its **Assistant** to the sales
assistant and its **Mode** to **Auto**, and leaves the support inbox as-is.

### Integrations & automation

The assistant and mode set here determine which persona and knowledge base
answer a given inbox, including in the Ask Copilot panel (see
Conversations) and in the AI's decision to reply, draft, or hand off (see
AI Behaviour).

## Settings (persona, language, lifecycle messages, guardrails)

### What it is

Settings is where an assistant's persona and tone are configured, along with
a smaller set of tenant-wide operational knobs. It has two parts: an
**Assistant persona** panel, scoped to whichever assistant is selected at
the top, and a **Tenant settings** panel that applies across the whole
workspace. Leaving any field empty keeps today's default behaviour — nothing
here needs to be filled in for the assistant to keep working as it already
does.

### Where to find it

In the left-hand navigation, open **Knowledge** and select **Settings**.

### How to use it

1. Open **Knowledge → Settings** and pick an assistant with the
   **Assistant** selector at the top to edit its persona.
2. Under **Basic**, set the assistant's **Name**, **Description**, **Product
   name**, and **Language**. The assistant always mirrors the language the
   customer actually writes in — this field never overrides that. It only
   acts as a tie-breaker preference for when the customer's language is
   unclear; leave it empty to let the assistant fall back to whatever
   language it judges best in that case, or set it (for example, to Bahasa
   Melayu) to tell it which language to prefer as that fallback.
3. Under **System**, write **System instructions** describing who the
   assistant is and how it should behave — this is the core of its persona.
   Adjust **Temperature** from precise (0) to creative (1); leaving it as-is
   keeps the current balance.
4. Under **Guardrails**, add short rules the assistant must never break
   (for example, "never quote a price that is not in the price list").
   These are enforced on top of the system instructions.
5. Under **Response guidelines**, add style and tone preferences (for
   example, "always reply in short paragraphs" or "always end with a
   follow-up question").
6. Under **Messages**, fill in the wording the assistant sends at key
   moments of a conversation: a **Welcome message** when it starts and a
   **Handoff message** when it hands off to a human agent — plus five more
   lifecycle messages: an **Idle warning message** (sent after a period of
   inactivity), an **Idle close message** (sent if the conversation is
   then closed for inactivity), a **Resolution prompt message** (asking
   whether the issue is resolved), and CSAT survey prompts split by who
   handled the chat — a **Survey AI message** and a **Survey agent
   message** — plus a **Thanks message** after a rating and an **Assign
   agent message** when a human is assigned. Leaving any of these blank
   keeps the platform's built-in default wording for that moment. A
   **Resolution message** field is also available on this page, but it is
   saved for future use only — nothing in the current build sends it to a
   customer.
7. Under **Features**, toggle whether this assistant uses knowledge-base/FAQ
   grounding, conversation memory context, source citations in its answers,
   and contact-attribute context.
8. Click **Save assistant** to apply the persona changes.
9. Below the persona panel, **Tenant settings** lets an administrator
   override a fixed set of workspace-wide values, each shown with whether
   it is currently on the platform's default (**env**) or has been
   overridden (**override**), and a **Reset** to return it to default
   individually:
   - **Assist Gemini model** — which model Suggest-a-reply uses.
   - **Copilot Gemini model** — which model the Ask Copilot panel uses.
   - **Copilot max tool iterations** — how many tool calls Copilot may make
     while answering a single question.
   - **AI assist enabled**, **Copilot enabled**, **AI drafts enabled** —
     tenant-wide toggles for Suggest-a-reply, the Ask Copilot panel, and AI
     auto-drafted replies respectively.
   - **Default mode** — the fallback Suggest/Auto mode used for any inbox
     that does not have its own explicit mode set under Inboxes.
   - **Debounce seconds** — how long the AI waits after a customer's
     message before answering, so a quick burst of messages is answered
     once instead of many times.

   Click **Save settings** to apply changes.

[[SCREENSHOT: ch04-settings | The assistant Settings page: persona, language, and lifecycle messages]]

### Example scenario

Proton's brand team feels the sales assistant sounds too formal for
WhatsApp. An administrator opens **Knowledge → Settings**, selects the Sales
Assistant, adds a response guideline of "use a warm, casual tone and keep
replies under three sentences," adds a guardrail of "never promise a
delivery date," and saves — the next Playground test immediately reflects
the new tone.

### Integrations & automation

The persona, guardrails, response guidelines, and lifecycle messages
configured here feed the assistant everywhere it operates: Playground
testing, the Ask Copilot panel, Suggest-a-reply, AI auto-drafted replies,
and the lifecycle messages sent during a conversation (see Conversations
and AI Behaviour). An empty field anywhere on this page means the platform's
built-in default is used instead — nothing has to be filled in for the
assistant to work.
