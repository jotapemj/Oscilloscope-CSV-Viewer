"""SI-prefixed number formatting and parsing."""


# =============================
# Unidades de canal (v1.29)
# =============================
# Lista de unidades seleccionables por canal. "V" es el default (igual que el
# comportamiento histórico). El símbolo "—" representa "sin unidad" (adimensional).
# Layout 5x5 en el picker dialog: 24 unidades + 1 hueco vacío.
CHANNEL_UNITS = [
    "V",  "A",   "W",  "VA",  "var",   # eléctricas
    "J",  "Ω",   "Hz", "%",   "dB",    # otras eléctricas / generales
    "N",  "kgf", "N·m","RPM", "T",     # mecánicas
    "°C", "°F",  "K",  "Pa",  "PSI",   # térmicas / presión
    "bar","lux", "lm", "—",            # presión / luz / adimensional
]
DEFAULT_UNIT = "V"

# Unidades que NO aceptan prefijo SI (no tiene sentido "k°C", "MdB", etc.).
# format_si las muestra con formato directo sin prefijo.
_NO_SI_PREFIX_UNITS = {"°C", "°F", "dB", "%", "PSI", "RPM", "lux", "lm", "—"}


def format_si(value, unit, sig_figs=6):
    """Formatea un valor con prefijo SI. `sig_figs` controla las cifras
    significativas (default 6 = comportamiento legacy). Para xticks/yticks
    usamos sig_figs=3 que da etiquetas cortas estilo scope real:
      "33.3 µs"  no  "33.3332 µs"
      "1.5 ms"   no  "1.50000 ms"
      "120 kHz"  no  "120000 Hz"
    """
    # Unidad adimensional: solo el número, sin sufijo.
    if unit == "—" or unit == "":
        if abs(value) < 1e-15:
            return "0"
        fmt = f"{{:.{sig_figs}g}}"
        return fmt.format(value)

    # Tratamiento de cero (ruido numérico)
    if abs(value) < 1e-15:
        return f"0 {unit}"

    # Unidades sin prefijo SI (°C, °F, dB, %, PSI, RPM, lux, lm):
    # mostrar con formato directo sin escalar a k/M/m/µ/etc.
    if unit in _NO_SI_PREFIX_UNITS:
        # % conserva el formato histórico con 2 decimales.
        if unit == "%":
            return f"{value:.2f} {unit}"
        fmt = f"{{:.{sig_figs}g}}"
        return f"{fmt.format(value)} {unit}"

    prefixes = [
        (1e9, "G"),
        (1e6, "M"),
        (1e3, "k"),
        (1, ""),
        (1e-3, "m"),
        (1e-6, "µ"),
        (1e-9, "n"),
        (1e-12, "p"),
    ]

    # Redondear ANTES de elegir prefijo: 999.96 µs con 3 cifras es 1 ms,
    # no "1e+03 µs"
    value = float(f"{value:.{sig_figs}g}")
    for scale, prefix in prefixes:
        if abs(value) >= scale:
            scaled = value / scale
            # Format con sig_figs cifras significativas
            fmt = f"{{:.{sig_figs}g}}"
            return f"{fmt.format(scaled)} {prefix}{unit}"

    fmt = f"{{:.{sig_figs}g}}"
    return f"{fmt.format(value)} {unit}"


def format_tick(value, step, unit, sig_figs=3):
    """Etiqueta de tick: redondea a step/1000 antes de formatear. Así la
    precisión va con la grilla y un resto de 26 ns con 1 ms/div es '0 s'."""
    q = step * 1e-3
    if q > 0:
        value = round(value / q) * q
    return format_si(value, unit, sig_figs=sig_figs)


# Prefijos SI para parsear strings. IMPORTANTE: case sensitive.
#   - m = mili (1e-3), M = mega (1e6)
#   - u (ASCII) y µ = micro (1e-6)
_SI_PARSE_PREFIXES = {
    "T": 1e12,
    "G": 1e9,
    "M": 1e6,
    "k": 1e3,
    "":  1.0,
    "m": 1e-3,
    "u": 1e-6,
    "µ": 1e-6,
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
}


def parse_si(text, unit=""):
    """Convierte un string con prefijo SI a float.

    Acepta: '240k', '240 k', '240 kHz', '1.5M', '20 us', '0.001', '-3.3', '.5m'
    CASE SENSITIVE en prefijos (m = mili, M = mega).
    La unidad final es opcional: si se pasa `unit`, se tolera al final.
    Devuelve None si no se puede parsear.
    """
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None

    # Quitar la unidad al final si se proporcionó (p. ej. 'Hz', 's', 'V').
    # Tolerante con mayúsculas/minúsculas para la unidad solo (no para el prefijo).
    if unit:
        u_lower = unit.lower()
        if s.lower().endswith(u_lower):
            s = s[: -len(unit)].rstrip()

    # Separar número del posible prefijo. El número puede llevar signo,
    # punto decimal y notación exponencial.
    # Recorremos desde el final quitando caracteres hasta quedarnos con un número válido.
    # Más simple: buscar el split entre dígitos/e/E/.,+/- y el resto.
    import re
    m = re.match(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([a-zA-ZµT]?)\s*$", s)
    if not m:
        return None

    num_str, prefix = m.group(1), m.group(2)
    # El prefijo debe estar EXACTAMENTE en la tabla (case sensitive)
    if prefix not in _SI_PARSE_PREFIXES:
        return None

    try:
        value = float(num_str) * _SI_PARSE_PREFIXES[prefix]
    except ValueError:
        return None
    return value
