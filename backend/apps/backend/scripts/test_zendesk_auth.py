#!/usr/bin/env python3
import base64
import os
import sys

import httpx

sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
from chatbot.platform.config import get_settings

settings = get_settings()
auth_str = f"{settings.zendesk_email}/token:{settings.zendesk_api_token}".encode()
encoded = base64.b64encode(auth_str).decode("ascii")
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Basic {encoded}",
}

url = f"https://{settings.zendesk_subdomain}.zendesk.com/api/v2/users/me.json"
with httpx.Client() as client:
    res = client.get(url, headers=headers)
    print("Status:", res.status_code)
    print("Body:", res.text)
