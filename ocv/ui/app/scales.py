"""Vertical and horizontal scale controls: V/div, t/div, offsets, sliders, auto-fit and snapping."""
import numpy as np

from ocv.core.scope import H_DIVS, TIME_DIV_VALUES, V_DIV_VALUES, V_DIVS
from ocv.core.si_format import DEFAULT_UNIT, format_si, parse_si


class ScalesMixin:
    """Mixin de OscilloscopeApp: usa su estado vía self."""

    # -----------------------------
    # Unidades de canal (v1.29)
    # -----------------------------
    def _ch_unit(self, ch_idx):
        """Devuelve la unidad del canal `ch_idx` con fallback al default.

        Backward-compat: canales cargados de sesiones <v1.29 no traen
        la clave 'unit'; en ese caso devolvemos DEFAULT_UNIT ('V')."""
        if not (0 <= ch_idx < len(self.channels)):
            return DEFAULT_UNIT
        return self.channels[ch_idx].get("unit", DEFAULT_UNIT)

    def _active_unit(self):
        """Unidad del canal activo (helper para ticks Y, V/div, cursores)."""
        return self._ch_unit(self.active_channel_idx)

    def _update_v_div_display(self):
        """Refresca la StringVar del label V/div con la unidad del canal
        activo. Útil cuando solo cambia la unidad (sin tocar el valor)."""
        try:
            self.v_div_display.set(
                format_si(self.v_div.get(), self._active_unit()))
        except Exception:
            pass

    # -----------------------------
    # Save image as...
    # -----------------------------
    def _snap_channel_gnd_to_grid(self, ch_idx):
        """Ajusta el v_offset del canal para que su 0V caiga exactamente
        sobre la división vertical más cercana a su posición actual."""
        if not (0 <= ch_idx < len(self.channels)):
            return
        ch = self.channels[ch_idx]
        v_div_active = self.v_div.get()
        if v_div_active <= 0 or ch["v_div"] <= 0:
            return
        # Posición pantalla actual del 0V del canal
        y_gnd_screen = (-ch["v_offset"] / ch["v_div"]) * v_div_active
        # División más cercana
        snapped = round(y_gnd_screen / v_div_active) * v_div_active
        # Clamp a ±4 divisiones para no salir del rango del slider
        max_y = v_div_active * (V_DIVS / 2 - 1)
        snapped = max(-max_y, min(max_y, snapped))
        # Calcular nuevo offset
        new_offset = -snapped / v_div_active * ch["v_div"]
        ch["v_offset"] = new_offset
        # Si es el canal activo, sincronizar var + slider
        if ch_idx == self.active_channel_idx:
            self.v_offset.set(new_offset)
            try:
                self.v_slider.set(-new_offset)
            except Exception:
                pass
        self.update_plot()

    def _t_display_origin(self):
        """Devuelve el offset de tiempo a sumar a las coordenadas internas
        para producir las etiquetas visibles del eje X y cursores.

        Si time_show_absolute=True (default): devuelve self.time_origin →
        los ticks muestran tiempos absolutos del CSV original (con pre-trigger
        si lo había, igual que un osciloscopio físico).

        Si False: devuelve 0.0 → el primer punto del CSV es t=0.

        Llama a este helper SIEMPRE en lugar de leer self.time_origin
        directamente para que el toggle de Appearance afecte de forma
        consistente.
        """
        try:
            return self.time_origin if self.time_show_absolute.get() else 0.0
        except Exception:
            return self.time_origin

    def _sync_active_channel_vars(self):
        """Copia v_div y v_offset del canal activo a los DoubleVar globales.
        Así cursores, FFT, medidas y sliders siguen usando self.v_div/self.v_offset
        sin saber nada del multi-canal."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        self._updating_display = True
        self.v_div.set(ch["v_div"])
        self.v_div_display.set(format_si(ch["v_div"], self._active_unit()))
        self._updating_display = False
        self.v_offset.set(ch["v_offset"])
        try:
            # IMPORTANTE: primero reconfigurar el rango del slider al nuevo
            # canal (v_div cambia → rango cambia). Si no, un set() con valor
            # fuera del rango antiguo dispararía un callback con valor clampado
            # y descuadra el canal.
            self.configure_v_slider()
            # Flag para que el callback del slider no re-escriba el canal
            self._syncing_active = True
            # Slider invertido: derecha = traza arriba = offset negativo
            self.v_slider.set(-ch["v_offset"])
            self._syncing_active = False
        except Exception:
            self._syncing_active = False
        # Apuntar self.voltage al canal activo para que FFT/medidas funcionen
        self.voltage = ch["voltage"]

    def _persist_active_channel_vars(self):
        """Vuelca self.v_div y self.v_offset al canal activo.
        Se llama tras cambios del usuario en V/div o drag vertical."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        ch["v_div"]    = self.v_div.get()
        ch["v_offset"] = self.v_offset.get()

    # -----------------------------
    def auto_fit(self):
        """Ajusta T/div, y para cada canal su V/div y v_offset."""
        if self.time is None or not self.channels:
            return

        # Horizontal: mostrar todo el CSV
        self.auto_time_div()

        # Vertical: para CADA canal, ajustar su v_div y v_offset
        for ch in self.channels:
            v = ch["voltage"]
            if len(v) == 0:
                continue
            vmin = float(np.min(v))
            vmax = float(np.max(v))
            vpp = vmax - vmin

            # v_div que deje la señal ocupando ~8 divisiones
            if vpp > 0:
                target = vpp / 8.0
                chosen = V_DIV_VALUES[-1]
                for vd in V_DIV_VALUES:
                    if vd >= target:
                        chosen = vd
                        break
                ch["v_div"] = chosen
            else:
                ch["v_div"] = 1.0

            # Offset al punto medio
            # Offset al punto medio de la señal (para centrarla en pantalla)
            # Modelo: y_screen = (v - offset)/v_div * v_div_activo
            # Con offset = mean(v), la señal queda centrada en 0.
            ch["v_offset"] = (vmax + vmin) / 2.0

        # Volcar del canal activo a las vars globales
        self._sync_active_channel_vars()

        # Sincronizar sliders (auto_fit sí resetea offset a 0)
        self.t_offset.set(0.0)
        self.configure_slider()
        self.configure_v_slider()
        # El slider va invertido: derecha=+ = traza arriba = offset negativo
        self.v_slider.set(-self.v_offset.get())

        self._sync_scale_displays()
        self.update_plot()

    # -----------------------------
    def _sync_scale_displays(self):
        """Actualiza los StringVar de display a partir de los valores numéricos."""
        self._updating_display = True
        self.v_div_display.set(format_si(self.v_div.get(), self._active_unit()))
        self.t_div_display.set(format_si(self.t_div.get(), "s"))
        self._updating_display = False

    # -----------------------------
    def auto_v_div(self):
        """Auto-fit vertical del canal ACTIVO. Mantiene compatibilidad."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        v = ch["voltage"]
        if len(v) == 0:
            return
        vmin = float(np.min(v))
        vmax = float(np.max(v))
        vpp = vmax - vmin
        if vpp <= 0:
            return
        target = vpp / 8.0
        chosen = V_DIV_VALUES[-1]
        for vd in V_DIV_VALUES:
            if vd >= target:
                chosen = vd
                break
        ch["v_div"] = chosen
        self.v_div.set(chosen)

    # -----------------------------
    def auto_time_div(self):
        """Usa el valor exacto t_total/H_DIVS para que la traza ocupe TODO
        el ancho del grid. Este valor no suele ser canónico; el spinbox lo
        acepta igualmente, y al pulsar una flecha se salta al canónico
        vecino. Si el exacto cae a <0.1 % de un canónico (captura de
        N·dt, no N-1), usa el canónico: '1 ms' y no '1.00001 ms'. Como
        mucho queda fuera el 0.1 % final (~1 px)."""
        if self.t_total <= 0:
            return
        exact = self.t_total / H_DIVS
        canon = min(TIME_DIV_VALUES, key=lambda v: abs(v - exact))
        if abs(canon - exact) < canon * 1e-3:
            exact = canon
        self.t_div.set(exact)
        self.t_offset.set(0.0)
        try:
            self._updating_display = True
            self.t_div_display.set(format_si(exact, "s"))
            self._updating_display = False
        except Exception:
            pass
        self._prev_t_div = exact

    # -----------------------------
    def configure_slider(self):
        window = self.t_div.get() * H_DIVS
        max_offset = max(0.0, self.t_total - window)

        # Anti-ruido de coma flotante: si el max_offset es ridículamente
        # pequeño comparado con la ventana visible, es porque la captura
        # cabe entera (o casi) en pantalla y el residuo viene de imprecisión
        # de float (t_total / H_DIVS no siempre da un t_div canónico,
        # y al combinar valores aparecen sobras de 1e-8 a 1e-7 que el slider
        # interpreta como rango utilizable). Eso provoca que tras un zoom
        # + auto fit el slider se quede mal alineado y los ticks del eje X
        # muestren "17.1 ns" o similar en vez de "0 s".
        #
        # Umbral: usamos t_div como referencia, no window. Cualquier offset
        # por debajo de t_div * 1e-4 (= 0.01 % de una división) es ruido
        # de float y lo tratamos como cero estricto. Con t/div = 1 ms eso
        # son 100 ns, suficiente para absorber cualquier error acumulado
        # por las operaciones t_total - canonical_t_div * H_DIVS.
        t_div = self.t_div.get()
        noise_threshold = t_div * 1e-4
        if max_offset < noise_threshold:
            max_offset = 0.0

        self.h_slider.config(from_=0, to=max_offset)
        # Mantener el offset actual (solo clamp si se sale)
        cur = self.t_offset.get()
        cur = max(0.0, min(cur, max_offset))
        # Mismo snap a 0 estricto si el cur queda en territorio "ruido".
        if abs(cur) < noise_threshold:
            cur = 0.0
        self.t_offset.set(cur)
        self.h_slider.set(cur)

    # -----------------------------
    def configure_v_slider(self):
        """Configura el rango del slider vertical en función de V/div.
        Se limita a ±4 divisiones."""
        v_div = self.v_div.get()
        max_v = v_div * 4   # máximo 4 divisiones arriba / abajo
        # Sentido normal: izquierda (-) → derecha (+)
        self.v_slider.config(from_=-max_v, to=max_v)

    # -----------------------------
    def on_slider_move(self, value):
        self.t_offset.set(float(value))
        self.update_plot()

    # -----------------------------
    def on_v_slider_move(self, value):
        # El slider va de -max (izquierda) a +max (derecha).
        # "derecha = traza arriba" → offset del canal = -value
        # (porque en el modelo nuevo, offset menor → traza arriba)
        if getattr(self, "_syncing_active", False):
            return
        v = float(value)
        self.v_offset.set(-v)
        if self.channels:
            self.channels[self.active_channel_idx]["v_offset"] = -v
        self.update_plot()

    # -----------------------------
    def on_time_div_change(self):
        if self.time is None:
            return
        # Zoom respecto al centro de la ventana visible.
        new_t_div = self.t_div.get()
        prev_t_div = getattr(self, "_prev_t_div", new_t_div)

        if prev_t_div != new_t_div and prev_t_div > 0:
            # Centro actual en segundos
            t_center = self.t_offset.get() + prev_t_div * H_DIVS / 2.0
            # Nuevo offset: el centro debe seguir siendo t_center
            new_offset = t_center - new_t_div * H_DIVS / 2.0
            # Clamp dentro del rango válido
            max_offset = max(0.0, self.t_total - new_t_div * H_DIVS)
            new_offset = max(0.0, min(new_offset, max_offset))
            # Anti-ruido: si tras todos los cálculos quedó un offset
            # ridículamente pequeño respecto al nuevo t/div, forzar a 0.
            # Esto evita arrastrar artefactos de coma flotante a través de
            # sucesivos cambios de t/div (zoom + auto-fit típico).
            if abs(new_offset) < new_t_div * 1e-4:
                new_offset = 0.0
            self.t_offset.set(new_offset)
        self._prev_t_div = new_t_div
        self.configure_slider()
        self.update_plot()

    def _snap_t_div_nearest(self, current):
        """Devuelve el valor de TIME_DIV_VALUES más cercano a 'current' en
        escala logarítmica."""
        import math
        lc = math.log10(max(current, 1e-18))
        return min(TIME_DIV_VALUES, key=lambda v: abs(math.log10(v) - lc))

    # -----------------------------
    def on_v_div_change(self):
        # Pivot desde el GND del canal activo, estilo scope real: el "0V
        # del canal" debe quedar en la misma posición RELATIVA AL GRID
        # (misma cantidad de divisiones desde el centro), no en el mismo
        # pixel absoluto. Esto requiere escalar v_offset proporcionalmente
        # a v_div: la posición del GND, en divisiones, es
        #     pos_GND_divs = -v_offset / v_div
        # y queremos que se conserve al cambiar v_div:
        #     v_offset_new = v_offset_old * (v_div_new / v_div_old)
        #
        # Sin esto, al subir V/div el centro del grid sigue mostrando el
        # mismo valor en voltios (label invariante) pero el GND del canal
        # se "acerca" al centro del grid en divisiones, lo que el usuario
        # percibe como un desplazamiento de la traza respecto a las líneas.
        new_v_div = self.v_div.get()
        if self.channels:
            # Leemos v_div ANTES de persistir el nuevo: ch["v_div"] del
            # canal activo aún tiene el valor antiguo en este punto
            # (_persist_active_channel_vars lo actualiza más abajo).
            old_v_div = self.channels[self.active_channel_idx].get("v_div")
            if (isinstance(old_v_div, (int, float)) and old_v_div > 0
                    and new_v_div > 0 and abs(new_v_div - old_v_div) > 1e-12):
                scaled = self.v_offset.get() * (new_v_div / old_v_div)
                self._syncing_active = True
                try:
                    self.v_offset.set(scaled)
                finally:
                    self._syncing_active = False

        # _syncing_active=True durante TODO el resto del método blinda
        # configure_v_slider() (al cambiar from_/to puede mover el thumb
        # y disparar on_v_slider_move espuriamente) y el set() final.
        self._syncing_active = True
        try:
            # Reconfigurar el slider vertical para adaptar el paso a la
            # nueva escala
            self.configure_v_slider()
            # Si el offset actual queda fuera del nuevo límite, recortarlo
            v_limit = new_v_div * 4
            current = self.v_offset.get()
            clamped = max(-v_limit, min(current, v_limit))
            if clamped != current:
                self.v_offset.set(clamped)
            # Sincronizar el slider con el offset definitivo (signo invertido).
            self.v_slider.set(-self.v_offset.get())
            # Persistir al canal activo
            self._persist_active_channel_vars()
        finally:
            self._syncing_active = False
        self.update_plot()

    # -----------------------------
    # Spinbox SCALE: flechas y edición manual
    # -----------------------------
    def _snap_to_list(self, value, values, direction=None):
        """Devuelve el valor de `values` más cercano a `value`.
        Si direction='up' busca el siguiente mayor; 'down', el anterior menor."""
        values = sorted(values)
        if direction == "up":
            for v in values:
                if v > value + 1e-15:
                    return v
            return values[-1]
        if direction == "down":
            for v in reversed(values):
                if v < value - 1e-15:
                    return v
            return values[0]
        # Más cercano (sin dirección)
        return min(values, key=lambda v: abs(v - value))

    def _spin_direction(self, spinbox, stringvar, last_text_attr):
        """Intenta inferir si el usuario pulsó ↑ o ↓ comparando el texto
        anterior con el actual. Si no se puede, devuelve None."""
        prev = getattr(self, last_text_attr, None)
        curr = stringvar.get()
        # Guardar para la próxima
        setattr(self, last_text_attr, curr)
        if prev is None:
            return None
        # Heurística simple: si el texto ha cambiado tras pulsar una flecha,
        # matplotlib/ttk nos daría el nuevo valor ya escrito. Aquí no tenemos
        # forma directa; usamos el valor numérico anterior guardado.
        return None

    # -----------------------------
    # Spinbox SCALE: valores pre-formateados, detección por trace
    # -----------------------------
    def _v_div_values_formatted(self):
        """Lista de textos válidos para el spinbox V/div.
        En modo normal usa V_DIV_VALUES; en Fine usa una rejilla de 0.1 V."""
        unit = self._active_unit()
        if self.fine_mode.get():
            # 0.1 a 50 (unit) en pasos de 0.1 (arriba de 50 sería exagerado)
            return [format_si(round(x * 0.1, 2), unit) for x in range(1, 501)]
        return [format_si(v, unit) for v in V_DIV_VALUES]

    def _on_fine_mode_toggle(self):
        """Al cambiar de modo Fine, re-acomoda el v_div actual a la nueva
        rejilla (Fine = pasos de 0.1V, Normal = V_DIV_VALUES canónicos)."""
        if self.fine_mode.get():
            snapped = round(self.v_div.get() * 10) / 10.0
            snapped = max(0.1, snapped)
        else:
            snapped = min(V_DIV_VALUES, key=lambda v: abs(v - self.v_div.get()))

        self.v_div.set(snapped)
        self._updating_display = True
        self.v_div_display.set(format_si(snapped, self._active_unit()))
        self._updating_display = False
        self.on_v_div_change()

    def _on_v_div_display_changed(self, *args):
        """Se invoca ante CUALQUIER cambio del StringVar (flechas o tipeo).
        Solo procesamos los cambios que provienen del usuario."""
        if getattr(self, "_updating_display", False):
            return
        text = self.v_div_display.get()
        val = parse_si(text, self._active_unit())
        if val is None or val <= 0:
            return  # texto incompleto o inválido: esperar a Enter/FocusOut
        if abs(val - self.v_div.get()) < 1e-15:
            return  # sin cambio real
        self.v_div.set(val)
        self.on_v_div_change()

    def _on_v_div_typed(self):
        """Enter o FocusOut: formatea el texto al canon."""
        text = self.v_div_display.get()
        val = parse_si(text, self._active_unit())
        if val is None or val <= 0:
            # Restaurar al valor anterior
            self._updating_display = True
            self.v_div_display.set(format_si(self.v_div.get(), self._active_unit()))
            self._updating_display = False
            return
        # Si el valor parseado es el mismo que ya tenemos, solo reformatear el
        # display — NO disparar on_v_div_change (eso tocaría el slider y movería
        # la traza sin motivo).
        if abs(val - self.v_div.get()) < 1e-15:
            self._updating_display = True
            self.v_div_display.set(format_si(self.v_div.get(), self._active_unit()))
            self._updating_display = False
            return
        self.v_div.set(val)
        self._updating_display = True
        self.v_div_display.set(format_si(val, self._active_unit()))
        self._updating_display = False
        self.on_v_div_change()

    def _step_v_div(self, direction):
        """Incrementa o decrementa v_div. En modo normal salta al V_DIV_VALUES
        vecino; en modo Fine usa pasos de 0.1 V."""
        current = self.v_div.get()
        if self.fine_mode.get():
            step = 0.1
            if direction == "up":
                # Sin techo: si el usuario está en una escala fuera del rango
                # cosmético del spinbox (p.ej. 100 V/div en buses de potencia),
                # un techo aplastaría el valor a la baja al primer click. Fine
                # solo cambia el TAMAÑO del paso, no el rango admisible.
                target = round((current + step) * 10) / 10.0
            else:
                target = round((current - step) * 10) / 10.0
                target = max(target, 0.1)
        else:
            if direction == "up":
                target = None
                for v in V_DIV_VALUES:
                    if v > current * 1.0001:
                        target = v
                        break
                if target is None:
                    target = V_DIV_VALUES[-1]
            else:
                target = None
                for v in reversed(V_DIV_VALUES):
                    if v < current * 0.9999:
                        target = v
                        break
                if target is None:
                    target = V_DIV_VALUES[0]

        self._updating_display = True
        self.v_div_display.set(format_si(target, self._active_unit()))
        self._updating_display = False
        self.v_div.set(target)
        self.on_v_div_change()

    def _step_t_div(self, direction):
        """Incrementa o decrementa t_div al TIME_DIV_VALUES vecino, partiendo
        SIEMPRE del valor actual de self.t_div (no del texto del display).
        El 'up' está capado: no permite alejarse más allá del t_div que hace
        que la traza completa ocupe H_DIVS divisiones."""
        current = self.t_div.get()

        # Cap superior: el t_div máximo útil es t_total/H_DIVS (más allá
        # queda pantalla vacía a la derecha de la traza).
        cap = (self.t_total / H_DIVS) if (self.t_total and self.t_total > 0) else None

        if direction == "up":
            if cap is not None and current >= cap * 0.9999:
                return  # ya estamos en el máximo útil
            target = None
            for v in TIME_DIV_VALUES:
                if v > current * 1.0001:
                    if cap is not None and v > cap:
                        # El siguiente canónico excede el cap: snapear al cap exacto
                        target = cap
                    else:
                        target = v
                    break
            if target is None:
                target = cap if cap is not None else TIME_DIV_VALUES[-1]
        else:
            target = None
            for v in reversed(TIME_DIV_VALUES):
                if v < current * 0.9999:
                    target = v
                    break
            if target is None:
                target = TIME_DIV_VALUES[0]

        self._updating_display = True
        self.t_div_display.set(format_si(target, "s"))
        self._updating_display = False
        self.t_div.set(target)
        self.on_time_div_change()

    def _on_t_div_spin_arrow(self):
        """Callback de las flechas del spinbox t_div. Ignora el valor que Tk
        haya puesto (incremento de 1 no tiene sentido aquí) y salta al
        TIME_DIV_VALUES vecino hacia arriba o hacia abajo según el delta
        aproximado."""
        try:
            new_text = self.t_div_display.get()
            new_val = parse_si(new_text, "s")
        except Exception:
            new_val = None
        current = self.t_div.get()
        # Dirección: si Tk subió, vamos al vecino mayor; si bajó, al menor
        direction = "up" if (new_val is not None and new_val > current) else "down"
        # Buscar el vecino canónico
        if direction == "up":
            target = None
            for v in TIME_DIV_VALUES:
                if v > current * 1.0001:
                    target = v
                    break
            if target is None:
                target = TIME_DIV_VALUES[-1]
        else:
            target = None
            for v in reversed(TIME_DIV_VALUES):
                if v < current * 0.9999:
                    target = v
                    break
            if target is None:
                target = TIME_DIV_VALUES[0]

        self._updating_display = True
        self.t_div_display.set(format_si(target, "s"))
        self._updating_display = False
        self.t_div.set(target)
        self.on_time_div_change()

    def _on_t_div_display_changed(self, *args):
        # El trace solo reacciona cuando el usuario escribe texto en directo.
        # NO aplica snap aquí (las flechas ya saltan por _on_t_div_spin_arrow,
        # y Enter/FocusOut los gestiona _on_t_div_typed).
        if getattr(self, "_updating_display", False):
            return
        text = self.t_div_display.get()
        val = parse_si(text, "s")
        if val is None or val <= 0:
            return
        if abs(val - self.t_div.get()) < 1e-18:
            return
        self.t_div.set(val)
        self.on_time_div_change()

    def _on_t_div_typed(self):
        text = self.t_div_display.get()
        val = parse_si(text, "s")
        if val is None or val <= 0:
            self._updating_display = True
            self.t_div_display.set(format_si(self.t_div.get(), "s"))
            self._updating_display = False
            return
        # Si el valor ya coincide con el actual (p.ej. tras auto_fit con valor
        # no-canónico), solo reformateamos el texto y NO hacemos snap.
        # Sin esto, un focus-out sobre un valor exacto se "saltaba" al
        # canónico más cercano.
        if abs(val - self.t_div.get()) < max(self.t_div.get() * 1e-6, 1e-18):
            self._updating_display = True
            self.t_div_display.set(format_si(self.t_div.get(), "s"))
            self._updating_display = False
            return
        # Valor distinto al actual → snap al canónico más cercano
        val = self._snap_t_div_nearest(val)
        self.t_div.set(val)
        self._updating_display = True
        self.t_div_display.set(format_si(val, "s"))
        self._updating_display = False
        self.on_time_div_change()

    # -----------------------------
    def center_trace(self):
        """Centra la traza del canal activo (offset = media de la señal)."""
        if not self.channels:
            return
        ch = self.channels[self.active_channel_idx]
        v = ch["voltage"]
        if len(v) == 0:
            return
        mid = float((np.max(v) + np.min(v)) / 2.0)
        ch["v_offset"] = mid
        self.v_offset.set(mid)
        self.v_slider.set(-mid)
        self.update_plot()

    def _snap_horizontal_to_zero(self):
        """Ajusta t_offset para que el instante 't = 0 absoluto del CSV'
        (i.e. -time_origin internamente) caiga exactamente sobre un tick
        del grid.

        Cómo funciona:
          - Solo aplica en modo Absolute (time_show_absolute=True).
          - El tiempo absoluto en la posición de pantalla x = k*t_div es:
                t_abs(k) = t_offset + k*t_div + time_origin
          - Queremos t_abs(k) = 0  →  t_offset = -k*t_div - time_origin
          - Elegimos k entero en [0, H_DIVS] que produce un t_offset
            válido (≥ 0 y ≤ max_offset) y más cercano al t_offset actual.
          - Si no hay ningún k válido (porque time_origin queda fuera
            del rango cubrible por t_offset), no hacemos nada y notificamos.
        """
        if self.time is None:
            self._show_toast("Load a CSV first")
            return
        if not getattr(self, "time_show_absolute", None) or \
                not self.time_show_absolute.get():
            self._show_toast("Switch Time axis to 'Absolute' in Appearance")
            return

        t_div = self.t_div.get()
        if t_div <= 0:
            return
        time_origin = self.time_origin
        window = t_div * H_DIVS
        max_offset = max(0.0, self.t_total - window)

        # Candidatos: t_offset = -k*t_div - time_origin, para k=0..H_DIVS.
        # Filtrar los que estén dentro del rango [0, max_offset].
        candidates = []
        for k in range(H_DIVS + 1):
            cand = -k * t_div - time_origin
            if 0.0 <= cand <= max_offset + t_div * 1e-6:
                candidates.append(cand)

        if not candidates:
            # No hay forma de alinear t=0 con un tick del grid actual.
            # Probablemente time_origin es positivo (no había pre-trigger)
            # o el zoom es demasiado grande/pequeño.
            if time_origin >= 0:
                self._show_toast("t=0 already at start (no pre-trigger)")
            else:
                self._show_toast("Cannot align: change T/div first")
            return

        # Elegir el candidato más cercano al t_offset actual (mínimo salto).
        cur = self.t_offset.get()
        best = min(candidates, key=lambda c: abs(c - cur))
        self.t_offset.set(best)
        try:
            self.h_slider.set(best)
        except Exception:
            pass
        self.update_plot()

    def _update_snap_t0_state(self):
        """Habilita/deshabilita el botón 'Snap horizontal to t=0' según
        si el modo Absolute está activo y hay un CSV cargado. Llamar desde:
          - construcción inicial de la UI
          - toggle del modo en Appearance
          - tras cargar/descargar un CSV
        """
        btn = getattr(self, "_snap_t0_btn", None)
        if btn is None:
            return
        try:
            absolute = (self.time_show_absolute.get()
                        if hasattr(self, "time_show_absolute") else False)
            has_csv = (self.time is not None and len(self.channels) > 0)
            btn.config(state=("normal" if (absolute and has_csv)
                              else "disabled"))
        except Exception:
            pass
