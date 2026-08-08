"""Client support for the AI-suggested-department feature:
`ChatwootClient.get_labels` and `ProtonConfigClient.get_escalation_departments`.
"""

import httpx
import respx

from app.clients.chatwoot import ChatwootClient
from app.clients.proton import ProtonConfigClient

CHATWOOT = "http://chatwoot-rails:3000"
PROTON = "http://proton-backend:8080"


@respx.mock
async def test_get_labels_returns_raw_payload():
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/9/labels").mock(
        return_value=httpx.Response(200, json={"payload": ["escalate", "dept_sales"]})
    )
    client = ChatwootClient(CHATWOOT, "token", 1)
    assert await client.get_labels(9) == {"payload": ["escalate", "dept_sales"]}
    await client.aclose()


@respx.mock
async def test_get_escalation_departments_returns_list():
    respx.get(f"{PROTON}/escalation/departments").mock(
        return_value=httpx.Response(
            200, json={"departments": ["engineer", "pre_sales", "sales"]}
        )
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_departments() == ["engineer", "pre_sales", "sales"]
    await client.aclose()


@respx.mock
async def test_get_escalation_departments_returns_none_on_error():
    respx.get(f"{PROTON}/escalation/departments").mock(return_value=httpx.Response(500))
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_departments() is None
    await client.aclose()


@respx.mock
async def test_get_escalation_departments_returns_none_on_non_dict_body():
    respx.get(f"{PROTON}/escalation/departments").mock(
        return_value=httpx.Response(200, json=["engineer"])
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_departments() is None
    await client.aclose()


@respx.mock
async def test_get_escalation_departments_returns_empty_list_not_none_when_none_configured():
    respx.get(f"{PROTON}/escalation/departments").mock(
        return_value=httpx.Response(200, json={"departments": []})
    )
    client = ProtonConfigClient(PROTON, "k")
    assert await client.get_escalation_departments() == []
    await client.aclose()
