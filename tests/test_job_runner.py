from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sandbox.job_runner import JobRunner, _macos_confined_profile
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
            resolved = str(Path(tmpdir).resolve())
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
                # Network namespace plus filesystem confinement: host mounted
                # read-only, workspace re-bound writable, private /tmp.
                self.assertEqual(argv[0], "/usr/bin/bwrap")
                self.assertIn("--unshare-net", argv)
                self.assertIn("--ro-bind", argv)
                self.assertIn("--tmpfs", argv)
                self.assertIn("--bind", argv)
                self.assertIn(resolved, argv)
                self.assertEqual(argv[-3:], ["python3", "-c", "print('x')"])

    def test_absolute_path_arg_outside_workspace_is_rejected(self) -> None:
        # Regression: the proven exploit was a command reading an absolute path
        # OUTSIDE the workspace (e.g. 'cat /abs/secret'). Pre-fix this ran and
        # returned the secret; it must now be rejected before exec, on any OS.
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("TOP_SECRET")
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(ws)))
            with self.assertRaises(ValueError) as ctx:
                runner.run(["cat", str(secret)])
            self.assertIn("escapes allowed workspace", str(ctx.exception))

    def test_relative_dotdot_path_arg_escape_is_rejected(self) -> None:
        # A relative path that climbs out of the (validated) cwd must also be
        # rejected, not just absolute paths.
        with tempfile.TemporaryDirectory() as ws:
            runner = JobRunner(ExecutionPolicy(workspace_root=Path(ws)))
            with self.assertRaises(ValueError) as ctx:
                runner.run(["cat", "../../etc/passwd"])
            self.assertIn("escapes allowed workspace", str(ctx.exception))

    def test_in_workspace_path_arg_is_allowed(self) -> None:
        # The guard must not reject legitimate in-workspace path arguments.
        with tempfile.TemporaryDirectory() as ws:
            inside = Path(ws) / "inside.txt"
            allowed_roots = (Path(ws).resolve(),)
            from sandbox.resource_limits import path_args_within_roots

            self.assertIsNone(
                path_args_within_roots(["cat", str(inside)], allowed_roots, cwd=Path(ws).resolve())
            )
            # A bare relative name and a non-path flag must not be flagged.
            self.assertIsNone(
                path_args_within_roots(["python3", "-c", "print('hi')"], allowed_roots, cwd=Path(ws).resolve())
            )

    @unittest.skipIf(sys.platform == "win32", "PosixPath cannot be instantiated on Windows")
    def test_macos_profile_confines_writes_to_workspace(self) -> None:
        # The Seatbelt profile must deny file writes by default and allow them
        # only under the workspace roots (in addition to denying network).
        with tempfile.TemporaryDirectory() as ws:
            ws_resolved = str(Path(ws).resolve())
            profile = _macos_confined_profile((Path(ws).resolve(),))
            self.assertIn("(deny network*)", profile)
            self.assertIn("(deny file-write*)", profile)
            self.assertIn("(allow file-write*", profile)
            self.assertIn(ws_resolved, profile)

    @unittest.skipUnless(sys.platform == "darwin", "sandbox-exec is macOS-only")
    def test_macos_seatbelt_denies_out_of_workspace_write(self) -> None:
        # End-to-end on macOS: a write to an absolute path OUTSIDE the workspace
        # whose target is hidden inside a code string (so the path-arg guard
        # cannot see it) must be denied by the kernel Seatbelt layer.
        with tempfile.TemporaryDirectory() as ws, tempfile.TemporaryDirectory() as outside:
            target = Path(outside) / "pwned.txt"
            runner = JobRunner(
                ExecutionPolicy(workspace_root=Path(ws), network_isolation_mode="auto")
            )
            result = runner.run(
                [sys.executable, "-c", f"open({str(target)!r}, 'w').write('x')"]
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(target.exists())
            # And an in-workspace write under the same profile still succeeds.
            ok = runner.run(
                [sys.executable, "-c", f"open({str(Path(ws) / 'ok.txt')!r}, 'w').write('x'); print('done')"]
            )
            self.assertEqual(ok.returncode, 0)


if __name__ == "__main__":
    unittest.main()
