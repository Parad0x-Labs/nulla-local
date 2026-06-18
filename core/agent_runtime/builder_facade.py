from __future__ import annotations

from typing import Any

from core import policy_engine
from core.agent_runtime.builder import controller as agent_builder_controller
from core.agent_runtime.builder import scaffolds as agent_builder_scaffolds
from core.agent_runtime.builder import support as agent_builder_support


class BuilderFacadeMixin:
    def _workspace_build_observations(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> dict[str, Any]:
        return agent_builder_controller.workspace_build_observations(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )

    def _workspace_build_degraded_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
    ) -> str:
        return agent_builder_controller.workspace_build_degraded_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
        )

    def _builder_support_gap_report(
        self,
        *,
        source_context: dict[str, object] | None,
        reason: str,
    ) -> dict[str, Any]:
        return agent_builder_support.support_gap_report(
            source_context=source_context,
            reason=reason,
            write_enabled=bool(policy_engine.get("filesystem.allow_write_workspace", False)),
        )

    def _builder_controller_profile(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any]:
        return agent_builder_support.controller_profile(
            self,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            source_context=source_context,
            plan_tool_workflow_fn=self._plan_tool_workflow,
            looks_like_workspace_bootstrap_request_fn=self._looks_like_workspace_bootstrap_request,
        )

    def _supports_bounded_builder_workflow_request(
        self,
        *,
        effective_input: str,
        task_class: str,
        source_context: dict[str, object] | None = None,
    ) -> bool:
        if self._looks_like_explicit_workspace_file_request(effective_input):
            return True
        if self._looks_like_workspace_bootstrap_request(effective_input):
            return True
        workflow_probe = self._plan_tool_workflow(
            user_text=effective_input,
            task_class=task_class,
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        if workflow_probe.handled and workflow_probe.next_payload and workflow_intent in {
            "workspace.read_file",
            "workspace.write_file",
            "workspace.ensure_directory",
            "orchestration.execute_envelope",
            "sandbox.run_command",
            "hive.create_topic",
        }:
            return True
        if not self._explicit_runtime_workflow_request(
            user_input=effective_input,
            task_class=task_class,
        ):
            return False
        lowered = f" {str(effective_input or '').lower()} "
        operation_markers = (
            " run ",
            " rerun ",
            " retry ",
            " inspect ",
            " search ",
            " find ",
            " read ",
            " open ",
            " replace ",
            " patch ",
            " edit ",
            " fix ",
            " debug ",
            " trace ",
            " diagnose ",
            " test ",
            " tests ",
        )
        target_markers = (
            " workspace ",
            " repo ",
            " repository ",
            " code ",
            " file ",
            " files ",
            ".py",
            ".ts",
            ".js",
            ".tsx",
            ".jsx",
            ".json",
            ".yaml",
            ".yml",
            ".toml",
            ".md",
            "`",
        )
        return any(marker in lowered for marker in operation_markers) and any(marker in lowered for marker in target_markers)

    def _builder_controller_step_record(
        self,
        *,
        execution: Any,
        tool_payload: dict[str, Any],
    ) -> dict[str, Any]:
        return agent_builder_support.controller_step_record(
            self,
            execution=execution,
            tool_payload=tool_payload,
        )

    def _workspace_build_verification_payload(self, *, target: dict[str, str]) -> dict[str, Any] | None:
        return agent_builder_support.workspace_build_verification_payload(target=target)

    def _builder_initial_payloads(
        self,
        *,
        mode: str,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
        initial_payloads: list[dict[str, Any]] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
        return agent_builder_support.initial_payloads(
            self,
            mode=mode,
            target=target,
            user_request=user_request,
            web_notes=web_notes,
            initial_payloads=initial_payloads,
        )

    def _builder_controller_backing_sources(self, executed_steps: list[dict[str, Any]]) -> list[str]:
        return agent_builder_support.controller_backing_sources(executed_steps)

    def _builder_controller_observations(
        self,
        *,
        mode: str,
        target: dict[str, str],
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        sources: list[dict[str, str]],
        final_status: str,
        artifacts: dict[str, Any],
    ) -> dict[str, Any]:
        return agent_builder_support.controller_observations(
            mode=mode,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            sources=sources,
            final_status=final_status,
            artifacts=artifacts,
        )

    def _builder_retry_history(self, executed_steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return agent_builder_support.retry_history(executed_steps)

    def _builder_controller_artifacts(
        self,
        *,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
    ) -> dict[str, Any]:
        return agent_builder_support.controller_artifacts(
            executed_steps=executed_steps,
            stop_reason=stop_reason,
        )

    def _builder_artifact_citation_block(self, artifacts: dict[str, Any]) -> str:
        return agent_builder_support.artifact_citation_block(self, artifacts)

    def _append_builder_artifact_citations(self, text: str, *, artifacts: dict[str, Any]) -> str:
        return agent_builder_support.append_artifact_citations(
            self,
            text,
            artifacts=artifacts,
        )

    def _builder_controller_degraded_response(
        self,
        *,
        target: dict[str, str],
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        failed_execution: Any | None,
        effective_input: str,
        session_id: str,
        artifacts: dict[str, Any],
    ) -> str:
        return agent_builder_support.controller_degraded_response(
            self,
            target=target,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            failed_execution=failed_execution,
            effective_input=effective_input,
            session_id=session_id,
        )

    def _builder_controller_direct_response(
        self,
        *,
        effective_input: str,
        executed_steps: list[dict[str, Any]],
    ) -> str | None:
        return agent_builder_support.controller_direct_response(
            self,
            effective_input=effective_input,
            executed_steps=executed_steps,
        )

    def _builder_controller_workflow_summary(
        self,
        *,
        mode: str,
        executed_steps: list[dict[str, Any]],
        stop_reason: str,
        artifacts: dict[str, Any],
    ) -> str:
        return agent_builder_support.controller_workflow_summary(
            self,
            mode=mode,
            executed_steps=executed_steps,
            stop_reason=stop_reason,
            artifacts=artifacts,
        )

    def _run_bounded_builder_loop(
        self,
        *,
        task: Any,
        session_id: str,
        effective_input: str,
        task_class: str,
        source_context: dict[str, object] | None,
        initial_payloads: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any], str, Any | None]:
        return agent_builder_controller.run_bounded_builder_loop(
            self,
            task=task,
            session_id=session_id,
            effective_input=effective_input,
            task_class=task_class,
            source_context=source_context,
            initial_payloads=initial_payloads,
            plan_tool_workflow_fn=self._plan_tool_workflow,
            execute_tool_intent_fn=self._execute_tool_intent,
        )

    def _maybe_run_builder_controller(
        self,
        *,
        task: Any,
        effective_input: str,
        classification: dict[str, Any],
        interpretation: Any,
        web_notes: list[dict[str, Any]],
        session_id: str,
        source_context: dict[str, object] | None,
    ) -> dict[str, Any] | None:
        return agent_builder_controller.maybe_run_builder_controller(
            self,
            task=task,
            effective_input=effective_input,
            classification=classification,
            interpretation=interpretation,
            web_notes=web_notes,
            session_id=session_id,
            source_context=source_context,
            render_capability_truth_response_fn=self._render_capability_truth_response,
            load_active_persona_fn=self._load_active_persona,
        )

    def _should_run_builder_controller(
        self,
        *,
        effective_input: str,
        classification: dict[str, Any],
        source_context: dict[str, object],
    ) -> bool:
        if not policy_engine.get("filesystem.allow_write_workspace", False):
            return False
        if not str(source_context.get("workspace") or source_context.get("workspace_root") or "").strip():
            return False
        task_class = str(classification.get("task_class") or "unknown")
        lowered = str(effective_input or "").lower()
        explicit_file_request = self._looks_like_explicit_workspace_file_request(effective_input)
        generic_bootstrap_request = self._looks_like_generic_workspace_bootstrap_request(lowered)
        workflow_probe = self._plan_tool_workflow(
            user_text=effective_input,
            task_class=task_class,
            executed_steps=[],
            source_context=dict(source_context or {}),
        )
        workflow_intent = str(dict(workflow_probe.next_payload or {}).get("intent") or "").strip()
        pure_inspection_workflow = workflow_intent in {
            "workspace.search_text",
            "workspace.symbol_search",
            "workspace.git_summary",
            "workspace.git_status",
            "workspace.list_files",
        }
        workflow_supported_request = bool(
            workflow_probe.handled
            and workflow_probe.next_payload
            and workflow_intent
            in {
                "workspace.read_file",
                "workspace.write_file",
                "workspace.ensure_directory",
                "orchestration.execute_envelope",
                "sandbox.run_command",
                "hive.create_topic",
            }
        )
        if pure_inspection_workflow and not explicit_file_request and not generic_bootstrap_request and not self._looks_like_builder_request(lowered):
            return False
        if task_class not in {
            "system_design",
            "integration_orchestration",
            "debugging",
            "dependency_resolution",
            "config",
            "file_inspection",
            "shell_guidance",
            "unknown",
        } and not generic_bootstrap_request and not explicit_file_request and not workflow_supported_request:
            return False
        if not self._looks_like_builder_request(lowered) and not explicit_file_request and not workflow_supported_request:
            return False
        if any(marker in lowered for marker in ("don't write", "do not write", "do not edit files", "advice only", "just plan", "no files")):
            return False
        scaffold_request = (
            any(marker in lowered for marker in ("build", "create", "scaffold", "implement", "generate", "start working"))
            and any(marker in lowered for marker in ("telegram", "discord", "bot", "agent", "service"))
            and any(marker in lowered for marker in ("workspace", "repo", "repository", "write the files", "create the files", "generate the code"))
        )
        return (
            "write the files" in lowered
            or "create the files" in lowered
            or "generate the code" in lowered
            or "build the code" in lowered
            or "building the code" in lowered
            or "start working" in lowered
            or "start building" in lowered
            or "start creating" in lowered
            or "implement it" in lowered
            or "edit the files" in lowered
            or "patch the files" in lowered
            or "launch local" in lowered
            or scaffold_request
            or generic_bootstrap_request
            or explicit_file_request
            or workflow_supported_request
            or self._explicit_runtime_workflow_request(user_input=effective_input, task_class=task_class)
        )

    def _workspace_build_target(self, *, query_text: str, interpretation: Any) -> dict[str, str]:
        return agent_builder_scaffolds.workspace_build_target(
            query_text=query_text,
            interpretation=interpretation,
            extract_requested_builder_root_fn=self._extract_requested_builder_root,
            search_user_heuristics_fn=self._search_user_heuristics,
        )

    def _workspace_build_file_map(
        self,
        *,
        target: dict[str, str],
        user_request: str,
        web_notes: list[dict[str, Any]],
    ) -> dict[str, str]:
        return agent_builder_scaffolds.workspace_build_file_map(
            target=target,
            user_request=user_request,
            web_notes=web_notes,
        )

    def _workspace_build_sources(self, web_notes: list[dict[str, Any]]) -> list[dict[str, str]]:
        return agent_builder_scaffolds.workspace_build_sources(web_notes)

    def _workspace_build_verification(
        self,
        *,
        target: dict[str, str],
        source_context: dict[str, object],
    ) -> dict[str, Any] | None:
        return agent_builder_controller.workspace_build_verification(
            target=target,
            source_context=source_context,
            execute_runtime_tool_fn=self._execute_runtime_tool,
        )

    def _workspace_build_response(
        self,
        *,
        target: dict[str, str],
        write_results: list[dict[str, Any]],
        write_failures: list[str],
        verification: dict[str, Any] | None,
        sources: list[dict[str, str]],
    ) -> str:
        return agent_builder_controller.workspace_build_response(
            target=target,
            write_results=write_results,
            write_failures=write_failures,
            verification=verification,
            sources=sources,
        )

    def _sources_section(self, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.sources_section(sources)

    def _generic_workspace_readme(
        self,
        *,
        user_request: str,
        root_dir: str,
        sources: list[dict[str, str]],
        language: str,
    ) -> str:
        return agent_builder_scaffolds.generic_workspace_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
            language=language,
        )

    def _generic_python_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_python_source(
            user_request=user_request,
            sources=sources,
        )

    def _generic_typescript_package_json(self, *, root_dir: str) -> str:
        return agent_builder_scaffolds.generic_typescript_package_json(root_dir=root_dir)

    def _generic_typescript_source(self, *, user_request: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_typescript_source(
            user_request=user_request,
            sources=sources,
        )

    def _telegram_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_python_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _telegram_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_python_bot_source(sources=sources)

    def _telegram_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_typescript_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _telegram_typescript_package_json(self) -> str:
        return agent_builder_scaffolds.telegram_typescript_package_json()

    def _telegram_typescript_tsconfig(self) -> str:
        return agent_builder_scaffolds.telegram_typescript_tsconfig()

    def _telegram_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.telegram_typescript_bot_source(sources=sources)

    def _discord_python_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_python_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _discord_python_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_python_bot_source(sources=sources)

    def _discord_typescript_readme(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_typescript_readme(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )

    def _discord_typescript_package_json(self) -> str:
        return agent_builder_scaffolds.discord_typescript_package_json()

    def _discord_typescript_bot_source(self, *, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.discord_typescript_bot_source(sources=sources)

    def _generic_build_brief(self, *, user_request: str, root_dir: str, sources: list[dict[str, str]]) -> str:
        return agent_builder_scaffolds.generic_build_brief(
            user_request=user_request,
            root_dir=root_dir,
            sources=sources,
        )
