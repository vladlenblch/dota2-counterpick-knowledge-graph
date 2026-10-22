"""Local SPARQL execution with typed results and fully local imports."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, Namespace, RDFS, XSD
from rdflib.compare import isomorphic

from dota_kg.ontology_queries import run_query


EX = Namespace("urn:stat:")


class SparqlRunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.ontology = self.directory / "ontology"
        self.graph = Graph()
        self.graph.add((EX.rubick, EX.value, Literal(0, datatype=XSD.integer)))
        self.graph.add((EX.rubick, RDFS.label, Literal("Рубик", lang="ru")))
        self.graph.add((EX.rubick, EX.evidence,
                        Literal('Текст, "цитата"\nвторая строка')))

    def query_file(self, text, suffix=".rq"):
        path = self.directory / ("query" + suffix)
        path.write_text(text, encoding="utf-8")
        return path

    def test_select_json_keeps_zero_datatype_and_unbound_variables(self):
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            SELECT ?hero ?value ?missing ?text WHERE {
                ?hero ex:value ?value .
                OPTIONAL { ?hero ex:missing ?missing }
                BIND("SERVICE and FROM are literal text" AS ?text)
            }
        ''', suffix=".sparql")
        output, progress = io.StringIO(), io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph) as load:
            count = run_query(path, directory=self.ontology, output_format="json",
                              output=output, progress=progress)
        load.assert_called_once_with(self.ontology)
        self.assertEqual(count, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["head"]["vars"], ["hero", "value", "missing", "text"])
        row = result["results"]["bindings"][0]
        self.assertEqual(row["hero"], {"type": "uri", "value": str(EX.rubick)})
        self.assertEqual(row["value"], {"type": "literal", "value": "0",
                                        "datatype": str(XSD.integer)})
        self.assertNotIn("missing", row)
        self.assertEqual(row["text"]["value"], "SERVICE and FROM are literal text")

    def test_ask_json_preserves_both_boolean_outcomes(self):
        for value, expected in ((0, True), (1, False)):
            with self.subTest(expected=expected):
                path = self.query_file(
                    f"ASK {{ <{EX.rubick}> <{EX.value}> {value} }}")
                output = io.StringIO()
                with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
                    count = run_query(path, directory=self.ontology, output_format="json",
                                      output=output, progress=io.StringIO())
                self.assertEqual(count, 1)
                self.assertIs(json.loads(output.getvalue())["boolean"], expected)

    def test_unbound_rows_are_preserved_in_table_and_csv(self):
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            SELECT ?missing WHERE {
                ex:rubick ex:value ?value .
                OPTIONAL { ex:rubick ex:missing ?missing }
            }
        ''')
        for output_format in ("table", "csv"):
            with self.subTest(output_format=output_format):
                output = io.StringIO()
                with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
                    count = run_query(path, directory=self.ontology, output_format=output_format,
                                      output=output, progress=io.StringIO())
                self.assertEqual(count, 1)
                if output_format == "csv":
                    self.assertEqual(list(csv.reader(io.StringIO(output.getvalue()))),
                                     [["missing"], [""]])
                else:
                    self.assertEqual(len(output.getvalue().splitlines()), 3)

    def test_construct_exports_precise_turtle(self):
        precise = Literal(0.12345678901234566, datatype=XSD.double)
        self.graph.add((EX.rubick, EX.precision, precise))
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
            CONSTRUCT { ?hero ex:copiedLabel ?name ; ex:copiedValue ?value }
            WHERE { ?hero rdfs:label ?name ; ex:precision ?value }
        ''')
        output = io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph) as load:
            count = run_query(path, directory=self.ontology,
                              output_format="turtle", output=output, progress=io.StringIO())
        load.assert_called_once_with(self.ontology)
        self.assertEqual(count, 2)
        restored = Graph().parse(data=output.getvalue(), format="turtle")
        expected = Graph()
        expected.add((EX.rubick, EX.copiedLabel, Literal("Рубик", lang="ru")))
        expected.add((EX.rubick, EX.copiedValue, precise))
        self.assertTrue(isomorphic(restored, expected))

    def test_invalid_or_external_queries_fail_before_loading_data(self):
        queries = (
            "SELECT WHERE {",
            "SELECT * WHERE { SERVICE <https://example.invalid/sparql> { ?s ?p ?o } }",
            "SELECT * WHERE { { SERVICE SILENT <https://example.invalid/> { ?s ?p ?o } } }",
            "SELECT * FROM <https://example.invalid/data.ttl> WHERE { ?s ?p ?o }",
            "SELECT * FROM NAMED <https://example.invalid/data.ttl> WHERE { GRAPH ?g { ?s ?p ?o } }",
        )
        for query in queries:
            with self.subTest(query=query):
                path = self.query_file(query)
                with patch("dota_kg.ontology.load_ontology") as load:
                    with self.assertRaises(RuntimeError):
                        run_query(path, directory=self.ontology, output=io.StringIO(),
                                  progress=io.StringIO())
                load.assert_not_called()

    def test_csv_preserves_russian_evidence_quotes_newlines_and_zero(self):
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            SELECT ?value ?evidence ?missing WHERE {
                ex:rubick ex:value ?value ; ex:evidence ?evidence .
                OPTIONAL { ex:rubick ex:missing ?missing }
            }
        ''')
        output = io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
            count = run_query(path, directory=self.ontology, output_format="csv",
                              output=output, progress=io.StringIO())
        self.assertEqual(count, 1)
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual(rows, [{"value": "0", "evidence": 'Текст, "цитата"\nвторая строка',
                                 "missing": ""}])


if __name__ == "__main__":
    unittest.main()
