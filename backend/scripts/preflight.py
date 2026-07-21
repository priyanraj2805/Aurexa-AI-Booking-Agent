import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Tuple
from urllib.parse import urlparse


REQUIRED_ENV_KEYS = ("LLM_API_KEY",)
MIN_PYTHON = (3, 11)


def _check_python_version(python_version: Tuple[int, int, int]) -> Dict[str, str]:
    ok = python_version >= (MIN_PYTHON[0], MIN_PYTHON[1], 0)
    return {
        "group": "backend",
        "name": "python_version",
        "ok": ok,
        "detail": (
            f"Python {python_version[0]}.{python_version[1]}.{python_version[2]} detected."
            if ok
            else f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required; found {python_version[0]}.{python_version[1]}.{python_version[2]}."
        ),
        "remediation": (
            "Install Python 3.11+ and re-run preflight."
            if not ok
            else "None"
        ),
    }


def _database_url_from_env(env: Mapping[str, str]) -> str:
    if env.get("DATABASE_URL"):
        return env["DATABASE_URL"]

    user = env.get("POSTGRES_USER", "postgres")
    password = env.get("POSTGRES_PASSWORD", "password")
    host = env.get("POSTGRES_SERVER", "localhost")
    port = env.get("POSTGRES_PORT", "5432")
    db = env.get("POSTGRES_DB", "dotstark_v2")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db}"


def _check_required_env(env: Mapping[str, str]) -> List[Dict[str, str]]:
    checks = []
    for key in REQUIRED_ENV_KEYS:
        present = bool(env.get(key))
        checks.append(
            {
                "group": "backend",
                "name": f"env:{key}",
                "ok": present,
                "detail": (
                    f"{key} is set."
                    if present
                    else f"{key} is missing."
                ),
                "remediation": (
                    "None" if present else f"Set env var {key} before startup."
                ),
            }
        )
    return checks


def _check_database_url(env: Mapping[str, str]) -> Dict[str, str]:
    db_url = _database_url_from_env(env)
    parsed = urlparse(db_url)
    valid_scheme = parsed.scheme in {"postgresql", "postgresql+asyncpg", "postgres"}
    has_location = bool(parsed.hostname) and bool(parsed.path and parsed.path != "/")
    ok = valid_scheme and has_location
    return {
        "group": "backend",
        "name": "database_url_format",
        "ok": ok,
        "detail": (
            f"DATABASE_URL format accepted: {db_url}"
            if ok
            else f"Invalid DATABASE_URL format: {db_url}"
        ),
        "remediation": (
            "Use postgresql://user:pass@host:5432/dbname (or postgresql+asyncpg://...)."
            if not ok
            else "None"
        ),
    }


def _check_command_available(
    group: str, command_name: str, which: Callable[[str], Optional[str]]
) -> Dict[str, str]:
    path = which(command_name)
    ok = bool(path)
    return {
        "group": group,
        "name": f"command:{command_name}",
        "ok": ok,
        "detail": (
            f"{command_name} found at {path}."
            if ok
            else f"{command_name} command not found."
        ),
        "remediation": (
            "None" if ok else f"Install {command_name} and ensure it is on PATH."
        ),
    }


def _check_widget_directory(widget_dir: Path) -> Dict[str, str]:
    ok = widget_dir.exists() and widget_dir.is_dir()
    return {
        "group": "frontend",
        "name": "chat_widget_directory",
        "ok": ok,
        "detail": (
            f"Chat widget directory found: {widget_dir}"
            if ok
            else f"Chat widget directory missing: {widget_dir}"
        ),
        "remediation": (
            "None"
            if ok
            else "Ensure frontend/chat-widget exists before running local startup."
        ),
    }


def run_preflight(
    env: Optional[Mapping[str, str]] = None,
    python_version: Optional[Tuple[int, int, int]] = None,
    which: Optional[Callable[[str], Optional[str]]] = None,
    widget_dir: Optional[Path] = None,
) -> Dict[str, object]:
    runtime_env = dict(env or os.environ)
    runtime_python = python_version or (
        sys.version_info.major,
        sys.version_info.minor,
        sys.version_info.micro,
    )
    runtime_which = which or shutil.which
    runtime_widget_dir = widget_dir or (
        Path(__file__).resolve().parents[2] / "frontend" / "chat-widget"
    )

    checks: List[Dict[str, str]] = []
    checks.append(_check_python_version(runtime_python))
    checks.extend(_check_required_env(runtime_env))
    checks.append(_check_database_url(runtime_env))
    checks.append(_check_command_available("frontend", "node", runtime_which))
    checks.append(_check_command_available("frontend", "npm", runtime_which))
    checks.append(_check_widget_directory(runtime_widget_dir))

    failed = sum(1 for check in checks if not check["ok"])
    total = len(checks)
    return {
        "checks": checks,
        "failed": failed,
        "passed": total - failed,
        "total": total,
        "status": "pass" if failed == 0 else "fail",
        "exit_code": 0 if failed == 0 else 1,
    }


def render_report(result: Dict[str, object]) -> str:
    checks = result["checks"]
    lines: List[str] = []
    for group in ("backend", "frontend"):
        lines.append(
            "BACKEND PREREQUISITES" if group == "backend" else "FRONTEND PREREQUISITES"
        )
        for check in checks:
            if check["group"] != group:
                continue
            status = "PASS" if check["ok"] else "FAIL"
            lines.append(f"- [{status}] {check['name']}: {check['detail']}")
            if not check["ok"]:
                lines.append(f"  Remediation: {check['remediation']}")
        lines.append("")

    summary = {
        "status": result["status"],
        "passed": result["passed"],
        "failed": result["failed"],
        "total": result["total"],
    }
    lines.append(f"SUMMARY_JSON: {json.dumps(summary, sort_keys=True)}")
    return "\n".join(lines)


def main() -> int:
    result = run_preflight()
    print(render_report(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
