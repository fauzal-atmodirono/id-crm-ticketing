"""Unit tests for provision_labels.py.

Run from chatwoot-config/:
    pip install httpx PyYAML pytest respx
    pytest tests/test_provision_labels.py -v
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest
import respx
import httpx

# Make the parent directory importable so we can import provision_labels
sys.path.insert(0, str(Path(__file__).parent.parent))
from provision_labels import (
    ChatwootClient,
    ChatwootError,
    ProvisionResult,
    _load_env_file,
    _normalize_color,
    provision,
    main,
)

BASE = "http://chatwoot-test"
ACCOUNT_ID = 1
TOKEN = "test-token"
LABELS_URL = f"{BASE}/api/v1/accounts/{ACCOUNT_ID}/labels"
FILTERS_URL = f"{BASE}/api/v1/accounts/{ACCOUNT_ID}/saved_filters"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_client() -> ChatwootClient:
    return ChatwootClient(BASE, TOKEN, ACCOUNT_ID)


def label_response(name: str, color: str, description: str = "") -> dict:
    return {"id": 1, "title": name, "color": color, "description": description}


def filter_response(name: str) -> dict:
    return {"id": 1, "name": name, "filter_type": "account", "query": {"payload": []}}


# ---------------------------------------------------------------------------
# _normalize_color
# ---------------------------------------------------------------------------

def test_normalize_color_adds_hash():
    assert _normalize_color("1F93FF") == "#1f93ff"


def test_normalize_color_preserves_hash():
    assert _normalize_color("#E74C3C") == "#e74c3c"


def test_normalize_color_lowercases():
    assert _normalize_color("#AABBCC") == "#aabbcc"


# ---------------------------------------------------------------------------
# ChatwootClient — list_labels
# ---------------------------------------------------------------------------

@respx.mock
def test_list_labels_returns_payload_list():
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"payload": [
            label_response("category_complaint", "#e74c3c"),
        ]})
    )
    with make_client() as c:
        labels = c.list_labels()
    assert len(labels) == 1
    assert labels[0]["title"] == "category_complaint"


@respx.mock
def test_list_labels_raises_on_401():
    respx.get(LABELS_URL).mock(return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ChatwootError) as exc_info:
        with make_client() as c:
            c.list_labels()
    assert exc_info.value.status == 401


# ---------------------------------------------------------------------------
# ChatwootClient — create_label
# ---------------------------------------------------------------------------

@respx.mock
def test_create_label_posts_correct_body():
    route = respx.post(LABELS_URL).mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#e74c3c"))
    )
    with make_client() as c:
        result = c.create_label("category_complaint", "#e74c3c", "General complaint")
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["title"] == "category_complaint"
    assert sent["color"] == "#e74c3c"
    assert sent["show_on_sidebar"] is True
    assert result["title"] == "category_complaint"


# ---------------------------------------------------------------------------
# ChatwootClient — update_label
# ---------------------------------------------------------------------------

@respx.mock
def test_update_label_patches_by_name():
    route = respx.patch(f"{LABELS_URL}/category_complaint").mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#ff0000"))
    )
    with make_client() as c:
        c.update_label("category_complaint", "#ff0000", "Updated desc")
    assert route.called
    sent = json.loads(route.calls[0].request.content)
    assert sent["color"] == "#ff0000"


# ---------------------------------------------------------------------------
# ChatwootClient — list_saved_filters
# ---------------------------------------------------------------------------

@respx.mock
def test_list_saved_filters_returns_list():
    respx.get(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=[filter_response("All Complaints")])
    )
    with make_client() as c:
        filters = c.list_saved_filters()
    assert len(filters) == 1
    assert filters[0]["name"] == "All Complaints"


# ---------------------------------------------------------------------------
# ChatwootClient — create_saved_filter
# ---------------------------------------------------------------------------

@respx.mock
def test_create_saved_filter_posts_correct_body():
    route = respx.post(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=filter_response("All Complaints"))
    )
    query = {"payload": [{"attribute_key": "labels", "filter_operator": "contains",
                          "query_operator": None, "values": ["category_complaint"]}]}
    with make_client() as c:
        c.create_saved_filter("All Complaints", "account", query)
    sent = json.loads(route.calls[0].request.content)
    assert sent["name"] == "All Complaints"
    assert sent["filter_type"] == "account"
    assert sent["query"] == query


# ---------------------------------------------------------------------------
# provision() — all labels exist and are unchanged (idempotency)
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_idempotent_no_api_calls_when_unchanged(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#e74c3c'\n"
        "    description: 'General complaint'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text(
        "filters:\n"
        "  - name: All Complaints\n"
        "    filter_type: account\n"
        "    query:\n"
        "      payload:\n"
        "        - attribute_key: labels\n"
        "          filter_operator: contains\n"
        "          query_operator: null\n"
        "          values:\n"
        "            - category_complaint\n"
    )
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"payload": [
            label_response("category_complaint", "#e74c3c", "General complaint")
        ]})
    )
    respx.get(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=[filter_response("All Complaints")])
    )
    create_label_route = respx.post(LABELS_URL).mock(
        return_value=httpx.Response(200, json={})
    )
    create_filter_route = respx.post(FILTERS_URL).mock(
        return_value=httpx.Response(200, json={})
    )

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=False)

    assert result.labels_unchanged == 1
    assert result.labels_created == 0
    assert result.labels_updated == 0
    assert result.filters_unchanged == 1
    assert result.filters_created == 0
    assert not create_label_route.called
    assert not create_filter_route.called


# ---------------------------------------------------------------------------
# provision() — net-new label + filter
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_creates_missing_label_and_filter(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#e74c3c'\n"
        "    description: 'General complaint'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text(
        "filters:\n"
        "  - name: All Complaints\n"
        "    filter_type: account\n"
        "    query:\n"
        "      payload: []\n"
    )
    # No existing labels or filters
    respx.get(LABELS_URL).mock(return_value=httpx.Response(200, json={"payload": []}))
    respx.get(FILTERS_URL).mock(return_value=httpx.Response(200, json=[]))
    respx.post(LABELS_URL).mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#e74c3c"))
    )
    respx.post(FILTERS_URL).mock(
        return_value=httpx.Response(200, json=filter_response("All Complaints"))
    )

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=False)

    assert result.labels_created == 1
    assert result.filters_created == 1


# ---------------------------------------------------------------------------
# provision() — color mismatch triggers update
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_updates_label_when_color_differs(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#ff0000'\n"
        "    description: 'General complaint'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text("filters: []\n")
    # Existing label has different color
    respx.get(LABELS_URL).mock(
        return_value=httpx.Response(200, json={"payload": [
            label_response("category_complaint", "#e74c3c", "General complaint")
        ]})
    )
    respx.get(FILTERS_URL).mock(return_value=httpx.Response(200, json=[]))
    update_route = respx.patch(f"{LABELS_URL}/category_complaint").mock(
        return_value=httpx.Response(200, json=label_response("category_complaint", "#ff0000"))
    )

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=False)

    assert result.labels_updated == 1
    assert update_route.called


# ---------------------------------------------------------------------------
# provision() — dry_run suppresses API mutation calls
# ---------------------------------------------------------------------------

@respx.mock
def test_provision_dry_run_does_not_mutate(tmp_path):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text(
        "labels:\n"
        "  - name: category_complaint\n"
        "    color: '#e74c3c'\n"
        "    description: 'New'\n"
        "    group: category\n"
    )
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text(
        "filters:\n"
        "  - name: New Filter\n"
        "    filter_type: account\n"
        "    query:\n"
        "      payload: []\n"
    )
    respx.get(LABELS_URL).mock(return_value=httpx.Response(200, json={"payload": []}))
    respx.get(FILTERS_URL).mock(return_value=httpx.Response(200, json=[]))
    create_label = respx.post(LABELS_URL).mock(return_value=httpx.Response(200, json={}))
    create_filter = respx.post(FILTERS_URL).mock(return_value=httpx.Response(200, json={}))

    with make_client() as c:
        result = provision(c, labels_yaml, filters_yaml, dry_run=True)

    assert result.labels_created == 1
    assert result.filters_created == 1
    assert not create_label.called
    assert not create_filter.called


# ---------------------------------------------------------------------------
# _load_env_file
# ---------------------------------------------------------------------------

def test_load_env_file_parses_key_value(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "# comment\n"
        "CHATWOOT_URL=http://chatwoot:3000\n"
        "CHATWOOT_API_TOKEN=abc123\n"
        "CHATWOOT_ACCOUNT_ID=2\n"
        "BLANK=\n"
    )
    env = _load_env_file(env_file)
    assert env["CHATWOOT_URL"] == "http://chatwoot:3000"
    assert env["CHATWOOT_API_TOKEN"] == "abc123"
    assert env["CHATWOOT_ACCOUNT_ID"] == "2"
    assert env["BLANK"] == ""


def test_load_env_file_skips_comments(tmp_path):
    env_file = tmp_path / "test.env"
    env_file.write_text("# this is a comment\nFOO=bar\n")
    env = _load_env_file(env_file)
    assert "# this is a comment" not in env
    assert env["FOO"] == "bar"


# ---------------------------------------------------------------------------
# main() CLI — missing credentials returns exit code 1
# ---------------------------------------------------------------------------

def test_main_returns_1_on_missing_credentials(tmp_path, capsys):
    labels_yaml = tmp_path / "labels.yaml"
    labels_yaml.write_text("labels: []\n")
    filters_yaml = tmp_path / "filters.yaml"
    filters_yaml.write_text("filters: []\n")
    exit_code = main([
        "--chatwoot-url", "",
        "--api-token", "",
        "--labels", str(labels_yaml),
        "--filters", str(filters_yaml),
    ])
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "required" in captured.err.lower()


# ---------------------------------------------------------------------------
# main() CLI — missing labels file returns exit code 1
# ---------------------------------------------------------------------------

def test_main_returns_1_on_missing_labels_file(tmp_path, capsys):
    exit_code = main([
        "--chatwoot-url", "http://fake",
        "--api-token", "token",
        "--labels", str(tmp_path / "nonexistent.yaml"),
        "--filters", str(tmp_path / "filters.yaml"),
    ])
    assert exit_code == 1
