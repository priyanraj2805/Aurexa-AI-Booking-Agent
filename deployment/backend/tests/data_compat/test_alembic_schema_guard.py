"""D-05 guard: schema refactors must ship with matching Alembic migrations."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_BASE_CMD = [sys.executable, "-m", "alembic", "-c", "alembic.ini"]


@dataclass
class GuardResult:
    ok: bool
    message: str


def _execute_alembic_schema_guard(
    runner=subprocess.run,
    *,
    cwd: Path = BACKEND_DIR,
) -> GuardResult:
    """Run Alembic guard commands that CI can use to block unmanaged drift."""
    upgrade = runner(
        [*ALEMBIC_BASE_CMD, "upgrade", "head"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if upgrade.returncode != 0:
        return GuardResult(
            ok=False,
            message=(
                "Alembic upgrade head failed. "
                "Schema refactors are allowed only with immediate migration coverage (D-05). "
                f"{(upgrade.stderr or upgrade.stdout).strip()}"
            ),
        )

    check = runner(
        [*ALEMBIC_BASE_CMD, "check"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if check.returncode != 0:
        return GuardResult(
            ok=False,
            message=(
                "Alembic check detected schema drift. "
                "Add/update migration artifacts before merging model changes (D-05). "
                f"{(check.stderr or check.stdout).strip()}"
            ),
        )

    return GuardResult(ok=True, message="Alembic schema guard passed.")


def test_alembic_guard_blocks_when_check_detects_drift():
    results = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr="FAILED: New upgrade operations detected",
        ),
    ]

    def fake_runner(*_args, **_kwargs):
        return results.pop(0)

    guard = _execute_alembic_schema_guard(fake_runner)

    assert guard.ok is False
    assert "schema drift" in guard.message.lower()
    assert "migration artifacts" in guard.message.lower()


def test_alembic_guard_passes_when_upgrade_and_check_are_aligned():
    results = [
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    ]

    def fake_runner(*_args, **_kwargs):
        return results.pop(0)

    guard = _execute_alembic_schema_guard(fake_runner)

    assert guard.ok is True
    assert "passed" in guard.message.lower()


@pytest.mark.skipif(
    os.getenv("ALEMBIC_GUARD_LIVE") != "1",
    reason="Set ALEMBIC_GUARD_LIVE=1 to run live alembic upgrade/check integration.",
)
def test_alembic_guard_live_upgrade_and_check():
    guard = _execute_alembic_schema_guard()
    assert guard.ok, guard.message
