# Known Gotchas — Google Docs API editing

These are the failure modes you'll hit if you don't know them in advance. Each is documented with the symptom,
the cause, and the workaround.

The context throughout is the technical-proposal master template: a Google Doc in which every client-specific
value has been replaced by a `{{TOKEN}}`, alongside reusable Devoteam boilerplate, Google Cloud service names,
SLA and escalation tables, and a timeline table with Bahasa Indonesia phase labels.

## 1. `replaceAllText` does not span paragraph boundaries

**Symptom:** A search string that includes embedded `\n` characters returns `occurrencesChanged: 0` even
though the same characters appear to be present in the doc.

**Cause:** Each `\n` in a Doc represents a paragraph mark. `replaceAllText` matches within a single paragraph
and treats paragraph marks as boundaries it cannot cross.

**Workaround:** Replace one paragraph at a time. If the section 6.2 Escalation Flow table visually shows two
names stacked in the Level 3 cell, that is two paragraphs inside one cell — build two separate
`replaceAllText` requests, each using only the text of that line, with no `\n`.

This is also why the master template collapses each multi-paragraph narrative block (the problem statement,
the architecture walk-through, the scope of works, the deliverables) down to a *single* paragraph holding only
its token. A token sitting alone on one paragraph is addressable; a token embedded in a five-paragraph run is
not.

## 2. Short cell labels collide globally

**Symptom:** You `replaceAllText` on something like `Cloud Composer` to rewrite one row of the section 5.4 SLA
table. Result: the architecture narrative, the solution-components section, the scope of works, and six other
places in the doc all get clobbered.

**Cause:** `replaceAllText` is unconditional and global. It does not respect cell boundaries.

**Workaround:** Use surgical `deleteContentRange` + `insertText` targeting that specific cell. See section 4
below for the index pattern. Before any `replaceAllText` on a short string — and Google Cloud service names
are always short strings that recur — grep the doc body to count occurrences:

```python
# walk paragraphs and table cells; `d` is the parsed Docs API document
def all_text(elements):
    out = []
    for el in elements:
        if 'paragraph' in el:
            out += [r.get('textRun', {}).get('content', '') for r in el['paragraph'].get('elements', [])]
        elif 'table' in el:
            for row in el['table']['tableRows']:
                for cell in row['tableCells']:
                    out.append(all_text(cell.get('content', [])))
    return ''.join(out)

text = all_text(d['body']['content'])
print(text.count('Cloud Composer'))  # if > expected, use surgical edit
```

(`scripts/verify_residuals.py` has the same walker if you would rather call it than paste this.)

`{{TOKEN}}` strings do not have this problem, which is the whole point of them: `{{CLIENT_SHORT_NAME}}` is
unambiguous where the client's bare name is not. Prefer editing tokens over editing prose.

## 3. Inserting into an empty cell lands in the previous cell

**Symptom:** You `insertText` at the start index of an empty cell's paragraph mark, expecting the text to
appear in that cell. It appears in the **previous** cell instead, concatenated with whatever was there.

**Cause:** At cell boundaries, the index of an empty paragraph is ambiguous — the API treats it as belonging to
the previous segment.

This bites hardest on the section 3.1 timeline table, which is mostly empty cells by design (the Gantt bars
are cell shading, not text), and on the Hierarchical Escalation matrix, whose P3 and P4 rows have empty cells
in the 50% column.

**Workarounds (pick one):**

- **Keep label and value in the same cell**, separated by ` — ` or `:` — e.g., one cell reads
  `Technical Lead — {{TECH_LEAD_NAME}} ( {{TECH_LEAD_EMAIL}} )`. This is the simplest and most reliable fix.
- **Insert a placeholder character first**: insert a non-empty character into the empty cell to anchor the
  index, then insert the real text after it, then clean up the placeholder. Brittle, avoid unless you really
  need separate cells.
- **Recreate the table**: delete the table entirely and insert a new one with content. Heavy-handed but
  guaranteed. For the timeline table this is often genuinely the right call, since each engagement has
  different phases and durations.

## 4. Surgical edit pattern: `deleteContentRange` + `insertText`

When `replaceAllText` won't work, use this two-step pattern. For a textRun with `startIndex=A`,
`endIndex=B`, and content ending in `\n` (i.e., the paragraph mark is at index B-1):

```json
{"deleteContentRange": {"range": {"startIndex": A, "endIndex": B - 1}}}
{"insertText": {"location": {"index": A}, "text": "new content"}}
```

The `endIndex: B - 1` preserves the trailing `\n` (paragraph mark). If you delete the paragraph mark, the cell
collapses or merges with the next paragraph.

**Critical: order back-to-front within a single batchUpdate.** Each delete/insert shifts the indices of
everything that follows. If you process bottom-up (highest index first), earlier (smaller-index) edits in the
same batch don't get their targets shifted out from under them.

Between batches, always re-fetch the doc — your saved indices are stale.

## 5. Doc JSON quirk: `rows` is a count, `tableRows` is the array

**Symptom:** Iterating with `jq '.table.rows[]'` or Python `for row in t['rows']` errors with "Cannot index
number" or "object of type int has no len".

**Cause:** The Docs API uses two different field names. `table.rows` is the integer **count** of rows;
`table.tableRows` is the array of actual row objects. Easy to confuse.

**Workaround:** Always use `tableRows` for iteration. In Python:

```python
for ri, row in enumerate(t['tableRows']):     # right
    for cell in row['tableCells']:
        ...
# NOT: for row in t['rows']
```

`jq` is harder to use cleanly because of how it handles this — prefer Python for navigation. The template has
eight tables (timeline, GCP SLA, Enhanced Support priorities, Incident Management RACI, Devoteam SLA,
Escalation Flow, Hierarchical Escalation matrix, Roles), so you will be walking `tableRows` a lot.

## 6. The `gws` CLI status line goes to stderr — do **not** strip the first line

**Symptom:** You see `Using keyring backend: keyring` interleaved with your JSON in the terminal, assume it is
part of the payload, pipe through `tail -n +2`, and the resulting file fails to parse —
`Expecting value: line 1 column 1` — because you just deleted the first line of real JSON.

**Cause:** `gws` prints its status line to **stderr**, not stdout. It only *looks* interleaved because both
streams share the terminal. Verified empirically: stdout is clean JSON starting with `{`.

**Workaround:** Redirect stdout and nothing else — it is already clean:

```bash
gws docs documents get --params '{"documentId":"<DOC_ID>"}' --format json > /tmp/doc.json
```

Add `2>/dev/null` only if the status line on your terminal bothers you; it changes nothing about the file.
**Never `| tail -n +2`.** If you are consuming stdout in-process rather than redirecting it (some wrappers do
merge the streams), skip to the first `{` instead of dropping a line — that is what the helper scripts in
`scripts/` do.

## 7. Ordering within a single `batchUpdate` matters

**Symptom:** A series of `replaceAllText` requests with overlapping search strings gives unexpected results.

**Cause:** Requests in a `batchUpdate` apply in order, each to the doc state left by the previous one. If the
short string runs first, it rewrites the text that the longer, more specific search was going to match, and
the specific request then matches nothing.

**Workaround:** Order from most-specific to least-specific. The general rule: longer / more-specific search
strings before shorter / more-general ones. See gotcha 11, which is this problem in its token-filling form and
is the one you will actually hit.

## 8. The signal-vs-noise problem with `occurrencesChanged`

**Symptom:** Your batch reports `total_requests: 30, total_replaced: 30, zero_match: 0`. You declare success.
Later you find that several intended changes did not happen.

**Cause:** Some replies come back as `{"replaceAllText": {}}` — i.e., the `occurrencesChanged` field is omitted
entirely (implicit zero). A naive jq query that defaults missing values to a non-zero treats them as
successes.

**Workaround:** Check for missing fields explicitly. The reliable query:

```bash
jq '.replies | to_entries | map(select(.value.replaceAllText.occurrencesChanged == null or .value.replaceAllText.occurrencesChanged == 0))' result.json
```

Better yet, always do a final residual scan on the doc body after the batch — don't trust the batch reply
alone. For this skill the residual scan is non-negotiable; see gotcha 11.

## 9. Image *content* cannot be replaced — but the image *element* can be deleted

**Symptom:** You expect the section 2.2 architecture diagram to update in place. It doesn't.

**Cause:** The Docs API has limited image manipulation. You can read an inline image's position and resize it,
but there is no request that swaps the embedded image content for a new one.

**Workaround — delete and re-insert; do not leave the old image in place.** An inline image occupies exactly
**one index** in the body (it is an `inlineObjectElement` inside a paragraph), so it is addressable:

1. Find its index with `scripts/inspect_doc.py`, which prints image-only paragraphs as
   `[IMAGE objectId=… idx=N]`.
2. `deleteContentRange` over that single index (`{"startIndex": N, "endIndex": N + 1}`) to remove the image.
3. Insert the new PNG at the `{{ARCHITECTURE_DIAGRAM}}` anchor with `scripts/insert_diagram.py`.

The full procedure, including clearing the anchor text afterwards, is **SKILL.md Phase D** — follow it there.

**This is not a manual TODO and the legacy image is not allowed to stay.** The template's §2.2 image is a
Finnet-era diagram belonging to a previous client; shipping it inside another customer's proposal is a
disclosure incident, not a cosmetic defect. Delete it in Phase D even when the replacement diagram fails to
generate, and report the doc as having no diagram rather than the wrong one. Never claim a diagram was
updated when it was not.

## 10. Multi-occurrence replacements

**Symptom:** A replacement that you expected to hit once actually hits twice (or more), and the second hit was
a place you didn't mean to change.

**Cause:** The search string accidentally matched somewhere else in the doc.

**Workaround:** Inspect `occurrencesChanged` for every request after the batch. Anything that returns `> 1`
deserves a sanity check against your intent. If it should have been singular, narrow the search string by
adding more surrounding context.

Note that several tokens in this template legitimately occur more than once, and a count greater than one is
correct for them: `{{CLIENT_SHORT_NAME}}` appears throughout the narrative sections, `{{SUPPORT_EMAIL}}`
appears in both the Support Contact Channel block and the Escalation Flow Level 1 row, `{{SUPPORT_TIMEZONE}}`
appears in two footnotes under the Devoteam SLA table, and `{{SDM_NAME}}` appears in both the Escalation Flow
Level 2 row and the Roles table. Know the expected count for each token before you run the batch, and compare
against it — the check is "does this match my expectation", not "is this equal to one".

## 11. Token-filling order: longest search string first, to protect what you have already inserted

**Note on a common misreading.** Every token in this template is `{{…}}`-delimited, so **no token name is a
substring of another** — `{{CLIENT_SHORT_NAME}}` cannot match inside `{{CLIENT_LEGAL_NAME}}` because of the
closing braces. You will not get artefacts like `PT Acme Sejahtera_LEGAL_NAME` from token-on-token collision.
The descending-length sort below is still the right discipline, but for a different reason: **value
collisions and partial chewing**.

**Symptom:** After a clean-looking batch, a replacement value has been partly overwritten, a `{{TBD — …}}`
placeholder you inserted comes back mangled or with a dangling `}}`, or a request reports
`occurrencesChanged: 0` even though you can see its target text in the document.

**Cause:** `batchUpdate` requests apply in sequence, each to the state left by the previous one — so a request
can match text that a *previous request wrote*, not text from the template. Two ways that bites:

- **Value collision.** If `{{CLIENT_SHORT_NAME}}` is filled with `Acme` first, the doc now contains the bare
  string `Acme` in several places; any later, broader search that happens to contain `Acme` — or any later
  search written against the *original* text — matches the wrong thing or nothing at all.
- **Partial chewing of an inserted placeholder.** If a fill value itself contains a `{{` sequence — which is
  exactly what happens when you insert a `{{TBD — …}}` — a subsequent broad replacement (say, one searching
  for `{{`) eats into it and leaves an orphaned `}}` behind.

Tokens that share a prefix are still worth knowing, because their *values* often land near each other and
because a hand-written partial search string (`{{SUPPORT_`, `{{ESCALATION_L3_`) will match several of them:

| Family | Members |
|---|---|
| `CLIENT_` | `{{CLIENT_LEGAL_NAME}}`, `{{CLIENT_SHORT_NAME}}` |
| `ESCALATION_L3_` | `{{ESCALATION_L3_NAME_1}}`, `{{ESCALATION_L3_NAME_2}}`, `{{ESCALATION_L3_EMAIL_1}}`, `{{ESCALATION_L3_EMAIL_2}}` |
| `SUPPORT_` | `{{SUPPORT_PORTAL_URL}}`, `{{SUPPORT_TIMEZONE}}`, `{{SUPPORT_EMAIL}}` |
| `ARCHITECTURE_` | `{{ARCHITECTURE_NARRATIVE}}`, `{{ARCHITECTURE_SUMMARY}}`, `{{ARCHITECTURE_DIAGRAM}}` (anchor — never filled) |

Always search on the **complete** `{{…}}` token, never on a prefix.

**Workaround — three rules, applied in this order:**

1. **Sort every replacement request by descending length of the search string** before submitting the batch.
   A longer search string is always at least as specific as a shorter one, so running the specific requests
   first means the broad ones have less already-written text left to chew into.

```python
requests.sort(key=lambda r: -len(r['replaceAllText']['containsText']['text']))
```

2. **Fill narrative-block tokens before scalar tokens.** Large blocks (`{{PROBLEM_STATEMENT}}`,
   `{{SOLUTION_COMPONENTS}}`, `{{SCOPE_OF_WORKS}}`, `{{DELIVERABLES}}`) may themselves contain scalar tokens
   such as `{{CLIENT_SHORT_NAME}}` in their prose. Insert the block first, then run the scalar pass so the
   scalars inside the newly inserted text also get resolved. Running the scalar pass first leaves live tokens
   inside content that was inserted afterwards.

3. **Residual scan for `{{` — always, and treat any hit as a failure.** The batch reply is not evidence; the
   document is. After the final batch, re-fetch the doc, flatten all paragraphs and table cells to text, and
   search for the literal `{{`:

```python
leftovers = [line for line in flattened_text.splitlines() if '{{' in line]
```

Every hit falls into exactly one of two categories, and you must classify each one:

- **A deliberate `{{TBD — …}}` placeholder.** Expected. Report it to the engineer as an open item, quoting the
  section it sits in.
- **Anything else.** A defect. An unfilled token means a section of the proposal is missing or wrong. Fix it
  and re-scan; do not report the document as complete.

`scripts/verify_residuals.py` does this classification for you: pass `--residuals '{{' '}}'` together with
`--allow-prefix '{{TBD'`, and the deliberate placeholders are subtracted from the residual count and listed
separately as open items instead of failing the run.

Also scan for the closing `}}` independently. A partially chewed token can leave a dangling `}}` with no
opening pair, which the `{{` scan will not catch.

Never report a proposal as finished without showing the engineer the residual-scan output.

## 12. Multi-paragraph token expansion: `replaceAllText` cannot create headings or bullets

**Symptom:** You replace `{{SOLUTION_COMPONENTS}}` with a well-structured block containing `## BigQuery`
headings and `-` bullets. The document renders one enormous wall of Normal-text body copy, with literal `##`
and `-` characters visible, no heading styles, no bullet glyphs, and no indentation. The same happens to
`{{SCOPE_OF_WORKS}}`, `{{DELIVERABLES}}`, `{{ARCHITECTURE_NARRATIVE}}`, and `{{OUT_OF_SCOPE}}`.

**Cause:** This is a hard limitation, not a bug to be worked around cleverly. `replaceAllText` inserts **plain
text only**. It cannot apply paragraph styles, cannot create list structures, and cannot interpret Markdown.
The `\n` characters in your replacement string do correctly create new paragraphs, but every one of those
paragraphs inherits the style of the paragraph the token was sitting on — Normal text. Meanwhile the tokens in
question each occupy a *single* paragraph in the template by design (see gotcha 1), yet must expand into
dozens of paragraphs at several heading levels with nested bullets.

**Recommended approach — insert, then style, in that order:**

1. **Locate the token's paragraph** and record its `startIndex`. Use `inspect_doc.py`.

2. **`deleteContentRange` the token text, then `insertText` the full multi-paragraph plain text** at that
   index, with `\n` separating paragraphs and no Markdown syntax — write the heading text as plain text
   (`BigQuery`), not as `## BigQuery`, and bullet text without a leading `-`.

3. **Re-fetch the document.** You now need real indices for the paragraphs you just created, and the ones you
   computed before the insert are stale.

4. **Apply `updateParagraphStyle`** to each paragraph that should be a heading, setting `namedStyleType` to
   `HEADING_3` or `HEADING_4` to match the surrounding `2.3.x` structure, with
   `fields: "namedStyleType"`.

5. **Apply `createParagraphBullets`** over each contiguous run of paragraphs that should be a list, with an
   appropriate `bulletPreset` such as `BULLET_DISC_CIRCLE_SQUARE`. Nesting level is controlled by the
   paragraph's indentation, so set `indentStart` / `indentFirstLine` via `updateParagraphStyle` for
   second-level bullets.

6. **Work back-to-front** across the styling requests, exactly as in gotcha 4, and re-fetch between batches.

**The pragmatic alternative, which is often the right choice.** This is a genuinely fiddly sequence, and the
failure mode is a visibly malformed customer-facing document. It is legitimate — and frequently faster and
safer — to insert the content as correctly-paragraphed plain text, then tell the engineer explicitly which
sections need heading and bullet formatting applied by hand in the Google Docs UI, listing them by section
number. Two minutes of manual restyling beats a mangled proposal.

**What is not acceptable** is inserting Markdown syntax into the document and leaving it there. Literal `##`
and `**` characters in a document that goes to a client are an obvious, embarrassing defect. If you are not
applying the styling programmatically, strip the Markdown before inserting and hand off the formatting task
explicitly.

## 13. Filling a rebuilt table: empty-cell insert index, and the fill-before-merge order

**Symptom:** `insertText` into a freshly inserted table fails with "The insertion index must be inside
the bounds of an existing paragraph", or text lands in the wrong cell; later, header cells you merged
have lost their text, or bold/white header styling silently did not apply.

**Causes and rules, in execution order:**

1. **Empty-cell insert index is the paragraph's `startIndex` — never `+1`.** An empty cell's only
   paragraph occupies `[start, start+1)`; `start` is the one valid insertion point. (`+1` is the index
   *after* the newline — outside every paragraph.)
2. **Fill before you merge.** `mergeTableCells` folds non-head cells away; text meant for a cell that a
   merge has consumed has nowhere to go, and a merge that spans cells you still need as separate
   headers (e.g. a 2-row × 3-col merge swallowing the `Task` header) destroys layout. Recover with
   `unmergeTableCells`, re-fill, then re-merge the intended ranges only.
3. **Cell background styling needs no text indices.** `updateTableCellStyle` addressed via
   `tableStartLocation` + `rowIndex`/`columnIndex`/spans works on an empty table and survives later
   fills — safe to run early.
4. **Text styling does NOT stick to empty cells.** `updateTextStyle` over an empty cell styles one
   newline; text inserted afterwards arrives unstyled. Run all `updateTextStyle` (bold, white header
   text, font size) AFTER the fill, from a fresh fetch.

Full safe sequence for replacing a template table with a rebuilt one (e.g. mirroring a Sheets Gantt):
`deleteContentRange` old table → `insertTable` → fill cells (back-to-front, rule 1) → merges → cell
backgrounds / column widths → re-fetch → text styles. To mirror a Sheets timeline, fetch the sheet with
`includeGridData:true` and read `effectiveFormat.backgroundColor` per cell — the Gantt bars are
backgrounds, not values.

## 14. Inline images: the Drive URL the Docs API can actually fetch, and the glue-paragraph trap

**Symptom 1:** `insertInlineImage` fails with "There was a problem retrieving the image" or "Access to
the provided image was forbidden", even though the Drive file exists and `insert_diagram.py` said it set
sharing.

**Fix:** upload the PNG (`gws drive files create --params '{"uploadType":"multipart"}' --upload
<file>`), grant `{"type":"anyone","role":"reader"}` via `permissions create`, wait a beat, then use the
**`https://lh3.googleusercontent.com/d/<FILE_ID>=w1600`** form — not `drive.google.com/uc`, which
serves an HTML interstitial. Verify before inserting: `curl -sI <lh3-url>` must return `200` with
`content-type: image/png`. If the upload itself 404s afterwards, re-upload; if a parent folder 404s,
drop `parents` or pass `supportsAllDrives:true`.

**Symptom 2:** the inserted image renders glued to the start of the following paragraph (text flows
beside it) instead of standing alone.

**Cause:** inserting at the anchor paragraph's end index places the image at the *next* paragraph's
first position; clearing the anchor token afterwards shifts it further.

**Fix:** after inserting, `insertText` a single `"\n"` at `imageIndex + 1` so the image owns its
paragraph. **Swapping an image later is cheap:** find its `inlineObjectElement` index, then one batch of
`deleteContentRange {idx, idx+1}` + `insertInlineImage` at `idx` — the paragraph and surrounding text
are untouched, so the anchor work never has to be redone.

## 15. Appending content to an existing bullet inherits its list — a feature with one trap

`replaceAllText` where the replacement is `<anchor text>\n<new line 1>\n<new line 2>` is the fastest
way to add items to an existing bulleted list: every appended paragraph inherits the anchor's bullet
preset and indent, no styling pass needed. The trap: a **section heading** appended this way arrives as
a bullet too. Follow up with `deleteParagraphBullets` + `updateParagraphStyle` (`HEADING_4`, and reset
`indentStart`/`indentFirstLine` to 0) on just the heading paragraphs, located by exact text in a fresh
fetch. Also remember `matchCase` anchors must be globally unique — pick the longest anchor available
and sort the batch by descending search length (gotcha 11).

## 16. Reading a non-native (.docx) file the Drive API refuses to download

**Symptom:** `gws drive files download` on an uploaded `.docx` returns `500 Internal error` repeatedly;
`files export` is not available because export only works on Google-native files.

**Fix:** copy-convert, then export the copy:
`files copy --json '{"name":"[temp] extract","mimeType":"application/vnd.google-apps.document"}'` →
`files export --params '{"mimeType":"text/plain"}'` → read the text → trash the temp copy
(`files update --json '{"trashed":true}'`). Doc comments (including reviewer threads) survive the
conversion and appear at the end of the plain-text export.
