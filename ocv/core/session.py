"""Session files (.ocv): JSON with the source CSV embedded.

Solo E/S y validación del fichero. Qué estado de la app se guarda y cómo
se reaplica sigue en OscilloscopeApp (_build_session_dict/_apply_session).
"""
import json

# Versión del formato:
#   1 = .oscsession con csv_path externo (v1.22 y anteriores, OBSOLETO).
#   2 = .ocv con csv_content embebido (v1.23+). Ruptura limpia: las
#       sesiones v1 NO se cargan en v1.23.
SESSION_VERSION = 2


class SessionError(Exception):
    """Fichero de sesión ilegible o no soportado. str(e) va al usuario."""


def write_session(path, payload):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def read_session(path):
    """Lee y valida formato mínimo y versión. Devuelve el payload.

    Las sesiones de una versión MÁS NUEVA se devuelven igualmente: la UI
    decide si preguntar (ver is_newer)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except FileNotFoundError:
        raise SessionError("Session file not found.")
    except json.JSONDecodeError as e:
        raise SessionError(
            f"The session file is corrupted or not valid JSON:\n\n{e}")
    except Exception as e:
        raise SessionError(f"Could not read file:\n\n{e}")

    if not isinstance(payload, dict) or "version" not in payload:
        raise SessionError(
            "This does not look like a valid .ocv session file.")
    ver = payload.get("version", 0)
    # Ruptura limpia: el formato v1 (.oscsession) ya no se carga.
    if ver < SESSION_VERSION:
        raise SessionError(
            f"This session uses the legacy v{ver} format (.oscsession) "
            f"which is no longer supported.\n\n"
            f"Sessions must be re-created with the current version of "
            f"the app.")
    return payload


def is_newer(payload):
    return payload.get("version", 0) > SESSION_VERSION


def embedded_csv(payload):
    """Devuelve (contenido, nombre) del CSV embebido."""
    csv_content = payload.get("csv_content")
    csv_name = payload.get("csv_name") or "embedded.csv"
    if not isinstance(csv_content, str) or not csv_content:
        raise SessionError("The session does not contain embedded CSV data.")
    return csv_content, csv_name


def serialize_annotations(annotations):
    """Anotaciones en memoria → lista JSON. Las corruptas se saltan."""
    out = []
    for a in annotations:
        try:
            if a.get("type") == "stroke":
                out.append({
                    "type":   "stroke",
                    "color":  a.get("color"),
                    "width":  a.get("width"),
                    "points": [list(p) for p in a.get("points", [])],
                    # ch_idx: canal con el que se creó la anotación; el
                    # voltaje en points[*][1] se referencia a la escala
                    # de ESTE canal, no del canal activo en el momento
                    # de cargar la sesión. Ver _annot_screen_from_v_real.
                    "ch_idx": a.get("ch_idx"),
                })
            elif a.get("type") == "text":
                out.append({
                    "type":    "text",
                    "color":   a.get("color"),
                    "size":    a.get("size"),
                    "pos":     list(a.get("pos", (0, 0))),
                    "content": a.get("content", ""),
                    "ch_idx":  a.get("ch_idx"),
                })
        except Exception:
            # anotación corrupta en memoria → la saltamos
            continue
    return out
