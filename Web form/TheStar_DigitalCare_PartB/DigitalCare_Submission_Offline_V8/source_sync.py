"""Synchronize edited RDF sources without discarding unrelated captured records.

The source file owns fields changed since its last import. Other values entered
through the website are kept. Fuseki applies each merge only if the live graph
still matches the snapshot taken before preparation.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.parse import quote
from uuid import uuid4

from model_source import (STORAGE_VERSION, ModelSourceError, model_path,
                          parse_model, model_info_from_bytes)
from rdflib import Graph, BNode, Literal, URIRef
from rdflib.compare import to_isomorphic
from rdflib.namespace import XSD


def normalized(graph):
    """Ignore equivalent Jena decimal/string serialization choices."""
    result = Graph()
    for s, p, o in graph:
        if isinstance(o, Literal) and not o.language:
            if o.datatype is None:
                o = Literal(str(o), datatype=XSD.string)
            elif o.datatype == XSD.decimal:
                try:
                    o = Literal(format(Decimal(str(o)).normalize(), 'f'), datatype=XSD.decimal)
                except Exception:
                    pass
        result.add((s, p, o))
    return result


def components(graph):
    """Group anonymous OWL expressions with their named incoming references."""
    triples = [t for t in graph if any(isinstance(x, BNode) for x in t)]
    nodes = {x for t in triples for x in t if isinstance(x, BNode)}
    by_node = {n: set() for n in nodes}
    for triple in triples:
        for node in triple:
            if isinstance(node, BNode):
                by_node[node].add(triple)
    answer = []
    while nodes:
        todo = [nodes.pop()]
        collected, seen = set(), set()
        while todo:
            node = todo.pop()
            if node in seen:
                continue
            seen.add(node)
            for triple in by_node[node]:
                collected.add(triple)
                for other in triple:
                    if isinstance(other, BNode) and other not in seen:
                        nodes.discard(other)
                        todo.append(other)
        part = Graph()
        for triple in collected:
            part.add(triple)
        roots = {(s, p) for s, p, o in part if not isinstance(s, BNode) and isinstance(o, BNode)}
        answer.append((str(to_isomorphic(part).graph_digest()), part, roots))
    return answer


def merge_graphs(previous, source, captured):
    previous, source, captured = map(normalized, (previous, source, captured))
    result = Graph()
    for triple in captured:
        result.add(triple)

    def named_values(graph):
        fields = {}
        for s, p, o in graph:
            if not isinstance(s, BNode) and not isinstance(o, BNode):
                fields.setdefault((s, p), set()).add(o)
        return fields

    old_fields, new_fields = named_values(previous), named_values(source)
    changed_fields = {pair for pair in old_fields.keys() | new_fields.keys()
                      if old_fields.get(pair, set()) != new_fields.get(pair, set())}
    old_parts, new_parts = components(previous), components(source)
    old_signatures = {key for key, _, _ in old_parts}
    new_signatures = {key for key, _, _ in new_parts}
    removed = [part for part in old_parts if part[0] not in new_signatures]
    added = [part for part in new_parts if part[0] not in old_signatures]
    changed_roots = set().union(*(roots for _, _, roots in removed + added)) if removed or added else set()
    removed_signatures = {key for key, _, _ in removed}
    for key, part, roots in components(captured):
        if key in removed_signatures or roots & (changed_roots | changed_fields):
            for triple in part:
                result.remove(triple)
    for s, p in changed_fields | changed_roots:
        result.remove((s, p, None))
        for obj in new_fields.get((s, p), set()):
            result.add((s, p, obj))
    present_signatures = {key for key, _, _ in components(result)}
    # A predicate can have several anonymous values; replacing one must retain
    # its unchanged source siblings, even when their blank-node IDs differ.
    affected = changed_fields | changed_roots
    restore = [entry for entry in new_parts
               if entry[0] not in old_signatures or entry[2] & affected]
    for key, part, _ in restore:
        if key not in present_signatures:
            for triple in part:
                result.add(triple)
    return result


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def source_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'sync.lock').open('a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        until = time.monotonic() + 20
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise ModelSourceError('Another source update is running. Refresh again shortly.')
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class SourceSync:
    def __init__(self, root, fuseki):
        self.root = Path(root)
        self.fuseki = fuseki
        self.directory = self.root / 'data' / STORAGE_VERSION / 'source-sync'
        self.snapshot = self.directory / 'last-source.rdf'
        self.state = self.directory / 'state.json'
        self.cached_info = None

    def invalidate_history(self):
        for pointer in [self.root / 'data' / STORAGE_VERSION / 'editor-history' / 'latest.json',
                        self.root / 'data' / STORAGE_VERSION / 'demo-backups' / 'latest-review-links.json']:
            if pointer.exists():
                pointer.replace(pointer.with_name(pointer.name + '.before-source-sync'))

    def sync(self):
        try:
            return self._sync()
        except (OSError, ValueError) as exc:
            if isinstance(exc, ModelSourceError):
                raise
            raise ModelSourceError('RDF synchronization could not finish: ' + str(exc)) from exc

    def _sync(self):
        with source_lock(self.directory):
            path = model_path(self.root)
            raw = path.read_bytes()
            source_hash = hashlib.sha256(raw).hexdigest()
            state = json.loads(self.state.read_text('utf-8')) if self.state.exists() else {}
            if state and state.get('dataset') != self.fuseki.dataset_url:
                raise ModelSourceError('The RDF source snapshot belongs to a different dataset.')
            # Store the imported source and its metadata in one atomic file.
            # last-source.rdf is a readable audit copy, not a second commit.
            old_raw = (state['sourceRdf'].encode('utf-8') if 'sourceRdf' in state
                       else self.snapshot.read_bytes() if self.snapshot.exists() else None)
            if old_raw is not None and state and hashlib.sha256(old_raw).hexdigest() != state.get('sha256'):
                raise ModelSourceError('The saved source snapshot is damaged. No source merge was attempted.')
            if state.get('sha256') == source_hash and old_raw is not None:
                if hashlib.sha256(old_raw).hexdigest() != source_hash:
                    raise ModelSourceError('The saved source snapshot is damaged. Restore its backup before retrying.')
                info = (self.cached_info if self.cached_info
                        and self.cached_info['sha256'] == source_hash
                        and self.cached_info['filename'] == path.name
                        else model_info_from_bytes(raw, path.name))
                self.cached_info = info
                return dict(info, changed=False)

            source = parse_model(raw)  # validate before any database mutation
            info = model_info_from_bytes(raw, path.name)
            previous = Graph()
            if old_raw is not None:
                previous = parse_model(old_raw)
            elif self.fuseki.ask('ASK { ?s ?p ?o }'):
                raise ModelSourceError('This existing database has no previous RDF source snapshot. Keep its data and recover the last imported RDF before synchronizing.')

            before = 'urn:digitalcare:source-before:' + uuid4().hex
            stage = 'urn:digitalcare:source-stage:' + uuid4().hex
            commit = 'urn:digitalcare:source-commit:' + uuid4().hex
            try:
                self.fuseki.update(f'COPY DEFAULT TO GRAPH <{before}>')
                # CONSTRUCT also returns an empty graph on the very first run;
                # a Graph Store GET would return 404 for an empty named graph.
                exported = self.fuseki.request('/query',
                           f'CONSTRUCT {{ ?s ?p ?o }} WHERE {{ GRAPH <{before}> {{ ?s ?p ?o }} }}'.encode(),
                           'application/sparql-query', 'text/turtle')
                captured = Graph().parse(data=exported, format='turtle')
                merged = merge_graphs(previous, source, captured)
                payload = merged.serialize(format='turtle', encoding='utf-8')
                self.fuseki.request('/data?graph=' + quote(stage, safe=''), payload, 'text/turtle', '*/*')
                atomic_write(self.directory / 'before-sync.ttl', exported)
                if old_raw is not None:
                    atomic_write(self.directory / 'before-source.rdf', old_raw)
                if hashlib.sha256(model_path(self.root).read_bytes()).hexdigest() != source_hash:
                    raise ModelSourceError('The RDF file changed during synchronization. Refresh to load the final saved file.')
                self.fuseki.update(f'''DELETE {{ ?s ?p ?o }} INSERT {{ ?newS ?newP ?newO
                  GRAPH <{commit}> {{ <{commit}> <urn:digitalcare:accepted> true }}
                }} WHERE {{
                  {{ SELECT (true AS ?accepted) WHERE {{
                    FILTER NOT EXISTS {{ ?a ?b ?c . FILTER NOT EXISTS {{ GRAPH <{before}> {{ ?a ?b ?c }} }} }}
                    FILTER NOT EXISTS {{ GRAPH <{before}> {{ ?a ?b ?c }} FILTER NOT EXISTS {{ ?a ?b ?c }} }}
                  }} LIMIT 1 }}
                  {{ {{ ?s ?p ?o }} UNION {{ GRAPH <{stage}> {{ ?newS ?newP ?newO }} }} }}
                }}''')
                # The marker is committed in the same transaction. Comparing
                # the mutable default graph afterward could misreport success
                # if another client writes immediately after our transaction.
                accepted = self.fuseki.ask(f'ASK {{ GRAPH <{commit}> {{ <{commit}> <urn:digitalcare:accepted> true }} }}')
                if not accepted:
                    raise ModelSourceError('The database changed during synchronization. No source overwrite was accepted; refresh to retry.')
                atomic_write(self.state, json.dumps(dict(info, dataset=self.fuseki.dataset_url,
                             sourceRdf=raw.decode('utf-8')), indent=2).encode('utf-8'))
                atomic_write(self.snapshot, raw)
                self.cached_info = info
                self.invalidate_history()
                return dict(info, changed=True)
            finally:
                for graph in (before, stage, commit):
                    try:
                        self.fuseki.update(f'DROP SILENT GRAPH <{graph}>')
                    except Exception:
                        pass
