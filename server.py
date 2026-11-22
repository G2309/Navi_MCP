import os
import subprocess
from datetime import date, datetime

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("navi")

CALENDAR = os.environ.get("NAVI_CALENDAR", "gustavoacruzbar@gmail.com")
HOLIDAYS = "Festivos en Guatemala"
DEFAULT_SINK = "@DEFAULT_AUDIO_SINK@"
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%H:%M"


def run(binary: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([binary, *args], capture_output=True, text=True)


# Convierte la salida de `wpctl get-volume @DEFAULT_AUDIO_SINK@` en texto legible.
# "Volume: 0.50\n" -> "Volume at 50%, not muted."
# "Volume: 0.35 [MUTED]\n" -> "Volume at 35%, muted."
# "Volume: 1.50" -> "Volume at 150%, not muted."
# "" o "garbage" -> "Could not read volume."
def parse_volume(output: str) -> str:
    if not output or "Could not read volume" in output:
        return "Could not read volume."
    volume, muted = output.splitlines()
    volume, muted = volume.split(": ")[1], muted.strip()
    if muted:
        return f"Volume at {int(float(volume) * 100)}%, muted."
    return f"Volume at {int(float(volume) * 100)}%, not muted."

# Valida una fecha YYYY-MM-DD real (no imposible como 2026-02-30).
# "2026-11-21" -> True
# "2026-02-30" -> False
# "21-11-2026" -> False
# "" -> False
# Reutiliza: DATE_FORMAT
def valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, DATE_FORMAT)
        return True
    except ValueError:
        return False


# Valida una hora 24h real; acepta "15:00" o "9:05" (rellena el cero de la hora).
# "15:00" -> True
# "9:05" -> True
# "25:00" -> False
# "9:5" -> False
# "" -> False
# Reutiliza: TIME_FORMAT
def valid_time(value: str) -> bool:
    try:
        datetime.strptime(value, TIME_FORMAT)
        return True
    except ValueError:
        return False

# Convierte la salida tsv de `gcalcli agenda --tsv` en líneas "YYYY-MM-DD HH:MM: Title".
# Para todo el día la hora se omite: "2026-11-22: Festivo".
# Cabecera "start_date\tstart_time\t..." y filas con menos de 5 columnas se omiten.
# "start_date\tstart_time\tend_date\tend_time\ttitle\n2026-11-21\t15:00\t2026-11-21\t15:30\tReunión\n"
#   -> ["2026-11-21 15:00: Reunión"]
# "" -> []
def parse_agenda(tsv: str) -> list[str]:
    lines = tsv.strip().splitlines()
    if not lines or lines[0] != "start_date\tstart_time\tend_date\tend_time\ttitle":
        return []
    events = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        start_date, start_time = parts[0], parts[1]
        end_date, end_time = parts[2], parts[3]
        title = parts[4]
        if start_date == end_date:
            events.append(f"{start_date} {start_time}: {title}")
        else:
            events.append(f"{start_date}: {title}")
    return events



# Lee el volumen del sink por defecto vía wpctl y devuelve texto legible.
# Éxito -> parse_volume(stdout). Fallo (returncode != 0) -> "Error: <stderr>".
# Reutiliza: run, DEFAULT_SINK, parse_volume
def read_volume() -> str:
    result = run("wpctl", "get-volume", DEFAULT_SINK)
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return parse_volume(result.stdout)


@mcp.tool()
# Fija el volumen del sink por defecto a un porcentaje, recortado a 0-150.
# set_volume(50) -> ejecuta wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ 50%
# set_volume(300) -> wpctl recibe "150%"; set_volume(-5) -> "0%"
# Devuelve el nivel resultante (read_volume) o "Error: <stderr>" si wpctl falla.
# Reutiliza: run, DEFAULT_SINK, read_volume
def set_volume(percent: int) -> str:
    percent = max(0, min(percent, 150))
    result = run("wpctl", "set-volume", "-l", str(percent), DEFAULT_SINK)
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return read_volume()


@mcp.tool()
# Mueve el volumen relativo delta puntos y devuelve el nivel resultante.
# change_volume(5) -> wpctl set-volume -l 1.5 @DEFAULT_AUDIO_SINK@ 5%+
# change_volume(-5) -> wpctl recibe "5%-"
# change_volume(0) -> no ejecuta set, solo read_volume (una llamada get-volume)
# Reutiliza: run, DEFAULT_SINK, read_volume
def change_volume(delta: int) -> str:
    result = run("wpctl", "set-volume", "-l", f"{abs(delta)}%", DEFAULT_SINK, f"{'+-'[delta < 0]}")
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return read_volume()


@mcp.tool()
# Silencia (True) o reactiva (False) el sink por defecto.
# set_mute(True) -> wpctl set-mute @DEFAULT_AUDIO_SINK@ 1
# set_mute(False) -> wpctl set-mute @DEFAULT_AUDIO_SINK@ 0
# Devuelve "Audio silenced." / "Audio unmuted." o "Error: <stderr>".
# Reutiliza: run, DEFAULT_SINK
def set_mute(muted: bool) -> str:
    result = run("wpctl", "set-mute", DEFAULT_SINK, "1" if muted else "0")
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return "Audio silenced." if muted else "Audio unmuted."

@mcp.tool()
# Devuelve el volumen y estado de silencio en texto legible.
# Reutiliza: read_volume
def volume_status() -> str:
    return read_volume()

@mcp.tool()
# Crea un evento en Google Calendar; si event_time vacío es de todo el día.
# create_event("Reunión","2026-11-21","15:00",30,10)
#   -> gcalcli add --when "2026-11-21 15:00" --duration 30 --reminder 10
# create_event("Cumple","2026-11-21")
#   -> gcalcli add --when "2026-11-21" --allday --duration 1 --reminder 10
# duration se recorta a 1-1440; reminder a 0-1440.
# Validar antes de ejecutar: título no vacío, valid_date(event_date),
# y valid_time(event_time) cuando no esté vacío; si no -> "Error: ..." sin ejecutar.
# Éxito -> "Event '...' created ...". Fallo (returncode != 0) -> "Error: <stderr>".
# Reutiliza: run, CALENDAR, valid_date, valid_time
def create_event(title: str, event_date: str, event_time: str = "", duration_minutes: int = 30, reminder_minutes: int = 10) -> str:
    if not title:
        return "Error: No title."
    if not valid_date(event_date):
        return f"Error: Invalid date '{event_date}'."
    if event_time and not valid_time(event_time):
        return f"Error: Invalid time '{event_time}'."
    duration_minutes = max(1, min(duration_minutes, 1440))
    reminder_minutes = max(0, min(reminder_minutes, 1440))
    event_when = event_date + (" " + event_time if event_time else "")
    result = run(
        "gcalcli",
        "add",
        "--calendar", CALENDAR,
        "--when", event_when,
        "--duration", str(duration_minutes),
        "--reminder", str(reminder_minutes)
    )
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return f"Event '{title}' created on {event_date} at {event_time}."


@mcp.tool()
# Lista eventos de Google Calendar entre dos fechas.
# agenda("2026-11-21") -> to_date por defecto igual a from_date
#   -> gcalcli agenda --tsv --calendar CALENDAR --calendar HOLIDAYS from to
# Devuelve "\n".join(parse_agenda(stdout)) o "No events." si no hay filas.
# Fecha inválida -> "Error: ...". Fallo (returncode != 0) -> "Error: <stderr>".
# Reutiliza: run, CALENDAR, HOLIDAYS, parse_agenda, valid_date
def agenda(from_date: str, to_date: str = "") -> str:
    if not valid_date(from_date):
        return f"Error: Invalid date '{from_date}'."
    if to_date and not valid_date(to_date):
        return f"Error: Invalid date '{to_date}'."
    result = run(
        "gcalcli",
        "agenda",
        "--tsv",
        "--calendar", CALENDAR,
        "--calendar", HOLIDAYS,
        from_date,
        to_date or from_date
    )
    if result.returncode != 0:
        return f"Error: {result.stderr}"
    return "\n".join(parse_agenda(result.stdout)) or "No events."


@mcp.tool()
def shutdown(confirm: bool = False, delay_minutes: int = 1) -> str:
    """Power off the computer. Only use confirm=True after the user explicitly confirmed.
    The shutdown is scheduled (1 to 60 minutes) so it can still be cancelled."""
    if not confirm:
        return "Shutdown not scheduled: ask the user to confirm first."
    delay = max(1, min(delay_minutes, 60))
    result = run("shutdown", "-h", f"+{delay}")
    if result.returncode != 0:
        return f"Shutdown failed: {result.stderr.strip()}"
    return f"Shutdown scheduled in {delay} minute(s). Use cancel_shutdown to abort."


@mcp.tool()
def cancel_shutdown() -> str:
    """Cancel a scheduled shutdown."""
    result = run("shutdown", "-c")
    if result.returncode != 0:
        return f"Could not cancel: {result.stderr.strip()}"
    return "Scheduled shutdown cancelled."


if __name__ == "__main__":
    mcp.run()
