"""Explanatory figures for each automatic measurement (Tek manual style)."""
import numpy as np


# =============================
# Imágenes explicativas de las medidas
# =============================
# Dibujadas con matplotlib puro (líneas, flechas, texto). Devuelven una Figure
# ya lista para insertar en la ventana de Measurements con FigureCanvasTkAgg.
# Fondo claro estilo Tektronix, traza amarilla, flechas/líneas de referencia.

def _meas_fig_base():
    """Crea una figura base con el estilo de los manuales de scope."""
    from matplotlib.figure import Figure
    fig = Figure(figsize=(5.5, 2.6), dpi=90)
    fig.patch.set_facecolor("#3a3a3a")
    ax = fig.add_subplot(111)
    ax.set_facecolor("#3a3a3a")
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(0, 10)
    ax.set_ylim(-2.5, 2.5)
    return fig, ax


def _draw_square_wave(ax, x_start=0.5, x_end=9.5, low=-1.2, high=1.2, cycles=2.5, overshoot=0.0):
    """Dibuja una onda cuadrada amarilla con flancos finitos y overshoot
    opcional. La usan casi todos los previews de time/amplitude measurements."""
    T = (x_end - x_start) / cycles
    edge_w = T * 0.08
    # Generar puntos
    x_pts = [x_start]
    y_pts = [low]
    t = x_start
    state = low
    while t < x_end:
        # Subida
        x_pts.append(t + edge_w); y_pts.append(high + overshoot)
        if overshoot != 0:
            x_pts.append(t + edge_w + edge_w*0.5); y_pts.append(high + overshoot*0.3)
            x_pts.append(t + edge_w + edge_w*1.2); y_pts.append(high)
        # Meseta alta
        t2 = t + T/2
        x_pts.append(t2); y_pts.append(high)
        # Bajada
        x_pts.append(t2 + edge_w); y_pts.append(low - overshoot)
        if overshoot != 0:
            x_pts.append(t2 + edge_w + edge_w*0.5); y_pts.append(low - overshoot*0.3)
            x_pts.append(t2 + edge_w + edge_w*1.2); y_pts.append(low)
        # Meseta baja
        t = t + T
        x_pts.append(t); y_pts.append(low)
    ax.plot(x_pts, y_pts, color="#ffeb3b", linewidth=2.0)
    return T


def _draw_sine(ax, x_start=0.5, x_end=9.5, cycles=2, amp=1.4, dc=0.0):
    xs = np.linspace(x_start, x_end, 400)
    ys = dc + amp * np.sin(2 * np.pi * cycles * (xs - x_start) / (x_end - x_start))
    ax.plot(xs, ys, color="#ffeb3b", linewidth=2.0)


def _hline(ax, y, x0=0.3, x1=9.7, **kw):
    kw.setdefault("color", "#cccccc")
    kw.setdefault("linewidth", 0.8)
    kw.setdefault("linestyle", (0, (4, 3)))
    ax.plot([x0, x1], [y, y], **kw)


def _vline(ax, x, y0, y1, **kw):
    kw.setdefault("color", "#cccccc")
    kw.setdefault("linewidth", 0.8)
    kw.setdefault("linestyle", (0, (4, 3)))
    ax.plot([x, x], [y0, y1], **kw)


def _arrow_v(ax, x, y0, y1, label=None, label_offset=(0.15, 0)):
    """Flecha vertical doble con etiqueta."""
    ax.annotate(
        "", xy=(x, y1), xytext=(x, y0),
        arrowprops=dict(arrowstyle="<->", color="#ffffff", lw=1.5),
    )
    if label:
        ax.text(x + label_offset[0], (y0 + y1) / 2 + label_offset[1], label,
                color="white", fontsize=11, va="center", ha="left")


def _arrow_h(ax, x0, x1, y, label=None, label_offset=(0, 0.2)):
    """Flecha horizontal doble con etiqueta."""
    ax.annotate(
        "", xy=(x1, y), xytext=(x0, y),
        arrowprops=dict(arrowstyle="<->", color="#ffffff", lw=1.5),
    )
    if label:
        ax.text((x0 + x1) / 2 + label_offset[0], y + label_offset[1], label,
                color="white", fontsize=11, va="bottom", ha="center")


def make_measurement_preview(name):
    """Genera una Figure con el dibujo explicativo de la medida `name`.
    Si no hay dibujo específico, devuelve una figura genérica con una
    onda cuadrada."""
    fig, ax = _meas_fig_base()

    top, base = 1.2, -1.2

    if name == "Peak-Peak":
        _draw_square_wave(ax, overshoot=0.5)
        _hline(ax, top + 0.5)   # máximo con overshoot
        _hline(ax, base - 0.5)
        _arrow_v(ax, 1.5, top + 0.5, base - 0.5, "Vpp")

    elif name == "Amplitude":
        _draw_square_wave(ax, overshoot=0.5)
        _hline(ax, top)
        _hline(ax, base)
        _arrow_v(ax, 1.5, base, top, "A")

    elif name == "Top Level":
        _draw_square_wave(ax, overshoot=0.5)
        _hline(ax, top, color="#4caf50", linewidth=1.2)
        ax.text(9.8, top, " Top", color="#4caf50", fontsize=11, va="center", ha="left", fontweight="bold")

    elif name == "Base Level":
        _draw_square_wave(ax, overshoot=0.5)
        _hline(ax, base, color="#4caf50", linewidth=1.2)
        ax.text(9.8, base, " Base", color="#4caf50", fontsize=11, va="center", ha="left", fontweight="bold")

    elif name == "Peak +":
        _draw_square_wave(ax, overshoot=0.6)
        _hline(ax, top + 0.6, color="#e91e63", linewidth=1.2)
        ax.text(9.8, top + 0.6, " Peak+", color="#e91e63", fontsize=11, va="center", ha="left", fontweight="bold")

    elif name == "Peak -":
        _draw_square_wave(ax, overshoot=0.6)
        _hline(ax, base - 0.6, color="#e91e63", linewidth=1.2)
        ax.text(9.8, base - 0.6, " Peak−", color="#e91e63", fontsize=11, va="center", ha="left", fontweight="bold")

    elif name == "Positive Overshoot":
        _draw_square_wave(ax, overshoot=0.6)
        _hline(ax, top)
        _hline(ax, top + 0.6)
        _arrow_v(ax, 3.2, top, top + 0.6, "+OS")

    elif name == "Negative Overshoot":
        _draw_square_wave(ax, overshoot=0.6)
        _hline(ax, base)
        _hline(ax, base - 0.6)
        _arrow_v(ax, 6.8, base - 0.6, base, "−OS")

    elif name == "RMS":
        _draw_sine(ax, cycles=2)
        # Indicar Vrms ≈ 0.707·A
        ax.plot([0.5, 9.5], [0.989, 0.989], color="#e91e63",
                linewidth=1.2, linestyle=(0, (4, 3)))
        ax.text(9.8, 0.989, " Vrms", color="#e91e63",
                fontsize=11, va="center", ha="left", fontweight="bold")

    elif name == "Period":
        _draw_square_wave(ax, cycles=2.5)
        # Flecha indicando T entre dos flancos ascendentes
        T_vis = (9.5 - 0.5) / 2.5
        _vline(ax, 0.5, base, top + 0.3)
        _vline(ax, 0.5 + T_vis, base, top + 0.3)
        _arrow_h(ax, 0.5, 0.5 + T_vis, top + 0.7, "T")

    elif name == "Frequency":
        _draw_square_wave(ax, cycles=2.5)
        T_vis = (9.5 - 0.5) / 2.5
        _vline(ax, 0.5, base, top + 0.3)
        _vline(ax, 0.5 + T_vis, base, top + 0.3)
        _arrow_h(ax, 0.5, 0.5 + T_vis, top + 0.7, "T = 1/F")

    elif name == "Rise Time":
        _draw_square_wave(ax, cycles=1.5, overshoot=0.0)
        # Niveles 10% y 90%
        v10 = base + 0.1 * (top - base)
        v90 = base + 0.9 * (top - base)
        _hline(ax, v10, color="#e91e63", linewidth=0.9)
        _hline(ax, v90, color="#e91e63", linewidth=0.9)
        ax.text(9.8, v10, " 10%", color="#e91e63", fontsize=10, va="center", ha="left")
        ax.text(9.8, v90, " 90%", color="#e91e63", fontsize=10, va="center", ha="left")
        # Flecha horizontal sobre el flanco ascendente
        T_vis = (9.5 - 0.5) / 1.5
        edge_x = 0.5 + T_vis * 0.08 * 0.5
        _arrow_h(ax, edge_x - 0.1, edge_x + 0.4, top + 0.6, "tr")

    elif name == "Fall Time":
        _draw_square_wave(ax, cycles=1.5, overshoot=0.0)
        v10 = base + 0.1 * (top - base)
        v90 = base + 0.9 * (top - base)
        _hline(ax, v10, color="#e91e63", linewidth=0.9)
        _hline(ax, v90, color="#e91e63", linewidth=0.9)
        ax.text(9.8, v10, " 10%", color="#e91e63", fontsize=10, va="center", ha="left")
        ax.text(9.8, v90, " 90%", color="#e91e63", fontsize=10, va="center", ha="left")
        T_vis = (9.5 - 0.5) / 1.5
        edge_x = 0.5 + T_vis * 0.5
        _arrow_h(ax, edge_x - 0.1, edge_x + 0.4, top + 0.6, "tf")

    elif name == "Positive Width":
        _draw_square_wave(ax, cycles=2.5)
        T = (9.5 - 0.5) / 2.5
        edge_w = T * 0.08
        # Pulso positivo: desde final del flanco subida (0.5+edge_w) hasta
        # inicio del flanco bajada (0.5+T/2).
        x0 = 0.5 + edge_w + edge_w * 0.5
        x1 = 0.5 + T / 2 + edge_w * 0.5
        _vline(ax, x0, base, top + 0.3)
        _vline(ax, x1, base, top + 0.3)
        _arrow_h(ax, x0, x1, top + 0.7, "+W")

    elif name == "Negative Width":
        _draw_square_wave(ax, cycles=2.5)
        T = (9.5 - 0.5) / 2.5
        edge_w = T * 0.08
        # Pulso negativo: desde inicio flanco bajada hasta inicio siguiente subida
        x0 = 0.5 + T / 2 + edge_w + edge_w * 0.5
        x1 = 0.5 + T + edge_w * 0.5
        _vline(ax, x0, base - 0.3, top)
        _vline(ax, x1, base - 0.3, top)
        _arrow_h(ax, x0, x1, base - 0.7, "−W")

    elif name == "Rising Slew Rate":
        # Onda con un flanco de subida bien marcado
        xs = np.linspace(0.5, 9.5, 300)
        ys = np.where(xs < 3.5, -1.2,
                      np.where(xs > 6.5, 1.2,
                               -1.2 + (xs - 3.5) * (2.4 / 3.0)))
        ax.plot(xs, ys, color="#ffeb3b", linewidth=2.0)
        v10 = -1.2 + 0.1 * 2.4
        v90 = -1.2 + 0.9 * 2.4
        _hline(ax, v10, color="#e91e63", linewidth=0.9)
        _hline(ax, v90, color="#e91e63", linewidth=0.9)
        ax.text(9.8, v10, " 10%", color="#e91e63", fontsize=10, va="center", ha="left")
        ax.text(9.8, v90, " 90%", color="#e91e63", fontsize=10, va="center", ha="left")
        # Flecha diagonal indicando la pendiente
        x_lo = 3.5 + (v10 - (-1.2)) / (2.4 / 3.0)
        x_hi = 3.5 + (v90 - (-1.2)) / (2.4 / 3.0)
        ax.annotate("", xy=(x_hi, v90), xytext=(x_lo, v10),
                    arrowprops=dict(arrowstyle="->", color="#4cc2ff", lw=2.0))
        ax.text((x_lo + x_hi) / 2 - 1.2, (v10 + v90) / 2 + 0.1, "ΔV/Δt",
                color="#4cc2ff", fontsize=10, fontweight="bold", va="center")

    elif name == "Falling Slew Rate":
        xs = np.linspace(0.5, 9.5, 300)
        ys = np.where(xs < 3.5, 1.2,
                      np.where(xs > 6.5, -1.2,
                               1.2 - (xs - 3.5) * (2.4 / 3.0)))
        ax.plot(xs, ys, color="#ffeb3b", linewidth=2.0)
        v10 = -1.2 + 0.1 * 2.4
        v90 = -1.2 + 0.9 * 2.4
        _hline(ax, v10, color="#e91e63", linewidth=0.9)
        _hline(ax, v90, color="#e91e63", linewidth=0.9)
        ax.text(9.8, v10, " 10%", color="#e91e63", fontsize=10, va="center", ha="left")
        ax.text(9.8, v90, " 90%", color="#e91e63", fontsize=10, va="center", ha="left")
        x_hi = 3.5 + (1.2 - v90) / (2.4 / 3.0)
        x_lo = 3.5 + (1.2 - v10) / (2.4 / 3.0)
        ax.annotate("", xy=(x_lo, v10), xytext=(x_hi, v90),
                    arrowprops=dict(arrowstyle="->", color="#4cc2ff", lw=2.0))
        ax.text((x_lo + x_hi) / 2 - 1.2, (v10 + v90) / 2 + 0.1, "ΔV/Δt",
                color="#4cc2ff", fontsize=10, fontweight="bold", va="center")

    elif name == "Positive Duty":
        _draw_square_wave(ax, cycles=2.0)
        T = (9.5 - 0.5) / 2.0
        edge_w = T * 0.08
        # Periodo completo
        _vline(ax, 0.5, base - 0.3, top + 0.3)
        _vline(ax, 0.5 + T, base - 0.3, top + 0.3)
        _arrow_h(ax, 0.5, 0.5 + T, top + 1.0, "T")
        # Ancho positivo
        x0 = 0.5 + edge_w * 0.5
        x1 = 0.5 + T / 2 + edge_w * 0.5
        _arrow_h(ax, x0, x1, top + 0.3, "+W")
        ax.text(5.0, base - 1.0, "Duty+ = +W / T × 100%",
                color="#4cc2ff", fontsize=10, fontweight="bold",
                ha="center", va="center")

    elif name == "Negative Duty":
        _draw_square_wave(ax, cycles=2.0)
        T = (9.5 - 0.5) / 2.0
        edge_w = T * 0.08
        _vline(ax, 0.5, base - 0.3, top + 0.3)
        _vline(ax, 0.5 + T, base - 0.3, top + 0.3)
        _arrow_h(ax, 0.5, 0.5 + T, top + 1.0, "T")
        # Ancho negativo
        x0 = 0.5 + T / 2 + edge_w * 0.5
        x1 = 0.5 + T + edge_w * 0.5
        _arrow_h(ax, x0, x1, base - 0.7, "−W")
        ax.text(5.0, top + 1.6, "Duty− = −W / T × 100%",
                color="#4cc2ff", fontsize=10, fontweight="bold",
                ha="center", va="center")

    elif name == "High Time":
        _draw_square_wave(ax, cycles=2.5)
        # Sombrear las zonas altas
        T = (9.5 - 0.5) / 2.5
        edge_w = T * 0.08
        for k in range(3):
            x0 = 0.5 + k * T + edge_w * 0.5
            x1 = 0.5 + k * T + T / 2 + edge_w * 0.5
            if x1 > 9.5:
                x1 = 9.5
            if x0 > 9.5:
                break
            ax.axvspan(x0, x1, ymin=0.5, ymax=0.95,
                       color="#4cc2ff", alpha=0.3)
        _hline(ax, 0.0, color="#888888", linewidth=0.8)
        ax.text(9.8, 0.0, " mid", color="#888888",
                fontsize=9, va="center", ha="left")
        ax.text(5.0, top + 0.9, "Σ time above mid",
                color="#4cc2ff", fontsize=10, fontweight="bold",
                ha="center", va="center")

    elif name == "Low Time":
        _draw_square_wave(ax, cycles=2.5)
        T = (9.5 - 0.5) / 2.5
        edge_w = T * 0.08
        for k in range(3):
            x0 = 0.5 + k * T + T / 2 + edge_w * 0.5
            x1 = 0.5 + (k + 1) * T + edge_w * 0.5
            if x1 > 9.5:
                x1 = 9.5
            if x0 > 9.5:
                break
            ax.axvspan(x0, x1, ymin=0.05, ymax=0.5,
                       color="#4cc2ff", alpha=0.3)
        _hline(ax, 0.0, color="#888888", linewidth=0.8)
        ax.text(9.8, 0.0, " mid", color="#888888",
                fontsize=9, va="center", ha="left")
        ax.text(5.0, base - 0.9, "Σ time below mid",
                color="#4cc2ff", fontsize=10, fontweight="bold",
                ha="center", va="center")

    elif name == "Time Outside Level":
        # Onda cuadrada con overshoot marcado
        _draw_square_wave(ax, overshoot=0.6, cycles=2.0)
        # Top/Base
        _hline(ax, top, color="#888888", linewidth=0.8)
        _hline(ax, base, color="#888888", linewidth=0.8)
        ax.text(9.8, top, " Top", color="#888888", fontsize=9, va="center", ha="left")
        ax.text(9.8, base, " Base", color="#888888", fontsize=9, va="center", ha="left")
        # Sombrear overshoots (arriba de top y debajo de base)
        T = (9.5 - 0.5) / 2.0
        edge_w = T * 0.08
        for k in range(2):
            x0 = 0.5 + k * T + edge_w
            x1 = x0 + edge_w * 1.2
            ax.axvspan(x0, x1, ymin=0.82, ymax=0.92,
                       color="#e91e63", alpha=0.5)
            x0b = 0.5 + k * T + T / 2 + edge_w
            x1b = x0b + edge_w * 1.2
            ax.axvspan(x0b, x1b, ymin=0.08, ymax=0.18,
                       color="#e91e63", alpha=0.5)
        ax.text(5.0, top + 1.1, "time outside [Base, Top]",
                color="#e91e63", fontsize=10, fontweight="bold",
                ha="center", va="center")

    elif name == "Phase":
        # Dos senoidales desfasadas
        xs = np.linspace(0.5, 9.5, 400)
        cycles = 1.5
        w = 2 * np.pi * cycles / (9.5 - 0.5)
        y1 = 1.0 * np.sin(w * (xs - 0.5))
        y2 = 1.0 * np.sin(w * (xs - 0.5) - np.pi / 3)  # 60° detrás
        ax.plot(xs, y1, color="#ffeb3b", linewidth=2.0, label="CH A")
        ax.plot(xs, y2, color="#4caf50", linewidth=2.0, label="CH B")
        # Buscar primer cruce por cero ascendente de cada uno
        def first_rising_zero(ys, xs):
            for i in range(len(ys) - 1):
                if ys[i] <= 0 < ys[i + 1]:
                    # interp
                    t = ys[i] / (ys[i] - ys[i + 1])
                    return xs[i] + t * (xs[i + 1] - xs[i])
            return xs[0]
        x_a = first_rising_zero(y1, xs)
        x_b = first_rising_zero(y2, xs)
        _vline(ax, x_a, -1.5, 1.5, color="#ffeb3b", linewidth=0.8)
        _vline(ax, x_b, -1.5, 1.5, color="#4caf50", linewidth=0.8)
        _arrow_h(ax, x_a, x_b, 1.6, "Δφ")
        ax.text(9.8, 0.9, " CH A", color="#ffeb3b",
                fontsize=10, va="center", ha="left", fontweight="bold")
        ax.text(9.8, -0.4, " CH B", color="#4caf50",
                fontsize=10, va="center", ha="left", fontweight="bold")

    else:
        _draw_square_wave(ax)

    fig.tight_layout(pad=0.4)
    return fig
