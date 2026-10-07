"""Allowlisted, schema-driven data edits with durable, conflict-aware undo."""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4
from editor_catalog import NS, XSD, FIELDS, CQ_FIELDS, TIPS, DEFAULTS

OWL = "http://www.w3.org/2002/07/owl#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"


def canonical(values):
    normalized = []
    for value in values:
        value = dict(value)
        if value.get("type") in {"literal", "typed-literal"}:
            value["type"] = "literal"
            if "xml:lang" not in value:
                value.setdefault("datatype", XSD + "string")
        normalized.append(value)
    return sorted(normalized, key=lambda value: json.dumps(value, sort_keys=True))


def revision(values):
    return hashlib.sha256(json.dumps(canonical(values), sort_keys=True).encode()).hexdigest()


def literal(value, datatype):
    if not isinstance(value, str) or len(value) > 1500 or not value.strip():
        raise ValueError("Enter a non-empty value of at most 1500 characters.")
    value = value.strip()
    if datatype == XSD + "boolean":
        if value not in {"true", "false"}:
            raise ValueError("Choose True or False; an unknown value is not False.")
    elif datatype == XSD + "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Use a valid date in YYYY-MM-DD format.")
        date.fromisoformat(value)
    elif datatype == XSD + "decimal":
        try:
            number = Decimal(value)
            if not number.is_finite() or not 0 <= number <= 50000:
                raise ValueError("Distance must be between 0 and 50000 km.")
            value = format(number, "f")
        except InvalidOperation as exc:
            raise ValueError("Enter a numeric distance in kilometres.") from exc
    elif datatype == XSD + "anyURI":
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or any(char.isspace() for char in value):
            raise ValueError("Enter an HTTP or HTTPS address without a username or password.")
    elif datatype != XSD + "string":
        raise ValueError("This datatype is not editable.")
    return {"type": "literal", "value": value, "datatype": datatype}


class Editor:
    def __init__(self, app, error, upstream_error, validate_iri, directory=None):
        self.app, self.error, self.upstream_error, self.validate = app, error, upstream_error, validate_iri
        self.directory = Path(directory) if directory is not None else app.backup_directory.parent / "editor-history"

    def fail(self, status, code, message):
        raise self.error(status, code, message)

    def field(self, cq, field):
        if cq not in CQ_FIELDS or field not in CQ_FIELDS[cq]:
            self.fail(400, "INVALID_FIELD", "Choose an editable field offered for this question.")
        return FIELDS[field]

    def schema(self, field):
        definition = FIELDS[field]
        # Every editable property, including CQ3/CQ5/CQ8 assessment fields,
        # must have its type, domain and range declared in the loaded RDF graph.
        # Python supplies UI labels and an allowlist, never missing schema.
        rows = self.app.fuseki.select(self.app.queries.get("editor_shape.rq", property=definition["iri"]))
        kinds = {row.get("kind") for row in rows}
        if not rows or len(kinds) != 1:
            if not self.app.fuseki.ask(f"ASK {{ <{definition['iri']}> <{RDFS}range> ?range }}"):
                self.fail(422, "MISSING_RANGE", f"Property {definition['iri']} has no rdfs:range. Declare its range in the ontology and reload the dropdown.")
            self.fail(422, "MISSING_SCHEMA", f"Property {definition['iri']} needs a declared type, domain and range. Check the model before filling this dropdown.")
        kind = "record" if kinds == {OWL + "ObjectProperty"} else "literal"
        constraints = {}
        for side in ("domain", "range"):
            expressions = {row.get(side) for row in rows}
            if len(expressions) != 1 or None in expressions:
                self.fail(422, "UNSUPPORTED_SCHEMA", "Multiple or missing domain/range expressions require an ontology review.")
            members = {row[side + "Member"] for row in rows if side + "Member" in row}
            values = sorted(members or expressions)
            if side == "range" and kind == "literal":
                if len(values) != 1 or values[0] not in {XSD + name for name in ("boolean", "date", "decimal", "string", "anyURI")}:
                    self.fail(422, "UNSUPPORTED_SCHEMA", "The property's datatype is not supported by this editor.")
            else:
                for value in values:
                    self.validate(value, side + " class")
            constraints[side + "s"] = values
        inverse = sorted({row["inverse"] for row in rows if "inverse" in row})
        for value in inverse:
            self.validate(value, "inverse property")
        return {**definition, **constraints, "kind": kind, "inverse": inverse,
                "help": "Choose existing records. Available types follow this property's domain and range in the current RDF graph." if kind == "record" else "Save replaces the current value for this field. Clear removes it and leaves the value unknown."}

    def choices(self, classes):
        values = " ".join(f"<{value}>" for value in classes)
        return self.app.fuseki.select(self.app.queries.get("editor_choices.rq", classes=values))

    def catalog(self, cq):
        if cq not in CQ_FIELDS:
            self.fail(404, "UNKNOWN_CQ", "Choose CQ1 to CQ9.")
        return {"cq": cq, "fields": [FIELDS[key] for key in CQ_FIELDS[cq]], "tip": TIPS[cq], "defaultSubject": NS + DEFAULTS[cq]}

    def options(self, cq, field):
        self.field(cq, field)
        shape = self.schema(field)
        return {"field": shape, "subjects": self.choices(shape["domains"]),
                "objects": self.choices(shape["ranges"]) if shape["kind"] == "record" else []}

    def values(self, subject, predicate):
        result = self.app.fuseki.query(self.app.queries.get("editor_values.rq", subject=subject, property=predicate))
        try:
            values = [row["value"] for row in result["results"]["bindings"]]
            for value in values:
                self.term(value)
            return canonical(values)
        except (KeyError, TypeError, ValueError) as exc:
            self.fail(422, "UNSUPPORTED_VALUE", "The field contains an unsupported RDF value. No change was made.")

    def term(self, term):
        if not isinstance(term, dict) or not isinstance(term.get("value"), str):
            raise ValueError("Invalid RDF term")
        if term.get("type") == "uri":
            return "<" + self.validate(term["value"], "record") + ">"
        if term.get("type") not in {"literal", "typed-literal"}:
            raise ValueError("Blank nodes cannot be edited here")
        text = json.dumps(term["value"], ensure_ascii=True)
        if "xml:lang" in term:
            if not re.fullmatch(r"[A-Za-z]+(?:-[A-Za-z0-9]+)*", term["xml:lang"]):
                raise ValueError("Invalid language tag")
            return text + "@" + term["xml:lang"]
        datatype = term.get("datatype", XSD + "string")
        if datatype not in {XSD + name for name in ("boolean", "date", "decimal", "string", "anyURI")}:
            raise ValueError("Unsupported datatype")
        return text + "^^<" + datatype + ">"

    def check_subject(self, subject, shape):
        self.validate(subject, "subject")
        if subject not in {row["iri"] for row in self.choices(shape["domains"])}:
            self.fail(422, "WRONG_TYPE", "The selected record does not match this property's domain. Reload the field.")

    def current(self, cq, field, subject):
        definition = self.field(cq, field)
        shape = self.schema(field)
        self.check_subject(subject, shape)
        values = self.values(subject, definition["iri"])
        return {"values": values, "revision": revision(values)}

    def read_history(self):
        try:
            data = json.loads((self.directory / "latest.json").read_text(encoding="utf-8"))
            if data["dataset"] != self.app.fuseki.dataset_url:
                raise ValueError("Wrong dataset")
            field = self.field(data["cq"], data["field"])
            shape = self.schema(data["field"])
            if not isinstance(data["slots"], list) or not 1 <= len(data["slots"]) <= 6:
                raise ValueError("Invalid history")
            for slot in data["slots"]:
                self.validate(slot["subject"], "history subject")
                if slot["predicate"] not in [field["iri"], *shape["inverse"]]:
                    raise ValueError("Invalid history predicate")
                for key in ("before", "after"):
                    for value in slot[key]:
                        self.term(value)
            return data
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.fail(503, "HISTORY_UNAVAILABLE", "The edit recovery file cannot be read. Check data/editor-history before editing again.")

    def status(self):
        history = self.read_history()
        return {"canUndo": bool(history), "message": f"Last edit: {history['cq']} - {FIELDS[history['field']]['label']}. Undo restores its previous values." if history else "No data edit to undo yet."}

    def write_history(self, history):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
            content = json.dumps(history, indent=2).encode()
            for suffix in (".json", ".tmp"):
                path = self.directory / (name + suffix)
                with path.open("xb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
            path.replace(self.directory / "latest.json")
        except OSError as exc:
            self.fail(503, "BACKUP_FAILED", "The previous values could not be backed up. No data edit was sent.")

    def triples(self, slot, key):
        return "\n".join(f"<{slot['subject']}> <{slot['predicate']}> {self.term(value)} ." for value in slot[key])

    def guarded_update(self, slots, old, new):
        guards = []
        for index, slot in enumerate(slots):
            subject, predicate = slot["subject"], slot["predicate"]
            for value in slot[old]:
                guards.append(f"FILTER EXISTS {{ <{subject}> <{predicate}> {self.term(value)} }}")
            unexpected = " && ".join(f"!sameTerm(?v{index}, {self.term(value)})" for value in slot[old])
            guards.append(f"FILTER NOT EXISTS {{ <{subject}> <{predicate}> ?v{index} . " + (f"FILTER({unexpected})" if unexpected else "") + " }")
        delete = "\n".join(f"<{slot['subject']}> <{slot['predicate']}> ?old{index} ." for index, slot in enumerate(slots))
        insert = "\n".join(self.triples(slot, new) for slot in slots)
        reads = " ".join(f"OPTIONAL {{ <{slot['subject']}> <{slot['predicate']}> ?old{index} }}" for index, slot in enumerate(slots))
        query = f"DELETE {{ {delete} }} INSERT {{ {insert} }} WHERE {{ {reads} {' '.join(guards)} }}"
        self.app.fuseki.update(query)
        if any(revision(self.values(slot["subject"], slot["predicate"])) != revision(slot[new]) for slot in slots):
            self.fail(409, "EDIT_CONFLICT", "The field changed in another request or the update could not be verified. Reload current values. Your recovery file is retained.")

    def save(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"cq", "field", "subject", "action", "value", "revision"}:
            self.fail(400, "INVALID_REQUEST", "Send the selected question, field, record, action, value and current revision.")
        cq, field, subject, action, value = (payload[key] for key in ("cq", "field", "subject", "action", "value"))
        if not all(isinstance(item, str) for item in (cq, field, subject, action, value, payload["revision"])):
            self.fail(400, "INVALID_REQUEST", "Editor request fields must be strings.")
        definition = self.field(cq, field)
        with self.app.save_lock:
            self.read_history()
            shape = self.schema(field)
            self.check_subject(subject, shape)
            before = self.values(subject, definition["iri"])
            if revision(before) != payload["revision"]:
                self.fail(409, "EDIT_CONFLICT", "This field changed since it was loaded. Reload current values before saving.")
            if shape["kind"] == "record":
                if action not in {"add", "remove", "replace"}:
                    self.fail(400, "INVALID_ACTION", "Use Add, Remove or Replace for an existing-record relationship.")
                self.validate(value, "target record")
                if value not in {row["iri"] for row in self.choices(shape["ranges"])}:
                    self.fail(422, "WRONG_TYPE", "The target record does not match this property's range.")
                term = {"type": "uri", "value": value}
                if action == "add" and before and term not in before and self.app.fuseki.ask(f"ASK {{ <{definition['iri']}> a <{OWL}FunctionalProperty> }}"):
                    self.fail(422, "FUNCTIONAL_PROPERTY", "This property is functional and already has a value. Use Replace, not Add.")
                after = canonical([*before, term]) if action == "add" and term not in before else [item for item in before if item != term] if action == "remove" else before
                if action == "replace":
                    after = [term]
                for removed in [item for item in before if item not in after]:
                    for inverse in shape["inverse"]:
                        if self.app.fuseki.ask(f"ASK {{ {self.term(removed)} <{inverse}> <{subject}> }}"):
                            self.fail(422, "EXPLICIT_INVERSE", "An inverse link was explicitly stored by another source. Review that duplicate assertion in Fuseki before changing this relationship; otherwise it could remain as an orphaned reverse link.")
                # Assert one direction only, as required by the outline.
                # The inverse axiom entails the other direction in a reasoner.
            else:
                if action not in {"set", "clear"}:
                    self.fail(400, "INVALID_ACTION", "Use Save value or Clear value for an attribute.")
                try:
                    after = [literal(value, shape["ranges"][0])] if action == "set" else []
                except ValueError as exc:
                    self.fail(400, "INVALID_VALUE", str(exc))
            slots = [{"subject": subject, "predicate": definition["iri"], "before": before, "after": after}]
            if all(revision(slot["before"]) == revision(slot["after"]) for slot in slots):
                return {"ok": True, "changed": False, "message": "This value is already in the requested state. No new edit was made."}
            history = {"dataset": self.app.fuseki.dataset_url, "cq": cq, "field": field, "slots": slots,
                       "createdAt": datetime.now(timezone.utc).isoformat()}
            self.write_history(history)
            try:
                # Save only the submitted record value. Missing property
                # declarations must be corrected in the source RDF, not here.
                self.guarded_update(slots, "before", "after")
            except self.upstream_error as exc:
                self.fail(503, "EDIT_UNCERTAIN", "The edit response was interrupted. Reload current values to check the database. Previous values were backed up before the update.")
            return {"ok": True, "changed": True, "message": f"Saved {definition['label']}. The selected question is being queried again.",
                    "before": before, "after": after, "affectedQuestions": [key for key, fields in CQ_FIELDS.items() if field in fields]}

    def undo(self, payload):
        if payload != {}:
            self.fail(400, "INVALID_REQUEST", "Send an empty JSON object to undo the last data edit.")
        with self.app.save_lock:
            history = self.read_history()
            if not history:
                self.fail(409, "NO_HISTORY", "No data edit is available to undo.")
            slots = history["slots"]
            current = [self.values(slot["subject"], slot["predicate"]) for slot in slots]
            if all(revision(values) == revision(slot["before"]) for values, slot in zip(current, slots)):
                return {"ok": True, "message": "The previous values are already present. Nothing else was changed."}
            if any(revision(values) != revision(slot["after"]) for values, slot in zip(current, slots)):
                self.fail(409, "UNDO_CONFLICT", "The field changed after this edit. Undo would overwrite newer data, so it was stopped. Reload the field and review its current values.")
            try:
                self.guarded_update(slots, "after", "before")
            except self.upstream_error:
                self.fail(503, "UNDO_UNCERTAIN", "Undo could not be verified. Reload the field; the recovery file is retained.")
            return {"ok": True, "message": f"Undid the last {history['cq']} data edit. Previous values are restored."}
