from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.job_runner import JobRunner
from sandbox.resource_limits import ExecutionPolicy


class JobRunnerTests(unittest.TestCase):
    def test_network_command_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(tmpdir)))
            with self.assertRaises(ValueError):
                runner.run(["curl", "https://example.com"])

    def test_workspace_escape_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(tmpdir)))
            escape_dir = "C:\\" if sys.platform == "win32" else "/"
            with self.assertRaises(ValueError):
                runner.run(["python3", "-c", "print('ok')"], cwd=escape_dir)

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_os_enforced_network_isolation_requires_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="os_enforced",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", return_value=None
            ):
                with self.assertRaises(ValueError):
                    runner.run(["python3", "-c", "print('safe')"])

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_auto_network_isolation_now_fails_closed_without_backend(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", return_value=None
            ):
                with self.assertRaises(ValueError):
                    runner.run(["python3", "-c", "print('safe')"])

    def test_heuristic_only_mode_remains_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="heuristic_only",
                )
            )
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", return_value="/usr/bin/unshare"
            ):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv, ["python3", "-c", "print('x')"])

    def test_linux_unshare_prefix_applied_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            def _which(cmd: str) -> str | None:
                return "/usr/bin/unshare" if cmd == "unshare" else None
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv[:3], ["/usr/bin/unshare", "-n", "--"])

    def test_macos_sandbox_exec_prefix_applied_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )

            def _which(cmd: str) -> str | None:
                return "/usr/bin/sandbox-exec" if cmd == "sandbox-exec" else None

            with patch("sandbox.job_runner.sys.platform", "darwin"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv[0], "/usr/bin/sandbox-exec")
                self.assertEqual(argv[1], "-p")
                self.assertIn("deny network*", argv[2])
                self.assertEqual(argv[3], "--")
                self.assertEqual(argv[4:], ["python3", "-c", "print('x')"])

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_macos_sandbox_exec_real_execution_succeeds(self) -> None:
        # Real end-to-end on macOS: a no-network 'auto' job runs under the kernel
        # Seatbelt wrapper without raising, and produces correct output.
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            result = runner.run(["/bin/echo", "sandbox-ok"])
            self.assertEqual(result.returncode, 0)
            self.assertIn("sandbox-ok", result.stdout)

    def test_windows_missing_backend_error_names_heuristic_only_and_wsl2(self) -> None:
        # On native Windows there is no kernel network-isolation backend, so a
        # no-network 'auto' job must fail closed with an ACTIONABLE message that
        # names the two real options: WSL2/Linux and the heuristic_only override.
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            with patch("sandbox.job_runner.os.name", "nt"), patch(
                "sandbox.job_runner.sys.platform", "win32"
            ), patch("sandbox.job_runner.shutil.which", return_value=None):
                message = runner._no_kernel_isolation_message()

        self.assertIn("heuristic_only", message)
        self.assertIn("WSL2", message)
        # Fail-closed default must be preserved: the override is named as an
        # explicit choice, not silently applied.
        self.assertIn("explicit", message.lower())

    def test_linux_bwrap_prefix_preferred_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            runner = JobRunner(
                ExecutionPolicy(
                    workspace_root=Path(tmpdir),
                    network_isolation_mode="auto",
                )
            )
            def _which(cmd: str) -> str | None:
                if cmd == "bwrap":
                    return "/usr/bin/bwrap"
                if cmd == "unshare":
                    return "/usr/bin/unshare"
                return None
            with patch("sandbox.job_runner.os.name", "posix"), patch("sandbox.job_runner.sys.platform", "linux"), patch(
                "sandbox.job_runner.shutil.which", side_effect=_which
            ):
                argv = runner._with_network_isolation(["python3", "-c", "print('x')"])
                self.assertEqual(argv[:3], ["/usr/bin/bwrap", "--unshare-net", "--"])


if __name__ == "__main__":
    unittest.main()
