"""Automatic waveform measurements (amplitude, time, phase)."""
import numpy as np


# =============================
# Measurement helpers
# =============================

def _top_base_level(v):
    """Estima top/base levels usando histograma bimodal.

    Es lo que hacen los osciloscopios reales: buscan los dos niveles más
    probables (el "1" lógico y el "0" lógico) en vez de usar min/max, que
    se ven afectados por overshoots y ruido.

    Devuelve (top, base). Si la señal no es bimodal (p.ej. una sinusoide
    pura), top/base acaban siendo ~max/~min, que también es razonable.
    """
    if len(v) < 2:
        return None, None
    vmin, vmax = float(np.min(v)), float(np.max(v))
    if vmax == vmin:
        return vmax, vmin

    # Histograma con resolución decente
    nbins = min(256, max(32, len(v) // 20))
    hist, edges = np.histogram(v, bins=nbins)
    centers = 0.5 * (edges[:-1] + edges[1:])

    # Dividir en mitad superior e inferior y coger la moda de cada una
    mid = (vmax + vmin) / 2.0
    upper = centers >= mid
    lower = ~upper

    if np.any(upper) and np.sum(hist[upper]) > 0:
        top = float(centers[upper][np.argmax(hist[upper])])
    else:
        top = vmax

    if np.any(lower) and np.sum(hist[lower]) > 0:
        base = float(centers[lower][np.argmax(hist[lower])])
    else:
        base = vmin

    return top, base


def _mid_level(v):
    """Nivel medio (50%) entre top y base."""
    top, base = _top_base_level(v)
    if top is None or base is None:
        return None
    return (top + base) / 2.0


def _interp_crossing(t, v, i, level):
    """Interpolación lineal del instante de cruce entre la muestra i y i+1."""
    v1, v2 = v[i], v[i + 1]
    if v2 == v1:
        return t[i]
    frac = (level - v1) / (v2 - v1)
    return t[i] + frac * (t[i + 1] - t[i])


def _find_crossings(t, v, level, edge="rising"):
    """Devuelve los instantes (interpolados) en los que la señal cruza `level`.

    edge: "rising" (de menos a más), "falling" (de más a menos), o "both".
    """
    if len(v) < 2:
        return np.array([])

    above = v >= level
    # Detectar transiciones
    diff = np.diff(above.astype(np.int8))
    if edge == "rising":
        idxs = np.where(diff == 1)[0]
    elif edge == "falling":
        idxs = np.where(diff == -1)[0]
    else:
        idxs = np.where(diff != 0)[0]

    return np.array([_interp_crossing(t, v, i, level) for i in idxs])


def _find_trigger_crossings(t, v, level, edge="rising", hyst_frac=0.05):
    """Cruces de trigger con histéresis, como un osciloscopio: un flanco
    de subida solo cuenta si antes la señal bajó de level - h (y al revés
    para bajada). Evita flancos falsos por ruido o por exports en peak
    detect (pares mín/máx en el mismo instante).

    h = hyst_frac · rango robusto (p1..p99), limitado a la mitad de la
    distancia entre level y el extremo de armado para no perder eventos
    con el nivel pegado al suelo/techo. edge: "rising" | "falling".
    """
    v = np.asarray(v, dtype=float)
    n = len(v)
    if n < 2:
        return np.array([])
    p_lo, p_hi = np.percentile(v, [1, 99])
    h = hyst_frac * (p_hi - p_lo)
    if edge == "rising":
        h = min(h, 0.5 * max(0.0, level - float(v.min())))
        set_mask, reset_mask = v >= level, v < level - h
    else:
        h = min(h, 0.5 * max(0.0, float(v.max()) - level))
        set_mask, reset_mask = v >= level + h, v < level
    if h <= 0:
        return _find_crossings(t, v, level, edge=edge)

    # Schmitt vectorizado: estado = último set/reset visto
    ev = np.full(n, -1, dtype=np.int8)
    ev[set_mask] = 1
    ev[reset_mask] = 0
    last = np.where(ev >= 0, np.arange(n), 0)
    np.maximum.accumulate(last, out=last)
    state = ev[last]
    # Sin evento previo: estado según el lado de level
    state[state < 0] = 1 if v[0] >= level else 0
    d = np.diff(state.astype(np.int8))
    # Índice previo al cambio: v[i] y v[i+1] quedan a ambos lados de level
    idxs = np.where(d == (1 if edge == "rising" else -1))[0]
    return np.array([_interp_crossing(t, v, i, level) for i in idxs])


# =============================
# Basic / Horizontal measurements
# =============================

def meas_peak_to_peak(v):
    if len(v) == 0:
        return None
    return float(np.max(v) - np.min(v))


def meas_period(t, v):
    """Periodo medio usando cruces ascendentes por el mid-level.

    Referenciar al mid-level (y no al 0 V) es clave: funciona tanto con señales
    AC como con señales con offset DC (p.ej. lógica 0-3.3V).
    """
    mid = _mid_level(v)
    if mid is None:
        return None
    cr = _find_crossings(t, v, mid, edge="rising")
    if len(cr) < 2:
        return None
    return float(np.mean(np.diff(cr)))


def meas_frequency(t, v):
    p = meas_period(t, v)
    if p is None or p == 0:
        return None
    return 1.0 / p


def _edge_time(t, v, edge):
    """Tiempo promedio de transición 10%→90% (rising) o 90%→10% (falling)
    referenciado a top/base reales (no min/max).

    Detecta TODOS los flancos cualificados y promedia. Un flanco solo cuenta
    si la señal realmente atraviesa tanto el umbral del 10% como el del 90%
    dentro de la misma transición.
    """
    if len(v) < 2:
        return None

    top, base = _top_base_level(v)
    if top is None or base is None or top == base:
        return None

    ampl = top - base
    v10 = base + 0.1 * ampl
    v90 = base + 0.9 * ampl

    times = []

    if edge == "rising":
        # Buscar todos los cruces ascendentes por v10 y v90
        cr10 = _find_crossings(t, v, v10, edge="rising")
        cr90 = _find_crossings(t, v, v90, edge="rising")
        for t10 in cr10:
            # Emparejar con el siguiente cruce por v90 que ocurra después
            later = cr90[cr90 > t10]
            if len(later) == 0:
                continue
            t90 = later[0]
            # Validar que entre t10 y t90 no haya más cruces ascendentes
            # por v10 (si los hubiera, es que se ha ido hacia abajo y ha
            # vuelto a subir: no es un flanco limpio).
            if np.any((cr10 > t10) & (cr10 < t90)):
                continue
            times.append(t90 - t10)
    else:  # falling
        cr10 = _find_crossings(t, v, v10, edge="falling")
        cr90 = _find_crossings(t, v, v90, edge="falling")
        for t90 in cr90:
            later = cr10[cr10 > t90]
            if len(later) == 0:
                continue
            t10 = later[0]
            if np.any((cr90 > t90) & (cr90 < t10)):
                continue
            times.append(t10 - t90)

    if not times:
        return None
    return float(np.mean(times))


def meas_rise_time(t, v):
    return _edge_time(t, v, "rising")


def meas_fall_time(t, v):
    return _edge_time(t, v, "falling")


# =============================
# Vertical measurements
# =============================

def meas_amplitude(v):
    """Amplitud = top_level - base_level (diferente de Peak-Peak cuando hay overshoot)."""
    top, base = _top_base_level(v)
    if top is None or base is None:
        return None
    return top - base


def meas_top_level(v):
    top, _ = _top_base_level(v)
    return top


def meas_base_level(v):
    _, base = _top_base_level(v)
    return base


def meas_peak_positive(v):
    """Valor máximo absoluto (Peak+)."""
    if len(v) == 0:
        return None
    return float(np.max(v))


def meas_peak_negative(v):
    """Valor mínimo absoluto (Peak−)."""
    if len(v) == 0:
        return None
    return float(np.min(v))


def meas_positive_overshoot(v):
    """Overshoot positivo en %:   (max - top) / amplitud · 100."""
    top, base = _top_base_level(v)
    if top is None or base is None or top == base:
        return None
    vmax = float(np.max(v))
    return (vmax - top) / (top - base) * 100.0


def meas_negative_overshoot(v):
    """Overshoot negativo en %:   (base - min) / amplitud · 100."""
    top, base = _top_base_level(v)
    if top is None or base is None or top == base:
        return None
    vmin = float(np.min(v))
    return (base - vmin) / (top - base) * 100.0


def meas_rms(v):
    if len(v) == 0:
        return None
    return float(np.sqrt(np.mean(v.astype(float) ** 2)))


# =============================
# Extended time measurements
# =============================

def _pulse_widths(t, v, polarity="positive"):
    """Devuelve array con los anchos de pulso de la polaridad indicada.
    Un pulso positivo: rising → falling por mid-level. Negativo al revés."""
    mid = _mid_level(v)
    if mid is None:
        return np.array([])
    rises = _find_crossings(t, v, mid, edge="rising")
    falls = _find_crossings(t, v, mid, edge="falling")
    if len(rises) == 0 or len(falls) == 0:
        return np.array([])

    widths = []
    if polarity == "positive":
        # Por cada rise, emparejar con la siguiente fall
        for r in rises:
            nxt = falls[falls > r]
            if len(nxt):
                widths.append(nxt[0] - r)
    else:
        for f in falls:
            nxt = rises[rises > f]
            if len(nxt):
                widths.append(nxt[0] - f)
    return np.array(widths)


def meas_positive_width(t, v):
    w = _pulse_widths(t, v, "positive")
    return float(np.mean(w)) if len(w) else None


def meas_negative_width(t, v):
    w = _pulse_widths(t, v, "negative")
    return float(np.mean(w)) if len(w) else None


def _slew_rates(t, v, edge="rising"):
    """Slew rate medio = (90% - 10%) / rise_time para cada flanco detectado.
    Devuelve V/s (positivo para rising, negativo para falling)."""
    top, base = _top_base_level(v)
    if top is None or top == base:
        return np.array([])
    amp = top - base
    lo = base + 0.1 * amp
    hi = base + 0.9 * amp
    cr_lo = _find_crossings(t, v, lo, edge=edge)
    cr_hi = _find_crossings(t, v, hi, edge=edge)
    rates = []
    if edge == "rising":
        for c_lo in cr_lo:
            nxt = cr_hi[cr_hi > c_lo]
            if len(nxt):
                dt = nxt[0] - c_lo
                if dt > 0:
                    rates.append((hi - lo) / dt)
    else:
        for c_hi in cr_hi:
            nxt = cr_lo[cr_lo > c_hi]
            if len(nxt):
                dt = nxt[0] - c_hi
                if dt > 0:
                    rates.append(-(hi - lo) / dt)
    return np.array(rates)


def meas_rising_slew_rate(t, v):
    r = _slew_rates(t, v, "rising")
    return float(np.mean(r)) if len(r) else None


def meas_falling_slew_rate(t, v):
    r = _slew_rates(t, v, "falling")
    return float(np.mean(r)) if len(r) else None


def meas_positive_duty(t, v):
    """Duty cycle positivo = pos_width / period (en %)."""
    pw = meas_positive_width(t, v)
    per = meas_period(t, v)
    if pw is None or per is None or per == 0:
        return None
    return float(pw / per * 100.0)


def meas_negative_duty(t, v):
    pw = meas_negative_width(t, v)
    per = meas_period(t, v)
    if pw is None or per is None or per == 0:
        return None
    return float(pw / per * 100.0)


def meas_high_time(t, v):
    """Tiempo total con señal > mid level, dentro de la ventana."""
    if len(v) < 2:
        return None
    mid = _mid_level(v)
    if mid is None:
        return None
    # Integración simple: fracción de muestras > mid × duración total
    above = v >= mid
    dt = np.diff(t)
    # Masa de 'above' en intervalos (promedio entre muestras vecinas)
    mass = (above[:-1].astype(float) + above[1:].astype(float)) * 0.5
    return float(np.sum(mass * dt))


def meas_low_time(t, v):
    if len(v) < 2:
        return None
    mid = _mid_level(v)
    if mid is None:
        return None
    below = v < mid
    dt = np.diff(t)
    mass = (below[:-1].astype(float) + below[1:].astype(float)) * 0.5
    return float(np.sum(mass * dt))


def meas_time_outside_level(t, v):
    """Tiempo total fuera de la banda [base, top] (overshoots + undershoots).
    Útil para detectar ringing / violaciones de niveles lógicos."""
    if len(v) < 2:
        return None
    top, base = _top_base_level(v)
    if top is None:
        return None
    outside = (v > top) | (v < base)
    dt = np.diff(t)
    mass = (outside[:-1].astype(float) + outside[1:].astype(float)) * 0.5
    return float(np.sum(mass * dt))


# =============================
# Phase (dual-channel) — via Hilbert
# =============================

def meas_phase_hilbert(t, v1, v2):
    """Diferencia de fase entre v1 y v2 en grados.
    Usa la fase instantánea de la Hilbert transform; devuelve la mediana
    para robustez. Rango: (-180, 180]."""
    if len(v1) < 4 or len(v2) < 4:
        return None
    try:
        from scipy.signal import hilbert
    except ImportError:
        # Fallback a cruces por cero si scipy no está
        return _meas_phase_crossings(t, v1, v2)
    # Quitar DC
    s1 = v1 - np.mean(v1)
    s2 = v2 - np.mean(v2)
    a1 = hilbert(s1)
    a2 = hilbert(s2)
    phase_diff = np.angle(a1 * np.conj(a2))  # fase de v1 - fase de v2
    # Desenvolver no aporta para la mediana; usar mediana del argumento
    med = float(np.median(phase_diff))
    deg = np.degrees(med)
    # Normalizar a (-180, 180]
    while deg > 180:  deg -= 360
    while deg <= -180: deg += 360
    return deg


def _meas_phase_crossings(t, v1, v2):
    """Fallback: por cruces por cero de señales centradas."""
    m1 = _mid_level(v1)
    m2 = _mid_level(v2)
    if m1 is None or m2 is None:
        return None
    cr1 = _find_crossings(t, v1, m1, edge="rising")
    cr2 = _find_crossings(t, v2, m2, edge="rising")
    if len(cr1) < 2 or len(cr2) < 1:
        return None
    period = float(np.mean(np.diff(cr1)))
    if period == 0:
        return None
    # Para cada rise de v1, encontrar el rise de v2 más próximo
    diffs = []
    for r in cr1:
        if len(cr2) == 0:
            break
        idx = int(np.argmin(np.abs(cr2 - r)))
        diffs.append(cr2[idx] - r)
    if not diffs:
        return None
    delay = float(np.median(diffs))
    deg = delay / period * 360.0
    while deg > 180:   deg -= 360
    while deg <= -180: deg += 360
    return deg


AMPLITUDE_MEASUREMENTS = {
    "Peak-Peak":          ("V", lambda t, v: meas_peak_to_peak(v)),
    "Amplitude":          ("V", lambda t, v: meas_amplitude(v)),
    "Top Level":          ("V", lambda t, v: meas_top_level(v)),
    "Base Level":         ("V", lambda t, v: meas_base_level(v)),
    "Peak +":             ("V", lambda t, v: meas_peak_positive(v)),
    "Peak -":             ("V", lambda t, v: meas_peak_negative(v)),
    "Positive Overshoot": ("%", lambda t, v: meas_positive_overshoot(v)),
    "Negative Overshoot": ("%", lambda t, v: meas_negative_overshoot(v)),
    "RMS":                ("V", lambda t, v: meas_rms(v)),
}


TIME_MEASUREMENTS = {
    "Period":             ("s",   meas_period),
    "Frequency":          ("Hz",  meas_frequency),
    "Rise Time":          ("s",   meas_rise_time),
    "Fall Time":          ("s",   meas_fall_time),
    "Positive Width":     ("s",   meas_positive_width),
    "Negative Width":     ("s",   meas_negative_width),
    "Rising Slew Rate":   ("V/s", meas_rising_slew_rate),
    "Falling Slew Rate":  ("V/s", meas_falling_slew_rate),
    "Positive Duty":      ("%",   meas_positive_duty),
    "Negative Duty":      ("%",   meas_negative_duty),
    "High Time":          ("s",   meas_high_time),
    "Low Time":           ("s",   meas_low_time),
    "Time Outside Level": ("s",   meas_time_outside_level),
    # Phase necesita dos canales → marcador especial "dual"
    "Phase":              ("°",   ("dual", meas_phase_hilbert)),
}


# Descripciones que aparecen en el panel de preview al seleccionar cada medida
MEASUREMENT_DESCRIPTIONS = {
    "Peak-Peak":
        "Peak-Peak is the absolute difference between the maximum and minimum "
        "sample values. It includes any overshoot and noise.",
    "Amplitude":
        "Amplitude is the difference between the Top value and the Base value. "
        "Unlike Peak-Peak, it ignores overshoot and noise by using the most "
        "common high/low levels in the signal.",
    "Top Level":
        "Top Level is the statistically most common value of the upper portion "
        "of the signal (the logic '1' level for digital signals).",
    "Base Level":
        "Base Level is the statistically most common value of the lower portion "
        "of the signal (the logic '0' level for digital signals).",
    "Peak +":
        "Peak+ is the absolute maximum sample value in the measurement window.",
    "Peak -":
        "Peak- is the absolute minimum sample value in the measurement window.",
    "Positive Overshoot":
        "Positive Overshoot is the amount by which the signal exceeds its Top "
        "level after a rising edge, expressed as a percentage of the Amplitude:"
        "  (Max - Top) / Amplitude × 100%.",
    "Negative Overshoot":
        "Negative Overshoot is the amount by which the signal falls below its "
        "Base level after a falling edge, expressed as a percentage of the "
        "Amplitude:  (Base - Min) / Amplitude × 100%.",
    "RMS":
        "RMS (Root Mean Square) is the square root of the mean of the squared "
        "sample values. For a sinusoid, RMS = Amplitude / √2.",
    "Period":
        "Period is the time required to complete one cycle. It is measured as "
        "the time between two adjacent rising crossings of the Mid reference "
        "level (the average of Top and Base).",
    "Frequency":
        "Frequency is the reciprocal of Period: F = 1/T. It tells you how many "
        "cycles the signal completes per second.",
    "Rise Time":
        "Rise Time is the time the signal takes to go from 10% to 90% of its "
        "Amplitude on a rising edge. Measured on each edge and averaged.",
    "Fall Time":
        "Fall Time is the time the signal takes to go from 90% to 10% of its "
        "Amplitude on a falling edge. Measured on each edge and averaged.",
    "Positive Width":
        "Positive Width is the time between a rising and the next falling "
        "crossing of the Mid reference level. Averaged over all pulses.",
    "Negative Width":
        "Negative Width is the time between a falling and the next rising "
        "crossing of the Mid reference level. Averaged over all pulses.",
    "Rising Slew Rate":
        "Rising Slew Rate is the average slope (V/s) between the 10% and 90% "
        "amplitude points of rising edges.",
    "Falling Slew Rate":
        "Falling Slew Rate is the average slope (V/s) between the 90% and 10% "
        "amplitude points of falling edges. Reported as a negative value.",
    "Positive Duty":
        "Positive Duty Cycle = Positive Width / Period × 100%. Fraction of "
        "the period the signal spends above the Mid level.",
    "Negative Duty":
        "Negative Duty Cycle = Negative Width / Period × 100%.",
    "High Time":
        "Total time within the measurement window that the signal stays "
        "above the Mid reference level.",
    "Low Time":
        "Total time within the measurement window that the signal stays "
        "below the Mid reference level.",
    "Time Outside Level":
        "Total time the signal spends outside the [Base, Top] band. Useful "
        "to detect ringing, overshoot and undershoot violations.",
    "Phase":
        "Phase difference (in degrees) between the selected channel and a "
        "reference channel, estimated from the analytic signal (Hilbert "
        "transform). Range: (-180°, 180°]. Requires two channels.",
}


# Tabla combinada para lookups rápidos desde la UI/cálculos
ALL_MEASUREMENTS = {}
ALL_MEASUREMENTS.update(AMPLITUDE_MEASUREMENTS)
ALL_MEASUREMENTS.update(TIME_MEASUREMENTS)

# Aliases retrocompat (el código antiguo los usa)
BASIC_MEASUREMENTS = TIME_MEASUREMENTS
VERTICAL_MEASUREMENTS = AMPLITUDE_MEASUREMENTS
