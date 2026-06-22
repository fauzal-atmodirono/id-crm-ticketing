#!/usr/bin/env python3
# ruff: noqa: S311
"""Script to generate 100 mock Malaysian customer profiles and import them into Zendesk.

Uses Zendesk's create_or_update_many API to perform a bulk idempotent import.
"""

from __future__ import annotations

import base64
import concurrent.futures
import os
import random
import sys
from typing import Any

import httpx

# Add src to python path so we can import config
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "src"))
from chatbot.platform.config import get_settings


def generate_mock_users(count: int = 100) -> list[dict[str, Any]]:
    # Pools for generating realistic Malaysian names
    first_names_malay = [
        "Muhammad",
        "Ahmad",
        "Abdul",
        "Siti",
        "Nur",
        "Mohd",
        "Farah",
        "Amir",
        "Khairul",
        "Zulkifli",
        "Hafiz",
        "Ibrahim",
        "Aisyah",
        "Fatimah",
        "Nadia",
        "Mohammad",
        "Rizal",
        "Syahmi",
        "Anas",
        "Aina",
    ]
    last_names_malay = [
        "Rosli",
        "Ahmad",
        "Yusof",
        "Ismail",
        "Halim",
        "Sulaiman",
        "Rahman",
        "Bakri",
        "Osman",
        "Ghani",
        "Hassan",
        "Kamal",
        "Zainal",
        "Razak",
        "Aris",
        "Salim",
        "Latif",
        "Hamid",
    ]

    names_chinese = [
        "Tan Kah Seng",
        "Michelle Tan Wei Ling",
        "Chong Wei Liam",
        "Lee Kok Wai",
        "Wong Siew Fern",
        "Lim Boon Hock",
        "Ng Chee Keong",
        "Chan Mei Yee",
        "Teoh Bee Yin",
        "Liew Kenji",
        "Low Kah Wai",
        "Yip Siew Kuan",
        "Sim Hock Beng",
        "Goh Chee Seng",
        "Chia Mei Ling",
    ]
    names_indian = [
        "Khavish Rajah",
        "Priya Ramasamy",
        "Suresh Kumar",
        "Ananthi Subramaniam",
        "Divya Naidu",
        "Arun Balakrishnan",
        "Vikneswaran",
        "Tharshini",
        "Logeshwaran",
        "Shamini",
        "Karthik Muniandy",
        "Pavithra",
        "Ganesan",
        "Revathy",
        "Yogeswaran",
    ]

    models = ["Saga", "Persona", "Iriz", "X50", "X70", "X90", "S70"]
    statuses = ["lead", "prospect", "existing_owner"]
    regions = [
        "Kuala Lumpur",
        "Selangor",
        "Penang",
        "Johor Bahru",
        "Perak",
        "Sarawak",
        "Sabah",
        "Melaka",
        "Pahang",
    ]
    dealers = [
        "Glenmarie Branch",
        "Petaling Jaya Showroom",
        "Jalan Ipoh Outlet",
        "Plentong Sales",
        "Bayan Lepas Showroom",
        "Ipoh Garden Branch",
        "Kuching Outlet",
        "Kota Kinabalu Sales",
    ]

    users = []
    used_emails = set()
    used_phones = set()

    random.seed(42)  # Make generation reproducible

    for i in range(count):
        # Choose ethnic name pool randomly
        pool_choice = random.choices(["malay", "chinese", "indian"], weights=[0.5, 0.3, 0.2])[0]

        if pool_choice == "malay":
            fn = random.choice(first_names_malay)
            ln = random.choice(last_names_malay)
            name = f"{fn} {ln}"
            email_prefix = f"{fn.lower()}.{ln.lower()}"
        elif pool_choice == "chinese":
            name = random.choice(names_chinese)
            parts = name.split()
            email_prefix = f"{parts[-1].lower()}.{parts[0].lower()}"
        else:
            name = random.choice(names_indian)
            parts = name.split()
            email_prefix = f"{parts[0].lower()}.{parts[-1].lower()}"

        # Clean email prefix
        email_prefix = "".join(c for c in email_prefix if c.isalnum() or c == ".").replace(
            "..", "."
        )
        email = f"{email_prefix}{i}@proton-mock.example.com"

        # Deduplicate email
        while email in used_emails:
            email = f"{email_prefix}{i}_{random.randint(10, 99)}@proton-mock.example.com"
        used_emails.add(email)

        # Generate unique phone
        phone_prefix = random.choice(
            ["+6012", "+6013", "+6014", "+6016", "+6017", "+6018", "+6019"]
        )
        phone_suffix = "".join(str(random.randint(0, 9)) for _ in range(7))
        phone = f"{phone_prefix}{phone_suffix}"
        while phone in used_phones:
            phone_suffix = "".join(str(random.randint(0, 9)) for _ in range(7))
            phone = f"{phone_prefix}{phone_suffix}"
        used_phones.add(phone)

        # Choose preferences
        model = random.choice(models)
        status = random.choice(statuses)
        region = random.choice(regions)
        dealer = random.choice(dealers)

        tag_model = f"car_interest_{model.lower()}"
        tag_status = f"buyer_status_{status}"
        tag_region = f"region_{region.lower().replace(' ', '_')}"

        details = f"Mock Proton Buyer: Interested in Proton {model}. Preferred Dealer: {dealer}. Region: {region}."

        users.append(
            {
                "name": name,
                "email": email,
                "phone": phone,
                "details": details,
                "tags": ["proton_mock", tag_model, tag_status, tag_region],
            }
        )

    return users


def run_import() -> None:
    print("Loading application settings...")
    settings = get_settings()

    if settings.crm_provider != "zendesk":
        print("ERROR: Application is not configured for Zendesk provider.")
        print(f"Current CRM_PROVIDER = '{settings.crm_provider}'")
        sys.exit(1)

    subdomain = settings.zendesk_subdomain
    email = settings.zendesk_email
    token = settings.zendesk_api_token

    if not subdomain or not email or not token:
        print("ERROR: Missing Zendesk configuration in environment or .env file.")
        print(f"Subdomain: {subdomain or '[MISSING]'}")
        print(f"Email: {email or '[MISSING]'}")
        print(f"API Token: {'[CONFIGURED]' if token else '[MISSING]'}")
        sys.exit(1)

    print(f"Zendesk Account: https://{subdomain}.zendesk.com")
    print("Generating 100 mock customer profiles...")
    users = generate_mock_users(100)

    # API Auth Headers
    auth_str = f"{email}/token:{token}".encode()
    encoded = base64.b64encode(auth_str).decode("ascii")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Basic {encoded}",
    }

    def create_single_user(
        client: httpx.Client, user_data: dict[str, Any], url: str, headers: dict[str, str]
    ) -> tuple[bool, str]:
        payload = {"user": user_data}
        try:
            res = client.post(url, json=payload, headers=headers)
            if res.status_code == 201:  # noqa: PLR2004
                return True, f"Created: {user_data['name']} ({user_data['email']})"
            if res.status_code == 422:  # noqa: PLR2004
                # 422 typically means the email or phone is already taken. Let's inspect the error response.
                err_msg = res.json().get("description", "Record invalid")
                return (
                    True,
                    f"Already exists / Skipped: {user_data['name']} ({user_data['email']}) - {err_msg}",
                )
            return False, f"Error {res.status_code} for {user_data['email']}: {res.text}"
        except Exception as e:
            return False, f"Exception for {user_data['email']}: {e}"

    print(f"Importing {len(users)} users in parallel using ThreadPoolExecutor...")
    url = f"https://{subdomain}.zendesk.com/api/v2/users.json"

    success_count = 0
    fail_count = 0

    with (
        httpx.Client(timeout=30.0) as client,
        concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor,
    ):
        # Submit all jobs
        future_to_user = {
            executor.submit(create_single_user, client, u, url, headers): u for u in users
        }

        for idx, future in enumerate(concurrent.futures.as_completed(future_to_user)):
            success, msg = future.result()
            if success:
                success_count += 1
            else:
                fail_count += 1

            # Print progress every 10 users or if it failed
            if not success or (idx + 1) % 10 == 0 or idx == len(users) - 1:
                print(f"[{idx + 1}/{len(users)}] {msg}")

    print(f"\nImport Finished: {success_count} succeeded, {fail_count} failed.")
    if fail_count > 0:
        print("Some users failed to import. Please check logs.")
        sys.exit(1)


if __name__ == "__main__":
    run_import()
