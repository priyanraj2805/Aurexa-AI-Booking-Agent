import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.error import URLError
from urllib.request import urlopen


@dataclass(frozen=True)
class ServiceCheck:
    name: str
    command: List[str]
    cwd: str
    ready_url: str
    timeout_seconds: int = 45


def _default_services() -> List[ServiceCheck]:
    backend_root = Path(__file__).resolve().parents[1]
    repo_root = backend_root.parent
    return [
        ServiceCheck(
            name="backend",
            command=[sys.executable, "run.py", "--skip-preflight", "--no-browser"],
            cwd=str(backend_root),
            ready_url="http://127.0.0.1:8005/docs",
        ),
        ServiceCheck(
            name="chat-widget",
            command=["npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5173"],
            cwd=str(repo_root / "frontend" / "chat-widget"),
            ready_url="http://127.0.0.1:5173",
        ),
    ]


def _launch_service(service: ServiceCheck):
    command = _resolve_launch_command(service)
    return subprocess.Popen(
        command,
        cwd=service.cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _resolve_launch_command(service: ServiceCheck) -> List[str]:
    if not service.command:
        return service.command
    if os.name != "nt":
        return service.command

    resolved = list(service.command)
    executable = resolved[0]
    if executable.lower() == "npm":
        npm_cmd = shutil.which("npm.cmd")
        resolved[0] = npm_cmd or "npm.cmd"
        return resolved

    command_path = shutil.which(executable)
    if command_path:
        resolved[0] = command_path
    return resolved


def _format_launch_failure(service: ServiceCheck, exc: Exception) -> str:
    command_display = " ".join(service.command)
    return (
        f"launch failed for {service.name}; command={command_display}; "
        f"cwd={service.cwd}; reason={exc}"
    )


def _probe_http_ready(service: ServiceCheck, _process) -> bool:
    try:
        with urlopen(service.ready_url, timeout=1):
            return True
    except URLError:
        return False
    except OSError:
        return False


def _terminate_by_pid(process) -> None:
    if process is None or getattr(process, "pid", None) is None:
        return
    if process.poll() is not None:
        return
    try:
        os.kill(process.pid, signal.SIGTERM)
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
            process.wait(timeout=5)
        except Exception:
            pass


def _capture_failure_output(process) -> str:
    if process is None:
        return "process not created"
    details = []
    code = process.poll()
    if code is not None:
        details.append(f"process exited early with code {code}")
    stdout_pipe = getattr(process, "stdout", None)
    stderr_pipe = getattr(process, "stderr", None)
    if stdout_pipe:
        try:
            chunk = stdout_pipe.read()
            if chunk:
                details.append(f"stdout: {chunk.strip()[:300]}")
        except Exception:
            pass
    if stderr_pipe:
        try:
            chunk = stderr_pipe.read()
            if chunk:
                details.append(f"stderr: {chunk.strip()[:300]}")
        except Exception:
            pass
    return "; ".join(details) if details else "service did not become ready before timeout"


def run_boot_check(
    services: Optional[List[ServiceCheck]] = None,
    launch_process: Optional[Callable[[ServiceCheck], object]] = None,
    probe_readiness: Optional[Callable[[ServiceCheck, object], bool]] = None,
    terminate_process: Optional[Callable[[object], None]] = None,
    sleep: Optional[Callable[[float], None]] = None,
) -> Dict[str, object]:
    checks = services or _default_services()
    launcher = launch_process or _launch_service
    prober = probe_readiness or _probe_http_ready
    terminator = terminate_process or _terminate_by_pid
    sleeper = sleep or time.sleep

    service_results = []
    failures = 0
    for service in checks:
        process = None
        start = time.time()
        status = "fail"
        diagnostic = ""
        resolved_service = service
        try:
            resolved_service = replace(service, command=_resolve_launch_command(service))
            process = launcher(resolved_service)
            while time.time() - start < service.timeout_seconds:
                if prober(resolved_service, process):
                    status = "pass"
                    diagnostic = "ready"
                    break
                if process.poll() is not None:
                    diagnostic = _capture_failure_output(process)
                    break
                sleeper(0.5)
            if status != "pass" and not diagnostic:
                diagnostic = _capture_failure_output(process)
        except Exception as exc:
            diagnostic = _format_launch_failure(resolved_service, exc)
        finally:
            if process is not None:
                terminator(process)

        if status != "pass":
            failures += 1
        service_results.append(
            {
                "name": service.name,
                "status": status,
                "diagnostic": diagnostic,
                "ready_url": service.ready_url,
            }
        )

    return {
        "status": "pass" if failures == 0 else "fail",
        "exit_code": 0 if failures == 0 else 1,
        "services": service_results,
    }


def render_report(result: Dict[str, object]) -> str:
    lines: List[str] = []
    for service in result["services"]:
        lines.append(
            f"[{service['status'].upper()}] {service['name']} - {service['diagnostic']} ({service['ready_url']})"
        )
    summary = {
        "status": result["status"],
        "total": len(result["services"]),
        "failed": len([item for item in result["services"] if item["status"] != "pass"]),
    }
    lines.append(f"SUMMARY_JSON: {json.dumps(summary, sort_keys=True)}")
    return "\n".join(lines)


def main() -> int:
    result = run_boot_check()
    print(render_report(result))
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
