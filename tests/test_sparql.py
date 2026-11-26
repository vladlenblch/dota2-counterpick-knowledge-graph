"""Local SPARQL execution with typed results and fully local imports."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, Namespace, RDFS, URIRef, XSD
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
        load.assert_called_once_with(self.ontology, cache=True)
        self.assertEqual(count, 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result["head"]["vars"], ["hero", "value", "missing", "text"])
        row = result["results"]["bindings"][0]
        self.assertEqual(row["hero"], {"type": "uri", "value": str(EX.rubick)})
        self.assertEqual(row["value"], {"type": "literal", "value": "0",
                                        "datatype": str(XSD.integer)})
        self.assertNotIn("missing", row)
        self.assertEqual(row["text"]["value"], "SERVICE and FROM are literal text")

    def test_text_uses_labels_and_short_names_without_changing_exported_uris(self):
        unknown = URIRef("https://example.invalid/resource/unlabeled")
        fragment = URIRef("https://example.invalid/schema#Mechanic")
        self.graph.add((EX.rubick, EX.related, unknown))
        self.graph.add((EX.rubick, RDFS.label, Literal("Rubick", lang="en")))
        self.graph.add((unknown, EX.kind, fragment))
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            SELECT ?hero ?other ?kind ?value WHERE {
                ?hero ex:value ?value ; ex:related ?other .
                ?other ex:kind ?kind .
            }
        ''')
        for output_format in ("text", "json", "csv"):
            with self.subTest(output_format=output_format):
                output = io.StringIO()
                with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
                    count = run_query(path, directory=self.ontology, output_format=output_format,
                                      output=output, progress=io.StringIO())
                self.assertEqual(count, 1)
                text = output.getvalue()
                if output_format == "text":
                    for cell in ('Герой: Рубик','other: unlabeled','Тип условия: Mechanic','value: 0'):
                        self.assertIn(cell, text)
                    self.assertNotIn(' | ',text)
                    self.assertNotIn("https://", text)
                    self.assertNotIn(str(EX.rubick), text)
                else:
                    self.assertIn(str(EX.rubick), text)
                    self.assertIn(str(unknown), text)
                    self.assertIn(str(fragment), text)

    def test_text_keeps_literal_urls_and_wraps_multiline_labels(self):
        self.graph.set((EX.rubick, RDFS.label, Literal("Рубик\nTelekinesis", lang="ru")))
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            SELECT ?hero ?url WHERE {
                ?hero ex:value ?value .
                BIND("https://example.invalid/literal" AS ?url)
            }
        ''')
        output = io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
            run_query(path, directory=self.ontology, output=output, progress=io.StringIO())
        self.assertIn("Рубик Telekinesis", output.getvalue())
        self.assertIn("https://example.invalid/literal", output.getvalue())

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

    def test_text_outputs_only_short_answers_with_multiple_candidates(self):
        path = self.query_file('''
            SELECT ?hero ?answer WHERE {
              VALUES (?hero ?answer) {
                ("Lion" "Lion может прервать применение через Earth Spike.")
                ("Beastmaster" "Beastmaster может прервать применение через Primal Roar.")
              }
            } ORDER BY ?hero
        ''')
        output = io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
            count = run_query(path, directory=self.ontology, output_format="text",
                              output=output, progress=io.StringIO())
        self.assertEqual(count, 2)
        self.assertEqual(output.getvalue().splitlines(), [
            "- Beastmaster может прервать применение через Primal Roar.",
            "- Lion может прервать применение через Earth Spike."])

    def test_default_text_supports_select_without_answer_and_boolean_queries(self):
        for query, expected in (("SELECT ?value WHERE { BIND(0 AS ?value) }", 'value: 0'),
                                ("ASK {}", 'Да.'), ("ASK { <urn:missing> <urn:missing> ?o }", 'Нет.')):
            with self.subTest(query=query):
                output=io.StringIO()
                with patch('dota_kg.ontology.load_ontology',return_value=self.graph):
                    run_query(self.query_file(query),directory=self.ontology,
                              output=output,progress=io.StringIO())
                self.assertIn(expected,output.getvalue())

    def test_long_explanations_are_wrapped_into_separate_records(self):
        explanation='Длинное объяснение взаимодействия. '*20
        self.graph.add((EX.rubick,EX.long,Literal(explanation)))
        path=self.query_file('SELECT ?evidence WHERE { <urn:stat:rubick> <urn:stat:long> ?evidence }')
        output=io.StringIO()
        with patch('dota_kg.ontology.load_ontology',return_value=self.graph):
            run_query(path,directory=self.ontology,output=output,progress=io.StringIO())
        self.assertTrue(all(len(line)<=88 for line in output.getvalue().splitlines()))
        self.assertEqual(' '.join(output.getvalue().split())[len('1. Объяснение: '):],explanation.strip())
        self.assertNotIn('-+-',output.getvalue())

    def test_text_rejects_missing_answers_without_printing_partial_results(self):
        path = self.query_file('SELECT ?answer WHERE { VALUES ?answer { "Known" UNDEF } }')
        output = io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
            with self.assertRaisesRegex(RuntimeError, "без значения"):
                run_query(path, directory=self.ontology, output_format="text",
                          output=output, progress=io.StringIO())
        self.assertEqual(output.getvalue(), "")

    def test_text_empty_result_reports_missing_candidates(self):
        path = self.query_file('SELECT ?answer WHERE { <urn:missing> <urn:missing> ?answer }')
        output = io.StringIO()
        with patch("dota_kg.ontology.load_ontology", return_value=self.graph):
            count = run_query(path, directory=self.ontology, output_format="text",
                              output=output, progress=io.StringIO())
        self.assertEqual(count, 0)
        self.assertEqual(output.getvalue(), "В размеченных данных подходящих вариантов не найдено.\n")

    def test_unbound_rows_are_preserved_in_text_and_csv(self):
        path = self.query_file('''
            PREFIX ex: <urn:stat:>
            SELECT ?missing WHERE {
                ex:rubick ex:value ?value .
                OPTIONAL { ex:rubick ex:missing ?missing }
            }
        ''')
        for output_format in ("text", "csv"):
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
                    self.assertIn("missing: Нет значения",output.getvalue())

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
        load.assert_called_once_with(self.ontology, cache=True)
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

    def test_custom_description_column_is_localized_without_changing_json_export(self):
        source='Shackleshot can be disjointed.'
        self.graph.add((EX.rubick,EX.description,Literal(source)))
        path=self.query_file('SELECT ?description WHERE { <urn:stat:rubick> <urn:stat:description> ?description }')
        for fmt in ('text','json'):
            output=io.StringIO()
            with patch('dota_kg.ontology.load_ontology',return_value=self.graph):
                run_query(path,directory=self.ontology,output_format=fmt,
                          output=output,progress=io.StringIO())
            if fmt=='text':
                self.assertIn('Снаряд этой способности можно сбить с цели.',output.getvalue())
                self.assertNotIn('can be disjointed',output.getvalue())
            else:
                self.assertIn(source,output.getvalue())

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
