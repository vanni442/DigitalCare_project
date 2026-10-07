"""Validate and describe the editable RDF source used by the local website.

The source file is authoritative. Its digest describes the current bytes; it is
not an approval pin. No missing schema or instance data is supplied by Python.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
if VENDOR.is_dir() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

import rdflib
from rdflib import BNode, Graph, URIRef
from rdflib.namespace import OWL as OWL_NS, RDF as RDF_NS, RDFS as RDFS_NS

# Preserve source lexical values when constructing source revision deltas.
rdflib.NORMALIZE_LITERALS = False

# Existing databases/backups stay in their original storage directory.
STORAGE_VERSION = "v7"
MODEL_VERSION = "v8"
MODEL_FILENAME = "DigitalCare_TheStar_ass2_v8.rdf"
RDF = str(RDF_NS)
RDFS = str(RDFS_NS)
OWL = str(OWL_NS)
NAMESPACE = "http://uow.edu.au/isit906/thestar/digitalcare#"
ONTOLOGY_IRI = NAMESPACE.rstrip("#")
LEGACY_CAPTURE_NAMESPACE = "http://uow.edu.au/isit906/thestar/digitalcare/capture#"


class ModelSourceError(ValueError):
    """The editable source is unavailable or is not a valid DigitalCare model."""


def model_path(root=ROOT):
    """Resolve config.json's model_file, with the V8 file as the default."""
    root = Path(root)
    config_path = root / "config.json"
    try:
        config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
        filename = config.get("model_file", "model/" + MODEL_FILENAME)
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("model_file is not a path")
        return root / filename
    except (OSError, ValueError, AttributeError) as exc:
        raise ModelSourceError("Set model_file in config.json to the source RDF file path.") from exc


def parse_model(content):
    """Validate UTF-8 RDF/XML and its DigitalCare ontology, then return a Graph.

    Parsing never follows imports, repairs declarations, or accesses Fuseki.
    Callers must finish this validation before changing the live graph.
    """
    if not isinstance(content, bytes):
        raise ModelSourceError("The source RDF must be supplied as bytes.")
    try:
        xml = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ModelSourceError("Save the source RDF/XML with UTF-8 encoding.") from exc
    # Reject entity declarations before either parser can resolve or expand them.
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise ModelSourceError("The source RDF/XML must not contain DTD or entity declarations.")
    try:
        ET.fromstring(content)
        graph = Graph()
        graph.parse(data=content, format="xml", publicID=ONTOLOGY_IRI)
        for triple in graph:
            for term in triple:
                if isinstance(term, URIRef):
                    term.n3()  # Reject malformed IRIs before generating SPARQL.
    except Exception as exc:
        raise ModelSourceError("The source model is not valid RDF/XML. Correct the file and try again.") from exc
    ontologies = set(graph.subjects(RDF_NS.type, OWL_NS.Ontology))
    if not any(isinstance(iri, URIRef) and str(iri).rstrip("#/") == ONTOLOGY_IRI
               for iri in ontologies):
        raise ModelSourceError("The source RDF must declare the DigitalCare owl:Ontology.")
    return graph


def read_model(root=ROOT):
    """Return the current validated source bytes without changing the file."""
    path = model_path(root)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ModelSourceError("Cannot read the source RDF: " + str(path)) from exc
    parse_model(content)
    return content


def _count_instances(graph, class_iri):
    # Implements rdf:type/rdfs:subClassOf* from queries/counts.rq, including
    # cycles/multiple paths without counting the same record more than once.
    classes, pending = set(), [URIRef(class_iri)]
    while pending:
        node = pending.pop()
        if node in classes:
            continue
        classes.add(node)
        pending.extend(graph.subjects(RDFS_NS.subClassOf, node))
    return len({record for node in classes for record in graph.subjects(RDF_NS.type, node)
                if isinstance(record, URIRef) and str(record).startswith(NAMESPACE)})


def _source_version(graph):
    ontologies = sorted(graph.subjects(RDF_NS.type, OWL_NS.Ontology), key=str)
    ontologies.sort(key=lambda iri: str(iri).rstrip("#/") != ONTOLOGY_IRI)
    for predicate in (OWL_NS.versionIRI, OWL_NS.versionInfo):
        for ontology in ontologies:
            for term in sorted(graph.objects(ontology, predicate), key=str):
                value = re.split(r"[/#]", str(term).rstrip("/#"))[-1]
                if value:
                    numeric = re.fullmatch(r"(?:version|v)?(\d+(?:\.\d+)*)", value, re.IGNORECASE)
                    return "v" + numeric.group(1) if numeric else value
    return MODEL_VERSION


def model_info_from_bytes(content, filename=MODEL_FILENAME):
    """Compute metadata from one validated byte snapshot of the RDF source."""
    graph = parse_model(content)
    return {
        "filename": Path(filename).name,
        "sha256": hashlib.sha256(content).hexdigest(),
        "version": _source_version(graph),
        "counts": {
            "guides": _count_instances(graph, NAMESPACE + "Guide"),
            "reviewers": _count_instances(graph, NAMESPACE + "HealthcareProfessional"),
            "individuals": len(set(graph.subjects(RDF_NS.type, OWL_NS.NamedIndividual))),
            "reviewLinks": sum(1 for _ in graph.triples((None, URIRef(NAMESPACE + "isReviewedBy"), None))),
        },
    }


def model_info(root=ROOT):
    """Return filename, current SHA-256, ontology version and source counts."""
    path = model_path(root)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ModelSourceError("Cannot read the source RDF: " + str(path)) from exc
    return model_info_from_bytes(content, path.name)


def model_check_query(root=ROOT):
    """Build a read-only ASK matching the schema declared in the current RDF.

    Anonymous class expressions use query variables because blank-node labels
    belong to one parse only. Existing user-created instances are allowed.
    """
    graph = parse_model(read_model(root))
    schema_types = {
        OWL_NS.Ontology, OWL_NS.Class, OWL_NS.ObjectProperty,
        OWL_NS.DatatypeProperty, OWL_NS.AnnotationProperty, OWL_NS.Restriction,
        OWL_NS.AllDisjointClasses, OWL_NS.AllDisjointProperties, OWL_NS.AllDifferent,
        RDFS_NS.Class, RDFS_NS.Datatype, RDF_NS.Property,
    }
    structural = {
        RDF_NS.type, RDFS_NS.domain, RDFS_NS.range, RDFS_NS.subClassOf,
        RDFS_NS.subPropertyOf, OWL_NS.versionIRI, OWL_NS.versionInfo,
        OWL_NS.equivalentClass, OWL_NS.equivalentProperty, OWL_NS.inverseOf,
        OWL_NS.disjointWith, OWL_NS.propertyDisjointWith, OWL_NS.disjointUnionOf,
        OWL_NS.unionOf, OWL_NS.intersectionOf, OWL_NS.complementOf, OWL_NS.oneOf,
        OWL_NS.propertyChainAxiom, OWL_NS.hasKey,
    }
    subjects = {subject for kind in schema_types for subject in graph.subjects(RDF_NS.type, kind)}
    triples = {triple for subject in subjects for triple in graph.triples((subject, None, None))
               if triple[1] in structural}
    # RDF/XML can declare a domain or subclass edge in rdf:Description without
    # an accompanying owl:Class/Property type statement.
    triples.update(triple for predicate in structural - {RDF_NS.type}
                   for triple in graph.triples((None, predicate, None)))
    pending = [term for triple in triples for term in (triple[0], triple[2]) if isinstance(term, BNode)]
    visited = set()
    while pending:
        node = pending.pop()
        if node in visited:
            continue
        visited.add(node)
        for triple in graph.triples((node, None, None)):
            triples.add(triple)
            if isinstance(triple[2], BNode):
                pending.append(triple[2])
    blank_nodes = sorted({term for triple in triples for term in triple if isinstance(term, BNode)}, key=str)
    variables = {node: "?schemaNode" + str(index) for index, node in enumerate(blank_nodes)}

    def term_text(term):
        return variables[term] if isinstance(term, BNode) else term.n3()

    # Check independent anonymous expressions independently. One flat join can
    # produce a large cross product when the live graph lacks a late statement.
    named = [triple for triple in triples if not any(isinstance(term, BNode) for term in triple)]
    named.sort(key=lambda triple: tuple(map(str, triple)))
    groups = [named[index:index + 20] for index in range(0, len(named), 20)]
    by_node = {node: set() for node in blank_nodes}
    for triple in triples:
        for term in triple:
            if isinstance(term, BNode):
                by_node[term].add(triple)
    remaining = set(blank_nodes)
    while remaining:
        pending = [min(remaining, key=str)]
        component = set()
        while pending:
            node = pending.pop()
            if node not in remaining:
                continue
            remaining.remove(node)
            component.update(by_node[node])
            pending.extend(term for triple in by_node[node] for term in triple
                           if isinstance(term, BNode) and term in remaining)
        groups.append(component)
    clauses = []
    for group in groups:
        statements = sorted(" ".join(term_text(term) for term in triple) + " ." for triple in group)
        clauses.append("FILTER EXISTS {\n" + "\n".join(statements) + "\n}")
    return "ASK {\n" + "\n".join(clauses) + "\n}"
