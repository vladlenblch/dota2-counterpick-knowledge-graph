"""Ask emits only its answer and handles cancellation and user errors."""
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from rdflib import Graph, RDF, RDFS

from dota_kg.__main__ import main
from dota_kg.ontology import literal
from dota_kg.ontology_schema import KG, RES


class CliTests(unittest.TestCase):
    def test_ask_json_has_no_progress_messages(self):
        graph = Graph()
        graph.add((RES["hero/109"], RDF.type, KG.Hero))
        graph.add((RES["hero/109"], RDFS.label, literal("Terrorblade")))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["dota_kg", "ask", "кто контрит тб", "--format", "json"]), \
                patch("dota_kg.ontology.load_ontology", return_value=graph) as loader, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            main()
        self.assertEqual(json.loads(stdout.getvalue())["status"], "no_data")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("progress", loader.call_args.kwargs)
        self.assertTrue(loader.call_args.kwargs["cache"])

    def test_invalid_limit_does_not_load_graph(self):
        with patch("sys.argv", ["dota_kg", "ask", "кто контрит тб", "--limit", "0"]), \
                patch("dota_kg.ontology.load_ontology") as loader, redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 1)
        loader.assert_not_called()

    def test_interrupt_has_no_traceback(self):
        stderr = io.StringIO()
        with patch("sys.argv", ["dota_kg", "ask", "кто контрит тб"]), \
                patch("dota_kg.ontology.load_ontology", side_effect=KeyboardInterrupt), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 130)
        self.assertIn("отменено", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_missing_dependency_points_to_project_environment(self):
        stderr = io.StringIO()
        with patch("sys.argv", ["dota_kg", "ask", "кто контрит тб"]), \
                patch("dota_kg.ontology.load_ontology", side_effect=ModuleNotFoundError(name="rdflib")), \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as error:
            main()
        self.assertEqual(error.exception.code, 1)
        self.assertIn("rdflib", stderr.getvalue())
        self.assertIn(".venv", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
