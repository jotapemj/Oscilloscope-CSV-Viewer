"""Ficheros de sesión .ocv: escritura, lectura, versiones y errores."""
import json
import os
import tempfile
import unittest

from ocv.core import session


class SessionFile(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "s.ocv")

    def payload(self, **over):
        p = {"version": session.SESSION_VERSION, "app": "OscilloscopeCSVViewer",
             "csv_name": "capture.csv", "csv_content": "Time,CH1\n0,1\n1,2\n",
             "channels": [{"idx": 0, "name": "CH1", "unit": "µs"}]}
        p.update(over)
        return p

    def write_raw(self, text):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def error_of(self):
        with self.assertRaises(session.SessionError) as cm:
            session.read_session(self.path)
        return str(cm.exception)

    def test_roundtrip(self):
        """write → read conserva todo, Unicode incluido."""
        p = self.payload()
        session.write_session(self.path, p)
        self.assertEqual(session.read_session(self.path), p)
        with open(self.path, encoding="utf-8") as fh:
            self.assertIn("µs", fh.read())      # sin escapes \\u

    def test_embedded_csv(self):
        content, name = session.embedded_csv(self.payload())
        self.assertEqual((content, name), ("Time,CH1\n0,1\n1,2\n", "capture.csv"))
        _, name = session.embedded_csv(self.payload(csv_name=None))
        self.assertEqual(name, "embedded.csv")

    def test_missing_csv(self):
        for bad in ("", None, 42):
            with self.subTest(csv_content=bad):
                with self.assertRaises(session.SessionError):
                    session.embedded_csv(self.payload(csv_content=bad))

    def test_legacy_rejected(self):
        """Formato v1 (.oscsession): ruptura limpia con mensaje claro."""
        session.write_session(self.path, self.payload(version=1))
        self.assertIn("legacy v1 format", self.error_of())

    def test_newer_loads_but_flags(self):
        p = self.payload(version=session.SESSION_VERSION + 1)
        session.write_session(self.path, p)
        self.assertTrue(session.is_newer(session.read_session(self.path)))
        self.assertFalse(session.is_newer(self.payload()))

    def test_bad_files(self):
        """No encontrado, JSON roto, sin versión, no-dict."""
        self.assertIn("not found", self.error_of())
        self.write_raw("{broken")
        self.assertIn("not valid JSON", self.error_of())
        for text in ['{"app": "x"}', "[1, 2]"]:
            with self.subTest(text=text):
                self.write_raw(text)
                self.assertIn("valid .ocv", self.error_of())


class Annotations(unittest.TestCase):

    def test_serialize(self):
        """Tuplas → listas; tipos desconocidos y corruptos se saltan."""
        annots = [
            {"type": "stroke", "color": "#f00", "width": 2,
             "points": [(0.1, 1.0), (0.2, 1.5)], "ch_idx": 1},
            {"type": "text", "color": "#0f0", "size": 10, "pos": (0.5, 2.0),
             "content": "ringing", "ch_idx": 0},
            {"type": "arrow"},
            {"type": "stroke", "points": [1, 2]},      # puntos corruptos
        ]
        out = session.serialize_annotations(annots)
        self.assertEqual(out, [
            {"type": "stroke", "color": "#f00", "width": 2,
             "points": [[0.1, 1.0], [0.2, 1.5]], "ch_idx": 1},
            {"type": "text", "color": "#0f0", "size": 10, "pos": [0.5, 2.0],
             "content": "ringing", "ch_idx": 0},
        ])
        json.dumps(out)                                 # serializable


if __name__ == "__main__":
    unittest.main()
