"""LIN decoder."""
import numpy as np

from ocv.decoders.base import (
    _binarize,
    _find_edges,
    _sample_at,
    ProtocolDecoder,
)


# -----------------------------
# LIN (Local Interconnect Network)
# -----------------------------
class LinDecoder(ProtocolDecoder):
    """LIN bus — single-wire UART con break dominante (>11 bits low),
    sync field (0x55), PID protected, 1..8 bytes data, checksum.

    LIN clásico: master + slaves en un bus de 12V. La señal lógica que
    vemos aquí asume que ya viene transceiver-traducida a TTL (idle = alto).
    """

    name = "LIN"
    description = (
        "Local Interconnect Network. UART single-wire con frame estructurado:\n"
        " 1) BREAK (≥13 bits dominantes / low)\n"
        " 2) SYNC field = 0x55 para calibrar baud\n"
        " 3) PID (6 bits ID + 2 parity)\n"
        " 4) 1..8 data bytes\n"
        " 5) Checksum (classic = sobre data; enhanced = sobre PID+data).\n"
        "Soporta versiones LIN 1.x, 2.x, J2602 y Any (auto).\n"
        "Asume señal recuperada en TTL (idle alto)."
    )
    channels_needed = ["LIN"]

    @classmethod
    def default_params(cls):
        return {
            "baud_preset": {"label": "Baud rate",
                            "default": "19200",
                            "kind": "choice",
                            "options": ["2400", "4800", "9600", "10417",
                                        "19200", "20000", "38400",
                                        "57600", "76800", "115200",
                                        "Custom"]},
            "baud_custom": {"label": "  Custom baud",
                            "default": 19200,
                            "kind": "int"},
            "checksum":    {"label": "Checksum",   "default": "Auto",
                            "kind": "choice",
                            "options": ["Auto", "Classic", "Enhanced"]},
            "lin_version": {"label": "LIN version", "default": "2.x",
                            "kind": "choice",
                            "options": ["1.x", "2.x", "J2602", "Any"]},
            "invert":      {"label": "Inverted",   "default": False,
                            "kind": "bool"},
        }

    @classmethod
    def decode(cls, time_arr, channel_voltages, params):
        v = channel_voltages["LIN"]
        # Resolver baudrate: el combobox manda; si está en Custom, usar el
        # campo numérico. Si el valor del combo es uno de los presets, lo
        # parseamos directamente. El parámetro 'baud' legacy se mantiene
        # como fallback para retrocompatibilidad con sesiones antiguas.
        preset = params.get("baud_preset", "19200")
        if preset == "Custom":
            baud = max(1, int(params.get("baud_custom", 19200)))
        else:
            try:
                baud = max(1, int(preset))
            except (ValueError, TypeError):
                # Fallback a 'baud' legacy si alguien pasa params raros
                baud = max(1, int(params.get("baud", 19200)))
        checksum_mode = params["checksum"]
        invert = bool(params["invert"])

        p_lo = float(np.quantile(v, 0.10))
        p_hi = float(np.quantile(v, 0.90))
        hyst = (p_hi - p_lo) * 0.10
        b = _binarize(time_arr, v, hysteresis=hyst)
        if invert:
            b = (1 - b).astype(np.uint8)

        bit_time = 1.0 / baud
        frames = []

        # Detectar BREAK: tramo dominante (low) ≥ 11*bit_time.
        # Recorremos las regiones low (0) y miramos su duración.
        # Las regiones low arrancan en flancos de bajada y terminan en
        # flancos de subida.
        falling = _find_edges(b, time_arr, "falling")
        rising = _find_edges(b, time_arr, "rising")
        if len(falling) == 0:
            return frames

        # Para cada flanco de bajada, buscar el siguiente flanco de subida
        # y medir la duración. Si ≥ 11*bit_time → BREAK.
        n = len(time_arr)
        for f_idx in falling:
            # Próximo rising tras este falling
            r_idx_arr = rising[rising > f_idx]
            if len(r_idx_arr) == 0:
                continue
            r_idx = int(r_idx_arr[0])
            low_dur = time_arr[r_idx] - time_arr[f_idx]
            if low_dur < 11.0 * bit_time:
                continue

            # BREAK detectado
            t_break_start = time_arr[f_idx]
            t_break_end = time_arr[r_idx]
            frames.append({
                "t_start": t_break_start,
                "t_end":   t_break_end,
                "type":    "break",
                "value":   0,
                "label":   "BREAK",
                "color":   "#cc4444",
                "error":   False,
            })

            # Sync field empieza después del break. Es un byte 0x55
            # (alternancia perfecta). Estructura UART: 1 start (0) +
            # 8 datos + 1 stop. Saltamos el sync (sólo lo marcamos).
            t_sync_start = t_break_end
            # El sync arranca con un start bit (low). Buscar la siguiente
            # caída cerca de t_sync_start. Por seguridad, asumimos que
            # el rising que cerró el break dejó la línea en idle alto
            # y el siguiente flanco descendente es el start del sync.
            next_falling = falling[falling > r_idx]
            if len(next_falling) == 0:
                break
            sync_start_idx = int(next_falling[0])
            t_sync_start = time_arr[sync_start_idx]

            # ---------- AUTO-CALIBRACIÓN DEL BIT-TIME DESDE EL SYNC ----------
            # LIN está diseñado para que el slave se auto-calibre con el SYNC
            # byte (0x55), porque no tiene oscilador preciso. El SYNC tiene
            # 8 transiciones equiespaciadas a 1*bit_time entre el flanco
            # descendente del start y el bit 7.
            #
            # Patrón temporal del SYNC byte (UART 8N1, LSB first, valor 0x55):
            #   t=0*bt  : ↓  (start bit empieza, línea pasa de idle a 0)
            #   t=1*bt  : ↑  (bit 0 = 1)
            #   t=2*bt  : ↓  (bit 1 = 0)
            #   t=3*bt  : ↑  (bit 2 = 1)
            #   t=4*bt  : ↓  (bit 3 = 0)
            #   t=5*bt  : ↑  (bit 4 = 1)
            #   t=6*bt  : ↓  (bit 5 = 0)
            #   t=7*bt  : ↑  (bit 6 = 1)
            #   t=8*bt  : ↓  (bit 7 = 0)
            #   t=9*bt  :    (línea ya en 0, pasa a stop=1 al final)
            #   t=10*bt :    (fin del byte, idle)
            #
            # Entonces, del primer flanco (descendente del start) al 9º
            # flanco (descendente del bit 7) hay EXACTAMENTE 8 bit_times.
            # Si encontramos los flancos en ese rango y medimos el span,
            # tenemos el bit_time real con precisión.
            #
            # Procedimiento robusto:
            #   1. Cogemos todos los flancos (rising + falling) en una
            #      ventana de [t_sync_start, t_sync_start + 12*bt_nominal]
            #   2. Si encontramos al menos 9 flancos, los primeros 9
            #      definen 8 bit_times.
            #   3. bit_time_real = (t_edge[8] - t_edge[0]) / 8
            #
            # Si fallan las suposiciones, caemos al bit_time nominal.
            search_end = t_sync_start + 12.0 * bit_time   # bit_time aquí aún nominal
            all_edges_idx = np.sort(np.concatenate([falling, rising]))
            window_mask = ((time_arr[all_edges_idx] >= t_sync_start - bit_time * 0.1) &
                           (time_arr[all_edges_idx] <= search_end))
            sync_edges_idx = all_edges_idx[window_mask]
            bit_time_sync = bit_time   # fallback al nominal
            sync_calibrated = False
            if len(sync_edges_idx) >= 9:
                t_e0 = time_arr[int(sync_edges_idx[0])]
                t_e8 = time_arr[int(sync_edges_idx[8])]
                span = t_e8 - t_e0
                if span > 0:
                    candidate = span / 8.0
                    # Sanity check: el bit_time real no debería diferir más
                    # de ±50% del nominal. Si se desvía mucho, algo raro
                    # pasa con la señal y mantenemos el nominal.
                    if 0.5 * bit_time <= candidate <= 2.0 * bit_time:
                        bit_time_sync = candidate
                        sync_calibrated = True

            # A partir de aquí, usamos bit_time_sync (calibrado) para todo
            # este frame. El bit_time nominal se mantiene SOLO para detectar
            # BREAKs (que ya hicimos arriba).
            bt = bit_time_sync

            t_sync_end = t_sync_start + 10.0 * bt
            # Validar lectura de 0x55 con el bit_time ya calibrado.
            sync_val = cls._read_uart_byte(b, time_arr, t_sync_start, bt)
            sync_ok = (sync_val == 0x55)
            measured_baud = (1.0 / bt) if bt > 0 else 0
            # Construir label de diagnóstico. Mostramos SIEMPRE el baud medido
            # cuando hubo calibración, sea OK o no — facilita detectar
            # mismatches entre lo configurado y lo real.
            if sync_ok:
                if sync_calibrated:
                    sync_label = f"SYNC 0x55 @ {measured_baud:.0f} baud"
                else:
                    sync_label = "SYNC 0x55"
            else:
                got = sync_val if sync_val is not None else 0
                if sync_calibrated:
                    sync_label = (f"SYNC! 0x{got:02X} "
                                  f"(measured @ {measured_baud:.0f} baud, "
                                  f"expected 0x55)")
                else:
                    sync_label = f"SYNC! 0x{got:02X} (no calibration)"
            frames.append({
                "t_start": t_sync_start,
                "t_end":   t_sync_end,
                "type":    "sync",
                "value":   sync_val if sync_val is not None else 0,
                "label":   sync_label,
                "color":   "#7fdb7f" if sync_ok else "#ff6b6b",
                "error":   not sync_ok,
            })

            # PID: siguiente byte UART tras el sync
            # Buscar el siguiente flanco descendente (start del PID).
            after_sync = falling[falling > sync_start_idx]
            # Saltar el flanco del sync mismo: el byte 0x55 tiene flancos
            # internos. Avanzamos hasta un flanco que esté > t_sync_end.
            after_sync = after_sync[time_arr[after_sync] > t_sync_end - bt * 0.5]
            if len(after_sync) == 0:
                continue
            pid_start_idx = int(after_sync[0])
            t_pid_start = time_arr[pid_start_idx]
            t_pid_end = t_pid_start + 10.0 * bt
            pid_val = cls._read_uart_byte(b, time_arr, t_pid_start, bt)
            if pid_val is None:
                continue
            pid_id = pid_val & 0x3F
            # Parity bits: P0 = ID0^ID1^ID2^ID4,  P1 = ~(ID1^ID3^ID4^ID5)
            id_bits = [(pid_id >> k) & 1 for k in range(6)]
            p0 = id_bits[0] ^ id_bits[1] ^ id_bits[2] ^ id_bits[4]
            p1 = 1 ^ (id_bits[1] ^ id_bits[3] ^ id_bits[4] ^ id_bits[5])
            expected_pid = pid_id | (p0 << 6) | (p1 << 7)
            pid_ok = (pid_val == expected_pid)
            # Longitud según ID (LIN 1.x): ID 0..31 → 2 bytes, 32..47 → 4,
            # 48..63 → 8. En LIN 2.x el length es libre (lo determina la
            # tabla de scheduling). Sin tabla, estimamos por convención.
            # J2602 y "Any" se tratan como 2.x para la estimación de longitud.
            if params["lin_version"] == "1.x":
                if pid_id < 32:
                    dlen = 2
                elif pid_id < 48:
                    dlen = 4
                else:
                    dlen = 8
            else:
                # En 2.x/J2602/Any asumimos 8 (peor caso). El detector de
                # break cortará si llega antes uno nuevo.
                dlen = 8

            frames.append({
                "t_start": t_pid_start,
                "t_end":   t_pid_end,
                "type":    "pid",
                "value":   pid_val,
                "label":   f"PID 0x{pid_val:02X} (ID {pid_id})"
                            + ("" if pid_ok else " !PAR"),
                "color":   "#ffb84d" if pid_ok else "#ff6b6b",
                "error":   not pid_ok,
            })

            # Leer bytes de respuesta. La spec LIN tiene dos modos:
            #
            #   LIN 1.x: la longitud de payload se deriva del ID
            #            (ID 0..31 → 2 bytes, 32..47 → 4, 48..63 → 8).
            #            El último byte siempre es el checksum.
            #
            #   LIN 2.x: la longitud la determina la tabla de schedule
            #            del master, que no tenemos. Solución: leer bytes
            #            hasta que el gap idle indique fin de frame, y el
            #            ÚLTIMO byte leído se interpreta como checksum.
            #
            # Umbrales de gap (en bit_times):
            #   - PID → primer byte:   hasta 40 bt (response space del slave)
            #   - Entre bytes:         hasta 40 bt (inter-byte space puede ser
            #                          relevante si el slave es lento)
            #
            # Estrategia unificada:
            #   1. Leemos hasta `max_read` bytes (9 = 8 data máx + 1 checksum).
            #   2. Si el gap antes del siguiente byte excede el umbral, paramos.
            #   3. El último byte leído es el checksum. Los previos son datos.
            #   4. En LIN 1.x usamos el dlen calculado del ID como pista para
            #      detectar inconsistencias si la longitud no encaja.
            max_read = 9
            max_gap_first  = 40.0 * bt
            max_gap_between = 40.0 * bt
            read_bytes = []          # lista de (t_start, t_end, value)
            t_cursor = t_pid_end
            for k in range(max_read):
                next_f = falling[time_arr[falling] > t_cursor - bt * 0.2]
                if len(next_f) == 0:
                    break
                byte_start_idx = int(next_f[0])
                t_byte_start = time_arr[byte_start_idx]
                max_gap = max_gap_first if k == 0 else max_gap_between
                if t_byte_start - t_cursor > max_gap:
                    break
                # Si lo que viene a continuación es un BREAK (pulso bajo
                # ≥ 11 bit_times), no es un byte de respuesta sino el
                # inicio del siguiente frame LIN. Paramos aquí.
                rs_after = rising[rising > byte_start_idx]
                if len(rs_after) > 0:
                    ri_after = int(rs_after[0])
                    low_dur = time_arr[ri_after] - t_byte_start
                    if low_dur >= 11.0 * bt:
                        break
                val = cls._read_uart_byte(b, time_arr, t_byte_start, bt)
                if val is None:
                    break
                t_byte_end = t_byte_start + 10.0 * bt
                read_bytes.append((t_byte_start, t_byte_end, val))
                t_cursor = t_byte_end

            if len(read_bytes) < 2:
                # Frame demasiado corto para tener data + checksum.
                continue

            # El último byte es el checksum, los previos son datos.
            data_bytes = [bv for (_, _, bv) in read_bytes[:-1]]
            cs_t_start, cs_t_end, cs_val = read_bytes[-1]

            # Emitir frames de datos
            for k, (ts, te, val) in enumerate(read_bytes[:-1]):
                frames.append({
                    "t_start": ts,
                    "t_end":   te,
                    "type":    "data",
                    "value":   val,
                    "label":   f"D{k} 0x{val:02X}",
                    "color":   "#5fa8ff",
                    "error":   False,
                })

            # Calcular checksum esperado.
            #
            # Reglas de la spec:
            #   - LIN 1.x      → Classic   (sólo data bytes)
            #   - LIN 2.x      → Enhanced  (PID + data), excepto IDs
            #                    reservados 0x3C/0x3D que siempre Classic
            #   - J2602        → Enhanced (incluso para diagnóstico, según
            #                    SAE J2602; aquí mantenemos la regla
            #                    estricta de excluir PID en 0x3C/0x3D
            #                    por compatibilidad práctica)
            #   - Any          → probar ambos. Si alguno coincide, OK.
            #
            # Parámetro `checksum`:
            #   - "Auto"     → respeta la versión (recomendado).
            #   - "Classic"  → fuerza classic (override manual).
            #   - "Enhanced" → fuerza enhanced (override manual).
            cs_mode_param = checksum_mode
            lin_ver = params.get("lin_version", "2.x")

            def _calc_cs(use_enhanced):
                acc = pid_val if use_enhanced else 0
                for d in data_bytes:
                    acc += d
                    if acc > 0xFF:
                        acc = (acc & 0xFF) + 1   # add with carry
                return (~acc) & 0xFF

            # Determinar qué modos probar
            if cs_mode_param == "Classic":
                modes_to_try = [("Classic", False)]
            elif cs_mode_param == "Enhanced":
                modes_to_try = [("Enhanced", True)]
            else:  # Auto
                # Si el ID es reservado, siempre Classic
                if pid_id in (0x3C, 0x3D):
                    modes_to_try = [("Classic", False)]
                elif lin_ver == "1.x":
                    modes_to_try = [("Classic", False)]
                elif lin_ver in ("2.x", "J2602"):
                    modes_to_try = [("Enhanced", True)]
                else:  # "Any"
                    modes_to_try = [("Enhanced", True), ("Classic", False)]

            # Probar y elegir el primero que coincida; si ninguno, usar el
            # primero como referencia para el mensaje de error.
            matched = None
            for name, use_enh in modes_to_try:
                expected = _calc_cs(use_enh)
                if cs_val == expected:
                    matched = (name, expected)
                    break
            if matched is not None:
                cs_ok = True
                cs_label_suffix = (f" [{matched[0]}]"
                                   if len(modes_to_try) > 1 else "")
                expected_cs = matched[1]
            else:
                cs_ok = False
                # Mostrar el esperado del primer modo intentado
                _, use_enh_first = modes_to_try[0]
                expected_cs = _calc_cs(use_enh_first)
                cs_label_suffix = ""

            frames.append({
                "t_start": cs_t_start,
                "t_end":   cs_t_end,
                "type":    "checksum",
                "value":   cs_val,
                "label":   (f"CRC 0x{cs_val:02X}{cs_label_suffix}"
                            if cs_ok else
                            f"CRC 0x{cs_val:02X} (exp 0x{expected_cs:02X})"),
                "color":   "#cc99ff" if cs_ok else "#ff6b6b",
                "error":   not cs_ok,
            })

        return frames

    @staticmethod
    def _read_uart_byte(b, time_arr, t_start, bit_time):
        """Lee un byte UART (1 start + 8 datos LSB + 1 stop) a partir de
        t_start (flanco de bajada del start). Devuelve int o None si no
        hay datos suficientes."""
        if t_start + 10.0 * bit_time > time_arr[-1]:
            return None
        # Validar start = 0
        if _sample_at(b, time_arr, t_start + bit_time * 0.5) != 0:
            return None
        value = 0
        for k in range(8):
            t_sample = t_start + bit_time * (1.5 + k)
            bit = _sample_at(b, time_arr, t_sample)
            value |= (bit & 1) << k
        return value

    @classmethod
    def draw_diagram(cls, canvas, x, y, w, h, theme):
        """Diagrama LIN: Break + Sync + PID + Data + Checksum.
        Estilo cajas (no señal): más legible para LIN."""
        pad_l, pad_r = 16, 16
        pad_t, pad_b = 28, 26
        x0 = x + pad_l
        x1 = x + w - pad_r
        plot_w = max(80, x1 - x0)
        box_h = h - pad_t - pad_b
        box_y = y + pad_t

        # Título
        cls._dgm_label(canvas, x + 10, y + 10, "LIN frame",
                       theme["dim"], font=("Segoe UI", 9, "bold"),
                       anchor="w")

        # Reparto proporcional (Break más ancho, data variable)
        # Break=2, Sync=1.5, PID=1.5, Data=4, Checksum=1
        weights = [("Break",    2.0, "err"),
                   ("Sync 0x55", 1.5, "ctrl"),
                   ("PID",       1.5, "accent"),
                   ("Data 1..8 B", 4.0, "data"),
                   ("Checksum",  1.5, "ok")]
        total = sum(w for _, w, _ in weights)
        unit = plot_w / total

        cursor = x0
        for label, width, ck in weights:
            bw = width * unit
            cls._dgm_field_row(canvas, cursor, box_y,
                               [(label, bw, ck)], box_h, theme)
            cursor += bw

        # Pequeña traza esquemática debajo (solo el Break + Sync para evocar)
        sig_y_hi = box_y + box_h + 6
        sig_y_lo = sig_y_hi + 8
        # Mantener nivel alto antes del frame: omitido por espacio.
        # Esquema: traza encima del campo Break baja y se mantiene
        break_w = 2.0 * unit
        sync_w = 1.5 * unit
        # Mini-traza opcional: omitida para no saturar; los campos hablan.

        # Nota de versiones soportadas
        cls._dgm_label(canvas, x + w / 2, y + h - 8,
                       "Versions: 1.x · 2.x · J2602 · Any",
                       theme["dim"], font=("Segoe UI", 7, "italic"))

        return True
