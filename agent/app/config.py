"""Application settings, sourced from environment variables.

Field names below map (case-insensitively) to the env vars documented in the
"Agent service" section of `deploy/.env.example` — names must match verbatim.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Internal service URL — used for all API calls (resolve via the
    # platform docker network)
    chatwoot_url: str

    # Public URL — used only for human-facing links embedded in notes.
    # Optional: falls back to the internal URL above so nothing breaks if
    # unset (links just won't be clickable from outside the docker network).
    chatwoot_public_url: str | None = None

    # Chatwoot
    chatwoot_api_token: str
    chatwoot_platform_token: str
    chatwoot_account_id: int
    chatwoot_webhook_secret: str
    chatwoot_bot_secret: str
    chatwoot_bot_token: str

    # Customer-facing acknowledgment posted (publicly) before reopening on a
    # handoff/escalation when the assistant has no persona handoff_message.
    # Empty by default (behaviour-preserving); set per tenant so a handoff
    # always acknowledges the customer instead of going silent.
    handoff_default_message: str = ""

    # Gemini / AI behavior.
    # Auth is via Application Default Credentials (Vertex AI) by default —
    # google_genai_use_vertexai=True uses the mounted ADC (GOOGLE_APPLICATION_
    # CREDENTIALS) with vertex_project_id/location; no API key needed. Set
    # google_genai_use_vertexai=False + gemini_api_key to use the AI-Studio path.
    gemini_api_key: str = ""
    google_genai_use_vertexai: bool = True
    vertex_project_id: str = ""
    vertex_location: str = "us-central1"
    gemini_model: str = "gemini-2.5-flash"
    agent_mode: str = "suggest"
    # Demo-only. When true, a trailing `[slug]` in a customer message repoints
    # that contact at a different demo persona (app/services/demo_persona.py).
    # Off by default: this REWRITES a contact record, which is not something a
    # real tenant should be one stray bracket away from.
    demo_persona_slugs_enabled: bool = False
    # Let the agent-bot ask the four investor-profiling questions (goal,
    # horizon, reaction to a 20% drawdown, experience) and store the answers
    # on the contact. Off by default, and off means the tool is not offered to
    # the model at all -- see orchestrator._tools_for_turn.
    investor_profiling_enabled: bool = False
    kb_grounded_replies: bool = False
    auto_resolve: bool = False
    # Route the agent-bot's decision through the backend's full ADK conversational
    # agent (POST /chat/turn) instead of the local 3-way gemini.decide router.
    # When True, the bot answers KB/spec questions itself and only hands off on
    # genuine intent (help request, negative sentiment, sales lead, KB miss) —
    # matching the Proton website agent. Default False = legacy router (byte-
    # identical). Supersedes kb_grounded_replies on this path when enabled.
    chat_agent_enabled: bool = False
    # Voice-note + image understanding for the agent-bot's chat-agent path
    # (orchestrator.py's _process_via_chat_agent, chat_agent_enabled=True
    # only). Default False = today's text-only behavior, byte-identical.
    # When True, incoming WhatsApp attachments (audio/image) are downloaded
    # and forwarded to backend/'s /chat/turn as multimodal Parts alongside
    # the text.
    whatsapp_media_understanding_enabled: bool = False
    # Inline-media budget for one turn, in RAW (pre-encode) bytes. Applied
    # BOTH to a single video and to the turn's combined audio+image+video
    # payload; anything over it is dropped (logged) and the turn proceeds on
    # what remains. google-genai ships inline_data as base64 inside a JSON
    # REST body, which inflates the payload by roughly 1.335x, and Gemini caps
    # an inline request at roughly 20 MB — so 14 MB raw encodes to ~18.7 MB
    # and still leaves headroom for the conversation history and the system
    # instruction. (Do NOT raise this to WhatsApp's own 16 MB inbound cap:
    # 16 MB encodes to ~21.4 MB and Gemini rejects the request, which is
    # exactly what this guard exists to prevent. A handful of legitimate
    # 14-16 MB clips are refused early, with a log, instead.)
    whatsapp_video_max_bytes: int = 14 * 1024 * 1024

    # Conversation lifecycle & auto-close (feature is a no-op when disabled).
    # Drives the Proton process-flow SOP: idle warn/close, resolution
    # confirmation, rating surveys, AI disclaimer. See
    # docs/superpowers/specs/2026-07-23-conversation-lifecycle-autoclose-design.md
    lifecycle_enabled: bool = False
    lifecycle_scan_interval_seconds: int = 60
    lifecycle_idle_warn_minutes: int = 10
    lifecycle_idle_close_grace_minutes: int = 5
    lifecycle_idle_close_out_of_hours_grace_minutes: int = 0
    lifecycle_confirm_grace_minutes: int = 10
    # Auto-resolve a handed-off (assigned) conversation once it has been idle
    # this many minutes, so an abandoned handoff self-clears instead of
    # swallowing the customer's later messages (which funnel into the still-open
    # thread while the bot stays silent). 0 = disabled: assigned conversations
    # are left entirely to the human (today's behavior).
    lifecycle_assigned_idle_resolve_minutes: int = 0
    # Skip `pending` conversations entirely in the idle scanner. For tenants
    # whose inbox is driven by a *third-party* agent bot (aeon360), `pending`
    # means that bot is mid-conversation, and our idle warning posts as an
    # outgoing message from a user — which its decision table reads as a human
    # taking over, cancelling the generation and flipping the conversation to
    # `open`. Default False: on our own tenants the orchestrator is the bot,
    # `pending` is ours to sweep, and behavior is unchanged.
    lifecycle_skip_pending: bool = False
    lifecycle_survey_enabled: bool = True
    lifecycle_disclaimer_enabled: bool = True
    lifecycle_auto_categorize: bool = False

    # EM-7: two-thread email escalation for natively-escalated Email-channel
    # conversations. Requires PROTON_BACKEND_URL/KEY to be set (fail-open,
    # no-op otherwise). Default off, byte-identical when unset.
    email_escalation_enabled: bool = False

    # P1: on the first inbound message, stamp received_in_business_hours /
    # received_at_local / attend_after onto the conversation, so after-hours
    # volume is reportable and the SLA clock has an arrival fact to read.
    # Written once at intake and never overwritten -- see
    # services/sync.py::maybe_stamp_business_hours for why report-time
    # recomputation answers a different question. Default off.
    business_hours_stamp_enabled: bool = False

    # Link an emailed reply (dealer/PIC/customer) back onto the conversation
    # it was escalated from. Requires the backend's
    # ESCALATION_REPLY_TO_TEMPLATE to be set, or no mail carries a token.
    escalation_reply_linking_enabled: bool = False
    # Post an AI-drafted customer reply as a second private note alongside a
    # linked internal reply. Never sends anything to the customer.
    escalation_reply_draft_enabled: bool = False
    # Record an ACKNOWLEDGED transition on the backend when an internal
    # (PIC/dealer) reply is linked, so the SLA engine can tell "the customer
    # was acknowledged" from "an agent typed in Chatwoot". Inert on its own:
    # the backend's SLA_ACKNOWLEDGEMENT_ENABLED decides whether any breach
    # reads it.
    escalation_reply_acknowledgement_enabled: bool = False
    # Stamp `customer_updated_at` when an agent sends the customer an outgoing
    # public message after a dealer/PIC has answered -- the stop signal for
    # the backend's customer-update clock (B-EM-05: the customer must be told
    # within 4 working hours of the answer existing). Inert on its own; the
    # backend's ESCALATION_CUSTOMER_UPDATE_ENABLED decides whether anything
    # reads the stamp.
    escalation_customer_update_enabled: bool = False

    # Fire the escalation on EVERY channel, not just Email. Before this, the
    # `escalate` label on a WhatsApp/Web/Phone case notified nobody and said
    # nothing about it -- the operator saw the label stick and assumed it had
    # worked. Default off so the existing Email-only behaviour is preserved
    # byte-for-byte until a tenant opts in.
    escalation_all_channels_enabled: bool = False

    # Notice when an escalation email BOUNCED (§4.39's other half). The DSN
    # already arrives in the tenant's own Email inbox -- no bounce mailbox
    # needed -- so this reads it, notes the failure on the case that caused it,
    # and resolves the DSN conversation so it stops inflating the SLA backlog.
    bounce_handling_enabled: bool = False

    # P6: gates the follow_up_at custom-attribute handling in
    # services/sync.py (a per-conversation follow-up date an agent sets,
    # distinct from the SLA deadline). Default off = panel hidden, no
    # attribute read or written. The other twelve P6 settings are
    # backend-only; see backend/apps/backend/src/chatbot/platform/config.py.
    follow_up_date_enabled: bool = False

    # Count reopens (a resolved -> not-resolved transition) onto
    # `reopen_count`/`last_reopened_at`. The warehouse column and the
    # v_reopen_rate view have existed since Phase 3 with nothing writing them,
    # so the reopen rate has been a chart of zeroes. Default off.
    reopen_tracking_enabled: bool = False

    # Suggest-only AI escalation-department nudge (app/services/dept_suggestion.py):
    # on an incoming customer message to an Email-channel conversation with no
    # dept_* label yet, classify it against the departments that actually have
    # a PIC configured (backend GET /escalation/departments) and post a private
    # note naming one. Never applies the label itself -- a human still has to
    # add dept_<slug> before escalate. Default off, byte-identical when unset.
    dept_suggestion_enabled: bool = False

    # SOP completion (B: categorization taxonomy; C1: email auto-ack).
    # Comma-separated category slugs the bot may assign on resolution (must
    # match the tenant's deployed taxonomy). Empty → auto-categorize no-ops.
    # DEPRECATED: superseded by case_taxonomy_json; kept for backward
    # compatibility so old deployments that set this don't hard-fail on startup.
    lifecycle_category_labels: str = ""

    # Case category/subcategory taxonomy — set to the SAME value as backend/'s
    # CASE_TAXONOMY_JSON (both services parse it independently; there is no
    # shared library between them). Used by services/categorize.py as the
    # resolution-time fallback classifier's candidate list. Default is the
    # client's own taxonomy: RFP 2026_028's "APPENDIX A - Case Categorisation"
    # (see docs/client-materials/RFP 2026_028/case-categorisation.json for the
    # full 6-column, 300-row transcription this is derived from) — division
    # slug -> {label: RFP "Case Division" value, subcategories: RFP "Level 1"
    # values for that division}. Level 2/3/4 have no representation here yet
    # (tracked as a follow-up, not built by this change). Two subcategory
    # strings differ only in whitespace around a slash ("Service/Recall
    # Campaign" vs "Service / Recall Campaign") because the client's own PDF
    # spells the same After Sales concept both ways on different rows —
    # preserved verbatim rather than silently merged.
    case_taxonomy_json: str = (
        '{"sales":{"label":"Sales","subcategories":["Delivery","Refund",'
        '"Customer Experience","Promotion","Test Drive","Outlet","Booking",'
        '"Insurance","Finance Information","Vehicle Details","New Model",'
        '"Custom EV Plate (Frame)","Staff","Sales Facilities","Others"]},'
        '"product":{"label":"Product","subcategories":["Infotainment",'
        '"Accessories","Vehicle Specification","Vehicle Model",'
        '"Specification","Performance","Features","Others"]},'
        '"network":{"label":"Network","subcategories":["Outlet Facilities"]},'
        '"charging":{"label":"Charging","subcategories":["Infotainment",'
        '"Others","Home Charging","Charging Credit","Public Charging",'
        '"Compatibility","Configuration","Booking","Refund","Billing",'
        '"Vendor"]},"apps":{"label":"Apps",'
        '"subcategories":["Apps Synchronization","Service Function",'
        '"Remote Control","Unable to update.","Dealer Information","E-Mall",'
        '"Notification","Vehicle Order Issue","smart points","Function",'
        '"Finance Calculator","Test Drive Order Issue","Profile","Others",'
        '"User ID","Unable to register","Operation","Apps Information",'
        '"Point system","Information","Content"]},'
        '"aftersales":{"label":"After Sales",'
        '"subcategories":["Brake / Electronic Parking Brake","Airconditioner",'
        '"Steering","Suspension","Cooling System","Body","Electrical","ADAS",'
        '"Features","Airbag","Others","Spare Part","Warranty",'
        '"Service Operation","Technical","Service/Recall Campaign",'
        '"Service / Recall Campaign","Roadside Assistance","User Manual",'
        '"Staff","Service Facilities"]},"others":{"label":"Others",'
        '"subcategories":["Misdial","Job Vacancy","Scams/Spams",'
        '"No Respond From Customer","Call Disconnected","Test Call"]},'
        '"marketing":{"label":"Marketing","subcategories":["Merchandise",'
        '"Charging Credit","Event/Campaign","Request to Collab",'
        '"Marketing inquiry","Sponsorship"]}}'
    )

    # Vehicle-model / case-type dimensions — SAME values as backend/'s
    # VEHICLE_MODELS_JSON / CASE_TYPE_OPTIONS_JSON (each service parses
    # independently). Used by services/categorize.py's fallback classifier.
    # case_type_options_json's 3 values are the client's exact "Case
    # Category" strings from RFP 2026_028 Appendix A (note "Compliment &
    # Feedback", not "Feedback").
    vehicle_models_json: str = '{"options": ["e.MAS 5", "e.MAS 7", "e.MAS 7 PHEV", "Not Applicable"]}'
    case_type_options_json: str = (
        '{"options": ["Inquiry", "Complaint", "Compliment & Feedback"]}'
    )

    # Level-2 (+ folded Level 3/4) case detail — RFP 2026_028 Appendix A's
    # fourth taxonomy tier stored in a THIRD custom attribute, "case_detail"
    # (not three more attributes for Level 2/3/4 separately). JSON object
    # {"options": [str, ...]}, the SAME shape and fail-open pattern as
    # CASE_TYPE_OPTIONS_JSON/VEHICLE_MODELS_JSON — reused as-is by
    # services/option_lists.py's OptionList/build_option_list, no new
    # parsing code needed.
    #
    # Each value is prefix-encoded against the FULL case_subcategory value
    # ("<Case Division>: <Level 1>"), one level deeper than case_subcategory
    # is against case_category, using the same ": " join:
    # "<Case Division>: <Level 1>: <Level 2>". Where the RFP row also has a
    # Level 3 and/or Level 4, they are folded into that SAME value (never a
    # 4th/5th picker) with " — " (em dash) appended in order: "...<Level
    # 2> — <Level 3> — <Level 4>", e.g. "Sales: Refund: Booking — Status
    # — Dealer Refund". An em dash was chosen over "/" (already used
    # inside several Level 2 strings themselves, e.g. "Roadside Assistance:
    # Panel Workshop/Location") and over a second ": " (which would collide
    # visually with the division/level-1 join).
    #
    # Full-chain prefixing (NOT Level-1-only, e.g. NOT "Delivery: No
    # Estimated Time Delivery") is deliberate: Level 1 names repeat across
    # divisions with their OWN distinct Level 2 sets — "Refund" under both
    # Sales and Charging, "Infotainment" under both Product and Charging,
    # "Charging Credit" under both Marketing and Charging, "Booking" under
    # both Sales and Charging. A Level-1-only prefix would mix unrelated
    # divisions' Level 2 options together in the sidebar cascade
    # (deploy/chatwoot-fork/patches/0050-case-detail-hierarchy.patch).
    #
    # 246 unique values, covering the 258 of 300 RFP rows that carry a
    # Level 2 (the remaining 42 rows have no Level 2 in the source PDF, so
    # no case_detail option is generated for them — their Level 1 alone,
    # via case_subcategory, is the full available detail). Generated from
    # docs/client-materials/RFP 2026_028/case-categorisation.json, not
    # hand-transcribed — see that file for the source-of-truth rows this
    # is derived from. SAME value as backend/'s CASE_DETAIL_OPTIONS_JSON
    # (both services parse it independently).
    case_detail_options_json: str = (
        '{"options":["Sales: Delivery: No Estimated Time Delivery","Sales: Refund: Delay","Sales: '
        'Customer Experience: Poor Buying Experience — Sale Specialist Profesionalism","Sales: Del'
        'ivery: No Allocation - Delay","Sales: Delivery: Overpromised - ETD","Sales: Refund: No Re'
        'cords","Sales: Refund: Others","Sales: Promotion: Ended Promotion","Sales: Test Drive: No'
        't Available","Sales: Test Drive: Others","Product: Infotainment: QR Code Broken","Product'
        ': Infotainment: Account not exist","Product: Infotainment: App Fault Notification","Produ'
        'ct: Infotainment: Data not Sync","Product: Infotainment: 3rd party Apps Update","Product:'
        ' Infotainment: OTA","Product: Infotainment: Malfunction","Product: Infotainment: Others",'
        '"Product: Infotainment: Display Malfunction","Product: Infotainment: No GPS Signal","Netw'
        'ork: Outlet Facilities: Ambience/Environment","Network: Outlet Facilities: Refreshment","'
        'Network: Outlet Facilities: Staff Grooming","Network: Outlet Facilities: Furniture & Fitt'
        'ings","Charging: Infotainment: Charging abnormally Interupted","Charging: Home Charging: '
        'Assessment","Charging: Home Charging: Installation","Charging: Charging Credit: Not Entit'
        'le","Charging: Charging Credit: Didn’t received Points","Charging: Charging Credit: other'
        's","Charging: Public Charging: Map Innacurate location","Charging: Public Charging: Price'
        '","Charging: Public Charging: Car DHU Map not Updated","Charging: Public Charging: smart '
        'point charging","Charging: Public Charging: others","Charging: Configuration: Charging sp'
        'eed","Charging: Configuration: Electical phase","Charging: Home Charging: Function","Char'
        'ging: Home Charging: Quality","Charging: Home Charging: Warranty","Charging: Home Chargin'
        'g: Others","Charging: Public Charging: Power supply Issue","Charging: Public Charging: Lo'
        'ose Cable","Charging: Public Charging: Pre-Authorization","Charging: Public Charging: Oth'
        'ers","Apps: Service Function: Apps Stuck / Frozen","Apps: Service Function: Payment","App'
        's: Remote Control: Unable to view data balance","Apps: Remote Control: Others","Apps: Una'
        'ble to update.: Apps Version","Apps: Remote Control: Climate / Weather","Apps: Remote Con'
        'trol: Car Location","Apps: Dealer Information: Contact not updated","Apps: Dealer Informa'
        'tion: Innacurate location","Apps: Service Function: Unable to received OTP","Apps: Servic'
        'e Function: Unable to register","Apps: Service Function: Others","Apps: E-Mall: Order Ful'
        'fillment","Apps: E-Mall: Order not received","Apps: E-Mall: Order not found","Apps: E-Mal'
        'l: Price","Apps: E-Mall: Merchandise Quality","Apps: E-Mall: Merchandise Stock","Apps: E-'
        'Mall: Others","Apps: Notification: Error","Apps: Notification: Others","Apps: Vehicle Ord'
        'er Issue: Others","Apps: Vehicle Order Issue: Order Synhcronization","Apps: smart points:'
        ' Validity","Apps: smart points: Unable to use","Apps: smart points: Others","Apps: Functi'
        'on: Data info not updated","After Sales: Brake / Electronic Parking Brake: Faulty","After'
        ' Sales: Brake / Electronic Parking Brake: Noise","After Sales: Brake / Electronic Parking'
        ' Brake: Judder","After Sales: Brake / Electronic Parking Brake: Warning Light","After Sal'
        'es: Brake / Electronic Parking Brake: Burned Smell","After Sales: Brake / Electronic Park'
        'ing Brake: Others","After Sales: Airconditioner: Smell","After Sales: Airconditioner: Fau'
        'lty","After Sales: Airconditioner: Leakage","After Sales: Airconditioner: Not Cold","Afte'
        'r Sales: Airconditioner: Others","After Sales: Steering: Noise","After Sales: Steering: V'
        'ibration","After Sales: Steering: Position","After Sales: Steering: Heavy","After Sales: '
        'Steering: Alignment","After Sales: Suspension: Leakage","After Sales: Suspension: Noise",'
        '"After Sales: Suspension: Hard","After Sales: Suspension: Alignment","After Sales: Suspen'
        'sion: Others","After Sales: Cooling System: Smell","After Sales: Cooling System: Leakage"'
        ',"After Sales: Cooling System: Others","After Sales: Body: Noise — Interior","After Sales'
        ': Body: Dented — Interior","After Sales: Body: Alignment — Interior","After Sales: Body: '
        'Gap — Interior","After Sales: Body: Leakage — Interior","After Sales: Body: Rusty — Inter'
        'ior","After Sales: Body: Vibration — Interior","After Sales: Body: Others — Interior","Af'
        'ter Sales: Body: Noise — Exterior","After Sales: Body: Dented — Exterior","After Sales: B'
        'ody: Allignment — Exterior","After Sales: Body: Gap — Exterior","After Sales: Body: Leaka'
        'ge — Exterior","After Sales: Body: Rusty — Exterior","After Sales: Body: Vibration — Exte'
        'rior","After Sales: Body: Others — Exterior","After Sales: Electrical: Failure","After Sa'
        'les: Electrical: Improper Function","After Sales: Electrical: Noise","After Sales: Electr'
        'ical: Others","After Sales: ADAS: Failure","After Sales: ADAS: Warning Light","After Sale'
        's: ADAS: Others","After Sales: Features: Failure","After Sales: Features: Improper Functi'
        'on","After Sales: Features: Safety Features","After Sales: Features: Others","After Sales'
        ': Airbag: Airbag not deploy","After Sales: Airbag: Airbag sudden deploy","After Sales: Ai'
        'rbag: Recall","After Sales: Airbag: Others","After Sales: Spare Part: Parts Arrival Delay'
        '","After Sales: Spare Part: No Stock","After Sales: Spare Part: Price","After Sales: Spar'
        'e Part: Others","After Sales: Spare Part: Quality","After Sales: Warranty: Rejected","Aft'
        'er Sales: Warranty: Status","After Sales: Warranty: Period","After Sales: Service Operati'
        'on: General Repair","After Sales: Service Operation: Accident Repair","After Sales: Servi'
        'ce Operation: Insurance Claim","After Sales: Service Operation: Repair Quality","After Sa'
        'les: Service Operation: Service Profesionalism","After Sales: Service Operation: Courtesy'
        ' Car","After Sales: Service Operation: Delay Vehicle Repair","After Sales: Service Operat'
        'ion: Outlet Facilities","After Sales: Service Operation: Others","After Sales: Technical:'
        ' Major Repair","After Sales: Technical: Others","After Sales: Technical: Comeback Job","A'
        'fter Sales: Service/Recall Campaign: Information","After Sales: Service/Recall Campaign: '
        'Others","Sales: Outlet: Contact","Sales: Outlet: Location","Sales: Outlet: Others","Sales'
        ': Booking: Fees","Sales: Booking: Status","Sales: Booking: Information","Sales: Booking: '
        'Cancellation","Sales: Booking: Others","Sales: Delivery: Vehicle ETD","Sales: Delivery: S'
        'tock Availability","Sales: Delivery: Others","Sales: Refund: Booking — Status — Dealer Re'
        'fund","Sales: Refund: Booking — Status — HQ Refund","Sales: Refund: Booking — Timeframe",'
        '"Sales: Insurance: Insurance Package","Sales: Insurance: Insurance Renewal","Sales: Insur'
        'ance: Insurance Coverage","Sales: Insurance: Other Insurance Matters","Sales: Test Drive:'
        ' Appointment Booking","Sales: Test Drive: Appointment Cancellation","Sales: Finance Infor'
        'mation: Bank Loan","Sales: Finance Information: Loan Tenure","Sales: Finance Information:'
        ' Others","Sales: Vehicle Details: Price","Sales: Vehicle Details: Colour","Sales: Vehicle'
        ' Details: Others","Sales: Promotion: Ongoing Promotion","Sales: Promotion: Irrelevant Pro'
        'motion","Sales: Promotion: Misleading Promotion","Sales: New Model: Launch Date","Sales: '
        'New Model: Price","Sales: New Model: Brochure","Sales: New Model: Promotion","Sales: New '
        'Model: Release Date","Product: Accessories: Package","Product: Accessories: Delivery Stat'
        'us","Product: Accessories: Others","Product: Infotainment: Vehicle Settings","Product: In'
        'fotainment: Data Information","Product: Infotainment: Software Updates","Product: Infotai'
        'nment: Charging Map Display","Marketing: Merchandise: Package","Marketing: Merchandise: D'
        'elivery Status","Marketing: Merchandise: Others","Marketing: Charging Credit: Not Entitle'
        '","Marketing: Charging Credit: Didn’t received","Charging: Booking: WallBox — Assessment"'
        ',"Charging: Booking: WallBox — Installation","Charging: Refund: Home Assesment — Status",'
        '"Charging: Refund: Home Assesment — Timeframe","Charging: Billing: Charging Billing","Cha'
        'rging: Home Charging: Price","Charging: Home Charging: Purchase Matter","Charging: Home C'
        'harging: Assessment — Status","Charging: Home Charging: Assessment — Reschedule","Chargin'
        'g: Home Charging: Installation — Status","Charging: Home Charging: Installation — Resched'
        'ule","Charging: Public Charging: Charging Location","Charging: Public Charging: Charger M'
        'alfunction","Charging: Public Charging: Promotion","Apps: User ID: Forget User ID","Apps:'
        ' User ID: Information","Apps: User ID: New Account ID","Apps: User ID: Others","Apps: Ope'
        'ration: Vehicle Registration","Apps: Operation: Data Balance Information","Apps: Operatio'
        'n: Finance Calculator","Apps: Point system: Validity","Apps: Point system: Redemption","A'
        'pps: Point system: others","Apps: Information: Vehicle data not updated","Apps: Informati'
        'on: Info / Event not updated","Apps: Operation: Test Drive Order Issue","Apps: Informatio'
        'n: Profile","After Sales: Spare Part: Stock Availbility","After Sales: Spare Part: Parts '
        'Information","After Sales: Spare Part: ETA","After Sales: Warranty: Warranty Information"'
        ',"After Sales: Warranty: Warranty Coverage","After Sales: Warranty: Warranty Period","Aft'
        'er Sales: Warranty: Extended Limited Warranty","After Sales: Warranty: Others","After Sal'
        'es: Service Operation: Vehicle Repair/Service Status","After Sales: Service Operation: Re'
        'gular Maintenance Service Time","After Sales: Service Operation: Service Appointment","Af'
        'ter Sales: Service Operation: Service / Repair Price","After Sales: Service Operation: Se'
        'rvice Advisor\'s Professionalism/Attitude","After Sales: Service / Recall Campaign: Infor'
        'mation","After Sales: Service / Recall Campaign: Others","After Sales: Roadside Assistanc'
        'e: Request for RSA Contact","After Sales: Roadside Assistance: Service Provided","After S'
        'ales: Roadside Assistance: Panel Workshop/Location","After Sales: Roadside Assistance: To'
        'w Truck Varieties","After Sales: Roadside Assistance: Towing Charges","After Sales: Roads'
        'ide Assistance: Others","After Sales: User Manual: Obtain User Manual","After Sales: User'
        ' Manual: Car Features"]}'
    )

    # C1: email once-per-thread auto-acknowledgement.
    email_autoack_enabled: bool = False
    email_autoack_template: str = (
        "Dear Customer,\n"
        "Thank you for your email. This message serves to acknowledge receipt "
        "of your enquiry.\n"
        "We will respond within one (1) business day during our operating hours.\n"
        "For urgent matters, please contact our Call Centre at 1300 888 877.\n"
        "Operating Hours:\n"
        "Monday–Friday: 8:30 AM – 5:30 PM\n"
        "Saturday, Sunday & Public Holidays: 9:00 AM – 5:00 PM\n"
        "Thank you for your patience and understanding.\n\n"
        "Warm regards,\n"
        "Proton e.MAS Centre"
    )

    # Agent service's own database
    agent_database_url: str

    # Proton conversational-AI backend (optional; feature disabled when blank)
    proton_backend_url: str | None = None
    proton_backend_key: str | None = None

    @property
    def chatwoot_display_url(self) -> str:
        """Chatwoot base URL for human-facing links: public if configured,
        otherwise the internal URL (still valid inside the docker network,
        just not clickable from outside it)."""
        return self.chatwoot_public_url or self.chatwoot_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
