"""Oscilloscope grid geometry and standard V/div, t/div steps."""


# -----------------------------
# Configuración de osciloscopio
# -----------------------------
H_DIVS = 12   # divisiones horizontales (tiempo)


V_DIVS = 10   # divisiones verticales (voltaje)


TIME_DIV_VALUES = [
    1e-9, 2e-9, 5e-9,
    10e-9, 20e-9, 40e-9,
    80e-9, 200e-9, 500e-9,
    1e-6, 2e-6, 5e-6,
    10e-6, 20e-6, 50e-6,
    100e-6, 200e-6, 500e-6,
    1e-3, 2e-3, 5e-3,
    10e-3, 20e-3, 50e-3,
    100e-3, 200e-3, 500e-3,
    1, 2, 5, 10, 20, 50, 100, 200, 500
]


V_DIV_VALUES = [
    # mV
    0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5,
    # V
    1, 2, 5, 10, 20, 50, 100, 200, 500,
    # kV
    1000, 2000, 5000, 10000, 20000, 50000,
]
