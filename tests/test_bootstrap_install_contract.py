from __future__ import annotations

import subprocess
import tarfile
from pathlib import Path

from tests.platform_helpers import bash_path, bash_script_args

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_shell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_nulla.sh").read_text(encoding="utf-8")

    assert "--install-profile <id>" in script
    assert "ollama-only" in script
    assert "ollama-max" in script
    assert "NULLA_INSTALL_PROFILE" in script
    assert "NULLA_BUILD_COMMIT" in script
    assert "NULLA_BUILD_DIRTY_STATE" in script
    assert 'BUILD_COMMIT=""' in script
    assert "SOURCE_COMMIT" in script
    assert "SOURCE_DIRTY_STATE" in script
    assert 'resolve_archive_commit() {' in script
    assert 'write_build_metadata() {' in script
    assert 'json_bool_or_null() {' in script
    assert 'archive_has_common_root() {' in script
    assert 'config/build-source.json' in script
    assert '--source-commit <sha>' in script
    assert '--source-dirty <bool>' in script
    assert '--source-commit)' in script
    assert '--source-dirty)' in script
    assert 'profile_args=(--install-profile "${INSTALL_PROFILE}")' in script
    assert 'exec_with_profile_args() {' in script
    assert 'if [[ ${#profile_args[@]} -gt 0 ]]; then' in script
    assert '${INSTALL_DIR}/install_nulla.sh' in script
    assert '${INSTALL_DIR}/installer/install_nulla.sh' in script
    assert script.index('${INSTALL_DIR}/installer/install_nulla.sh') < script.index('${INSTALL_DIR}/install_nulla.sh')
    assert 'exec_with_profile_args "${launcher}"' in script
    assert 'exec_with_profile_args "${canonical}" --yes --start --openclaw default' in script
    assert 'exec_with_profile_args "${canonical}" --yes --openclaw default' in script
    assert 'no usable installer entrypoint was found' in script


def test_shell_bootstrap_handles_flat_git_archive_without_stripping_root_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "nulla-flat-bootstrap.tar.gz"

    source_root.mkdir(parents=True)
    (source_root / "Install_And_Run_NULLA.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{bash_path(marker_path)}"\n',
        encoding="utf-8",
    )
    (source_root / "Install_And_Run_NULLA.sh").chmod(0o755)
    installer_dir = source_root / "installer"
    installer_dir.mkdir()
    (installer_dir / "install_nulla.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "exit 99\n",
        encoding="utf-8",
    )
    (installer_dir / "install_nulla.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(source_root / "Install_And_Run_NULLA.sh", arcname="Install_And_Run_NULLA.sh")
        tar.add(installer_dir, arcname="installer")

    subprocess.run(
        bash_script_args(
            PROJECT_ROOT / "installer" / "bootstrap_nulla.sh",
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--dir",
            str(install_dir),
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )

    assert marker_path.exists()
    assert (install_dir / "Install_And_Run_NULLA.sh").exists()
    assert (install_dir / "installer" / "install_nulla.sh").exists()


def test_powershell_bootstrap_falls_back_to_canonical_installer() -> None:
    script = (PROJECT_ROOT / "installer" / "bootstrap_nulla.ps1").read_text(encoding="utf-8")

    assert '[string]$InstallProfile = $env:NULLA_INSTALL_PROFILE' in script
    assert '[string]$SourceCommit = $env:NULLA_BUILD_COMMIT' in script
    assert '[string]$SourceDirtyState = $env:NULLA_BUILD_DIRTY_STATE' in script
    assert 'function Resolve-ArchiveCommit' in script
    assert 'function Resolve-DirtyState' in script
    assert 'function Write-BuildMetadata' in script
    assert 'build-source.json' in script
    assert '$psLauncher = Join-Path $InstallDir "Install_And_Run_NULLA.ps1"' in script
    assert '& powershell -NoProfile -ExecutionPolicy Bypass -File $psLauncher @psArgs' in script
    assert '-SkipBenchmark' not in script
    assert '/INSTALLPROFILE=$InstallProfile' in script
    assert 'install_nulla.bat' in script
    assert 'installer\\\\install_nulla.bat' in script
    assert script.index('installer\\\\install_nulla.bat') < script.index('install_nulla.bat')
    assert '& $canonical /Y /START "/OPENCLAW=default" @profileArgs' in script
    assert '& $canonical /Y "/OPENCLAW=default" @profileArgs' in script
    assert 'no usable installer entrypoint was found' in script


def test_shell_bootstrap_executes_launcher_without_profile_override(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    archive_root = source_root / "nulla-hive-mind-main"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "nulla-bootstrap.tar.gz"

    archive_root.mkdir(parents=True)
    (archive_root / "Install_And_Run_NULLA.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{bash_path(marker_path)}"\n',
        encoding="utf-8",
    )
    (archive_root / "Install_And_Run_NULLA.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root, arcname=archive_root.name)

    subprocess.run(
        bash_script_args(
            PROJECT_ROOT / "installer" / "bootstrap_nulla.sh",
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--dir",
            str(install_dir),
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )

    assert marker_path.exists()
    assert marker_path.read_text(encoding="utf-8") == "\n"
    metadata_path = install_dir / "config" / "build-source.json"
    assert metadata_path.exists()
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"ref": "main"' in metadata
    assert f'"source_url": "{archive_path.resolve().as_uri()}"' in metadata


def test_shell_bootstrap_records_explicit_source_commit_for_custom_archive(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    archive_root = source_root / "nulla-hive-mind-branch"
    install_dir = tmp_path / "install"
    marker_path = tmp_path / "launcher_args.txt"
    archive_path = tmp_path / "nulla-bootstrap.tar.gz"

    archive_root.mkdir(parents=True)
    (archive_root / "Install_And_Run_NULLA.sh").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'printf "%s\\n" "$@" > "{bash_path(marker_path)}"\n',
        encoding="utf-8",
    )
    (archive_root / "Install_And_Run_NULLA.sh").chmod(0o755)

    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(archive_root, arcname=archive_root.name)

    subprocess.run(
        bash_script_args(
            PROJECT_ROOT / "installer" / "bootstrap_nulla.sh",
            "--ref",
            "codex/honest-ollama-prewarm-bootstrap",
            "--archive-url",
            archive_path.resolve().as_uri(),
            "--source-commit",
            "0123456789abcdef0123456789abcdef01234567",
            "--source-dirty",
            "true",
            "--dir",
            str(install_dir),
        ),
        check=True,
        cwd=PROJECT_ROOT,
    )

    metadata_path = install_dir / "config" / "build-source.json"
    metadata = metadata_path.read_text(encoding="utf-8")
    assert '"ref": "codex/honest-ollama-prewarm-bootstrap"' in metadata
    assert '"branch": "codex/honest-ollama-prewarm-bootstrap"' in metadata
    assert '"commit": "0123456789abcdef0123456789abcdef01234567"' in metadata
    assert '"dirty_state": true' in metadata
    assert '"source_kind": "archive"' in metadata
