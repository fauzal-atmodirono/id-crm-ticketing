# Demo conversation scripts

One customer turn per line; blank lines and `#` comments ignored. Fed to the
replay harness with `--script`:

    agent/.venv/bin/python deploy/scripts/bahana_replay.py \
        --slug moderat --script deploy/scripts/demo-scripts/bahana-moderat.txt

These three are the scripts behind the transcripts printed in
`docs/bahana-demo-guide-customer-v3.md` §5. They are kept in the repo so that
document's claim to quote real output stays checkable: re-run them and compare.

Each script is built to cross the same five beats — greeting, profile question,
concern, **refusal**, escalation — and each ends on a deliberately different
escalation trigger, so the four rows of v3 §6 are covered by something you can
actually run:

| Script | Ends on | Escalation kind |
|---|---|---|
| `bahana-konservatif.txt` | "saya mau bicara dengan relationship manager" | customer asks |
| `bahana-moderat.txt` | "tolong ubah nomor rekening bank saya" | operational request |
| `bahana-agresif.txt` | "ada IPO apa yang bagus minggu ini?" | compliance boundary |

The fourth kind (the `BERHENTI` keyword) is a Chatwoot automation rule, not an
AI decision, so it has no script here — the harness only exercises the model.

The wording is exact on purpose, including the refusal turn. That turn is the
one that used to dead-end the conversation, so it is the regression these
scripts exist to catch. Expect the model's *phrasing* to differ run to run;
what should not differ is which products it names, which it refuses, and where
it hands off.
