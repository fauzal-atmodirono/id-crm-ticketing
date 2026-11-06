# logisly-agents

Google ADK demo agents for Logisly, served to Gemini Enterprise as A2A agents from Cloud Run.

| Agent | ADK app | root agent | Cloud Run service |
|---|---|---|---|
| UC2 purchasing: tender and quotation filtering | `adk_apps/uc2_purchasing` | `logisly_purchasing` | `logisly-uc2` |
| UC3 product manager: PRD, backlog consistency, GitHub proposals | `adk_apps/uc3_product` | `logisly_product_manager` | `logisly-uc3` |

Demo data lives in `../../demo-data/logisly/` (see its README). Everything in it is fictional.

## Prerequisites

- `uv`, Docker (only for local image checks) and `gcloud`.
- Application Default Credentials: `gcloud auth application-default login`. The model is Gemini on Vertex AI; there is no API key anywhere.
- `gcloud auth login` as a Devoteam account with access to project `lv-playground-genai`.
- `cp .env.example .env` for local runs (git-ignored).

## Run locally

```bash
just sync          # uv sync
just web           # ADK dev UI (adk web adk_apps) on http://localhost:8000
just serve uc2     # A2A server on :8078, agent card at /.well-known/agent-card.json
just test          # offline tests
just test-live     # calls Vertex AI, takes minutes
just lint
```

## Deploy to Cloud Run

```bash
just deploy agent=uc2
just deploy agent=uc3
```

`deploy.sh <uc2|uc3>` stages the needed demo data (pricing DB, business rules, UC3 templates, GitHub conventions and snapshot, team files) into the build context as `logisly_agents/data/`, then runs `gcloud run deploy logisly-<agent> --source .` in `asia-southeast2` with `--no-allow-unauthenticated`, `--min-instances=1 --max-instances=1`. A second step sets `LOGISLY_PUBLIC_URL` to the service URL so the agent card advertises the right address. The script never changes IAM; it checks the runtime service account and prints the command to run if `roles/aiplatform.user` is missing:

```bash
gcloud projects add-iam-policy-binding lv-playground-genai \
  --member=serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com \
  --role=roles/aiplatform.user
```

Service URLs (fill in after deploying):

- UC2: `https://logisly-uc2-<hash>-et.a.run.app`
- UC3: `https://logisly-uc3-<hash>-et.a.run.app`

Check a service (the service is private, so use an identity token):

```bash
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  <SERVICE_URL>/.well-known/agent-card.json
```

## Register in Gemini Enterprise

The deploy never registers anything. Do this once per agent in the Gemini Enterprise app.

1. Open the Gemini Enterprise app `<GEMINI_ENTERPRISE_APP_ID>` in the Google Cloud console, then Agents, Add agent, Custom agent via A2A.
2. Paste the agent card from `<SERVICE_URL>/.well-known/agent-card.json` (or its URL, if the form asks for one).
3. Authorisation: the services require Google identity. Grant the Gemini Enterprise service agent `roles/run.invoker` on `logisly-uc2` and `logisly-uc3` (a person with IAM rights runs this; it is not scripted here) and choose Google-service-account authentication in the form.
4. Share the agent with the demo users, then run the demo scripts below.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `LOGISLY_AGENT` | none (required by `serve.py`) | `uc2` or `uc3` |
| `LOGISLY_PUBLIC_URL` | `http://localhost:$PORT` | URL written into the agent card |
| `PORT` | `8080` | Port for uvicorn (Cloud Run sets it) |
| `LOGISLY_MODEL` | `gemini-3.5-flash` | Gemini model; works only at location `global` |
| `LOGISLY_AS_OF` | `2026-11-09` | Demo "today" |
| `LOGISLY_REQUIRE_CONFIRMATION` | `false` | Ask for tool confirmation before writes |
| `LOGISLY_DATA_DIR` | bundled `logisly_agents/data/`, else `demo-data/logisly` | Demo data root |
| `GOOGLE_GENAI_USE_VERTEXAI` | `true` | Use Vertex AI with ADC |
| `GOOGLE_CLOUD_PROJECT` | `lv-playground-genai` | Project for Vertex AI |
| `GOOGLE_CLOUD_LOCATION` | `global` | Vertex location |

## Demo script UC2: purchasing

Files are in `demo-data/logisly/uc2/inbox/attachments/`.

1. Unggah dokumen tender `TND-NAL-2026-11_Undangan_Tender.pdf` dan semua penawaran: `Penawaran_PT_Trans_Cahaya_Logistik_Q-TCL-1.pdf`, `Penawaran_CV_Mitra_Armada_Jaya_Q-MAJ-1.xlsx`, `Penawaran_PT_Lintas_Jawa_Ekspres_Q-LJE-1.pdf`, `Penawaran_PT_Garuda_Angkut_Sejahtera_Q-GAS-1.pdf`, `Penawaran_PT_Prima_Fleet_Nusantara_Q-PFN-1.xlsx`, `Penawaran_PT_Trans_Cahaya_Logistik_Q-TCL-2.pdf`. Satu atau beberapa pesan boleh.
2. Tempel isi email `inbox/04_quote_sti.eml` sebagai teks (penawaran Samudra Truk hanya ada di badan email).
3. Prompt: "Ini dokumen tender dan penawaran untuk RFQ-LGS-2611-007. Tolong catat semuanya, bandingkan, dan beri rekomendasi per lane."
4. Tanya lanjutan:
   - "Penawaran Mitra Armada berapa untuk Cikarang ke Semarang?"
   - "Kenapa Garuda tidak dipilih?"
   - "Bandingkan harga Trans Cahaya dengan rata-rata 6 bulan terakhir."
   - "Berapa harga jual yang kamu usulkan untuk T2 dengan margin 15%?"
5. "Buatkan draf email tindak lanjut untuk transporter yang penawarannya belum lengkap." Agen menulis draf di chat saja, tidak mengirim email.
6. Setelah disetujui: "Setuju, pilih Trans Cahaya untuk T1 sampai T3 dan Prima Fleet untuk T4." Agen mencatat keputusan.

Expected: Trans Cahaya (revisi TCL-2) di T1 sampai T3, Prima Fleet di T4. Mitra Armada murah tapi belum termasuk tol. Garuda per ton dan sudah kedaluwarsa. Prima Fleet di Lampung memakai Fuso, bukan Tronton.

## Demo script UC3: product manager

Files are in `demo-data/logisly/uc3/sources/`.

1. Unggah empat file: `01_request_driver-app-ai-assistant.docx`, `02_meeting-notes_2026-11-03_product-sync.md`, `03_email_cs_epod-request.eml`, `04_ops-chat-export_whatsapp-cost.txt`.
2. Prompt: "Siapkan PRD AI assistant driver app dan backlog GitHub dari input minggu ini."
3. Agen membaca template, konvensi, tim dan kalender, mencari issue yang sudah ada, lalu menulis PRD sesuai template dan menyimpannya.
4. Tanya lanjutan:
   - "Apa saja konflik antar sumber?"
   - "Apakah ada issue yang duplikat?"
   - "Apakah kapasitas tim cukup sebelum code freeze 18 Desember?"
5. "Usulkan issue yang perlu dibuat atau diubah." Setelah disetujui: "Setuju, terapkan semuanya ke tracker." Perubahan hanya masuk ke tracker tiruan di sesi, bukan GitHub sungguhan.

## Known risks

- The agent card advertises PDF, xlsx, docx, eml, PNG and JPEG input (see `INPUT_MODES` in `serve.py`). Whether Gemini Enterprise actually passes uploaded files through to a custom A2A agent still has to be checked right after registration. Fallbacks if it does not: upload PDFs, or paste the text.
- Sessions, tracker state and saved documents are in memory on a single instance (`--max-instances=1`). A redeploy, restart or scale-to-zero clears demo chats.
- `to_a2a` is marked experimental in ADK 2.2.0 and needs `google-adk[a2a]` plus `a2a-sdk[http-server]`.
- The services are private. Gemini Enterprise needs `roles/run.invoker` before it can call them.
