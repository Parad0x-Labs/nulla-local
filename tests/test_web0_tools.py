from __future__ import annotations

import base64
import json
import urllib.parse
from pathlib import Path

from core.nulla_wallet import NullaWallet
from core.web0_gated_html import make_gate_challenge
from core.web0_tools import (
    _GLOBAL_GATE_STORE,
    _active_projects,
    dna_pay_and_unlock,
    web0_add_gated_section,
    web0_compile_preview,
    web0_create_project,
    web0_gate_key_store,
    web0_open_builder_draft,
    web0_project,
    web0_publish,
)


def _reset_projects() -> None:
    _active_projects.clear()
    _GLOBAL_GATE_STORE._entries.clear()


def _wallet(tmp_path: Path) -> NullaWallet:
    wallet = NullaWallet(runtime_home=tmp_path / "runtime", derivation_key=b"z" * 32)
    wallet.generate_and_save()
    return wallet


def test_web0_gated_section_registers_the_real_encryption_key_and_preview_hides_plaintext(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project(
        "landing_page",
        "loop.null",
        "Loop",
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )

    added = web0_add_gated_section(
        created["project_id"],
        "home",
        "founder-only memo",
        [wallet.pubkey],
    )
    project = web0_project(created["project_id"])
    assert project is not None
    project_key = project.key_store.get_aes_key(added["block_id"], wallet.pubkey)
    global_key = web0_gate_key_store().get_aes_key(added["block_id"], wallet.pubkey)
    preview = web0_compile_preview(
        created["project_id"],
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )

    assert added["status"] == "encrypted"
    assert project_key is not None
    assert project_key == global_key
    assert "founder-only memo" not in preview["html"]
    assert "data-block-id" in preview["html"]


def test_web0_global_gate_key_unlocks_tool_created_section(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project(
        "landing_page",
        "loop.null",
        "Loop",
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )
    added = web0_add_gated_section(created["project_id"], "home", "private", [wallet.pubkey])

    challenge = make_gate_challenge(added["block_id"], wallet.pubkey)
    body = {
        "block_id": added["block_id"],
        "wallet_pubkey": wallet.pubkey,
        "nonce": challenge,
        "signature": base64.b64encode(wallet.sign_message(challenge)).decode("ascii"),
    }
    from core.web0_gated_html import NullaGateHandler

    response = NullaGateHandler(web0_gate_key_store()).handle(body)

    assert "aes_key" in response


def test_publish_and_spend_fail_closed_without_explicit_permission(tmp_path: Path) -> None:
    _reset_projects()
    wallet = _wallet(tmp_path)
    created = web0_create_project(
        "landing_page",
        "loop.null",
        "Loop",
        http=lambda *args, **kwargs: {"error": True, "message": "offline"},
    )

    publish = web0_publish(created["project_id"], wallet)
    spend = dna_pay_and_unlock("https://example.test/resource", wallet)

    assert publish["error"] == "publish_requires_explicit_allow_network_publish"
    assert spend["error"] == "spend_requires_explicit_allow_spend"


def test_web0_open_builder_draft_returns_payload_url_for_local_portal() -> None:
    draft = web0_open_builder_draft(
        "Nully Web0",
        "<style>body{background:#020812;color:#56e7ff}</style><main>Nully owns this page.</main>",
        domain="nully",
        base_url="http://127.0.0.1:3000",
        updated_at="2026-06-17T12:00:00Z",
    )

    parsed = urllib.parse.urlparse(draft["builder_url"])
    query = urllib.parse.parse_qs(parsed.query)
    payload = query["payload"][0]
    padded = payload + "=" * (-len(payload) % 4)
    project = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))

    assert draft["status"] == "builder_url_ready"
    assert parsed.scheme == "http"
    assert parsed.netloc == "127.0.0.1:3000"
    assert parsed.path == "/templates/editor/"
    assert query["t"] == ["code"]
    assert query["name"] == ["nully"]
    assert project["v"] == 1
    assert project["template"] == "code"
    assert project["domain"] == "nully"
    assert project["content"]["title"] == "Nully Web0"
    assert "Nully owns this page." in project["content"]["code"]
