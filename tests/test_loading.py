"""The fast loader must retain local imports, exact RDF terms and invalidation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF, URIRef
from rdflib.compare import isomorphic

from dota_kg.ontology import load_ontology


class LoadingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        (self.directory / "catalog-v001.xml").write_text('''
            <catalog xmlns="urn:oasis:names:tc:entity:xmlns:xml:catalog">
              <uri name="urn:schema" uri="schema.ttl"/>
            </catalog>''', encoding="utf-8")
        (self.directory / "game.ttl").write_text('''
            @prefix owl: <http://www.w3.org/2002/07/owl#> .
            <urn:game> owl:imports <urn:schema> .
            <urn:hero> <urn:number> "1.2345678901234567"^^<http://www.w3.org/2001/XMLSchema#double> ;
                       <urn:label> "Герой"@ru ; <urn:blank> [ <urn:zero> 0 ] .
            ''', encoding="utf-8")
        (self.directory / "schema.ttl").write_text('<urn:class> a <urn:Class> .', encoding="utf-8")

    def test_cache_preserves_graph_and_skips_turtle_parsing(self):
        original = load_ontology(self.directory)
        messages = []
        uncached = load_ontology(self.directory, cache=True, progress=messages.append)
        self.assertTrue(isomorphic(original, uncached))
        real_parse = Graph.parse
        formats = []

        def parse(graph, *args, **kwargs):
            formats.append(kwargs.get("format"))
            return real_parse(graph, *args, **kwargs)

        with patch.object(Graph, "parse", parse):
            cached = load_ontology(self.directory, cache=True, progress=messages.append)
        self.assertEqual(formats, [])
        self.assertTrue(isomorphic(original, cached))
        self.assertTrue(any("кэша" in m for m in messages))

    def test_source_change_invalidates_cache(self):
        load_ontology(self.directory, cache=True)
        (self.directory / "schema.ttl").write_text('<urn:new> a <urn:Class> .', encoding="utf-8")
        changed = load_ontology(self.directory, cache=True)
        self.assertIn((URIRef("urn:new"), RDF.type, URIRef("urn:Class")), changed)
        self.assertNotIn((URIRef("urn:class"), RDF.type, URIRef("urn:Class")), changed)

    def test_broken_cache_falls_back_to_originals(self):
        original = load_ontology(self.directory, cache=True)
        cache = self.directory / ".cache/game.json"
        header = cache.read_bytes().splitlines(keepends=True)[0]
        cache.write_bytes(header + b"broken RDF\n")
        self.assertTrue(isomorphic(original, load_ontology(self.directory, cache=True)))

    def test_cache_cannot_bypass_path_or_import_checks(self):
        load_ontology(self.directory, cache=True)
        catalog = self.directory / "catalog-v001.xml"
        catalog.write_text(catalog.read_text(encoding="utf-8").replace('uri="schema.ttl"', 'uri="../schema.ttl"'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "bundled local"):
            load_ontology(self.directory, cache=True)

    def test_unknown_import_is_rejected_without_network(self):
        (self.directory / "game.ttl").write_text('''
            <urn:game> <http://www.w3.org/2002/07/owl#imports> <https://example.invalid/remote> .
            ''', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unmapped"):
            load_ontology(self.directory, cache=True)


if __name__ == "__main__":
    unittest.main()
