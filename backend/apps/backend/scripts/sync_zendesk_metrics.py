"""Deprecated alias — PROTON migrated its CRM to Chatwoot+Zammad.

The metrics batch sync now pages Chatwoot conversations, not Zendesk tickets.
Use ``scripts/sync_chatwoot_metrics.py``. This shim forwards to it so existing
references / cron entries keep working.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sync_chatwoot_metrics import main

if __name__ == "__main__":
    sys.exit(main())
