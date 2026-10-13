"""config.json: lectura, mezcla, borrado y redirección por entorno."""
import json
import os
import tempfile
import unittest
from unittest import mock

from ocv.core import config


class UserConfig(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        env = mock.patch.dict(os.environ, {"OCV_CONFIG_DIR": self._tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.path = os.path.join(self._tmp.name, "config.json")

    def test_env_redirects(self):
        """OCV_CONFIG_DIR manda sobre la carpeta de la app."""
        self.assertEqual(config._user_config_path(), self.path)

    def test_missing_is_empty(self):
        self.assertEqual(config.load_user_config(), {})

    def test_merge_and_delete(self):
        """save mezcla claves; None borra la clave."""
        self.assertTrue(config.save_user_config({"a": 1, "b": {"x": 2}}))
        self.assertTrue(config.save_user_config({"c": "z", "a": None}))
        self.assertEqual(config.load_user_config(), {"b": {"x": 2}, "c": "z"})

    def test_corrupt_or_wrong_type(self):
        """JSON roto o no-dict → {} sin excepción."""
        for text in ["{not json", "[1, 2]", ""]:
            with self.subTest(text=text):
                with open(self.path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                self.assertEqual(config.load_user_config(), {})

    def test_unicode_roundtrip(self):
        config.save_user_config({"unit": "µs", "deg": "°C"})
        with open(self.path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["unit"], "µs")

    def test_default_dir_is_project_root(self):
        """Sin variable de entorno: carpeta de main.py (modo portable)."""
        with mock.patch.dict(os.environ, {}, clear=True):
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            self.assertEqual(config.config_dir(), root)


if __name__ == "__main__":
    unittest.main()
