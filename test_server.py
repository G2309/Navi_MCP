import subprocess

import server


def fake_run(calls, returncode=0):
    def run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, "", "denied")
    return run


def test_shutdown_requires_confirmation(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls))
    assert "confirm" in server.shutdown()
    assert calls == []


def test_shutdown_delay_is_clamped(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls))
    server.shutdown(confirm=True, delay_minutes=0)
    server.shutdown(confirm=True, delay_minutes=999)
    assert calls == [["shutdown", "-h", "+1"], ["shutdown", "-h", "+60"]]


def test_shutdown_reports_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run([], returncode=1))
    assert server.shutdown(confirm=True).startswith("Shutdown failed")


def test_cancel(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", fake_run(calls))
    assert "cancelled" in server.cancel_shutdown()
    assert calls == [["shutdown", "-c"]]
