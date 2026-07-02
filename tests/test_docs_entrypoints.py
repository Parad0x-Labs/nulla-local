from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_readme_frontloads_product_summary_and_install() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    early_block = "\n".join(readme.splitlines()[:120])

    assert "NULLA is a local-first agent runtime" in early_block
    assert "Current state:" in early_block
    assert "Windows (one-click)" in early_block
    assert "## Try It" in readme
    assert "docs/INSTALL.md" in readme
    assert "docs/STATUS.md" in readme
    assert "docs/SYSTEM_SPINE.md" in readme
    assert "docs/CONTROL_PLANE.md" in readme
    assert "docs/PROOF_PATH.md" in readme


def test_docs_home_only_points_to_curated_entry_docs() -> None:
    docs_home = (REPO_ROOT / "docs" / "README.md").read_text(encoding="utf-8")

    assert "INSTALL.md" in docs_home
    assert "STATUS.md" in docs_home
    assert "SYSTEM_SPINE.md" in docs_home
    assert "CONTROL_PLANE.md" in docs_home
    assert "LOCAL_ACCEPTANCE.md" in docs_home
    assert "PLATFORM_REFACTOR_PLAN.md" in docs_home
    assert "PROOF_PATH.md" in docs_home
    assert "TRUST.md" in docs_home
    assert "archive/README.md" in docs_home
    assert "HANDOVER_" not in docs_home
    assert "NULLA_SALES_PITCH" not in docs_home


def test_docs_root_is_curated_after_archive_sweep() -> None:
    docs_root = REPO_ROOT / "docs"
    actual_files = {path.name for path in docs_root.iterdir() if path.is_file()}
    expected_files = {
        "BRAIN_HIVE_API_CONTRACT.md",
        "BRAIN_HIVE_ARCHITECTURE.md",
        "CUMULATIVE_STABILIZATION.md",
        "CONTROL_PLANE.md",
        "INSTALL.md",
        "INSTALL_PROVIDER_EXECUTION_PLAN.md",
        "LICENSING_MATRIX.md",
        "LLM_ACCEPTANCE_REPORT.md",
        "LOCAL_ACCEPTANCE.md",
        "LOCAL_LLM_PROOF_DOSSIER.md",
        "MEET_AND_GREET_API_CONTRACT.md",
        "MEET_AND_GREET_GLOBAL_TOPOLOGY.md",
        "MEET_AND_GREET_PREFLIGHT.md",
        "MEET_AND_GREET_SERVER_ARCHITECTURE.md",
        "MODEL_INTEGRATION_POLICY.md",
        "MODEL_PROVIDER_POLICY.md",
        "NULLA_OPENCLAW_TOOL_DOCTRINE.md",
        "OVERNIGHT_SOAK_RUNBOOK.md",
        "PLATFORM_REFACTOR_PLAN.md",
        "PROCEDURAL_LLM_AUDIT_HARNESS.md",
        "PRODUCTION_AUDIT_2026-06.md",
        "PROOF_PATH.md",
        "PROOF_PASS_REPORT.md",
        "LAUNCH_TECH_BRIEF.md",
        "PUBLIC_LAUNCH_READINESS.md",
        "README.md",
        "STATUS.md",
        "SYSTEM_SPINE.md",
        "TDL.md",
        "THIRD_PARTY_LICENSES.md",
        "TRUST.md",
        "WINDOWS_LOCAL_OPENCLAW_SETUP.md",
        "WINDOWS_ONE_CLICK_READINESS.md",
        "x402-proof.md",
    }

    assert actual_files == expected_files


def test_root_handover_and_starter_kit_redirect_to_current_truth() -> None:
    handover = (REPO_ROOT / "AGENT_HANDOVER.md").read_text(encoding="utf-8")
    starter_kit = (REPO_ROOT / "NULLA_STARTER_KIT.md").read_text(encoding="utf-8")

    assert "docs/SYSTEM_SPINE.md" in handover
    assert "docs/PROOF_PATH.md" in handover
    assert "docs/archive/README.md" in handover

    assert "docs/INSTALL.md" in starter_kit
    assert "docs/STATUS.md" in starter_kit
    assert "docs/PROOF_PATH.md" in starter_kit


def test_front_door_docs_do_not_treat_main_as_a_beta_side_branch() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    install_doc = (REPO_ROOT / "docs" / "INSTALL.md").read_text(encoding="utf-8")
    handover = (REPO_ROOT / "AGENT_HANDOVER.md").read_text(encoding="utf-8")
    plan = (REPO_ROOT / "docs" / "PLATFORM_REFACTOR_PLAN.md").read_text(encoding="utf-8")
    status_doc = (REPO_ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")

    assert "If you need this exact beta branch before it lands on `main`" not in readme
    assert "If you need the current beta branch instead of whatever is on `main`" not in install_doc
    assert "rock-solid local beta" not in handover
    assert "current beta bar" not in plan
    assert "Working branch head" not in status_doc
    assert "Current default-branch head" in status_doc


def test_shared_coordination_docs_do_not_lead_with_legacy_swarm_identity() -> None:
    meet_arch = (REPO_ROOT / "docs" / "MEET_AND_GREET_SERVER_ARCHITECTURE.md").read_text(encoding="utf-8")
    meet_topology = (REPO_ROOT / "docs" / "MEET_AND_GREET_GLOBAL_TOPOLOGY.md").read_text(encoding="utf-8")
    meet_preflight = (REPO_ROOT / "docs" / "MEET_AND_GREET_PREFLIGHT.md").read_text(encoding="utf-8")
    meet_api = (REPO_ROOT / "docs" / "MEET_AND_GREET_API_CONTRACT.md").read_text(encoding="utf-8")

    assert "first shared entry point for a small NULLA swarm" not in meet_arch
    assert "friend-to-friend swarm joining" not in meet_arch
    assert "friend-swarm deployment" not in meet_arch
    assert "controlled global swarm growth" not in meet_topology
    assert "global test swarm" not in meet_topology
    assert "swarm presence visibility" not in meet_preflight
    assert "friend-to-friend local swarm bootstrapping" not in meet_preflight
    assert "friend-swarm use" not in meet_api


def test_repo_map_points_to_canonical_roots_and_archive_policy() -> None:
    repo_map = (REPO_ROOT / "REPO_MAP.md").read_text(encoding="utf-8")

    assert "README.md" in repo_map
    assert "CONTRIBUTING.md" in repo_map
    assert "docs/SYSTEM_SPINE.md" in repo_map
    assert "docs/CONTROL_PLANE.md" in repo_map
    assert "docs/PLATFORM_REFACTOR_PLAN.md" in repo_map
    assert "docs/PROOF_PATH.md" in repo_map
    assert "apps/README.md" in repo_map
    assert "core/README.md" in repo_map
    assert "storage/README.md" in repo_map
    assert "tools/README.md" in repo_map
    assert "network/README.md" in repo_map
    assert "tests/legacy/" in repo_map
    assert "docs/archive/audits/" in repo_map


def test_package_maps_exist_and_define_boundaries() -> None:
    package_docs = {
        "apps/README.md": "entrypoints should stay thin",
        "core/README.md": "highest-risk modules",
        "storage/README.md": "feature stores should depend on persistence primitives",
        "tools/README.md": "tool contract requirements",
        "network/README.md": "business logic should not hide in transport code",
    }

    for relative_path, marker in package_docs.items():
        body = (REPO_ROOT / relative_path).read_text(encoding="utf-8").lower()
        assert marker in body


def test_platform_refactor_plan_tracks_current_high_risk_modules_and_gate_order() -> None:
    plan = (REPO_ROOT / "docs" / "PLATFORM_REFACTOR_PLAN.md").read_text(encoding="utf-8")

    assert "apps/nulla_agent.py" in plan
    assert "core/brain_hive_dashboard.py" in plan
    assert "core/tool_intent_executor.py" in plan
    assert "core/public_hive_bridge.py" in plan
    assert "core/local_operator_actions.py" in plan
    assert "core/control_plane_workspace.py" in plan
    assert "apps/nulla_api_server.py" in plan
    assert "apps/brain_hive_watch_server.py" in plan
    assert "Phase 1 - Extract `core/execution/`" in plan
    assert "Phase 6 - Split `core/control_plane_workspace.py`" in plan
    assert "pytest tests/ -q" in plan


def test_status_page_stays_honest_about_ci_and_proof_posture() -> None:
    status_doc = (REPO_ROOT / "docs" / "STATUS.md").read_text(encoding="utf-8")
    proof_doc = (REPO_ROOT / "docs" / "PROOF_PASS_REPORT.md").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "| **CI pipeline** | **Enforced** |" in status_doc
    assert "check Actions for the latest branch conclusion" in status_doc
    assert "INSTRUMENTED" in proof_doc
    assert "READY TO RUN" not in proof_doc
    assert 'description = "Nulla Hive Mind — local-first AI agent runtime with memory, tools, optional helpers, and visible proof"' in pyproject
