"""OscilloscopeApp._apply_theme_immediate con un self mínimo (sin UI)."""
import os
import tempfile
import tkinter as tk
import unittest
from unittest import mock

import main
from ocv.core import config
from ocv.ui import theme


class FakeApp:
    """Lo justo que usa _apply_theme_immediate."""

    def __init__(self, root):
        self.root = root
        self.grid_theme = {}
        self.redraws = 0

    def update_plot(self):
        self.redraws += 1

    def _gear_redraw(self):
        pass

    def _show_toast(self, msg):
        self.toast = msg


class ApplyThemeImmediate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as e:
            raise unittest.SkipTest(f"Tk no disponible: {e}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        theme.apply_theme(cls.root, "Dark")
        cls.root.destroy()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OCV_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        config.save_user_config({"grid_theme": {"bg": "#123456"}})
        self.app = FakeApp(self.root)

    def apply(self, name, sync_plot):
        main.OscilloscopeApp._apply_theme_immediate(self.app, name, sync_plot)

    def test_global_theme_drops_custom_grid(self):
        """Tema global (sync_plot) borra la personalización Advanced
        guardada y adopta los colores PLOT_* del tema."""
        self.apply("Blueprint", sync_plot=True)
        cfg = config.load_user_config()
        self.assertNotIn("grid_theme", cfg)
        self.assertEqual(cfg.get("app_theme"), "Blueprint")
        self.assertEqual(self.app.grid_theme["bg"],
                         theme.THEMES["Blueprint"]["PLOT_BG"])
        self.assertEqual(self.app.redraws, 1)

    def test_chrome_only_keeps_custom_grid(self):
        """Sin sync_plot el plot conserva su personalización."""
        self.apply("Light", sync_plot=False)
        self.assertEqual(config.load_user_config().get("grid_theme"),
                         {"bg": "#123456"})
        self.assertEqual(self.app.grid_theme, {})


if __name__ == "__main__":
    unittest.main()
