"""Smart CSV loading: separator, header, time column detection."""
import re

import numpy as np


# =============================
# Inteligencia de carga de CSV
# =============================

def _try_float(s):
    """Intenta convertir a float aceptando coma decimal europea. Devuelve None si no."""
    if s is None:
        return None
    s = s.strip().strip('"').strip("'")
    if not s:
        return None
    # Coma decimal → punto (solo si no hay ya punto, para no fastidiar miles)
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _detect_separator(lines, min_cols=2):
    """Detecta el separador más probable (',', ';', '\\t') mirando una
    muestra de líneas. Puntúa por:
      - coherencia (frecuencia relativa de la moda de columnas)
      - cantidad de filas que, al partirse, son 'mayormente numéricas'
      - nº de columnas (pero menos que los dos primeros)
    Esto resuelve el caso europeo (sep=';', decimales con ',')."""
    candidates = [",", ";", "\t"]
    best_sep = ","
    best_score = -1

    for sep in candidates:
        col_counts = []
        numeric_rows = 0
        for line in lines:
            if not line.strip():
                continue
            cells = line.split(sep)
            col_counts.append(len(cells))
            if len(cells) >= min_cols and _is_mostly_numeric_row(cells):
                numeric_rows += 1
        if not col_counts:
            continue
        from collections import Counter
        mode_cols, mode_freq = Counter(col_counts).most_common(1)[0]
        if mode_cols < min_cols:
            continue
        # coherencia: 0..1
        coherence = mode_freq / len(col_counts)
        # Score: priorizamos "filas realmente numéricas" y coherencia
        score = numeric_rows * 10 + coherence * 5 + mode_cols * 0.5
        if score > best_score:
            best_score = score
            best_sep = sep
    return best_sep


def _is_mostly_numeric_row(cells, min_ratio=0.8):
    """Una fila es 'mayormente numérica' si al menos min_ratio de sus celdas
    se parsean como float. Ignoramos celdas vacías."""
    non_empty = [c for c in cells if c.strip()]
    if len(non_empty) < 2:
        return False
    numeric = sum(1 for c in non_empty if _try_float(c) is not None)
    return numeric / len(non_empty) >= min_ratio


def _find_data_start(lines, sep):
    """Devuelve el índice de la PRIMERA línea de datos numéricos.

    Criterio robusto: buscamos al menos 3 líneas consecutivas "mayormente
    numéricas" y tomamos la primera de esa racha. Así descartamos líneas
    sueltas de metadata que contengan números (p.ej. 'Record Length,10000').
    """
    streak_start = None
    streak_len = 0
    REQUIRED = 3

    for i, line in enumerate(lines):
        cells = line.split(sep)
        if _is_mostly_numeric_row(cells):
            if streak_start is None:
                streak_start = i
            streak_len += 1
            if streak_len >= REQUIRED:
                return streak_start
        else:
            streak_start = None
            streak_len = 0

    # Si no hay racha de 3 pero hay alguna numérica, cogemos la primera
    for i, line in enumerate(lines):
        if _is_mostly_numeric_row(line.split(sep)):
            return i
    return -1  # no se encontró nada


_EXACT_ALIASES = {"fs"}


def _extract_metadata(header_lines, sep):
    """Extrae pares clave→valor conocidos de la cabecera.
    Soporta los formatos 'Key,value', 'Key:,value', 'Key:value'."""
    meta = {}
    interesting_keys = {
        # Nombre limpio → lista de aliases conocidos
        "x_increment": ["xincrement", "x increment", "sample interval",
                        "increment", "horizontal increment", "time per sample"],
        "x_start":     ["xstart", "x start", "horizontal start", "start",
                        "trigger offset", "x zero", "xorigin"],
        "sample_rate": ["sample rate", "sampling rate", "samplerate", "fs"],
    }

    for line in header_lines:
        # Intento parsear con el separador detectado
        parts = [p.strip().strip('"').strip("'") for p in line.split(sep)]
        if len(parts) < 2:
            # A veces la metadata viene como "Key: valor" con dos puntos
            if ":" in line:
                k, _, v = line.partition(":")
                parts = [k.strip(), v.strip()]
            else:
                continue

        key_raw = parts[0].lower().rstrip(":").strip()
        # El valor puede estar en parts[1] o más adelante
        val_raw = None
        for p in parts[1:]:
            if p:
                val_raw = p
                break
        if val_raw is None:
            continue

        val = _try_float(val_raw)
        if val is None:
            continue

        # Mapear a clave limpia
        for clean, aliases in interesting_keys.items():
            # Alias cortos solo por igualdad: "fs" casaba con "offset"
            if any(a == key_raw or (a not in _EXACT_ALIASES and a in key_raw)
                   for a in aliases):
                if clean not in meta:   # primera ocurrencia gana
                    meta[clean] = val
                break
    return meta


def _score_time_column(col):
    """Evalúa qué tan bien una columna sirve como eje temporal.

    Devuelve un dict {score: float 0..1, monotonic: bool, dt_median: float,
    cv_robust: float, jumps: int, reason: str}. score alto = candidato fuerte.

    Criterios (todos pesan):
      - monotonía creciente (must); estricta salvo pares peak detect
      - dt mediana > 0 (must)
      - coeficiente de variación ROBUSTO bajo: IQR(diffs)/median(diffs)
        (resiste outliers puntuales mucho mejor que std/mean)
      - número de saltos atípicos (diff > 5x mediana) → no descalifica si pocos
    """
    n = len(col)
    if n < 3:
        return {"score": 0.0, "monotonic": False, "dt_median": 0.0,
                "cv_robust": float("inf"), "jumps": 0,
                "reason": "too few rows"}
    diffs = np.diff(col)
    if np.any(diffs <= 0):
        # Peak detect (R&S y otros): cada instante sale 2 veces (mín, máx).
        # Se acepta creciente no estricto con repeticiones de 2 como máximo;
        # runs más largos = escalera (contador), no tiempo.
        pos = diffs > 0
        runs = np.diff(np.flatnonzero(np.concatenate(([True], pos, [True]))))
        if np.any(diffs < 0) or not pos.any() or runs.max() > 2:
            return {"score": 0.0, "monotonic": False, "dt_median": 0.0,
                    "cv_robust": float("inf"), "jumps": 0,
                    "reason": "not strictly monotonic"}
        diffs = diffs[pos]

    dt_median = float(np.median(diffs))
    if dt_median <= 0:
        return {"score": 0.0, "monotonic": True, "dt_median": dt_median,
                "cv_robust": float("inf"), "jumps": 0,
                "reason": "non-positive median dt"}

    # CV robusto: usamos IQR (Q3-Q1) / mediana. IQR es resistente a outliers.
    q1 = float(np.quantile(diffs, 0.25))
    q3 = float(np.quantile(diffs, 0.75))
    iqr = q3 - q1
    cv_robust = iqr / dt_median if dt_median > 0 else float("inf")

    # Cuántas diffs son anomalías gruesas (>5x la mediana)
    jumps = int(np.sum(diffs > 5.0 * dt_median))

    # Score: 1.0 si cv_robust ≈ 0 y sin jumps, decae conforme empeora.
    # cv_robust=0 (perfectamente uniforme) → 1.0
    # cv_robust=0.05 → ~0.95
    # cv_robust=0.5 → ~0.5
    # cv_robust=1.0 o más → bajo
    score = 1.0 / (1.0 + 5.0 * cv_robust)
    # Penalizar jumps: cada jump resta 0.05, hasta -0.5
    score *= max(0.5, 1.0 - 0.05 * jumps)
    score = max(0.0, min(1.0, score))

    return {
        "score":     score,
        "monotonic": True,
        "dt_median": dt_median,
        "cv_robust": cv_robust,
        "jumps":     jumps,
        "reason":    f"cv_robust={cv_robust:.3f}, jumps={jumps}",
    }


def _read_csv_smart(filename):
    """Carga un CSV ignorando cabeceras de metadata y devuelve un dict:
        {
          'time':            np.array  (None si no había columna temporal clara),
          'columns':         lista de np.arrays (el resto de canales),
          'col_names':       lista de strings con los nombres detectados,
          'all_columns':     TODAS las columnas como arrays (incluida la que
                              se eligió como tiempo, si la hubo). Útil para
                              que un diálogo "elegir columna de tiempo" pueda
                              re-mapear sin volver a parsear el fichero.
          'all_col_names':   nombres de todas las columnas.
          'time_col_index':  índice (en all_columns) de la columna usada como
                              tiempo, o None si no se eligió ninguna.
          'time_confidence': 'high' | 'medium' | 'low' | 'none'.
                              'high'   → eje temporal totalmente fiable.
                              'medium' → mejor candidato pero con dudas.
                              'low'    → hay una columna que parece tiempo
                                          pero la heurística no está segura.
                              'none'   → ninguna columna parece tiempo.
          'time_scores':     lista de dicts con el score de cada columna
                              (en orden de all_columns).
          'metadata':        dict con XStart, XIncrement, etc. si los había.
          'n_metadata_lines': cuántas líneas se saltaron.
          'separator':       el separador detectado.
          'brand':           marca de scope detectada, si la hay.
        }
    Lanza ValueError con mensaje descriptivo si no consigue interpretar el fichero.
    """
    # Leer el fichero completo como texto (los CSV de scope no son enormes)
    with open(filename, "r", encoding="utf-8", errors="replace") as f:
        raw_lines = f.readlines()

    if not raw_lines:
        raise ValueError("El fichero está vacío.")

    # Quitar BOM si aparece en la primera línea
    if raw_lines[0].startswith("\ufeff"):
        raw_lines[0] = raw_lines[0].lstrip("\ufeff")

    # Limpiamos saltos de línea
    lines = [l.rstrip("\r\n") for l in raw_lines]

    # 1) Detectar separador usando las primeras 60 líneas
    sample = lines[:60]
    sep = _detect_separator(sample)

    # 2) Localizar inicio de datos
    data_start = _find_data_start(lines, sep)
    if data_start < 0:
        raise ValueError("No se han encontrado datos numéricos en el fichero.")

    header_lines = lines[:data_start]
    data_lines = lines[data_start:]

    # 3) Intentar leer la línea inmediatamente anterior como nombres de columna
    col_names = None
    if data_start > 0:
        last_header = header_lines[-1].split(sep)
        # Si las celdas parecen nombres (texto + pocas numéricas), usar
        non_num = sum(1 for c in last_header if _try_float(c) is None and c.strip())
        if non_num >= max(1, len(last_header) // 2):
            col_names = [c.strip().strip('"').strip("'") for c in last_header]

    # 4) Extraer metadata (Start, Increment, etc.)
    metadata = _extract_metadata(header_lines, sep)

    # 5) Parsear los datos con numpy. Primero en memoria para tolerar comas decimales.
    n_rows = len(data_lines)
    n_cols = len(data_lines[0].split(sep))

    # Descartar columnas vacías al final (Tek y otros meten ",," al final)
    parsed = np.full((n_rows, n_cols), np.nan, dtype=float)
    for i, line in enumerate(data_lines):
        cells = line.split(sep)
        for j, c in enumerate(cells[:n_cols]):
            val = _try_float(c)
            if val is not None:
                parsed[i, j] = val

    # Quitar columnas que son todo NaN (celdas vacías estructurales)
    col_mask = ~np.all(np.isnan(parsed), axis=0)
    parsed = parsed[:, col_mask]
    if col_names:
        col_names = [col_names[j] for j in range(len(col_names))
                     if j < len(col_mask) and col_mask[j]]

    # Filas enteramente NaN fuera
    row_mask = ~np.all(np.isnan(parsed), axis=1)
    parsed = parsed[row_mask]

    if parsed.shape[0] < 2 or parsed.shape[1] < 1:
        raise ValueError("Datos insuficientes tras el parseo.")

    # Garantizar nombres
    if not col_names or len(col_names) != parsed.shape[1]:
        col_names = [f"col{j}" for j in range(parsed.shape[1])]

    all_columns = [parsed[:, j] for j in range(parsed.shape[1])]
    all_col_names = list(col_names)

    # 6) Scorear TODAS las columnas como candidatas a tiempo. La columna 0
    #    NO tiene prioridad por posición — solo por su score.
    scores = [_score_time_column(c) for c in all_columns]
    # Mejor candidato
    best_j = int(np.argmax([s["score"] for s in scores])) if scores else -1
    best_score = scores[best_j]["score"] if best_j >= 0 else 0.0

    # 7) Decidir confidence con umbrales explícitos:
    #    high   ≥ 0.85   → no preguntar al usuario
    #    medium 0.55–0.85 → preguntar al usuario (probable pero verificar)
    #    low    0.25–0.55 → preguntar obligatoriamente
    #    none   < 0.25    → ofrecer reconstruir vía metadata o pedir manual
    if best_score >= 0.85 and scores[best_j]["monotonic"]:
        confidence = "high"
    elif best_score >= 0.55:
        confidence = "medium"
    elif best_score >= 0.25:
        confidence = "low"
    else:
        confidence = "none"

    time = None
    data_cols = []
    data_names = []
    time_col_index = None

    if confidence == "high":
        time = all_columns[best_j]
        time_col_index = best_j
        for j in range(parsed.shape[1]):
            if j == best_j:
                continue
            data_cols.append(all_columns[j])
            data_names.append(all_col_names[j])
    elif confidence == "none":
        # No hay candidato fiable. Probar metadata XStart/XIncrement.
        xinc = metadata.get("x_increment")
        xstart = metadata.get("x_start", 0.0)
        if xinc is not None and xinc > 0:
            time = xstart + np.arange(parsed.shape[0]) * xinc
            for j in range(parsed.shape[1]):
                # Saltamos la primera si parece índice 0,1,2,...
                if j == 0 and np.allclose(parsed[:, j],
                                          np.arange(parsed.shape[0]),
                                          atol=0.5):
                    continue
                data_cols.append(all_columns[j])
                data_names.append(all_col_names[j])
        else:
            # Sin info temporal y sin candidato: dejamos time=None y devolvemos
            # todas las columnas como datos. El caller decidirá si abrir el
            # diálogo de selección o pedir paso de muestreo.
            for j in range(parsed.shape[1]):
                data_cols.append(all_columns[j])
                data_names.append(all_col_names[j])
    else:
        # medium / low: PROVISIONALMENTE asignamos el mejor candidato y
        # marcamos para que el caller pregunte al usuario.
        time = all_columns[best_j]
        time_col_index = best_j
        for j in range(parsed.shape[1]):
            if j == best_j:
                continue
            data_cols.append(all_columns[j])
            data_names.append(all_col_names[j])

    # Detectar marca del osciloscopio buscando pistas en la cabecera
    brand = _detect_scope_brand(header_lines)

    return {
        "time":            time,
        "columns":         data_cols,
        "col_names":       data_names,
        "all_columns":     all_columns,
        "all_col_names":   all_col_names,
        "time_col_index":  time_col_index,
        "time_confidence": confidence,
        "time_scores":     scores,
        "metadata":        metadata,
        "n_metadata_lines": data_start,
        "separator":       sep,
        "brand":           brand,
    }


# Marcas de osciloscopio y cómo reconocerlas en la cabecera.
# Dos niveles: primero el nombre de marca (fiable), luego el modelo.
# Todo con límites de palabra: subcadenas sueltas daban falsos
# positivos ("rte" en "converted", "ds2" en "SDS2104").
_SCOPE_BRAND_NAMES = [
    ("Tektronix",       r"tektronix|\btek\b"),
    ("Keysight",        r"keysight|agilent|infiniivision|infiniium"),
    ("Rohde & Schwarz", r"rohde|schwarz|\br&s\b"),
    ("Rigol",           r"rigol"),
    ("Siglent",         r"siglent"),
    ("Hantek",          r"hantek"),
    ("LeCroy",          r"lecroy|teledyne"),
    ("Pico",            r"picoscope|\bpico\b"),
    ("OWON",            r"owon"),
]

# MSO + 4 dígitos (MSO4054...) existe en Tek y Rigol: ambiguo, no se
# reconoce. MSO + 2 dígitos (MSO46, MSO58B) es Tek series 2/4/5/6.
_SCOPE_MODEL_PATTERNS = [
    ("Tektronix",       r"\b(?:tds|dpo|mdo)\s?\d|\bmso[2-6]\d[a-z]?\b"),
    ("Keysight",        r"\b(?:dso|mso)-x\b"),
    ("Rohde & Schwarz", r"\brt[bmoea]\s?\d"),
    ("Rigol",           r"\bds[1-9]\d{3}|\bdho\d{3,4}"),
    ("Siglent",         r"\bsds\s?\d"),
]


def _detect_scope_brand(header_lines):
    """Escanea las primeras líneas de cabecera buscando pistas de marca.
    Devuelve el nombre comercial ("Tektronix", "Rohde & Schwarz"…) o None."""
    if not header_lines:
        return None
    # Unir las primeras líneas para hacer una búsqueda plana
    blob = " ".join(header_lines[:30]).lower()
    for table in (_SCOPE_BRAND_NAMES, _SCOPE_MODEL_PATTERNS):
        for brand, pattern in table:
            if re.search(pattern, blob):
                return brand
    return None
