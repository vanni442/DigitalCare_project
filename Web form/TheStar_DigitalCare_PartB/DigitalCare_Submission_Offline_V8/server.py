#!/usr/bin/env python3
"""Local, dependency-free Task 2 app. All graph data lives in Apache Jena Fuseki.

The live graph stays in Fuseki. The local RDF source is synchronized at startup
and before API requests. Full demo resets retain Turtle recovery backups.
"""

import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import threading
from uuid import uuid4
import competency
from data_editor import Editor
from partb import PartB
from model_source import (STORAGE_VERSION, ModelSourceError, read_model,
                          model_info, model_path, model_check_query)
from source_sync import SourceSync
import hashlib
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, parse_qs, quote
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

ROOT = Path(__file__).resolve().parent
NAMESPACE = "http://uow.edu.au/isit906/thestar/digitalcare#"
PREDICATE = NAMESPACE + "isReviewedBy"
CQ2_QUESTION = "Which dementia guides for carers about my mum’s type of dementia have been reviewed by a healthcare professional?"
MAX_BODY = 8192
MAX_RESPONSE = 16 * 1024 * 1024
STATIC = {"/": "index.html", "/index.html": "index.html", "/app.js": "app.js", "/editor.js": "editor.js", "/styles.css": "styles.css"}
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


class APIError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class FusekiError(Exception):
    """Sanitized upstream error; upstream HTML and credentials are never exposed."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise FusekiError("Fuseki returned an unexpected redirect.")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class DatabaseDemo:
    def __init__(self, app, error, upstream_error, root):
        self.app, self.error, self.upstream_error = app, error, upstream_error
        self.root = Path(root)
        self.directory = app.backup_directory
        self.pointer = self.directory / "latest-reset.json"

    def fail(self, status, code, message):
        raise self.error(status, code, message)

    def sources(self):
        try:
            return read_model(self.root)
        except OSError:
            self.fail(503, "BASELINE_UNAVAILABLE", "The local RDF source is unavailable. No data was reset.")
        except ModelSourceError as exc:
            self.fail(409, "INVALID_MODEL_SOURCE", str(exc))

    def ensure_target_model(self):
        # Check the current source schema before a whole-graph operation.
        if not self.app.fuseki.ask(model_check_query(self.root)):
            self.fail(409, "MODEL_MISMATCH", "This dataset does not match the current RDF source schema. Refresh the page to synchronize it, then retry. No data was reset or restored.")

    def read_backup(self):
        try:
            meta = json.loads(self.pointer.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            self.fail(503, "DATABASE_BACKUP_UNAVAILABLE", "The full-data recovery file cannot be read. Check data/demo-backups.")
        try:
            if (not isinstance(meta, dict) or meta.get("version") != 2
                    or meta.get("dataset") != self.app.fuseki.dataset_url
                    or not isinstance(meta.get("modelVersion"), str)
                    or not isinstance(meta.get("modelFilename"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", meta.get("baselineHash", ""))
                    or not isinstance(meta.get("createdAt"), str)
                    or not re.fullmatch(r"[0-9]{8}T[0-9]{6}-[0-9a-f]{32}\.ttl", meta.get("file", ""))):
                raise ValueError("Invalid snapshot metadata")
            content = (self.directory / meta["file"]).read_bytes()
            if len(content) > 16 * 1024 * 1024 or digest(content) != meta["sha256"]:
                raise ValueError("Snapshot content changed")
        except (OSError, KeyError, TypeError, ValueError):
            self.fail(409, "INVALID_DATABASE_BACKUP", "The full-data backup is damaged or belongs to another dataset. No data was changed.")
        return meta, content

    def backup_matches_source(self, meta, source=None):
        source = source or self.app.source_info()
        return (meta.get("baselineHash") == source["sha256"]
                and meta.get("modelFilename") == source["filename"]
                and meta.get("modelVersion") == source["version"])

    def status(self):
        backup = self.read_backup()
        source = self.app.source_info()
        matches = bool(backup) and self.backup_matches_source(backup[0], source)
        status = {"canRestore": matches, "createdAt": backup[0]["createdAt"] if backup else None,
                  "baseline": source["counts"], "baselineHash": source["sha256"]}
        if backup and not matches:
            status.update({"reason": "BACKUP_BASELINE_CHANGED", "message":
                           "The saved backup uses an earlier RDF source. It is retained in data/demo-backups. Reset to the current source to create a compatible recovery backup."})
        return status

    @staticmethod
    def durable_write(path, content):
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    def write_pointer(self, meta):
        temp = self.directory / (uuid4().hex + ".tmp")
        self.durable_write(temp, json.dumps(meta, indent=2).encode("utf-8"))
        temp.replace(self.pointer)

    def snapshot(self, purpose, recovery=False):
        # Export the whole default graph, including blank nodes and all literals.
        content = self.app.fuseki.export()
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
        source = self.app.source_info()
        meta = {"version": 2, "dataset": self.app.fuseki.dataset_url,
                "createdAt": datetime.now(timezone.utc).isoformat(), "purpose": purpose,
                "file": name + ".ttl", "sha256": digest(content),
                "baselineHash": source["sha256"], "modelVersion": source["version"],
                "modelFilename": source["filename"]}
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.durable_write(self.directory / meta["file"], content)
            self.durable_write(self.directory / (name + ".json"), json.dumps(meta, indent=2).encode("utf-8"))
            if recovery:
                self.write_pointer(meta)
        except OSError:
            self.fail(503, "DATABASE_BACKUP_FAILED", "The full-data backup could not be saved. No data was reset. Check data/demo-backups is writable.")
        return meta

    def fingerprint(self):
        # SPARQL result term IDs are stable within the running TDB graph. No
        # Turtle ordering or output-prefix assumptions are used for repeat clicks.
        rows = self.app.fuseki.query("SELECT ?s ?p ?o WHERE { ?s ?p ?o }")["results"]["bindings"]
        return digest("\n".join(sorted(json.dumps(row, sort_keys=True) for row in rows)).encode("utf-8"))

    def invalidate_old_actions(self):
        # Keep dated archives, but stop pre-reset Undo/Restore links resurrecting
        # previous demo values after this whole-graph operation.
        for path in (self.app.editor.directory / "latest.json",
                     self.app.backup_directory / "latest-review-links.json"):
            try:
                if path.exists():
                    path.replace(path.with_name("before-database-reset-" + uuid4().hex + ".json"))
            except OSError:
                self.fail(503, "HISTORY_RESET_FAILED", "Old demo actions could not be cleared. No database replacement was attempted; the full-data backup is safe.")

    def upload(self, graph, content, content_type):
        self.app.fuseki.request("/data?graph=" + quote(graph, safe=""), content, content_type, "*/*")

    def same_as_stage(self, graph):
        return self.app.fuseki.ask(f"""ASK {{
          FILTER NOT EXISTS {{ ?s ?p ?o . FILTER NOT EXISTS {{ GRAPH <{graph}> {{ ?s ?p ?o }} }} }}
          FILTER NOT EXISTS {{ GRAPH <{graph}> {{ ?s ?p ?o }} FILTER NOT EXISTS {{ ?s ?p ?o }} }}
        }}""")

    def replace_from_stage(self, graph):
        # COPY replaces the destination in one SPARQL transaction; a failed
        # import never leaves the user's default graph partially cleared.
        self.app.fuseki.update(f"COPY GRAPH <{graph}> TO DEFAULT")
        if not self.same_as_stage(graph):
            self.fail(409, "DATABASE_RESET_NOT_VERIFIED", "The database replacement could not be verified. Use Restore previous data to recover the full backup.")

    def drop_stage(self, graph):
        try:
            self.app.fuseki.update(f"DROP SILENT GRAPH <{graph}>")
        except self.upstream_error:
            # Never mask the main action result. This private named graph is
            # not read by the app; a failed cleanup does not affect its answers.
            pass

    def reset(self, payload):
        self.app.empty_demo_payload(payload)
        with self.app.save_lock:
            source = self.app.ensure_source_current()
            baseline = self.sources()
            if digest(baseline) != source["sha256"]:
                self.fail(409, "MODEL_SOURCE_CHANGED", "The RDF source changed during reset preparation. Refresh the page and retry. No data was reset.")
            self.ensure_target_model()
            old_backup = self.read_backup()
            if old_backup and not self.backup_matches_source(old_backup[0], source):
                old_backup = None
            current = self.fingerprint()
            if old_backup and old_backup[0].get("resetFingerprint") == current:
                self.invalidate_old_actions()
                return {"ok": True, "message": "Already at the current RDF source data. Your previous full-data backup is still available.",
                        "counts": self.app.counts(), "backup": self.status()}
            graph = "urn:digitalcare:reset-stage:" + uuid4().hex
            try:
                self.upload(graph, baseline, "application/rdf+xml")
                # Confirm the staged graph includes the required original data.
                query = self.app.queries.get("counts.rq")
                query = query.replace("WHERE {", f"FROM <{graph}> WHERE {{", 1)
                rows = self.app.fuseki.select(query)
                if not rows or any(int(rows[0][key]) != value for key, value in source["counts"].items()):
                    self.fail(409, "INVALID_BASELINE", "The staged original data did not pass its checks. No data was reset.")
                if self.fingerprint() != current:
                    self.fail(409, "DATABASE_CHANGED", "The database changed during preparation. Please try the reset again.")
                meta = self.snapshot("before-reset", recovery=True)
                self.invalidate_old_actions()
                self.replace_from_stage(graph)
                counts = self.app.counts()
                meta["resetFingerprint"] = self.fingerprint()
                try:
                    self.write_pointer(meta)
                except OSError:
                    self.fail(503, "DATABASE_RESET_UNCERTAIN", "Original data was restored, but recovery status could not be updated. The full backup is saved in data/demo-backups.")
                return {"ok": True, "message": "Current RDF source data restored. Test records and edits removed. Ready for a new demo.",
                        "counts": counts, "backup": self.status()}
            except self.upstream_error as exc:
                raise self.error(503, "DATABASE_RESET_UNCERTAIN", "The reset could not be completed or verified. Reload the page to check the data. If a recovery backup is available, use Restore previous data.") from exc
            finally:
                self.drop_stage(graph)

    def restore(self, payload):
        self.app.empty_demo_payload(payload)
        with self.app.save_lock:
            source = self.app.ensure_source_current()
            self.sources()
            self.ensure_target_model()
            backup = self.read_backup()
            if not backup:
                self.fail(409, "NO_DATABASE_BACKUP", "There is no full-data reset backup to restore yet.")
            meta, content = backup
            if not self.backup_matches_source(meta, source):
                self.fail(409, "BACKUP_BASELINE_CHANGED", "The saved backup uses an earlier RDF source and cannot replace the current model. It is retained in data/demo-backups. Reset to the current source to create a compatible recovery backup.")
            graph = "urn:digitalcare:restore-stage:" + uuid4().hex
            try:
                self.upload(graph, content, "text/turtle")
                self.snapshot("before-restore")  # Keep the reset recovery target.
                self.invalidate_old_actions()
                self.replace_from_stage(graph)
                return {"ok": True, "message": "Previous data restored from the full backup. Data created after that reset was archived before restoring.",
                        "counts": self.app.counts(), "backup": self.status()}
            except self.upstream_error as exc:
                raise self.error(503, "DATABASE_RESTORE_UNCERTAIN", "The restore could not be completed or verified. The full-data backup is retained; reload and retry Restore previous data.") from exc
            finally:
                self.drop_stage(graph)


def validate_iri(value, field):
    # Deliberately narrower than all legal IRIs. The supplied model local names are
    # covered, including percent-encoded spaces; SPARQL delimiters are forbidden.
    if not isinstance(value, str) or not value.startswith(NAMESPACE):
        raise APIError(400, "INVALID_IRI", f"{field} must be an existing IRI in the DigitalCare namespace.")
    suffix = value[len(NAMESPACE):]
    if len(value) > 1024 or not re.fullmatch(r"(?:[A-Za-z0-9_.~-]|%[0-9A-Fa-f]{2})+", suffix):
        raise APIError(400, "INVALID_IRI", f"{field} contains an invalid record IRI.")
    return value


def validate_dataset_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_-]+/?", parsed.path)):
        raise ValueError("dataset_url must be a local HTTP Fuseki dataset, for example http://127.0.0.1:3033/digitalcare_v7")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("dataset_url contains an invalid port") from exc
    return value.rstrip("/")


class Queries:
    def __init__(self, directory=ROOT / "queries"):
        self.directory = Path(directory)

    def get(self, name, **values):
        source = (self.directory / name).read_text(encoding="utf-8")
        for key, value in values.items():
            source = source.replace("__" + key.upper() + "__", value)
        return source


class Fuseki:
    def __init__(self, dataset_url, timeout=12):
        self.dataset_url = validate_dataset_url(dataset_url)
        self.timeout = timeout
        # The endpoint is explicitly local. Do not send local graph requests
        # through a machine-wide proxy or follow redirects to another endpoint.
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, endpoint, data=None, content_type=None, accept=None):
        headers = {"Accept": accept or "application/sparql-results+json"}
        if content_type:
            headers["Content-Type"] = content_type
        request = Request(self.dataset_url + endpoint, data=data, headers=headers,
                          method="POST" if data is not None else "GET")
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                if response.geturl() != request.full_url:
                    raise FusekiError("Fuseki returned an unexpected redirect.")
                payload = response.read(MAX_RESPONSE + 1)
                if len(payload) > MAX_RESPONSE:
                    raise FusekiError("Fuseki response is too large for this local app.")
                return payload
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise FusekiError("Cannot complete the request to Fuseki. Check that Fuseki is running and the configured DigitalCare dataset is available.") from exc

    def query(self, sparql):
        raw = self.request("/query", sparql.encode("utf-8"), "application/sparql-query; charset=utf-8")
        try:
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ValueError("not an object")
            return result
        except (ValueError, UnicodeDecodeError) as exc:
            raise FusekiError("Fuseki returned an invalid SPARQL result.") from exc

    def select(self, sparql):
        result = self.query(sparql)
        try:
            rows = result["results"]["bindings"]
            if not isinstance(rows, list):
                raise ValueError("bindings are not a list")
            return [{key: item["value"] for key, item in row.items()} for row in rows]
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise FusekiError("Fuseki returned an invalid SELECT result.") from exc

    def ask(self, sparql):
        result = self.query(sparql)
        if not isinstance(result.get("boolean"), bool):
            raise FusekiError("Fuseki returned an invalid ASK result.")
        return result["boolean"]

    def update(self, sparql):
        self.request("/update", sparql.encode("utf-8"), "application/sparql-update; charset=utf-8", "*/*")

    def export(self):
        return self.request("/data?default", accept="text/turtle")

    def export_rdf(self):
        # Negotiate RDF/XML from the same current graph used by queries and
        # edits. This is a serialization of live data, not a copy of the seed.
        return self.request("/data?default", accept="application/rdf+xml")


class Application:
    def __init__(self, fuseki, queries=None, backup_directory=None, model_root=None,
                 enable_source_sync=False, source_sync=None):
        self.fuseki = fuseki
        self.model_root = Path(model_root) if model_root is not None else ROOT
        self.queries = queries or Queries(self.model_root / "queries")
        self.save_lock = threading.RLock()
        self.backup_directory = Path(backup_directory) if backup_directory is not None else self.model_root / "data" / STORAGE_VERSION / "demo-backups"
        self.source_sync = source_sync if source_sync is not None else (SourceSync(self.model_root, fuseki) if enable_source_sync else None)
        self._source_info = None
        self.editor = Editor(self, APIError, FusekiError, validate_iri)
        self.partb = PartB(self)
        self.database_demo = DatabaseDemo(self, APIError, FusekiError, self.model_root)

    def ensure_source_current(self):
        # HTTP callers hold this same reentrant lock through the graph action,
        # so a concurrent request cannot synchronize halfway through a save.
        with self.save_lock:
            self._source_info = (self.source_sync.sync() if self.source_sync is not None
                                 else model_info(self.model_root))
            return self._source_info

    def source_info(self):
        if self.source_sync is None:
            return model_info(self.model_root)
        return self._source_info if self._source_info is not None else self.ensure_source_current()

    def review_pairs(self):
        # Preserve RDF term types; never interpolate a literal or blank node as
        # an IRI or silently omit it from a reset backup.
        result = self.fuseki.query(self.queries.get("demo_reviews.rq"))
        try:
            pairs = set()
            for row in result["results"]["bindings"]:
                if any(row[key]["type"] != "uri" for key in ("guide", "reviewer")):
                    raise ValueError("Unsupported RDF term")
                pairs.add(tuple(validate_iri(row[key]["value"], key) for key in ("guide", "reviewer")))
            return sorted(pairs)
        except (KeyError, TypeError, ValueError, APIError) as exc:
            raise APIError(409, "UNSUPPORTED_REVIEW_DATA", "Review links contain unsupported RDF terms. No reset or restore was attempted. Check the graph in Fuseki.") from exc

    def read_review_backup(self):
        path = self.backup_directory / "latest-review-links.json"
        try:
            with path.open(encoding="utf-8") as stream:
                backup = json.load(stream)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise APIError(503, "BACKUP_UNAVAILABLE", "The review-link backup could not be read. Check data/demo-backups before resetting again.") from exc
        try:
            if backup["version"] != 1 or backup["dataset"] != self.fuseki.dataset_url:
                raise ValueError("Different dataset or backup format")
            if not isinstance(backup["createdAt"], str) or not isinstance(backup["links"], list) or not backup["links"]:
                raise ValueError("Invalid backup")
            for pair in backup["links"]:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValueError("Invalid pair")
                for value in pair:
                    validate_iri(value, "backup record")
            return backup
        except (KeyError, TypeError, ValueError, APIError) as exc:
            raise APIError(409, "INVALID_BACKUP", "The backup is invalid or belongs to a different dataset. No relationships were changed.") from exc

    def demo_status(self):
        backup = self.read_review_backup()
        return {"canRestore": bool(backup), "backupCount": len(backup["links"]) if backup else 0,
                "createdAt": backup["createdAt"] if backup else None}

    def backup_review_pairs(self, pairs):
        backup = {"version": 1, "dataset": self.fuseki.dataset_url,
                  "createdAt": datetime.now(timezone.utc).isoformat(), "links": pairs}
        contents = json.dumps(backup, indent=2).encode("utf-8")
        name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
        try:
            self.backup_directory.mkdir(parents=True, exist_ok=True)
            # Complete a durable, unique archive BEFORE changing the recovery
            # pointer or the graph. Older archives are retained.
            archive = self.backup_directory / (name + ".json")
            with archive.open("xb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            pointer = self.backup_directory / (name + ".tmp")
            with pointer.open("xb") as stream:
                stream.write(contents)
                stream.flush()
                os.fsync(stream.fileno())
            pointer.replace(self.backup_directory / "latest-review-links.json")
        except OSError as exc:
            raise APIError(503, "BACKUP_FAILED", "The backup could not be saved. No relationships were deleted. Check that data/demo-backups is writable.") from exc

    @staticmethod
    def empty_demo_payload(payload):
        if not isinstance(payload, dict) or payload:
            raise APIError(400, "INVALID_REQUEST", "Send an empty JSON object for this demo action.")

    def reset_reviews(self, payload):
        self.empty_demo_payload(payload)
        with self.save_lock:
            self.read_review_backup()  # Do not silently replace a damaged backup.
            pairs = self.review_pairs()
            before = self.counts()
            if not pairs:
                return {"ok": True, "message": "There are already 0 review links. Any previous backup is still available.",
                        "removed": 0, "counts": before, "backup": self.demo_status()}
            self.backup_review_pairs(pairs)
            values = "\n".join(f"(<{guide}> <{reviewer}>)" for guide, reviewer in pairs)
            try:
                # Delete only the backed-up snapshot. Concurrent external
                # additions are not removed without a backup.
                self.fuseki.update(self.queries.get("reset_reviews.ru", pairs=values))
                remaining = self.review_pairs()
                after = self.counts()
            except (FusekiError, APIError) as exc:
                raise APIError(503, "RESET_UNCERTAIN", "The reset could not be verified. A backup was saved. Reload choices to check the graph, or use Restore previous links.") from exc
            if set(pairs).intersection(remaining):
                raise APIError(409, "RESET_NOT_VERIFIED", "Some backed-up review links are still present. The graph may have changed. Reload choices; the backup is available to restore.")
            message = f"Reset complete: {len(pairs)} review links removed; {after['reviewLinks']} remain."
            if after["reviewLinks"]:
                message += " Other links were added during the reset."
            return {"ok": True, "message": message, "removed": len(pairs), "counts": after,
                    "verification": {"beforeIndividuals": before["individuals"], "afterIndividuals": after["individuals"]},
                    "backup": self.demo_status()}

    def restore_reviews(self, payload):
        self.empty_demo_payload(payload)
        with self.save_lock:
            backup = self.read_review_backup()
            if backup is None:
                raise APIError(409, "NO_BACKUP", "There is no previous reset to restore on this computer.")
            pairs = {tuple(pair) for pair in backup["links"]}
            existing = set(self.review_pairs())
            triples = "\n".join(f"<{guide}> <{PREDICATE}> <{reviewer}> ." for guide, reviewer in sorted(pairs))
            try:
                self.fuseki.update(self.queries.get("restore_reviews.ru", triples=triples))
                restored = set(self.review_pairs())
                counts = self.counts()
            except (FusekiError, APIError) as exc:
                raise APIError(503, "RESTORE_UNCERTAIN", "The restore could not be verified. Reload choices and check the graph. The backup is retained and restoring it again is safe.") from exc
            if not pairs.issubset(restored):
                raise APIError(409, "RESTORE_NOT_VERIFIED", "Some backup links are missing after restore. Reload choices and check the graph. The backup is retained.")
            return {"ok": True, "message": f"Restore complete: all {len(pairs)} backup links are present. Any links added since the reset are also kept.",
                    "added": len(pairs - existing), "counts": counts, "backup": self.demo_status()}

    def counts(self):
        rows = self.fuseki.select(self.queries.get("counts.rq"))
        try:
            return {key: int(rows[0][key]) for key in ("guides", "reviewers", "individuals", "reviewLinks")}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise FusekiError("Fuseki returned invalid graph counts.") from exc

    def health(self):
        counts = self.counts()
        source = self.source_info()
        return {"ok": True, "fusekiUrl": self.fuseki.dataset_url, "namespace": NAMESPACE, "modelVersion": source["version"],
                "modelSourceFilename": source["filename"], "modelSourceSha256": source["sha256"],
                "modelSourcePath": str(model_path(self.model_root).resolve()),
                "modelSourceCounts": source["counts"], "sourceSyncVersion": 1,
                "partBVersion": 1, "databaseResetVersion": 2, "quickAddVersion": 1, "guidedEntryVersion": 1,
                "counts": counts, "modelReady": counts["guides"] > 0 and counts["reviewers"] > 0}

    def options(self):
        return {"guides": self.partb.range_options(NAMESPACE + "isTargetAudienceOf")["records"],
                "reviewers": self.partb.range_options(NAMESPACE + "isReviewedBy")["records"]}

    def links(self):
        return {"links": self.fuseki.select(self.queries.get("links.rq"))}

    def evidence(self):
        query = self.queries.get("cq2.rq")
        return {"cqId": "CQ2", "question": CQ2_QUESTION, "query": query,
                "scenario": "SarahCarer: follow the cared-for older adult and condition stored in the current RDF graph.",
                "rows": self.fuseki.select(query),
                "queries": {"guides": self.queries.get("guides.rq"),
                            "reviewers": self.queries.get("reviewers.rq"),
                            "links": self.queries.get("links.rq"),
                            "updateTemplate": self.queries.get("insert_review.ru")}}

    def competency_question(self, cq_id):
        if cq_id not in competency.QUESTIONS:
            raise APIError(404, "UNKNOWN_CQ", "Choose a question from CQ1 to CQ9.")
        return competency.answer(self, cq_id)

    def record(self, iri, class_name, field):
        rows = self.fuseki.select(self.queries.get("record_status.rq", **{"record": iri, "class": class_name}))
        if len(rows) != 1:
            raise FusekiError("Fuseki returned an invalid record check.")
        row = rows[0]
        if row.get("exists") not in {"true", "1"}:
            raise APIError(422, "MISSING_RECORD", f"The selected {field} no longer exists in the graph. Refresh the lists.")
        if row.get("validType") not in {"true", "1"}:
            raise APIError(422, "WRONG_TYPE", f"The selected {field} is not a {class_name} or a typed subclass.")
        return row.get("label", iri.split("#", 1)[-1])

    def save_link(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"guide", "reviewer"}:
            raise APIError(400, "INVALID_REQUEST", "Send exactly the guide and reviewer record IRIs.")
        guide = validate_iri(payload["guide"], "guide")
        reviewer = validate_iri(payload["reviewer"], "reviewer")
        # Serializes saves made through this app. The guarded update also handles
        # a duplicate created by another client between validation and saving.
        with self.save_lock:
            guide_label = self.record(guide, "Guide", "guide")
            reviewer_label = self.record(reviewer, "HealthcareProfessional", "reviewer")
            ask = self.queries.get("ask_link.rq", guide=guide, reviewer=reviewer)
            if self.fuseki.ask(ask):
                raise APIError(409, "DUPLICATE_LINK", "This guide is already linked to this healthcare professional.")
            before = self.counts()["individuals"]
            update = self.queries.get("insert_review.ru", guide=guide, reviewer=reviewer)
            try:
                self.fuseki.update(update)
            except FusekiError as exc:
                raise APIError(503, "SAVE_UNCERTAIN", "Fuseki did not confirm the save. The link may have been saved. Refresh existing links before retrying.") from exc
            try:
                exists = self.fuseki.ask(ask)
                after = self.counts()["individuals"]
            except FusekiError as exc:
                raise APIError(503, "VERIFY_UNAVAILABLE", "The update was sent, but verification failed. The link may already be saved. Refresh existing links before retrying.") from exc
            if not exists:
                raise APIError(409, "LINK_NOT_PRESENT", "The link was not present after the update. The graph may have changed; refresh and check both records.")
            return {"ok": True, "message": "The link is present in Fuseki and was verified with ASK.",
                    "triple": f"<{guide}> <{PREDICATE}> <{reviewer}> .",
                    "verification": {"beforeIndividuals": before, "afterIndividuals": after, "exists": True},
                    "link": {"guide": guide, "guideLabel": guide_label, "reviewer": reviewer, "reviewerLabel": reviewer_label}}


class Handler(BaseHTTPRequestHandler):
    server_version = "DigitalCareTask2/1.0"
    sys_version = ""

    def log_message(self, format, *args):
        # Record only method/status; URLs or malicious request text are not logs.
        pass

    def send_bytes(self, status, body, mime, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, value):
        self.send_bytes(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def validate_host(self):
        hosts = self.headers.get_all("Host", [])
        if len(hosts) != 1 or hosts[0] not in self.server.allowed_hosts:
            raise APIError(400, "INVALID_HOST", "Use the local application URL shown when starting the server.")
        return hosts[0]

    def read_payload(self):
        host = self.validate_host()
        origins = self.headers.get_all("Origin", [])
        if len(origins) > 1 or (origins and origins[0] != "http://" + host):
            raise APIError(400, "INVALID_ORIGIN", "This request must originate from this local application.")
        if self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise APIError(400, "INVALID_ORIGIN", "Cross-site change requests are not allowed.")
        if self.headers.get("Transfer-Encoding"):
            raise APIError(400, "INVALID_REQUEST", "Chunked request bodies are not supported.")
        if self.headers.get_content_type() != "application/json":
            raise APIError(400, "INVALID_CONTENT_TYPE", "The request must use application/json.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0]):
            raise APIError(400, "INVALID_REQUEST", "A valid Content-Length is required.")
        length = int(lengths[0])
        if not 0 < length <= MAX_BODY:
            raise APIError(400, "INVALID_REQUEST", "The request body is empty or exceeds 8192 bytes.")
        self.connection.settimeout(10)
        try:
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("truncated JSON")
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, TimeoutError) as exc:
            raise APIError(400, "INVALID_JSON", "The request body must contain valid UTF-8 JSON.") from exc

    def handle_api_error(self, error):
        if isinstance(error, APIError):
            self.send_json(error.status, {"ok": False, "error": error.code, "message": error.message})
        elif isinstance(error, ModelSourceError):
            self.send_json(409, {"ok": False, "error": "INVALID_MODEL_SOURCE", "message": str(error)})
        else:
            self.send_json(503, {"ok": False, "error": "FUSEKI_UNAVAILABLE", "message": str(error)})

    def do_GET(self):
        try:
            self.validate_host()
            # Exact route matching rejects traversal and arbitrary proxy URLs.
            route = self.path
            app = self.server.app
            if route.startswith("/api/"):
                with app.save_lock:
                    app.ensure_source_current()
                    self.serve_get(route, app)
            else:
                self.serve_get(route, app)
        except (APIError, FusekiError, ModelSourceError) as exc:
            self.handle_api_error(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def serve_get(self, route, app):
        if route == "/api/health":
            self.send_json(200, app.health())
        elif route == "/api/options":
            self.send_json(200, app.options())
        elif route == "/api/links":
            self.send_json(200, app.links())
        elif route == "/api/demo/status":
            self.send_json(200, app.demo_status())
        elif route == "/api/demo/database-status":
            self.send_json(200, app.database_demo.status())
        elif route.startswith("/api/demo/plan/"):
            self.send_json(200, app.partb.demo.plan(route[len("/api/demo/plan/"):]))
        elif route == "/api/editor/status":
            self.send_json(200, app.editor.status())
        elif route.startswith("/api/partb/range?"):
            try:
                params = parse_qs(urlsplit(route).query, strict_parsing=True, max_num_fields=1)
            except ValueError as exc:
                raise APIError(400, "INVALID_REQUEST", "Provide one property IRI.") from exc
            if set(params) != {"property"} or len(params["property"]) != 1:
                raise APIError(400, "INVALID_REQUEST", "Provide one property IRI.")
            self.send_json(200, app.partb.range_options(params["property"][0]))
        elif route.startswith("/api/editor/catalog/"):
            self.send_json(200, app.editor.catalog(route[len("/api/editor/catalog/"):]))
        elif route.startswith(("/api/editor/options?", "/api/editor/current?")):
            parsed = urlsplit(route)
            expected = {"cq", "field", "subject"} if parsed.path.endswith("/current") else {"cq", "field"}
            try:
                params = parse_qs(parsed.query, strict_parsing=True, max_num_fields=3)
            except ValueError as exc:
                raise APIError(400, "INVALID_REQUEST", "Invalid editor parameters.") from exc
            if set(params) != expected or any(len(value) != 1 for value in params.values()):
                raise APIError(400, "INVALID_REQUEST", "Provide the required editor parameters once each.")
            action = app.editor.current if "subject" in expected else app.editor.options
            self.send_json(200, action(**{key: value[0] for key, value in params.items()}))
        elif route == "/api/evidence":
            self.send_json(200, app.evidence())
        elif route == "/api/cqs":
            self.send_json(200, {"questions": competency.CATALOG})
        elif route.startswith("/api/cqs/"):
            self.send_json(200, app.competency_question(route[len("/api/cqs/"):]))
        elif route == "/api/export":
            self.send_bytes(200, app.fuseki.export(), "text/turtle; charset=utf-8",
                            {"Content-Disposition": 'attachment; filename="digitalcare-task2-export.ttl"'})
        elif route == "/api/export.rdf":
            self.send_bytes(200, app.fuseki.export_rdf(), "application/rdf+xml; charset=utf-8",
                            {"Content-Disposition": 'attachment; filename="digitalcare-current.rdf"'})
        elif route in STATIC:
            web_root = (ROOT / "web").resolve()
            file_path = (web_root / STATIC[route]).resolve()
            if not file_path.is_relative_to(web_root) or not file_path.is_file():
                raise APIError(404, "NOT_FOUND", "The requested page was not found.")
            self.send_bytes(200, file_path.read_bytes(), MIME[file_path.suffix])
        else:
            raise APIError(404, "NOT_FOUND", "The requested page was not found.")

    def do_POST(self):
        try:
            self.validate_host()
            actions = {"/api/links": (201, self.server.app.save_link),
                       "/api/demo/reset-reviews": (200, self.server.app.reset_reviews),
                       "/api/demo/restore-reviews": (200, self.server.app.restore_reviews)}
            actions.update({"/api/editor/change": (200, self.server.app.editor.save),
                            "/api/demo/add-record": (201, self.server.app.partb.demo.add),
                            "/api/demo/reset-database": (200, self.server.app.database_demo.reset),
                            "/api/demo/restore-database": (200, self.server.app.database_demo.restore),
                            "/api/editor/undo": (200, self.server.app.editor.undo),
                            "/api/partb/create": (201, self.server.app.partb.create),
                            "/api/partb/link": (200, self.server.app.partb.link)})
            if self.path not in actions:
                raise APIError(404, "NOT_FOUND", "The requested endpoint was not found.")
            status, action = actions[self.path]
            payload = self.read_payload()
            with self.server.app.save_lock:
                self.server.app.ensure_source_current()
                self.send_json(status, action(payload))
        except (APIError, FusekiError, ModelSourceError) as exc:
            self.handle_api_error(exc)
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(app, host="127.0.0.1", port=8767):
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("This application must bind to 127.0.0.1 or localhost.")
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.app = app
    actual_port = server.server_address[1]
    server.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    parser.add_argument("--dataset", help="Override config dataset_url")
    parser.add_argument("--host", help="Override config app_host (loopback only)")
    parser.add_argument("--port", type=int, help="Override config app_port")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config.is_file() else {}
        if not isinstance(config, dict):
            raise ValueError("config.json must contain a JSON object")
        dataset = args.dataset or config.get("dataset_url", "http://127.0.0.1:3033/digitalcare_v7")
        host = args.host or config.get("app_host", "127.0.0.1")
        port = args.port if args.port is not None else int(config.get("app_port", 8767))
        if not 1 <= port <= 65535:
            raise ValueError("app_port must be between 1 and 65535")
        fuseki = Fuseki(dataset)
        app = Application(fuseki, enable_source_sync=True)
        app.ensure_source_current()
        server = make_server(app, host, port)
    except (ValueError, TypeError, OSError, FusekiError) as exc:
        parser.error(str(exc))
    print(f"DigitalCare Task 2: http://{host}:{port}", flush=True)
    print(f"Fuseki dataset: {dataset}. The local RDF source is synchronized at startup and on API refresh.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
