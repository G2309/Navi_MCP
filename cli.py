import asyncio
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

OLLAMA_URL = os.environ.get("NAVI_OLLAMA", "http://127.0.0.1:11434/api/chat")
MODEL = os.environ.get("NAVI_MODEL", "qwen3.5:4b")
SERVER = Path(__file__).with_name("server.py")
PENDING = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "navi" / "pending.json"
PENDING_TTL = 300.0
MAX_STEPS = 8

SYSTEM = (
    "Eres Navi, un asistente de voz en una computadora con Linux. "
    "Responde en español en máximo 3 frases cortas, sin listas, sin markdown y sin emojis, "
    "porque tu respuesta se lee en voz alta. Usa las herramientas para acciones del sistema "
    "y nunca digas que ejecutaste algo que no ejecutaste."
)

YES = re.compile(r"^(sí|si|confirmo|confirmar|adelante|hazlo|dale|va|ok)\b", re.IGNORECASE)
NO = re.compile(r"^(no|cancela|cancelar|olv[ií]dalo|mejor no|para)\b", re.IGNORECASE)


@dataclass
class PendingAction:
    """An action requested by the user that waits for an explicit yes before it runs."""

    kind: str
    delay_minutes: int
    created: float


# Lee la acción pendiente desde PENDING (JSON {"kind","delay_minutes","created"}).
# Sin archivo -> None. JSON inválido -> None. (time.time() - created) > PENDING_TTL -> None.
# Reutiliza: PENDING, PendingAction, PENDING_TTL
def load_pending() -> PendingAction | None:
    if not PENDING.exists():
        return None
    try:
        pending = json.loads(PENDING.read_text())
    except json.decoder.JSONDecodeError:
        return None
    created = float(pending["created"])
    if time.time() - created > PENDING_TTL:
        return None
    return PendingAction(pending["kind"], pending["delay_minutes"], created)


# Guarda action como JSON en PENDING (crea el directorio con mkdir). action None -> borra el archivo si existe.
# Reutiliza: PENDING
def save_pending(action: PendingAction | None) -> None:
    if action is None:
        PENDING.parent.mkdir(exist_ok=True, parents=True)
        PENDING.unlink(missing_ok=True)
    else:
        pending = json.dumps(
            {"kind": action.kind, "delay_minutes": action.delay_minutes, "created": time.time()}
        )
        PENDING.parent.mkdir(exist_ok=True, parents=True)
        PENDING.write_text(pending)
        PENDING.chmod(0o600)  # chmod 600

# True si el texto (limpio) empieza con palabra de sí: sí, si, confirmo, confirmar, adelante, hazlo, dale, va, ok.
# "sí" -> True; "Sí, adelante" -> True; "quiero que sí" -> False; "" -> False.
# Reutiliza: YES
def is_confirmation(text: str) -> bool:
    return re.match(YES, text.strip(), re.IGNORECASE) is not None

# True si el texto empieza con palabra de no: no, cancela, cancelar, olvídalo, mejor no, para.
# "no" -> True; "Cancela eso" -> True; "sí" -> False; "" -> False.
# Reutiliza: NO
def is_rejection(text: str) -> bool:
    return re.match(NO, text.strip(), re.IGNORECASE) is not None

# Devuelve la pregunta hablada en español para confirmar la acción pendiente.
# PendingAction("shutdown", 5, 0.0) -> "¿Apago la computadora en 5 minutos? Di sí para confirmar."
# PendingAction("shutdown", 1, 0.0) -> "¿Apago la computadora en 1 minuto? Di sí para confirmar."
def ask_confirmation(action: PendingAction) -> str:
    if action.delay_minutes == 0:
        return f"¿{action.kind}? Di sí para confirmar."
    elif action.delay_minutes == 1:
        return f"¿{action.kind} en 1 minuto? Di sí para confirmar."
    else:
        return f"¿{action.kind} en {action.delay_minutes} minutos? Di sí para confirmar."

# Resuelve una acción pendiente. action None -> None.
# "sí"/"confirmo" -> guarda None y llama execute("shutdown", {"confirm": True, "delay_minutes": action.delay_minutes});
#   si ese texto empieza con "Shutdown failed" lo devuelve tal cual, si no -> "Listo, apago la computadora en N minutos." (singular si N==1).
# "no"/"cancela" -> guarda None y devuelve "Apagado cancelado." (no ejecuta).
# otro texto -> guarda None y devuelve None (se sigue como comando nuevo).
# Reutiliza: is_confirmation, is_rejection, save_pending
def handle_pending(text: str, action: PendingAction | None, execute: Callable[[str, dict], str]) -> str | None:
    if action is None:
        return None
    if is_confirmation(text):
        save_pending(None)
        if (result := execute(action.kind, {"confirm": True, "delay_minutes": action.delay_minutes})) == "Shutdown failed":
            return result
        return f"Listo, apago la computadora en {action.delay_minutes} minutos." if action.delay_minutes != 1 else f"Listo, apago la computadora en 1 minuto."
    elif is_rejection(text):
        save_pending(None)
        return "Apagado cancelado."
    else:
        save_pending(None)
        return None


# Convierte una llamada a tool del modelo en PendingAction; solo "shutdown" produce una.
# pending_from_tool("shutdown", {"delay_minutes": 5}) -> PendingAction("shutdown", 5, time.time())
# delay recortado a 1-60; si falta "delay_minutes" -> 1. Cualquier otra tool -> None.
# Reutiliza: PendingAction
def pending_from_tool(name: str, args: dict) -> PendingAction | None:
    if name != "shutdown":
        return None
    delay_minutes = args.get("delay_minutes", 1)
    return PendingAction(name, max(min(60, delay_minutes), 1), time.time())


# Convierte tool defs del MCP ({name, description, inputSchema}) al formato tools de Ollama.
# Falta inputSchema -> parameters {}.
# [{"name":"set_volume","description":"Set volume.","inputSchema":{"type":"object"}}]
#   -> [{"type":"function","function":{"name":"set_volume","description":"Set volume.","parameters":{"type":"object"}}}]
# [] -> []
def to_ollama_tools(tools: list) -> list:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t.get("inputSchema", {"type": "object"})}}
        for t in tools
    ]


# Llama a /api/chat de Ollama (POST JSON {model: MODEL, stream: False, messages, tools, think: False,
# options: {temperature: 0.3, num_ctx: 8192}}) y devuelve {"role":"assistant","content":...,"tool_calls":[...]}.
# Sin conexión (urllib.error.URLError) o JSON inválido -> {"role":"assistant","content":"No pude contactar al modelo.","tool_calls":[]}.
# Reutiliza: OLLAMA_URL, MODEL, urllib.request
def chat_once(messages: list, tools: list) -> dict:
    headers = {"Content-Type": "application/json"}
    data = {
        "model": MODEL,
        "stream": False,
        "messages": messages,
        "tools": tools,
        "think": False,
        "options": {
            "temperature": 0.3,
            "num_ctx": 8192,
        },
    }
    try:
        with urllib.request.urlopen(OLLAMA_URL, json.dumps(data).encode("utf-8"), headers=headers) as r:
            try:
                result = json.loads(r.read().decode("utf-8"))
            except json.JSONDecodeError as e:
                print(f"ERROR: {e}")
                result = {
                    "role": "assistant",
                    "content": "No pude contactar al modelo.",
                    "tool_calls": [],
                }
            return result
    except urllib.error.URLError as e:
        print(f"ERROR: {e}")
        return {
            "role": "assistant",
            "content": "No pude contactar al modelo.",
            "tool_calls": [],
        }

# Ejecuta una tool MCP vía session.call_tool(name, args) y devuelve el texto del primer contenido.
# Tool desconocida o error del servidor -> "Error al ejecutar <name>: <mensaje>" (texto para el modelo).
# Reutiliza: ninguna (usa session).
async def run_tool(session: ClientSession, name: str, args: dict) -> str:
    try:
        result = await session.call_tool(name, args)
        return next(iter(result.values()))["content"] if "content" in result.values() else f"Error al ejecutar {name}: {result}"
    except Exception as e:
        return f"Error al ejecutar {name}: {e}"


# Responde a una frase hablada: si hay pendiente, handle_pending(text, load_pending(), execute_mcp); si devuelve algo lo devuelve.
# Si no, bucle modelo<->tools vía MCP (hasta MAX_STEPS turnos) y devuelve la respuesta final hablada.
# texto vacío -> "Uso: navi <frase>". Sin modelo -> "No pude contactar al modelo.". Más de MAX_STEPS -> "No terminé a tiempo, intenta de nuevo."
# Reutiliza: SYSTEM, MAX_STEPS, SERVER, load_pending, handle_pending, to_ollama_tools, chat_once, run_tool, ClientSession, stdio_client
async def ask(text: str) -> str:
    if (pending := load_pending()) is not None:
        if (result := handle_pending(text, pending, execute_mcp)) is not None:
            return result
        return f"Uso: navi <frase>. Ejemplo: navi apaga la computadora en 5 minutos."
    if text.strip() == "":
        return "Uso: navi <frase>. Ejemplo: navi apaga la computadora en 5 minutos."
    messages = [{"role": "system", "content": SYSTEM}]
    tools = to_ollama_tools([t async for t in session.tools])
    for _ in range(MAX_STEPS):
        try:
            messages.append({"role": "user", "content": text})
            response = chat_once(messages, tools)
            messages.append(response)
            for call in response["tool_calls"]:
                args = json.loads(call["arguments"])
                tool_name = call["function"]["name"]
                tool_text = await run_tool(session, tool_name, args)
                messages.append({"role": "function", "name": tool_name, "content": tool_text})
            if not response["tool_calls"] and "assistant" in response:
                return response["assistant"]["content"]
        except Exception as e:
            return f"ERROR: {e}"
    return "No terminé a tiempo, intenta de nuevo."


# CLI `navi <frase>`: imprime la respuesta hablada por stdout. Sin frase -> "Uso: navi <frase>" en stderr y código 1.
# Reutiliza: ask
def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv
    if len(argv) == 2:
        print(ask(argv[1]), end="")
    else:
        print("Uso: navi <frase>", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
