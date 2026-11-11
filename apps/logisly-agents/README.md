# logisly-agents

Google ADK demo agents for Logisly, served to Gemini Enterprise as A2A agents from Cloud Run.

| Agent | ADK app | root agent | Cloud Run service |
|---|---|---|---|
| UC2 purchasing: tender and quotation filtering | `adk_apps/uc2_purchasing` | `logisly_purchasing` | `logisly-uc2` |
| UC3 product manager: PRD (Google Doc), backlog consistency, Jira proposals | `adk_apps/uc3_product` | `logisly_product_manager` | `logisly-uc3` |

Demo data lives in `../../demo-data/logisly/` (see its README). Everything in it is fictional.

## Prerequisites

- `uv`, Docker (only for local image checks) and `gcloud`.
- Application Default Credentials: `gcloud auth application-default login`. The model is Gemini on Vertex AI; there is no API key anywhere.
- `gcloud auth login` as a Devoteam account with access to project `lv-playground-genai`.
- APIs enabled in `lv-playground-genai` (once, by someone with rights to do so): Cloud Run, Cloud Build and Artifact Registry, besides Vertex AI.

  ```bash
  gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
    --project lv-playground-genai
  ```

  The first `gcloud run deploy --source` in the region creates the Artifact Registry repository `cloud-run-source-deploy` and asks to confirm; answer yes.
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
just deploy uc2
just deploy uc3
```

The argument is positional: `just deploy agent=uc2` would pass the literal `agent=uc2` to `deploy.sh`, which rejects it.

`deploy.sh <uc2|uc3>` stages the needed demo data (pricing DB, business rules, UC3 templates, Jira conventions and snapshot, team files) into the build context as `logisly_agents/data/`, then runs `gcloud run deploy logisly-<agent> --source .` in `asia-southeast2` with `--no-allow-unauthenticated`, `--min-instances=1 --max-instances=1`. A second step sets `LOGISLY_PUBLIC_URL` to the service URL so the agent card advertises the right address. The script never changes IAM; it checks the runtime service account and prints the command to run if `roles/aiplatform.user` is missing:

```bash
gcloud projects add-iam-policy-binding lv-playground-genai \
  --member=serviceAccount:<PROJECT_NUMBER>-compute@developer.gserviceaccount.com \
  --role=roles/aiplatform.user
```

Service URLs (deployed 2026-11-08, project `lv-playground-genai`; agent card, plain chat and an .xlsx file part smoke-tested over A2A):

- UC2: `https://logisly-uc2-swgjfxtwwq-et.a.run.app`
- UC3: `https://logisly-uc3-swgjfxtwwq-et.a.run.app`

`/healthz` answers locally but not on Cloud Run: Google's front end reserves paths ending in `z` and returns its own 404. Use the agent card as the health check.

Check a service (the service is private, so use an identity token):

```bash
curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" \
  <SERVICE_URL>/.well-known/agent-card.json
```

## Register in Gemini Enterprise

The deploy never registers anything. Do this once per agent in the Gemini Enterprise app.

1. Open the Gemini Enterprise app `<GEMINI_ENTERPRISE_APP_ID>` in the Google Cloud console, then Agents, Add agent, Custom agent via A2A.
2. Paste the agent card from `<SERVICE_URL>/.well-known/agent-card.json` (or its URL, if the form asks for one).
3. Authorisation: the services are private (`--no-allow-unauthenticated`), so whatever identity Gemini Enterprise calls them with needs `roles/run.invoker` on `logisly-uc2` and `logisly-uc3`. Not yet verified: the principal is likely the Discovery Engine service agent of the project that hosts the Gemini Enterprise app, `service-<GE_PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com`. Confirm during the smoke test (a 403 from the service in Cloud Run logs names the caller). The Gemini Enterprise project may differ from `lv-playground-genai`: take `<GE_PROJECT_NUMBER>` from the project that hosts the app (`gcloud projects describe <GE_PROJECT_ID> --format='value(projectNumber)'`). Nothing in this repo runs IAM changes; a person with IAM rights on `lv-playground-genai` runs, per service:

   ```bash
   for svc in logisly-uc2 logisly-uc3; do
     gcloud run services add-iam-policy-binding "$svc" \
       --project lv-playground-genai --region asia-southeast2 \
       --member=serviceAccount:service-<GE_PROJECT_NUMBER>@gcp-sa-discoveryengine.iam.gserviceaccount.com \
       --role=roles/run.invoker
   done
   ```

   Pick the authentication option the registration form offers for calling a private Cloud Run service with that identity.
4. Smoke test before the demo: send the UC3 first turn (four files plus the prompt) through Gemini Enterprise and time it. That turn reads every source, searches the tracker, writes the PRD and checks capacity, and takes minutes locally. If Gemini Enterprise times out the A2A call before the answer arrives, split the turn (upload first, then ask for the PRD, then the backlog). Streaming is not enabled on purpose; do not turn it on for this demo.
5. Share the agent with the demo users, then run the demo scripts below.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `LOGISLY_AGENT` | none (required by `serve.py`) | `uc2` or `uc3` |
| `LOGISLY_PUBLIC_URL` | `http://localhost:$PORT` | URL written into the agent card |
| `PORT` | `8080` | Port for uvicorn (Cloud Run sets it) |
| `LOGISLY_MODEL` | `gemini-3.5-flash` | Gemini model; works only at location `global` |
| `LOGISLY_AS_OF` | `2026-11-09` | Demo "today" |
| `LOGISLY_PRD_FOLDER_ID` | empty | UC3: Shared Drive folder for the PRD Google Doc; empty keeps the PRD in chat |
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

1. Unggah empat file: `01_request_milestone-follow-up.docx`, `02_meeting-notes_2026-11-10_product-sync.md`, `03_email_cs_unloading-update.eml`, `04_ops-chat-export_ct-followup.txt`.
2. Prompt: "Siapkan PRD follow-up driver berbasis milestone dan backlog Jira dari input minggu ini."
3. Agen membaca template, konvensi, tim dan kalender, mencari issue Jira yang sudah ada (LOG-n), lalu menulis PRD sesuai template PRD Logisly dan menerbitkannya sebagai Google Doc (link dilaporkan; tanpa folder Doc, PRD tetap di chat).
4. Tanya lanjutan:
   - "Apa saja konflik antar sumber?"
   - "Apakah ada issue yang duplikat?"
   - "Apakah kapasitas tim cukup sebelum code freeze 18 Desember?"
5. "Usulkan issue yang perlu dibuat atau diubah." Agen memvalidasi semua usulan lalu menampilkan tabel usulan bernomor P1, P2, dan seterusnya. Setelah tabel usulan muncul, setujui: "Setuju, terapkan semuanya ke tracker." (atau sebut id tertentu, mis. "Terapkan P1 sampai P5 saja."). Agen menerapkan usulan sekaligus, melaporkan jumlah yang diterapkan, mengisi kolom "Jira #" di tabel Stories PRD, dan memperbarui Doc yang sama. Perubahan hanya masuk ke Jira tiruan di sesi, bukan Jira sungguhan.

Expected: LOG-3417 duplikat LOG-3412 (jadi Won't Do), LOG-3398 diberi komentar superseded tanpa dibuka ulang, release 18 Desember (bukan 4 Desember), LOG-3405 memblokir trigger milestone otomatis. Jawaban lengkap di `demo-data/logisly/uc3/expected/`.

## Google Doc output (UC3)

`publish_prd` mengubah PRD menjadi Google Doc lewat Drive API. Setup:

- Set `LOGISLY_PRD_FOLDER_ID` ke ID folder di Shared Drive. Service account runtime Cloud Run harus anggota Shared Drive itu (peran Content manager) dan Drive API aktif di project. Saat deploy: `LOGISLY_PRD_FOLDER_ID=<id> just deploy uc3` (deploy.sh meneruskannya hanya bila diisi).
- Run lokal: `gcloud auth application-default login --scopes=https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/cloud-platform`.
- Tanpa folder (atau bila Drive menolak), PRD tetap disimpan dan ditampilkan di chat, dan agen menyebut bahwa Doc tidak tersedia.

## Known risks

- The agent card advertises PDF, xlsx, docx, eml, PNG and JPEG input (see `INPUT_MODES` in `serve.py`). Whether Gemini Enterprise actually passes uploaded files through to a custom A2A agent still has to be checked right after registration. Fallbacks if it does not: upload PDFs, or paste the text.
- Sessions, tracker state and saved documents are in memory on a single instance (`--max-instances=1`). A redeploy, restart or scale-to-zero clears demo chats.
- `to_a2a` is marked experimental in ADK 2.2.0 and needs `google-adk[a2a]` plus `a2a-sdk[http-server]`.
- The services are private. Gemini Enterprise needs `roles/run.invoker` before it can call them, and the exact principal is unconfirmed until the smoke test (see Register in Gemini Enterprise).
- UC3 turn 1 is long. Time it during the smoke test; a Gemini Enterprise A2A timeout shorter than that turn is the main live-demo risk.
- UC3 Google Doc: the PRD is converted from HTML by Drive, so styling is basic, and the Doc is overwritten on each `publish_prd` (edits made by hand in the Doc are lost). It needs a Shared Drive folder and a runtime service account with access; without them the PRD stays in chat. The Jira tracker is a mock held in the session, not a real Jira.
