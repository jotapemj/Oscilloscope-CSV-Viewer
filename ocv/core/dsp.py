"""Signal helpers: min/max decimation, FFT windows and Y scales."""
import numpy as np


# -----------------------------
# Peak detect: instantes repetidos
# -----------------------------
def repeated_time_index(t):
    """Si `t` repite instantes (export peak detect: pares mín/máx con el
    mismo tiempo), devuelve (t_unicos, inverse) con t == t_unicos[inverse].
    None si `t` es estrictamente creciente (caso normal, sin coste)."""
    t = np.asarray(t, dtype=float)
    if len(t) < 2 or np.all(np.diff(t) > 0):
        return None
    return np.unique(t, return_inverse=True)


def merge_repeated_times(t, v):
    """Un valor por instante (media de las muestras que lo comparten).
    Para lo que necesita dt > 0: FFT, derivada. Devuelve t y v tal cual
    si no hay repetidos."""
    idx = repeated_time_index(t)
    if idx is None:
        return t, v
    tu, inv = idx
    v = np.asarray(v, dtype=float)
    return tu, np.bincount(inv, weights=v) / np.bincount(inv)


# -----------------------------
# Decimation min/max para render
# -----------------------------
# Umbral: si hay más de POINTS_PER_PIXEL * ancho_pixeles puntos visibles,
# aplicamos decimation min/max. Con 2 (min y max por bucket) ya conservamos
# picos; subir esto pinta más pero da más detalle fino.
POINTS_PER_PIXEL = 4


def minmax_decimate(t, v, n_pixels):
    """Reduce (t, v) a ~4*n_buckets puntos conservando min/max por bucket.

    Diferencia clave vs. downsample por salto:
      - salto   -> pierde picos (el pico puede caer entre muestras elegidas)
      - min/max -> conserva envolvente (siempre ves el peor caso por bucket)

    Version robusta (sin "resto" que rompa la traza):
      - Los buckets cubren TODO el array con bordes np.linspace; el ultimo
        absorbe decimales. No hay muestras fuera que haya que concatenar.
      - Por cada bucket emitimos hasta 4 puntos: primero, min, max, ultimo.
        Emitir first y last ademas de min/max evita el artefacto de "linea
        que salta de pico a pico" entre buckets y deja cruces limpios.
    """
    n = len(v)
    if n_pixels < 1:
        n_pixels = 1
    threshold = POINTS_PER_PIXEL * n_pixels
    if n <= threshold:
        return t, v

    # Bordes de bucket: cubren [0, n) exactamente.
    edges = np.linspace(0, n, n_pixels + 1, dtype=np.int64)

    # Preallocamos holgado (hasta 4 puntos por bucket, des-duplicados luego)
    out_t = np.empty(4 * n_pixels, dtype=t.dtype)
    out_v = np.empty(4 * n_pixels, dtype=v.dtype)
    k = 0

    for b in range(n_pixels):
        lo = int(edges[b])
        hi = int(edges[b + 1])
        if hi <= lo:
            continue
        seg_v = v[lo:hi]
        seg_t = t[lo:hi]
        i_min = int(np.argmin(seg_v))
        i_max = int(np.argmax(seg_v))
        # Indices distintivos dentro del bucket: primero, min, max, ultimo.
        # Los emitimos en orden temporal y sin repetidos.
        idx_set = sorted({0, i_min, i_max, hi - lo - 1})
        for j in idx_set:
            out_t[k] = seg_t[j]
            out_v[k] = seg_v[j]
            k += 1

    return out_t[:k], out_v[:k]


# -----------------------------
# FFT: ventanas y sus ENBW
# -----------------------------
# ENBW = equivalent noise bandwidth (en bins). Se usa para:
#   - relación RBW <-> W  (RBW = ENBW * fs / N)
#   - compensación de amplitud al aplicar la ventana
FFT_WINDOWS = {
    "Rectangular": {"enbw": 1.0,    "coherent_gain": 1.0},
    "Hann":        {"enbw": 1.5,    "coherent_gain": 0.5},
    "Hamming":     {"enbw": 1.3628, "coherent_gain": 0.54},
    "Blackman":    {"enbw": 1.7269, "coherent_gain": 0.42},
}


def apply_window(v, name):
    """Devuelve la señal ventaneada y la ganancia coherente de la ventana."""
    N = len(v)
    if name == "Rectangular":
        w = np.ones(N)
    elif name == "Hann":
        w = np.hanning(N)
    elif name == "Hamming":
        w = np.hamming(N)
    elif name == "Blackman":
        w = np.blackman(N)
    else:
        w = np.ones(N)

    cg = np.mean(w)   # ganancia coherente real de la ventana generada
    return v * w, cg


# Escalas verticales del FFT
FFT_Y_SCALES = ["Linear (Vrms)", "dBV", "dBm (50Ω)", "dBµV"]



def vrms_to_scale(vrms, scale):
    """Convierte un array de Vrms a la escala pedida."""
    # Evitar log(0)
    eps = 1e-20
    v = np.maximum(vrms, eps)

    if scale == "Linear (Vrms)":
        return vrms
    elif scale == "dBV":
        return 20.0 * np.log10(v)
    elif scale == "dBm (50Ω)":
        # P = V² / R ; dBm = 10·log10(P / 1mW)
        p_mw = (v ** 2) / 50.0 * 1000.0
        return 10.0 * np.log10(p_mw)
    elif scale == "dBµV":
        # 1 µV = 1e-6 V ; dBµV = 20·log10(V / 1µV)
        return 20.0 * np.log10(v / 1e-6)
    return vrms


def scale_unit(scale):
    """Devuelve la unidad que se muestra en el eje Y."""
    if scale == "Linear (Vrms)":
        return "Vrms"
    if scale == "dBV":
        return "dBV"
    if scale == "dBm (50Ω)":
        return "dBm"
    if scale == "dBµV":
        return "dBµV"
    return ""
