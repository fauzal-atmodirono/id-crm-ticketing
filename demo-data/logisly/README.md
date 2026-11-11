# Logisly demo data: UC2 and UC3

Mock data for the Logisly use case 2 (pricing & quotation filtering) and use
case 3 (Product Manager agent) demos. Everything here is fictional: companies,
people, plates, prices and GitHub issues. The three shippers named in UC1's
trips (`features/logisly/trips.py`) reappear here so the three demos read as
one Logisly. Replace with Logisly's anonymised samples once they send them
(MOM 29 Oct 2026, section 6).

Source text is in Bahasa Indonesia, the way Logisly's own documents would be.

## Regenerate

```bash
cd demo-data/logisly
/usr/local/bin/python3 gen_uc2.py   # needs openpyxl, python-docx, Pillow, LibreOffice (soffice)
/usr/local/bin/python3 gen_uc3.py   # needs python-docx
```

`gen_uc2.py` is seeded. The CSVs, SQLite DB and expected answers come out the
same every run. The PDFs differ only in their embedded timestamps.

## UC1: driver follow-up milestone checklists (`features/logisly/`)

Three seeded trips, one scenario each (see `trips.py`). Open the console at
`/logisly/console?key=<PROTON_BACKEND_KEY>`; the "Next milestone" control moves
a trip and opens the driver conversation (app or WhatsApp).

| Order | Driver / shipper | Starts at | Scenario |
|---|---|---|---|
| LGS-24017 | Pak Budi Santoso, FMCG | `assigned` | Clean trip: plate matches, locations are in the app. Exactly one item is open: Unit kosong (`unit_empty`, asked of the driver). |
| LGS-24021 | Pak Agus Wibowo, Shopee | `to_pickup` | Problem trip: driver app not active, not on site at the Shopee gate, DO not in the app. Moving it to `at_pickup` carries the install link and a photo ask, and the DO gap goes to the allocation team as a routed ticket. |
| LGS-24033 | Pak Dedi Kurniawan, FMCG | `in_transit` | Checkpoint trip: the ETA-within-RAD fact is false, so that system item already shows not OK when the console opens (set at seed). Moving on to `at_drop` reports no system item not OK, because `unloading_registered` is Shopee-only. |

**Demo script (about 6 minutes):**
1. Open the console. All three trips show their current milestone and checklist.
2. Move LGS-24021 to "Di lokasi muat" (`at_pickup`). Between 22:00 and 05:00 WIB the move is deferred and nothing is sent, so run this in the daytime or tick "Force (ignore quiet hours)". The opening message carries the driver-app install link and asks for a photo of the unit with its plate.
3. As the driver, reply "sya blum bisa login". The agent re-sends the install link and the login steps instead of ticking anything.
4. Send a photo of the plate. The `unit_photo` item turns ticked in the console.
5. Open the driver conversation in Chatwoot. "DO not in app" is a private note labelled `team-alloc` on that conversation, filed only when the opening message is actually sent (not on a deferred move).
6. Show LGS-24033: its ETA-within-RAD item is already not OK in the console; moving it to `at_drop` applies the milestone with no new system item failing. Then show quiet hours: between 22:00 and 05:00 WIB a move is applied but the driver is not messaged (the response says `deferred`). Tick "Force (ignore quiet hours)" to override.

If Chatwoot is unreachable the move still applies and the API answers 502; with no
driver channel configured it answers 503.

## UC2: pricing & quotation filtering (`uc2/`)

**Scenario:**
1. Shipper PT Nusantara Agro Lestari invites Logisly to tender 4 lanes for Dec 2026 to Feb 2027.
2. Logisly purchasing sends `RFQ-LGS-2611-007` to its transporters.
3. The purchasing mailbox then receives 7 quotations from 6 transporters, one of them a revision. It also gets 2 emails that are not quotations.

Quotation direction (vendor quotes vs Logisly's quotes to customers) is still an open item. The data covers the vendor side and carries customer contract rates for the sell side.

| Path | What |
|---|---|
| `pricing/pricing.db` | SQLite stand-in for the SQL Server pricing DB. Holds 600 customers, 16 transporters, 20 lanes, 6 truck types, 3,182 monthly buy rates (Nov 2025 to Oct 2026, Lebaran peak in March), 446 customer contract rates and quarterly transporter performance. |
| `pricing/schema_sqlserver.sql` | The same schema in T-SQL. |
| `pricing/*.csv` | Every table as CSV. |
| `business_rules.yaml` | Thresholds the agent applies: baseline, flags, required fields, scoring, margin. Illustrative until Logisly gives theirs. |
| `inbox/*.eml` | The mailbox, in arrival order, with attachments embedded. `inbox/index.json` lists them. |
| `inbox/attachments/` | The same attachments, unpacked. |
| `sent/RFQ-LGS-2611-007.pdf` | Logisly's RFQ. |
| `expected/recommendation.{json,md}` | Answer key, computed from the same data. |

**Problems planted in the quotations (what the agent should catch):**

| Email | Transporter | Format | Planted problem |
|---|---|---|---|
| 02 | PT Trans Cahaya Logistik | PDF | Superseded by the revision in 09 |
| 03 | CV Mitra Armada Jaya | xlsx | Cheapest, but: Cikarang to Semarang excludes toll, a cell footnote, so it looks 27% below baseline before normalising. On-time 82%. 14-day payment term. |
| 04 | PT Samudra Truk Indonesia | email body only | No payment term or validity |
| 05 | PT Lintas Jawa Ekspres | scanned PDF, no text layer | 12% above baseline on Cikarang to Surabaya |
| 06 | (dealer newsletter) | | Not a quotation |
| 07 | PT Garuda Angkut Sejahtera | PDF | Priced per ton, not per trip. Validity expired 31 Oct. |
| 08 | PT Prima Fleet Nusantara | xlsx | Lampung quoted on Fuso instead of the required Tronton. Best compliant price on Cibitung to Bandung. |
| 09 | PT Trans Cahaya Logistik | PDF | Revision: lower price on Cikarang to Surabaya |
| 10 | CV Mitra Armada Jaya | PDF | October invoice, not a quotation |

The answer key recommends Trans Cahaya (revised quote) on T1 to T3 and Prima Fleet on T4. It also lists follow-ups for the incomplete, expired and wrong-truck quotes. Sell prices are proposed at a 15% gross margin on the sell price, and T2 is compared with the customer's current contract.

## UC3: Product Manager agent (`uc3/`)

**Scenario:** Logisly's product team must turn this week's inputs into a PRD and a GitHub backlog before sprint planning on Monday 9 Nov. The inputs are an Ops request, the product sync notes, a Customer Success email and an Ops WhatsApp chat.

| Path | What |
|---|---|
| `sources/01_request_driver-app-ai-assistant.docx` | Head of Ops asks for an AI assistant in the driver app (as if from Drive) |
| `sources/02_meeting-notes_2026-11-03_product-sync.md` | Product sync notes |
| `sources/03_email_cs_epod-request.eml` | Shipper request for digital proof of delivery, forwarded by CS |
| `sources/04_ops-chat-export_whatsapp-cost.txt` | Ops WhatsApp group export about WhatsApp cost |
| `templates/` | Logisly's PRD and user story templates |
| `github/conventions.yaml` | Repos, labels, title and branch format, definition of ready/done |
| `github/issues_snapshot.json` | 17 existing issues across 5 repos, with comment threads |
| `team/roster.yaml`, `team/calendar.yaml` | People, capacity, leave, sprints, code freeze, holidays |
| `expected/prd_driver-app-ai-assistant.md` | Example PRD in the template |
| `expected/backlog.json` | Issues to create and update |
| `expected/consistency_report.md` | Conflicts, duplicates, gaps and the capacity check |

**Problems planted across the sources:**
- Release date: 18 Dec in the meeting notes vs a January pilot in the request. 18 Dec is also the last day before the code freeze.
- Automated call: phase 1 in the request, phase 2 in the meeting.
- Chat depends on push notifications, and platform-api#131 has been blocked with no ETA.
- Follow-up rules differ: call after 5 minutes vs at most 2 messages, then a ticket.
- The CS email's e-POD request duplicates driver-app#142, and #148 duplicates #142 too. The shipper-portal issue Laras asked for doesn't exist.
- The WhatsApp cost target has no baseline. The chat and data-pipelines#22 disagree on what billing data the BSP provides.

The agents that run on this data are in `apps/logisly-agents/` (see its README).
