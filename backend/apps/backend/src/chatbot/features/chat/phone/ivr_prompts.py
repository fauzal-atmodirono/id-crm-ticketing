"""Every word the DTMF IVR speaks, English and Bahasa Melayu, in one place.

Task 31 (FIX-31, FIX-56 step 7). Proton edits IVR wording through change
requests, so it lives here and nowhere else: `ivr.py` builds the TwiML,
`dtmf_menu.py` / `after_hours.py` re-export the menu and after-hours strings
under their historical names, and nothing else hard-codes a prompt. To change
what a caller hears, edit this file (and only this file), then run
`test_ivr.py`, whose SOP drift tests re-read the source of truth.

**Source of truth.** `docs/client-materials/CRM Process Flow (1).xlsx`, sheet
"IVR Call": cells B4-B32 and the flowchart shapes in `xl/drawings/drawing4.xml`
(the `<a:t>` text -- openpyxl drops shapes, so the tests read the XML). Where
the sheet and this file differ, the difference is one of these, and only these:

- The flowchart's "Asistance" is spelt "Assistance".
- B30's "pelanngan" is spelt "pelanggan", and the missing space in
  "terdekat.Sila" is restored.
- The after-hours message renders the sheet's "Monday-Friday: 8:30 AM - 5:30
  PM" layout (en dashes in the sheet) as a spoken sentence ("Monday to Friday, 8:30 AM to 5:30 PM"),
  which is the same wording the Studio flow already used.

**Why the welcome and the language choice are four separate strings.** The
pre-selection prompt is bilingual, and a single `<Say>` speaks in one voice:
the Studio flow read the Malay half in the English voice. Here each half gets
its own `<Say language=... voice=...>`, so the Malay is pronounced by the
Malay voice. `LANGUAGE_GATHER_PROMPT` (all four joined) is kept only as the
historical export of `dtmf_menu.py`.

**The Studio flow is not the live path** (see `deploy/twilio/README.md` and the
Task 31 report): Proton's Twilio number posts to `/voice/phone/incoming`. The
Studio export was left untouched, so its welcome still says "service centre"
and its Malay menu "sifar" where the SOP says "centre" and "0".
"""

from __future__ import annotations

# --- Welcome and language choice (flowchart: START shape) -------------------
WELCOME_EN = "Welcome to Proton e.MAS centre."
WELCOME_MS = "Selamat datang ke Pusat Perkhidmatan Proton e.MAS."
LANGUAGE_CHOICE_EN = "For English, press 1."
LANGUAGE_CHOICE_MS = "Untuk Bahasa Melayu, tekan 2."

LANGUAGE_GATHER_PROMPT = " ".join(
    (WELCOME_EN, WELCOME_MS, LANGUAGE_CHOICE_EN, LANGUAGE_CHOICE_MS)
)

# --- Category menu (flowchart: English / Bahasa Melayu shapes) --------------
MENU_EN = (
    "For Roadside Assistance, press 1. For Inquiry, press 2. "
    "For Complaint, press 3. To repeat, please press zero."
)
MENU_MS = (
    "Untuk Bantuan Kerosakan, tekan 1. Untuk Pertanyaan, tekan 2. "
    "Untuk Aduan, tekan 3. Untuk Ulangan, sila tekan 0."
)

# --- Queue busy, non-RSA, after 10 s of ringing (B28-B30) -------------------
QUEUE_BUSY_EN = (
    "All our agents are currently assisting other customers. Please stay on the "
    "line, or alternatively contact us through the Proton e.MAS app. Thank you "
    "for your patience."
)
QUEUE_BUSY_MS = (
    "Semua ejen kami sedang melayani pelanggan. Ejen kami akan membantu anda "
    "dalam masa terdekat. Sila terus berada di talian dan kami akan melayan "
    "panggilan anda secepat mungkin. Sebagai alternatif, anda boleh menghubungi "
    "kami melalui aplikasi Proton e.MAS."
)

# --- After hours (flowchart: Within Office Hour = NO) -----------------------
AFTER_HOURS_EN = (
    "Thank you for calling Proton e.MAS. Our operating hours are Monday to "
    "Friday, 8:30 AM to 5:30 PM, and Saturday, Sunday and Public Holidays, "
    "9:00 AM to 5:00 PM. Please leave your message and contact details. Our "
    "customer support team will reach out on the next business day."
)
AFTER_HOURS_MS = (
    "Terima kasih kerana menghubungi Proton e.MAS. Waktu operasi kami adalah "
    "Isnin hingga Jumaat, 8.30 pagi hingga 5.30 petang, dan Sabtu, Ahad dan "
    "Cuti Umum, 9.00 pagi hingga 5.00 petang. Sila tinggalkan mesej dan "
    "maklumat perhubungan anda. Pasukan khidmat pelanggan kami akan menghubungi "
    "anda pada hari bekerja berikutnya."
)

# --- Voicemail, goodbye, nobody answered (not in the SOP; Studio flow text) --
VOICEMAIL_EN = "Please leave your message and contact details after the tone."
VOICEMAIL_MS = "Sila tinggalkan mesej dan maklumat perhubungan anda selepas nada."
VOICEMAIL_PROMPT = f"{VOICEMAIL_EN} {VOICEMAIL_MS}"

GOODBYE_EN = "Thank you for calling Proton e.MAS. Goodbye."
GOODBYE_MS = "Terima kasih kerana menghubungi Proton e.MAS."

# Spoken when the ring stage ends unanswered and voicemail is off. Same text
# as the AI bridge's unanswered-handoff apology in router.py.
NO_AGENT_EN = (
    "We're sorry, none of our agents are available to take your call right now. "
    "We've noted your call and someone will call you back soon. Goodbye."
)
NO_AGENT_MS = (
    "Maaf, tiada ejen kami tersedia untuk menjawab panggilan anda sekarang. "
    "Kami telah mencatatkan panggilan anda dan seseorang akan menghubungi anda semula. "
    "Selamat tinggal."
)

# Lookup used by ivr.py: one entry per language the menu offers.
PROMPTS: dict[str, dict[str, str]] = {
    "en": {
        "menu": MENU_EN,
        "queue_busy": QUEUE_BUSY_EN,
        "after_hours": AFTER_HOURS_EN,
        "voicemail": VOICEMAIL_EN,
        "goodbye": GOODBYE_EN,
        "no_agent": NO_AGENT_EN,
    },
    "ms": {
        "menu": MENU_MS,
        "queue_busy": QUEUE_BUSY_MS,
        "after_hours": AFTER_HOURS_MS,
        "voicemail": VOICEMAIL_MS,
        "goodbye": GOODBYE_MS,
        "no_agent": NO_AGENT_MS,
    },
}
