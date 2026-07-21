from scripts import dev_boot_check


class FakeProcess:
    def __init__(self, pid=123, return_code=None):
        self.pid = pid
        self.returncode = return_code

    def poll(self):
        return self.returncode


def test_backend_boot_detected_and_terminated_cleanly():
    service = dev_boot_check.ServiceCheck(
        name="backend",
        command=["python", "run.py"],
        cwd=".",
        ready_url="http://localhost:8005/docs",
    )
    terminated = []

    result = dev_boot_check.run_boot_check(
        services=[service],
        launch_process=lambda _service: FakeProcess(pid=1001),
        probe_readiness=lambda _service, _process: True,
        terminate_process=lambda process: terminated.append(process.pid),
    )

    assert result["exit_code"] == 0
    assert terminated == [1001]
    assert result["services"][0]["status"] == "pass"


def test_chat_widget_boot_detected_and_terminated_cleanly():
    service = dev_boot_check.ServiceCheck(
        name="chat-widget",
        command=["npm", "run", "dev"],
        cwd="frontend/chat-widget",
        ready_url="http://localhost:5173",
    )
    terminated = []

    result = dev_boot_check.run_boot_check(
        services=[service],
        launch_process=lambda _service: FakeProcess(pid=2002),
        probe_readiness=lambda _service, _process: True,
        terminate_process=lambda process: terminated.append(process.pid),
    )

    assert result["exit_code"] == 0
    assert terminated == [2002]
    assert result["services"][0]["status"] == "pass"


def test_startup_failure_returns_non_zero_with_diagnostics():
    service = dev_boot_check.ServiceCheck(
        name="backend",
        command=["python", "run.py"],
        cwd=".",
        ready_url="http://localhost:8005/docs",
    )

    result = dev_boot_check.run_boot_check(
        services=[service],
        launch_process=lambda _service: (_ for _ in ()).throw(RuntimeError("launch failed")),
        probe_readiness=lambda _service, _process: False,
        terminate_process=lambda _process: None,
    )

    assert result["exit_code"] != 0
    assert result["services"][0]["status"] == "fail"
    assert "launch failed" in result["services"][0]["diagnostic"]


def test_windows_chat_widget_launch_resolves_to_cmd_executable(monkeypatch):
    service = dev_boot_check.ServiceCheck(
        name="chat-widget",
        command=["npm", "run", "dev"],
        cwd="frontend/chat-widget",
        ready_url="http://localhost:5173",
    )
    captured = {}

    class DummyProcess:
        def poll(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs.get("cwd")
        return DummyProcess()

    monkeypatch.setattr(dev_boot_check.os, "name", "nt")
    monkeypatch.setattr(dev_boot_check.subprocess, "Popen", fake_popen)

    dev_boot_check._launch_service(service)

    assert captured["command"][0].lower().endswith("npm.cmd")
    assert captured["cwd"] == "frontend/chat-widget"


def test_chat_widget_launch_failure_includes_command_and_cwd_diagnostics():
    service = dev_boot_check.ServiceCheck(
        name="chat-widget",
        command=["npm", "run", "dev"],
        cwd="frontend/chat-widget",
        ready_url="http://localhost:5173",
    )

    result = dev_boot_check.run_boot_check(
        services=[service],
        launch_process=lambda _service: (_ for _ in ()).throw(
            FileNotFoundError("[WinError 2] The system cannot find the file specified")
        ),
        probe_readiness=lambda _service, _process: False,
        terminate_process=lambda _process: None,
    )

    diagnostic = result["services"][0]["diagnostic"]
    assert "command=" in diagnostic
    assert "cwd=" in diagnostic
    assert "WinError 2" in diagnostic


def test_render_report_preserves_summary_json_contract():
    result = {
        "status": "fail",
        "services": [
            {"name": "backend", "status": "pass", "diagnostic": "ready", "ready_url": "http://localhost:8005/docs"},
            {"name": "chat-widget", "status": "fail", "diagnostic": "not ready", "ready_url": "http://localhost:5173"},
        ],
    }

    report = dev_boot_check.render_report(result)

    assert "[PASS] backend - ready (http://localhost:8005/docs)" in report
    assert "[FAIL] chat-widget - not ready (http://localhost:5173)" in report
    assert 'SUMMARY_JSON: {"failed": 1, "status": "fail", "total": 2}' in report
