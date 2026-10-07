"""Read-only comparison of the current RDF declarations and live Fuseki.

Run after start.cmd. Missing source declarations stay missing; no triples are
inserted, deleted or inferred, and worksheet examples are never treated as facts.
"""
import json
from pathlib import Path
import sys

from model_source import NAMESPACE, model_check_query, model_info_from_bytes, model_path, parse_model, read_model
from rdflib import BNode, Graph, URIRef
from rdflib.compare import isomorphic
from rdflib.namespace import OWL, RDF, RDFS
import server

ROOT = Path(__file__).resolve().parent
FIELDS_TO_CHECK = (
    "isReliable", "reliabilityEvidence", "distanceKm", "distanceFromMaryKm",
    "requiresPersonalInformationForSearch", "searchRequirementEvidence",
    "distanceFromChenKm", "nearbyRadiusKm",
)


def declaration(graph, iri):
    """Copy a property's actual type/domain/range/inverse declaration."""
    result = Graph()
    pending = []
    for predicate in (RDF.type, RDFS.domain, RDFS.range, OWL.inverseOf):
        for triple in graph.triples((iri, predicate, None)):
            result.add(triple)
            if isinstance(triple[2], BNode):
                pending.append(triple[2])
    visited = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        for triple in graph.triples((node, None, None)):
            result.add(triple)
            if isinstance(triple[2], BNode):
                pending.append(triple[2])
    return result


def verify():
    path = model_path(ROOT)
    content = read_model(ROOT)
    source = parse_model(content)
    info = model_info_from_bytes(content, path.name)
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    fuseki = server.Fuseki(config["dataset_url"])
    print("Source: " + info["filename"])
    print("Current source SHA-256: " + info["sha256"])
    print("Ontology version: " + info["version"])
    print("Source counts: " + json.dumps(info["counts"], sort_keys=True))
    print("Database: " + config["dataset_url"])
    if not fuseki.ask(model_check_query(ROOT)):
        raise ValueError("Fuseki is missing schema declared in the current source RDF. Synchronize the source first.")
    live = Graph()
    try:
        live.parse(data=fuseki.export(), format="turtle")
    except server.FusekiError:
        raise
    except Exception as exc:
        raise ValueError("Fuseki did not return a valid RDF graph.") from exc
    for field in FIELDS_TO_CHECK:
        iri = URIRef(NAMESPACE + field)
        expected, actual = declaration(source, iri), declaration(live, iri)
        if not isomorphic(expected, actual):
            raise ValueError("Fuseki declaration does not match the current source RDF: " + str(iri))
        if not len(expected):
            print("PASS " + field + " | no declaration in source or database")
        else:
            domains = ", ".join(str(value).split("#")[-1] if isinstance(value, URIRef) else "anonymous expression"
                                for value in sorted(source.objects(iri, RDFS.domain), key=str)) or "not declared"
            ranges = ", ".join(str(value).split("#")[-1] if isinstance(value, URIRef) else "anonymous expression"
                               for value in sorted(source.objects(iri, RDFS.range), key=str)) or "not declared"
            print("PASS " + field + " | " + domains + " | " + ranges)
    if read_model(ROOT) != content or model_path(ROOT) != path:
        raise ValueError("The source RDF changed during verification. Run this check again.")
    print("VERIFIED: source schema and assessment field declarations match Fuseki. No data changed.")


if __name__ == "__main__":
    try:
        verify()
    except (OSError, ValueError, server.APIError, server.FusekiError) as error:
        print("NOT VERIFIED: " + str(error), file=sys.stderr)
        print("Start this project's website first. Check config.json and the source RDF.", file=sys.stderr)
        sys.exit(1)
