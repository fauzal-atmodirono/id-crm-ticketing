# Proton CRM — Knowledge Menu Features & Capabilities Guide

## Executive Summary

The **Knowledge Menu** is the central AI management and Knowledge Base (KB) operations portal within Proton CRM (built on custom Chatwoot Community & Gemini AI). It replaces proprietary enterprise paywalls (such as Chatwoot Captain) with a self-hosted, operator-managed Retrieval-Augmented Generation (RAG) and SOP automation suite.

Designed specifically for non-technical business operators and CRM administrators, the Knowledge Menu allows teams to author FAQs, ingest enterprise documentation (PDF, DOCX, Markdown, TXT), configure AI bot personas, define operational scenarios (SOPs), test AI responses in a sandbox, and manage per-channel grounding without requiring GCP console configuration or technical coding.

---

## Architecture & Grounding Overview

The Knowledge Menu powers two major consumption channels:
1. **Conversational AI Bot**: Automatically grounds customer interactions across WhatsApp, Email, Web Chat, and Social Media (FB/IG) using live FAQs, vector search, and SOP scenario flows.
2. **Agent Copilot**: Surfaces real-time answer suggestions and document references inside the agent inbox panel to assist human support representatives.

```
                  ┌─────────────────────────────────────────────────────────┐
                  │                 KNOWLEDGE MENU (UI)                      │
                  └────┬──────────────┬─────────────┬─────────────┬─────────┘
                       │              │             │             │
        ┌──────────────▼───┐  ┌───────▼──────┐  ┌───▼──────┐  ┌───▼────────────┐
        │  FAQs (Live QA)  │  │  Documents   │  │Assistants│  │ Scenarios (SOP)│
        └──────────────┬───┘  └───────┬──────┘  └───┬──────┘  └───┬────────────┘
                       │              │             │             │
                       ▼              ▼             ▼             ▼
       ┌──────────────────────────────────────────────────────────────────┐
       │                MergedKnowledgeAdapter (Backend Engine)           │
       │     ┌────────────────────┬──────────────────┬──────────────┐     │
       │     │ Live FAQ (Fast)    │ pgvector / HNSW  │ Vertex AI    │     │
       │     └────────────────────┴──────────────────┴──────────────┘     │
       └──────────────────────────────────┬───────────────────────────────┘
                                          │
                   ┌──────────────────────┴──────────────────────┐
                   │                                             │
        ┌──────────▼──────────┐                       ┌──────────▼──────────┐
        │ Conversational AI   │                       │    Agent Copilot    │
        │   (Customer Bot)    │                       │  (Inbox Sidebar)    │
        └─────────────────────┘                       └─────────────────────┘
```

---

## Detailed Sub-Menu Breakdown

The Knowledge Menu on the left navigation sidebar comprises **8 core modules**:

---

### 1. FAQs (Live FAQ Management)

#### Meaning
The **FAQs** module is a real-time authoring system for curated Question-and-Answer (Q&A) pairs. It provides instant, deterministic grounding for high-frequency customer inquiries.

#### Key Features & Capabilities
* **Full CRUD Management**: Create, edit, search, and delete FAQ entries directly from the CRM interface.
* **Instant Availability**: Saved FAQs immediately ground both the AI Bot and Agent Copilot without needing re-indexing or server restarts.
* **Active/Inactive Toggling**: Temporarily disable outdated or seasonal entries without deleting them.
* **Keywords & Tags Indexing**: Attach comma-separated keywords (e.g., `battery, warranty, x50`) and tags (e.g., `warranty, vehicle`) to boost retrieval accuracy for specific queries.
* **Search & Filter**: Real-time filter by question text, answer content, or metadata tags.

---

### 2. Documents (Document Base & Vector RAG)

#### Meaning
The **Documents** module provides a no-code document ingestion pipeline, allowing non-technical operators to upload technical manuals, spec sheets, warranty guidelines, and policy documents.

#### Key Features & Capabilities
* **Multi-Format Ingestion**: Supports pasting raw text or uploading files (`.pdf`, `.docx`, `.md`, `.txt`).
* **Automated Processing Pipeline**:
  1. Text extraction and cleaning.
  2. Semantic chunking into optimal context blocks.
  3. Vector embedding generation via Vertex `text-embedding-004`.
  4. Vector storage in per-tenant PostgreSQL using `pgvector` with HNSW indexing for millisecond-level cosine similarity search.
* **Processing Status Tracker**: Displays real-time ingestion statuses (`Pending`, `Indexed`, `Failed`) along with character and chunk counts.
* **Coexistence Architecture**: Merges seamlessly with GCP Vertex AI Search corpus data and Live FAQs at query time.

---

### 3. Assistants (AI Persona & Assistant Configuration)

#### Meaning
The **Assistants** module is the persona control center where administrators define the behavior, voice, and instructions for AI agents operating on different channels.

#### Key Features & Capabilities
* **Multi-Assistant Management**: Create distinct assistants for different departments or brands (e.g., Sales Bot, Technical Support Bot, General Inquiries Bot).
* **System Instructions / Persona Engineering**: Define detailed system prompts specifying tone (professional, empathetic), response style, and boundary limits.
* **Welcome Messages**: Configure customized greeting messages triggered upon initial customer contact.
* **LLM Hyperparameter Tuning**: Adjust model parameters including temperature, maximum response length, and similarity score floors (`kb_score_floor`).

---

### 4. Settings (Grounding & Multilingual Rules)

#### Meaning
The **Settings** module defines global AI operational standards, language constraints, and fallback mechanisms.

#### Key Features & Capabilities
* **Strict Multilingual Enforcement**: Configures the bot to automatically detect customer language (Bahasa Melayu, English, Mandarin) and respond strictly in the same language.
* **Confidence Floor & Fallbacks**: Set minimum relevance thresholds (`kb_score_floor`). When knowledge confidence is below the threshold, the bot seamlessly triggers human handoff or out-of-hours messages.
* **Data Sources Precedence**: Define priority rules between Live FAQs, pgvector documents, and external search indexes.

---

### 5. Playground (Interactive AI Sandbox)

#### Meaning
The **Playground** is an interactive testing and debugging sandbox that allows administrators to simulate customer conversations and inspect AI reasoning before deploying changes live.

#### Key Features & Capabilities
* **Real-Time Simulation**: Test arbitrary customer questions against specific AI assistants and knowledge bases.
* **Grounding Context Inspection**: View exact document chunk snippets, FAQ entries, and similarity scores retrieved for each query.
* **Prompt Iteration**: Evaluate how modifications to system instructions or FAQ answers impact generated responses.

---

### 6. Tools (AI Function Calling & Action Integrations)

#### Meaning
The **Tools** module binds autonomous action capabilities to the AI bot via function calling, enabling the bot to perform backend tasks beyond text generation.

#### Key Features & Capabilities
* **API Action Registration**: Connect backend endpoints for dynamic data retrieval and operational actions.
* **Capabilities**:
  * Ticket status lookups.
  * Customer record retrieval.
  * Appointment & test-drive booking checks.
  * Automated tag/label assignments.
* **Safety Guardrails**: Define execution permissions and human approval requirements for sensitive actions.

---

### 7. Scenarios (SOP & Business Process Flow Automation)

#### Meaning
The **Scenarios** module maps customer conversation journeys to organizational Standard Operating Procedures (SOPs) and compliance requirements.

#### Key Features & Capabilities
* **Conversational SOP Controls**:
  * **AI Disclaimer Trigger**: Automatically posts standardized disclaimer messages upon conversation start.
  * **Business Hours & Out-of-Hours**: Enforces operational schedules and automated out-of-office replies.
  * **Idle Management**: Automatically issues inactivity warnings and executes auto-close workflows (e.g., 10-minute warning / 15-minute close).
  * **Resolution Gate**: Prompts customers with "Was your case resolved? (YES/NO)" before closing.
  * **CSAT & AI Rating Surveys**: Triggers 1–5 star rating surveys after resolution.
* **Auto-Categorization**: Automatically analyzes chat context via Gemini to assign proper division labels (e.g., `category_warranty`, `category_sales`).
* **SLA & Escalation Engine**: Monitors response times (e.g., 2-minute SLA for WhatsApp) and triggers manager email alerts upon breaches.

---

### 8. Inboxes (Channel Association & Inbox Mapping)

#### Meaning
The **Inboxes** module centralizes the mapping between communication channels and their respective Knowledge Base configurations and AI settings.

#### Key Features & Capabilities
* **Per-Inbox Channel Mapping**: Connect specific AI Assistants and Knowledge sources to distinct inboxes (WhatsApp, Email, Live Chat Widget, Facebook, Instagram).
* **Channel Priority Matrix**: Enforce SLA priority routing across channels (e.g., WhatsApp > Voice > Email > Social).
* **Feature Toggles**: Toggle automated acknowledgements (e.g., once-per-thread email auto-ack) per inbox.

---

## Summary Matrix of Features & Capabilities

| Module | Core Function / Meaning | Primary Features | Target Outcome |
| :--- | :--- | :--- | :--- |
| **FAQs** | Q&A Authoring | Full CRUD, Keywords, Tags, Active toggle | Instant, exact answers for frequent questions |
| **Documents** | Vector Document RAG | PDF/DOCX/MD/TXT upload, `pgvector`, HNSW | Deep grounding on manuals & spec guides |
| **Assistants** | AI Persona Config | System prompts, Tone, Welcome messages | Customized brand voice per channel |
| **Settings** | Grounding Rules | Multilingual rules, Score floor, Fallbacks | High answer precision & language consistency |
| **Playground** | Sandbox Testing | Real-time chat, Chunk inspection, Score view | Risk-free verification before going live |
| **Tools** | Autonomous Actions | API function calling, Ticket lookups | Automated action execution beyond chat |
| **Scenarios** | SOP Automation | Disclaimers, Auto-close, Resolution gate, CSAT | Compliance with business process SOPs |
| **Inboxes** | Channel Binding | Inbox assignment, Priority matrix, Auto-ack | Tailored AI behavior per communication channel |

---

## Conclusion

The **Knowledge Menu** transforms Proton CRM into an enterprise-grade AI operations platform. By combining flexible Q&A management, document vector search (`pgvector`), customizable AI personas, interactive debugging, and strict SOP scenario automation, non-technical teams can effortlessly maintain high-quality AI service delivery across all customer touchpoints.
