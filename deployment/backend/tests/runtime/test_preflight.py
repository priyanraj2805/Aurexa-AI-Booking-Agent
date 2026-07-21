import json
from pathlib import Path

from scripts import preflight


def test_missing_critical_env_returns_non_zero_with_remediation(tmp_path):
    env = {"DATABASE_URL": "postgresql://user:pass@localhost:5432/dotstark"}

    result = preflight.run_preflight(
        env=env,
        python_version=(3, 11, 0),
        which=lambda _: "/usr/bin/fake",
        widget_dir=tmp_path,
    )
    output = preflight.render_report(result)

    assert result["exit_code"] != 0
    assert "Set env var LLM_API_KEY" in output


def test_all_checks_pass_returns_zero_and_machine_parseable_summary(tmp_path):
    env = {
        "LLM_API_KEY": "secret",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/dotstark",
    }

    result = preflight.run_preflight(
        env=env,
        python_version=(3, 11, 0),
        which=lambda _: "/usr/bin/fake",
        widget_dir=tmp_path,
    )
    output = preflight.render_report(result)
    summary_line = [line for line in output.splitlines() if line.startswith("SUMMARY_JSON:")][
        0
    ]
    summary = json.loads(summary_line.split("SUMMARY_JSON:", 1)[1].strip())

    assert result["exit_code"] == 0
    assert summary["status"] == "pass"
    assert summary["failed"] == 0


def test_output_identifies_backend_and_frontend_groups(tmp_path):
    env = {
        "LLM_API_KEY": "secret",
        "DATABASE_URL": "postgresql://user:pass@localhost:5432/dotstark",
    }
    result = preflight.run_preflight(
        env=env,
        python_version=(3, 11, 0),
        which=lambda _: "/usr/bin/fake",
        widget_dir=Path(tmp_path),
    )
    output = preflight.render_report(result)

    assert "BACKEND PREREQUISITES" in output
    assert "FRONTEND PREREQUISITES" in output
