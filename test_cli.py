import json
import time
import urllib.error

import cli
from cli import PendingAction


def write_pending(tmp_path, delay_minutes=5, created=1.0):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "pending.json"
    path.write_text(json.dumps({
        "kind": "shutdown",
        "delay_minutes": delay_minutes,
        "created": created,
    }))
    return path


def fake_execute(calls, result="Shutdown scheduled in 5 minute(s). Use cancel_shutdown to abort."):
    def execute(name, args):
        calls.append((name, args))
        return result
    return execute


def test_pending_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PENDING", tmp_path / "pending.json")
    action = PendingAction("shutdown", 5, time.time())
    cli.save_pending(action)
    assert cli.load_pending() == action


def test_load_missing_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PENDING", tmp_path / "pending.json")
    assert cli.load_pending() is None


def test_load_expired_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PENDING", write_pending(tmp_path, created=1.0))
    assert cli.load_pending() is None


def test_load_bad_json(tmp_path, monkeypatch):
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "pending.json"
    path.write_text("{no es json")
    monkeypatch.setattr(cli, "PENDING", path)
    assert cli.load_pending() is None


def test_save_none_removes_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "PENDING", write_pending(tmp_path))
    cli.save_pending(None)
    assert not (tmp_path / "pending.json").exists()
    assert cli.load_pending() is None


def test_is_confirmation():
    assert cli.is_confirmation("sí") is True
    assert cli.is_confirmation("Sí, adelante") is True
    assert cli.is_confirmation("confirmo") is True
    assert cli.is_confirmation("hazlo") is True
    assert cli.is_confirmation("quiero que sí") is False
    assert cli.is_confirmation("no") is False
    assert cli.is_confirmation("") is False


def test_is_rejection():
    assert cli.is_rejection("no") is True
    assert cli.is_rejection("Cancela eso") is True
    assert cli.is_rejection("olvídalo") is True
    assert cli.is_rejection("sí") is False
    assert cli.is_rejection("") is False


def test_ask_confirmation_text():
    assert cli.ask_confirmation(PendingAction("shutdown", 5, 0.0)) == \
        "¿Apago la computadora en 5 minutos? Di sí para confirmar."
    assert cli.ask_confirmation(PendingAction("shutdown", 1, 0.0)) == \
        "¿Apago la computadora en 1 minuto? Di sí para confirmar."


def test_handle_pending_without_action():
    calls = []
    assert cli.handle_pending("sí", None, fake_execute(calls)) is None
    assert calls == []


def test_handle_pending_confirms_shutdown():
    calls = []
    result = cli.handle_pending("sí", PendingAction("shutdown", 5, 0.0), fake_execute(calls))
    assert result == "Listo, apago la computadora en 5 minutos."
    assert calls == [("shutdown", {"confirm": True, "delay_minutes": 5})]


def test_handle_pending_confirms_one_minute():
    calls = []
    result = cli.handle_pending("confirmo", PendingAction("shutdown", 1, 0.0), fake_execute(calls))
    assert result == "Listo, apago la computadora en 1 minuto."
    assert calls == [("shutdown", {"confirm": True, "delay_minutes": 1})]


def test_handle_pending_reports_command_error():
    calls = []
    execute = fake_execute(calls, result="Shutdown failed: denied")
    assert cli.handle_pending("sí", PendingAction("shutdown", 5, 0.0), execute) == "Shutdown failed: denied"


def test_handle_pending_rejects():
    calls = []
    result = cli.handle_pending("no", PendingAction("shutdown", 5, 0.0), fake_execute(calls))
    assert result == "Apagado cancelado."
    assert calls == []


def test_handle_pending_drops_on_other_text():
    calls = []
    result = cli.handle_pending("sube el volumen", PendingAction("shutdown", 5, 0.0), fake_execute(calls))
    assert result is None
    assert calls == []


def test_pending_from_tool_shutdown_clamps():
    action = cli.pending_from_tool("shutdown", {"delay_minutes": 5})
    assert isinstance(action, PendingAction)
    assert action.kind == "shutdown"
    assert action.delay_minutes == 5
    assert cli.pending_from_tool("shutdown", {"delay_minutes": 999}).delay_minutes == 60
    assert cli.pending_from_tool("shutdown", {"delay_minutes": 0}).delay_minutes == 1
    assert cli.pending_from_tool("shutdown", {}).delay_minutes == 1


def test_pending_from_tool_other_tools():
    assert cli.pending_from_tool("set_volume", {"percent": 50}) is None
    assert cli.pending_from_tool("cancel_shutdown", {}) is None


def test_to_ollama_tools_mapping():
    tools = [{
        "name": "set_volume",
        "description": "Set volume.",
        "inputSchema": {"type": "object", "properties": {"percent": {"type": "integer"}}},
    }, {
        "name": "volume_status",
        "description": "Report volume.",
    }]
    assert cli.to_ollama_tools(tools) == [
        {
            "type": "function",
            "function": {
                "name": "set_volume",
                "description": "Set volume.",
                "parameters": {"type": "object", "properties": {"percent": {"type": "integer"}}},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "volume_status",
                "description": "Report volume.",
                "parameters": {},
            },
        },
    ]
    assert cli.to_ollama_tools([]) == []


def test_chat_once_unreachable(monkeypatch):
    def boom(request, timeout=None):
        raise urllib.error.URLError("down")
    monkeypatch.setattr(cli.urllib.request, "urlopen", boom)
    assert cli.chat_once([], []) == {
        "role": "assistant",
        "content": "No pude contactar al modelo.",
        "tool_calls": [],
    }


def test_main_without_sentence(capsys):
    assert cli.main([]) == 1
    assert "uso" in capsys.readouterr().err.lower()
