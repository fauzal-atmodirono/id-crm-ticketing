# Live ladder test on proton — end to end

**Written against the deployed state on 2026-08-19.** Backend and agent are
running the new code; the contact matrix below is already seeded. The only
thing left is the env block in §1, which you run.

**CRM:** https://proton.crm.34-50-103-151.nip.io
**Email inbox:** `devotech29@gmail.com` (inbox id 4)

## The cast

| Who | Address | Set up as |
|---|---|---|
| Customer | `yuda.adi.pratama@devoteam.com` | just sends the email |
| After Sales PIC | `jacipsbusiness@gmail.com` | `dept_aftersales` |
| Dealer CRE | `yudaadipratama2209@gmail.com` | step 1 TO |
| Sales/Aftersales Mgr | `yudaadipratama2209+sam@gmail.com` | step 1 TO |
| Dealer Principal | `yudaadipratama2209+dp@gmail.com` | **step 3** TO |
| Dealer Owner | `yudaadipratama2209+owner@gmail.com` | **step 4** TO |
| PRO-NET Area/Regional Mgr | `jacipsbusiness+arm@gmail.com` | CC on every rung |
| PRO-NET HOD | `jacipsbusiness+hod@gmail.com` | CC on every rung |

Everything with a `+` lands in the same inbox as its base address, so you
watch four "different people" from two mailboxes. The label to apply is
**`dealer_petaling_jaya`** — I created it, and it maps to the dealer record
`petaling_jaya` carrying the four roles above.

> Your existing **"Petaling Jaya Dealer"** group (with spaces) can never match
> a Chatwoot label — labels have no spaces, and the lookup is exact. It's
> harmless, but delete it in Escalation Routing to avoid confusion. The one
> the ladder uses is `petaling_jaya`.

> When the dealer replies, Gmail sends from the **base** address
> (`yudaadipratama2209@gmail.com`) whichever `+alias` received it. That address
> is the CRE, so the reply links and the ladder stops — which is what you want
> to see.

---

## 1. Turn it on — you run this

I can't edit the tenant env from here. Paste this on the VM:

```bash
gcloud compute ssh crm-ticketing --zone=asia-southeast2-a
sudo tee -a /opt/platform/deploy/tenants/proton.env >/dev/null <<'EOF'

# --- 2026-08-19 escalation ladder, TEST SETTINGS ---
ESCALATION_CUSTOMER_UPDATE_ENABLED=true
ESCALATION_CUSTOMER_UPDATE_HOURS=4
ESCALATION_POLICY_ENABLED=true
ESCALATION_POLICY_DRY_RUN=true
ESCALATION_POLICY_SCAN_INTERVAL_SECONDS=60
ESCALATION_POLICY_STEPS_JSON=[{"step_no":1,"delay_working_hours":0,"to_roles":["cre","sales_aftersales_mgr"],"cc_roles":["principal","area_regional_mgr","hod"]},{"step_no":2,"delay_working_hours":0.033,"to_roles":[],"cc_roles":[],"label":"ACKNOWLEDGEMENT DUE"},{"step_no":3,"delay_working_hours":0.066,"to_roles":["principal"],"cc_roles":["owner","sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"1ST REMINDER"},{"step_no":4,"delay_working_hours":0.133,"to_roles":["owner"],"cc_roles":["principal","sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"2ND REMINDER"},{"step_no":5,"delay_working_hours":0.133,"to_roles":["principal","owner"],"cc_roles":["sales_aftersales_mgr","cre","area_regional_mgr","hod"],"label":"FINAL ESCALATION - TELEPHONE","channel":"phone"}]
EOF

cd /opt/platform/deploy && sudo docker compose -p proton \
  -f docker-compose.tenant.yml --env-file tenants/proton.env up -d backend agent
```

That step table compresses **2h / 4h / 8h working hours into 2 / 4 / 8 minutes**,
so the whole ladder runs in about ten minutes. Delete those lines when you're
done testing and recreate — the defaults are the real SOP timers.

Optional, if you also want the inbound auto-acknowledgement in the run:
`EMAIL_AUTOACK_ENABLED=true`.

**Confirm it started:**

```bash
sudo docker logs proton-backend 2>&1 | grep escalation_ladder_scheduler_started
# expect: interval_seconds=60 dry_run=True
```

**Keep this open in a second terminal for the whole test:**

```bash
sudo docker logs -f proton-backend 2>&1 | grep -E "escalation_ladder|customer_update|sla_"
```

---

## 2. The customer's email

From `yuda.adi.pratama@devoteam.com` to `devotech29@gmail.com`:

> **Subject:** Home charger stopped working — e.MAS 7, VAB 3271
>
> Hi, I bought an e.MAS 7 from Proton e.MAS Petaling Jaya last month, plate
> VAB 3271. The home charger has stopped charging the car. It worked fine for
> three weeks and now the indicator light stays red and nothing happens. I have
> tried a different socket. Please help — I cannot charge at home at all.
>
> Danish

Wait ~2 minutes for the IMAP poll.

**PASS:** a new conversation on the Email inbox.
If `EMAIL_AUTOACK_ENABLED=true`, Danish also gets exactly one acknowledgement.

---

## 3. Escalate

Open the case and apply **three labels**, in this order:

1. `dept_aftersales`
2. `dealer_petaling_jaya`
3. `escalate`

Within seconds, **three emails**:

| Inbox | What arrives | Check |
|---|---|---|
| `yuda.adi.pratama@devoteam.com` | `Update on your case (#N)` | **subject is not his own email quoted back**, it lands **inside his own thread**, and there is **no CC** |
| `jacipsbusiness@gmail.com` | `[Escalation] [CASE-N] Home charger stopped working…` | the PIC leg |
| `yudaadipratama2209@gmail.com` (+`+sam`) | `[Escalation] [CASE-N] …` | TO = CRE + Sales/AS Mgr, **CC** = Principal + `jacipsbusiness+arm` + `+hod` |

**Then check the conversation sidebar** — `escalation_notified_at` is stamped.
That stamp is what the ladder measures from; without it nothing climbs.

---

## 4. The ladder, in dry run first

Do nothing. Watch the log. Roughly one line per minute:

```
escalation_ladder_dry_run conv_id=N step_no=2 label="ACKNOWLEDGEMENT DUE" to=[] cc=[] elapsed_working_hours=0.04
escalation_ladder_dry_run conv_id=N step_no=3 label="1ST REMINDER" to=["yudaadipratama2209+dp@gmail.com"] cc=[...] elapsed_working_hours=0.07
escalation_ladder_dry_run conv_id=N step_no=4 label="2ND REMINDER" to=["yudaadipratama2209+owner@gmail.com"] cc=[...]
escalation_ladder_dry_run conv_id=N step_no=5 label="FINAL ESCALATION - TELEPHONE" ...
```

**PASS:** four lines, one per sweep, **no email sent**, and the recipients are
the right person for each rung. This is the rehearsal — on a real tenant you
read a week of this before going live.

---

## 5. The ladder, for real

Switch dry run off and escalate a **fresh** case (a stamped step never re-fires,
so re-use the same case and nothing will happen):

```bash
sudo sed -i 's/^ESCALATION_POLICY_DRY_RUN=true/ESCALATION_POLICY_DRY_RUN=false/' \
  /opt/platform/deploy/tenants/proton.env
cd /opt/platform/deploy && sudo docker compose -p proton \
  -f docker-compose.tenant.yml --env-file tenants/proton.env up -d backend
```

Send a second customer email, label it the same three ways, then leave it alone.

| ~T+2 min | **Step 2** — the acknowledgement window closes. **No email.** Sidebar gains `escalation_step2_sent_at`. |
|---|---|
| ~T+4 min | **Step 3** — mail to `+dp` (Principal), CC includes `+owner`, `+sam`, base, `+arm`, `+hod`. Subject `[1ST REMINDER] [CASE-N] …`, body says how many working hours it has gone unanswered and asks for action taken + status update. |
| ~T+8 min | **Step 4** — mail to `+owner` (Owner). Subject `[2ND REMINDER] …`, body requires immediate action and a resolution status. |
| ~T+9 min | **Step 5** — **no email**. A private note on the case: `☎️ FINAL ESCALATION — TELEPHONE REQUIRED`, who to ring in order, the 1-hour window, the Daily Complaint Clause. `follow_up_at` set one hour out. |

**The thing to watch:** each rung goes to a **different** person, and only one
rung fires per minute even though several are overdue by the end.

---

## 6. The dealer replies — the path that matters most

On a **third** case, escalate, let step 3 go out, then **hit Reply** on the
1st-reminder email from `yudaadipratama2209@gmail.com`. Keep the subject line
(the `[CASE-N]` tag) and leave the quoted trail alone.

Within a poll cycle, on the **original** case:

- a private note: `Reply from petaling_jaya (cre) <yudaadipratama2209@gmail.com>:` with just what you typed;
- a second private note: `Suggested customer reply (draft — review before sending)`;
- label `escalation_replied` + attribute `escalation_replied_at`;
- the throwaway conversation the reply landed in is labelled `escalation_reply` and resolved.

**PASS: step 4 never fires.** The ladder is stopped.

**No email reaches Danish.** That is the design — the dealer's words are
internal, and a person decides what the customer is told.

### 6a. The clock that now runs

`escalation_replied_at` starts the customer-update clock: 4 working hours to
pass the answer on. Two ways to see it:

- **Clear it:** paste the draft into the reply composer, edit, send as a normal
  public reply. `customer_updated_at` appears in the sidebar. (A private note
  does **not** clear it — deliberately.)
- **Breach it:** set `ESCALATION_CUSTOMER_UPDATE_HOURS=0.02` (about a minute),
  recreate the backend, and leave a replied case alone. Within one SLA scan
  (**up to 15 minutes** — that scan is separate from the ladder's 60s sweep) a
  private note appears saying the dealer answered N hours ago and the customer
  has not been updated, plus the usual PIC alert email.

---

## 7. The out-of-office trap

Worth doing once, because before this week it broke the whole policy.

On a fourth case, escalate, then reply from `yudaadipratama2209@gmail.com` with
the subject **`Automatic reply: out of office`**.

**PASS:**
- a private note appears: *"Automatic reply … (not counted as a response; the
  escalation clock is still running)"*;
- `escalation_replied_at` is **NOT** stamped;
- the ladder **keeps climbing** to step 4.

An away message must never satisfy an escalation policy.

---

## 8. Things that should not happen

- Danish never receives an email with anyone in CC.
- Danish never receives the dealer's words automatically.
- No rung is ever sent twice, however many sweeps run.
- A reply from an address not in Escalation Routing is **not** linked — you get
  a note naming the address, without its text.

---

## 9. Putting it back

```bash
# remove the whole "2026-08-19 escalation ladder, TEST SETTINGS" block, then:
cd /opt/platform/deploy && sudo docker compose -p proton \
  -f docker-compose.tenant.yml --env-file tenants/proton.env up -d backend agent
```

Removing `ESCALATION_POLICY_STEPS_JSON` restores the real SOP timers
(2h/4h/8h **working** hours). Setting `ESCALATION_POLICY_ENABLED=false` stops
the ladder entirely; the `escalation_step*` attributes left on old cases are
inert.

---

## Known limits of this run

- **My-Tasks is not deployed on proton** (only faq-admin and agent-assist are),
  so the Customer-update and Attend-after columns aren't visible here. The
  clock still fires its alert note and email.
- **One-click "Send to customer"** on the draft note isn't built — copy-paste.
- The mailbox is still the Gmail relay; `e.mascentre@pronet.my` needs Proton's
  IMAP/SMTP credentials.
