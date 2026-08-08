# P7 — AI Conversational Quality: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AI layer do the things it is already wired to do — write the sentiment field it exposes, rank on the keywords the CRM team authors, ask the model to diagnose the photos it is already sent — and add the two capabilities that genuinely do not exist: agent-facing translation and a resolved-case index.

**Architecture:** Nothing here adds a model round-trip to the hot path. Sentiment rides the existing turn's tool call as an extra argument. Keyword ranking is a weighted blend defaulting to weight 0, so the default ordering is today's ordering. 1-click apply moves FAQ suggestions onto the fork surface that patch `0002` already uses to write the composer, rather than fighting the iframe sandbox.

**Tech Stack:** Python 3.12, google-genai / google-adk, pgvector, Firestore, Chatwoot fork patches, pytest.

**Spec:** `docs/superpowers/specs/2026-08-08-rfp-p7-ai-conversational-quality-design.md`

## Global Constraints

- **One Gemini call per turn stays one Gemini call per turn.** Sentiment is an extra tool argument, never a second request. Task 1 asserts the call count.
- **`FAQ_KEYWORD_WEIGHT=0.0` must reproduce today's ranking exactly**, entry for entry, score for score. That equivalence is the safety argument for shipping it on a live tenant.
- **Never normalise the text the model or the agent sees.** Normalisation applies to the retrieval query only — the register cues in "brp lama siap? nk service" are what make the bot answer in the customer's voice.
- **Outbound Tamil ships disabled.** Inbound translation for Tamil is fine and useful; customer-facing Tamil replies wait for a signed-off evaluation. This is the client's risk to accept, with evidence.
- **The resolved-case index stores summaries, never transcripts**, and the summariser is instructed to omit identifiers. This is a mitigation, not PII masking — R16 is the real fix and is blocked on Q7. Off by default.
- **Sentiment `None` becomes `neutral`, never stays `None`** once the classifier is on. Absent must not read as "we looked and it was fine".
- Env vars in `config.py` + `deploy/tenants/example.env` + `tests/conftest.py`.
- Work on branch `dev-yuda`. Never merge to `main`.

---

## File Structure

| File | Responsibility |
|---|---|
| `backend/.../features/chat/models.py` | **Modify.** Widen `Sentiment` to four levels |
| `backend/.../features/chat/agents.py` | **Modify.** `sentiment` argument on the turn tool |
| `backend/.../features/chat/service.py` | **Modify.** Write `session_state["sentiment"]` |
| `backend/.../features/chat/detection.py` | **Modify.** `urgent` joins `_NEGATIVE_SENTIMENTS` |
| `backend/.../features/chat/chat_persona.py` | **Modify.** Sentiment-selected tone block |
| `backend/.../features/assist/translate_router.py` | **New.** `POST /assist/translate` |
| `backend/.../features/chat/adapters/live_faq.py` | **Modify.** `_rank` hybrid blend |
| `backend/.../features/chat/nlu_normalise.py` | **New.** Query-side normaliser + abbreviations |
| `backend/.../features/chat/test_malay_sms_corpus.py` | **New.** 50-case corpus |
| `backend/.../features/chat/resolved_case_index.py` | **New.** pgvector namespace + writer |
| `backend/.../features/chat/prompts.py` | **Modify.** Media diagnosis instruction |
| `deploy/chatwoot-fork/patches/00NN-faq-composer-apply.patch` | **New.** Suggestions on the fork surface |
| `docs/testing/2026-08-08-ai-calibration-baseline.md` | **New.** Methodology + baseline |

---

### Task 1: Sentiment classification

**Files:**
- Modify: `models.py`, `agents.py`, `service.py`, `detection.py`
- Create: `backend/apps/backend/src/chatbot/features/chat/test_sentiment.py`

**Interfaces:**
- Consumes: the existing per-turn tool call.
- Produces: `session_state["sentiment"]` ∈ `positive|neutral|negative|urgent`, which `service.py:597` and `:1145` already read; plus a `sentiment` conversation custom attribute.

**Tests first:**

```python
async def test_a_positive_turn_writes_positive_to_session_state():
async def test_an_angry_turn_writes_negative():
async def test_a_safety_critical_turn_writes_urgent():
async def test_a_turn_where_the_model_omits_sentiment_falls_back_to_neutral():
async def test_the_fallback_is_neutral_and_never_none_when_the_flag_is_on():
async def test_urgent_trips_the_existing_ticket_creation_gate():
async def test_negative_still_trips_the_gate_exactly_as_before():
async def test_positive_and_neutral_do_not_trip_the_gate():
async def test_exactly_one_gemini_call_is_made_per_turn():        # latency guard
async def test_the_sentiment_reaches_the_conversation_custom_attributes():
async def test_the_flag_off_leaves_sentiment_none_exactly_as_today():
```

**Test nine is the constraint that makes this shippable.** Assert the call count
on the injected client — if a future change adds a classification round-trip,
this fails rather than quietly doubling per-turn latency and cost.

Tests six and seven together assert the gate got *stricter* and not *different*.

**Verify:** `cd backend/apps/backend && uv run pytest src/chatbot/features/chat/test_sentiment.py -q`

---

### Task 2: Tone adjustment

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/chat_persona.py`
- Modify: the Knowledge Settings tenant-store registry (four new tone keys)
- Create: its test file

**Interfaces:**
- Consumes: task 1's `session_state["sentiment"]`.
- Produces: a tone block selected per sentiment, operator-editable, replacing the static "## Tone" paragraph when the flag is on.

**Tests first:**

```python
async def test_a_negative_sentiment_selects_the_measured_apologetic_tone():
async def test_an_urgent_sentiment_selects_the_urgency_acknowledging_tone():
async def test_a_neutral_sentiment_selects_the_default_tone():
async def test_an_operator_edited_tone_block_is_used_over_the_default():
async def test_a_tenant_store_outage_falls_back_to_the_static_paragraph():
async def test_the_flag_off_produces_the_exact_static_paragraph_used_today():
async def test_the_tone_block_augments_and_never_replaces_the_agent_instruction():
```

**Test seven preserves the existing architectural rule** stated in CLAUDE.md:
persona *augments*, never replaces, the static `AGENT_INSTRUCTION`. Tone is
persona.

**Verify:** `uv run pytest src/chatbot/features/chat/test_chat_persona.py -q`

---

### Task 3: Translation

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/assist/translate_router.py`
- Create: its test file
- Modify: `SUMMARIZER_INSTRUCTION` — widen `en|ms|zh` to include `ta`
- Create: `deploy/chatwoot-fork/patches/00NN-translate-action.patch`

**Interfaces:**
- Consumes: text + target language.
- Produces: `POST /assist/translate` returning the translation and the detected source language; a fork-side action rendering it as a private note.

**Tests first:**

```python
async def test_a_malay_message_translates_to_english():
async def test_a_tamil_message_translates_to_english():
async def test_a_chinese_message_translates_to_english():
async def test_the_detected_source_language_is_returned():
async def test_a_message_already_in_the_target_language_is_returned_unchanged():
async def test_the_translation_posts_as_a_private_note_not_an_outgoing_message():
async def test_outbound_tamil_replies_are_blocked_while_the_tamil_flag_is_off():
async def test_inbound_tamil_translation_works_regardless_of_the_outbound_flag():
async def test_the_endpoint_is_rbac_gated():
async def test_a_model_failure_returns_a_clear_error_and_does_not_post_a_note():
```

**Test six is a customer-safety property**, and the same class of bug as P2's:
a translation posted as an outgoing message would send the customer a translation
of their own message. Assert `private=True` on the payload.

Tests seven and eight encode the Tamil split — inbound useful now, outbound
gated.

**Verify:** `uv run pytest src/chatbot/features/assist/test_translate_router.py -q`

---

### Task 4: Hybrid FAQ ranking

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/adapters/live_faq.py` (`_rank`)
- Create: `backend/apps/backend/src/chatbot/features/chat/test_faq_hybrid_rank.py`

**Interfaces:**
- Consumes: `settings.faq_keyword_weight`, the authored `keywords` field, the query string (in addition to its embedding).
- Produces: `_rank(entries, query_embedding, limit, *, query_text=None, keyword_weight=0.0)`.

**Tests first:**

```python
def test_weight_zero_reproduces_the_current_ordering_exactly():
def test_weight_zero_reproduces_the_current_scores_exactly():
def test_a_keyword_hit_lifts_an_entry_that_semantic_search_ranked_lower():
def test_an_entry_with_no_keywords_is_unaffected_by_the_weight():
def test_keyword_matching_is_case_insensitive():
def test_an_exact_model_code_like_emas7_is_matched_as_a_keyword():
def test_the_weight_is_read_from_settings_not_hardcoded():
def test_query_text_none_degrades_to_pure_semantic_ranking():
```

**Tests one and two are the safety argument** and must be written before the
implementation: capture today's `_rank` output as a fixture and assert the
defaulted call reproduces it, ordering *and* scores.

Test six is the case the keyword signal exists for — `e.MAS7` embeds poorly and
matches exactly.

**Verify:** `uv run pytest src/chatbot/features/chat/test_faq_hybrid_rank.py -q`

---

### Task 5: The Malay SMS corpus (do this before the normaliser)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/test_malay_sms_corpus.py`
- Create: `backend/.../features/chat/fixtures/malay_sms_corpus.json`

**Interfaces:**
- Consumes: the FAQ retrieval path and the intent classifier.
- Produces: a pass rate, printed, and a per-case report.

**Tests first:**

```python
def test_the_corpus_contains_at_least_fifty_cases():
def test_the_rfp_example_brp_lama_siap_nk_service_is_a_named_case():
def test_every_case_has_an_expected_intent_or_expected_faq():
async def test_the_corpus_runs_and_reports_a_pass_rate():
async def test_the_pass_rate_is_recorded_as_the_baseline_not_asserted_as_a_threshold():
```

**Test five is deliberate and important.** This task **measures**; it does not
gate. Asserting a threshold before a baseline exists would either be trivially
satisfied or block the build on an arbitrary number. Record the rate, then agree
a threshold with the client (task 10), then enforce it.

**Order matters:** run this before building the normaliser. It is entirely
possible Gemini already handles SMS-register Malay well, in which case the right
deliverable is the measurement and not a normaliser nobody needed.

**Verify:** `uv run pytest src/chatbot/features/chat/test_malay_sms_corpus.py -q -s`

---

### Task 6: Query normaliser (only if task 5 shows it is needed)

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/nlu_normalise.py`
- Create: its test file

**Interfaces:**
- Consumes: a raw query string.
- Produces: `normalise(text) -> str`, applied **only** to the retrieval query.

**Tests first:**

```python
def test_repeated_characters_are_collapsed():
def test_known_abbreviations_are_expanded():
def test_brp_expands_to_berapa_and_nk_to_nak():
def test_an_unknown_token_is_left_untouched():
def test_normalisation_is_applied_to_the_retrieval_query_only():
def test_the_text_passed_to_the_model_is_never_normalised():
def test_the_text_shown_to_the_agent_is_never_normalised():
def test_the_corpus_pass_rate_improves_or_the_normaliser_is_not_shipped():
```

**Tests six and seven are the constraint.** Assert them against the actual call
payloads, not by inspection.

Test eight is the acceptance gate: if the normaliser does not improve task 5's
measured rate, it is complexity with no benefit and should be dropped.

**Verify:** `uv run pytest src/chatbot/features/chat/test_nlu_normalise.py -q`

---

### Task 7: FAQ suggestions on the fork surface (1-click apply)

**Files:**
- Create: `deploy/chatwoot-fork/patches/00NN-faq-composer-apply.patch`
- Modify: `backend/apps/chatwoot-agent-app/README.md` — record that the iframe
  path is superseded for this feature

**Interfaces:**
- Consumes: `GET /kb/suggest` (unchanged).
- Produces: suggestions rendered in the fork's AI-assist surface, with an Apply
  button that writes the composer — the mechanism patch `0002` already uses.

**Tests first:**

```python
def test_the_patch_applies_cleanly_onto_the_pinned_upstream_ref():
def test_the_apply_button_writes_the_suggestion_into_the_composer():
def test_the_existing_ai_assist_composer_write_is_unaffected():
def test_the_suggestion_strip_is_hidden_when_the_popup_flag_is_off():
def test_a_suggestion_below_the_confidence_threshold_is_not_shown_as_a_popup():
def test_dismissing_the_strip_does_not_re_show_it_for_the_same_message():
```

**Fork-patch note:** this sandbox cannot clone upstream (see the network
restriction note in memory). Reconstruct from patch `0002`'s structure — it is
the closest analogue and the one whose composer-write mechanism is being reused.
Build via Cloud Build for `amd64`; never on the prod VM, never from an arm64 Mac.

**Keep the agent-app README's explanation** of why the iframe cannot write the
composer. It is still true, and it is why this patch exists.

**Verify:** patch applies; manual verification on a scratch tenant with a
screenshot recorded.

---

### Task 8: Media diagnosis prompting

**Files:**
- Modify: `backend/apps/backend/src/chatbot/features/chat/prompts.py`
- Modify: the Knowledge Settings registry (a `media_diagnosis_instruction` key)
- Create: `backend/apps/backend/src/chatbot/features/chat/test_media_prompt.py`

**Tests first:**

```python
async def test_the_diagnosis_instruction_is_present_when_an_image_is_attached():
async def test_the_diagnosis_instruction_is_absent_when_no_media_is_attached():
async def test_the_instruction_is_operator_editable():
async def test_a_video_attachment_gets_the_same_instruction():
async def test_the_flag_off_reproduces_todays_generic_instruction_exactly():
async def test_the_instruction_asks_for_a_confidence_statement():
async def test_the_instruction_asks_for_at_most_one_follow_up_question():
```

**Test seven bounds the behaviour:** a model told to "ask follow-up questions"
will ask five, and a customer sending a photo of a dented door will receive an
interrogation.

**Definition of done for this task includes a real-WhatsApp verification.**
`WHATSAPP_MEDIA_UNDERSTANDING_ENABLED` has never been confirmed against a real
number. Send a real photo through a real WhatsApp inbox, record the exchange in
`docs/testing/`, and reference it. A prompt improvement that has never run end to
end is not a delivered feature.

**Verify:** `uv run pytest src/chatbot/features/chat/test_media_prompt.py -q` **plus** the recorded live check.

---

### Task 9: Resolved-case index and auto-summary

**Files:**
- Create: `backend/apps/backend/src/chatbot/features/chat/resolved_case_index.py`
- Create: its test file
- Modify: the resolve-event handler — fire the summariser, then index

**Interfaces:**
- Consumes: the resolve event, `POST /assist/summarize`, the pgvector store.
- Produces: an indexed record in a `resolved_cases` namespace; a summary private note on the conversation.

**Tests first:**

```python
async def test_resolving_a_conversation_posts_a_summary_private_note():
async def test_the_summary_note_is_private():
async def test_re_resolving_appends_a_second_summary_rather_than_overwriting():
async def test_the_resolved_case_is_indexed_in_its_own_namespace():
async def test_the_index_stores_the_summary_and_never_the_raw_transcript():
async def test_the_namespace_can_be_purged_without_touching_authored_faqs():
async def test_a_suggestion_sourced_from_a_resolved_case_is_labelled_as_such():
async def test_a_summariser_failure_does_not_prevent_the_resolve():
async def test_both_flags_off_leaves_resolve_handling_unchanged():
```

**Tests five and six are the PII containment**, such as it is. Test seven means
an agent can tell approved guidance from what a colleague did last month —
without that label, machine-generated content silently acquires the authority of
the curated KB.

Test three: a case resolved, reopened and resolved again did different work the
second time.

**Verify:** `uv run pytest src/chatbot/features/chat/test_resolved_case_index.py -q`

---

### Task 10: Calibration baseline and methodology

**Files:**
- Create: `docs/testing/2026-08-08-ai-calibration-baseline.md`
- Create: `backend/.../features/chat/fixtures/calibration_sets/` (four labelled sets)

**Deliverable:** the document §2.2.4 and §8.1.8 are actually asking for —

1. a labelled evaluation set per capability: intent classification, FAQ match,
   sentiment, summary quality;
2. the baseline measured **before** any tuning in this package;
3. the post-P7 measurement;
4. acceptance thresholds proposed for client sign-off;
5. a re-run procedure and a cadence for §8.1.15's monthly review.

**Tests first:**

```python
def test_each_of_the_four_calibration_sets_has_at_least_thirty_labelled_cases():
async def test_the_calibration_runner_produces_a_score_per_capability():
def test_the_baseline_document_records_a_pre_change_and_post_change_number():
def test_the_thresholds_are_marked_as_proposed_pending_client_sign_off():
```

**The point of test three:** "we calibrated the AI" is unfalsifiable without a
before-number. With one, the monthly AI-accuracy review has something to report
and the client has something to hold the delivery to.

**Verify:** `uv run pytest src/chatbot/features/chat/test_calibration.py -q -s`

---

### Task 11: Flags, env, docs

**Files:**
- Modify: `deploy/tenants/example.env`, `backend/.../platform/config.py`
- Modify: `README.md`, `docs/feature-guide/`

**Tests first:**

```python
def test_all_nine_settings_are_present_in_example_env():
def test_faq_keyword_weight_defaults_to_zero():
def test_every_boolean_setting_defaults_to_false():
def test_the_service_starts_with_none_of_them_set():
```

**Docs note (the deliverable):**

> **Tamil.** Inbound Tamil translation — so an agent can read a Tamil message —
> is enabled with `TRANSLATION_ENABLED`. **Outbound Tamil replies to customers
> remain disabled** pending an evaluation of 30 real Tamil enquiries scored by a
> Tamil speaker. Enabling `TRANSLATION_OUTBOUND_TAMIL_ENABLED` before that
> evaluation sends unverified machine translation to customers.
>
> **Resolved-case suggestions** are generated from summaries of previously
> resolved cases, and are labelled as such in the suggestion panel. They are not
> approved guidance. The index stores summaries rather than transcripts and the
> summariser is instructed to omit customer identifiers, but this is a
> mitigation and not PII masking — that is gap R16.

**Verify:** full suite green with all flags off, then with all on except
outbound Tamil.

---

## Definition of done

- [ ] All nine flags at defaults → suite green, behaviour identical to `d85f0d4`.
- [ ] `sentiment` populated on every turn; `urgent` trips the gate; still one Gemini call per turn.
- [ ] `FAQ_KEYWORD_WEIGHT=0.0` reproduces today's ordering **and scores** exactly.
- [ ] The Malay SMS corpus runs and its baseline pass rate is recorded.
- [ ] The normaliser ships only if it improves that rate; otherwise the measurement is the deliverable.
- [ ] 1-click apply works from the fork surface; the iframe limitation documented as superseded.
- [ ] Media diagnosis verified against a **real** WhatsApp number, recorded in `docs/testing/`.
- [ ] Resolved-case suggestions labelled and purgeable; summaries not transcripts.
- [ ] Calibration baseline documented with pre- and post-change numbers.
- [ ] Outbound Tamil still disabled.
- [ ] Nothing merged to `main`.
