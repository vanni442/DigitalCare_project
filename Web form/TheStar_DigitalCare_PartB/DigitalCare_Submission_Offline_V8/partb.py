"""Part B create and link forms; range lookup receives property IRIs."""
import re
import json
import os
import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4
from data_editor import literal, revision
from editor_catalog import NS, XSD, FIELDS

CREATE_REQUIRED = ("isIntendedFor", "isRelatedTo")
CREATE_OPTIONAL = ("addresses", "describes", "isAvailableIn", "isAvailableInFormat", "isReviewedBy")
CREATE_FIELDS = (*CREATE_REQUIRED, *CREATE_OPTIONAL)


class PartB:
    def __init__(self, app):
        self.app, self.editor = app, app.editor
        self.demo = DemoCreator(app)

    def range_options(self, property_iri):
        matches = [field for field, definition in FIELDS.items() if definition["iri"] == property_iri]
        if len(matches) != 1:
            self.editor.fail(400, "INVALID_PROPERTY", "This property is not offered by the Part B forms.")
        shape = self.editor.schema(matches[0])
        if shape["kind"] != "record":
            self.editor.fail(400, "INVALID_PROPERTY", "This dropdown requires an object property.")
        return {"property": property_iri, "range": shape["ranges"], "records": self.editor.choices(shape["ranges"])}

    def create(self, payload):
        allowed = {"title", "publishedOn", "links", "isDownloadable", "isPrintable", "supportsLargeText"}
        if not isinstance(payload, dict) or set(payload) != allowed or not isinstance(payload["links"], dict):
            self.editor.fail(400, "INVALID_REQUEST", "Provide the guide title, date, audience, topic and optional fields from the form.")
        if set(payload["links"]) - set(CREATE_FIELDS) or not all(payload["links"].get(key) for key in CREATE_REQUIRED):
            self.editor.fail(400, "MISSING_FIELDS", "Choose at least an existing audience and an existing topic.")
        with self.app.save_lock:
            try:
                title = literal(payload["title"], XSD + "string")
                published = literal(payload["publishedOn"], XSD + "date")
            except ValueError as exc:
                self.editor.fail(400, "INVALID_VALUE", str(exc))
            objects = []
            for field, value in payload["links"].items():
                if not value: continue
                self.editor.validate(value, field)
                options = self.range_options(FIELDS[field]["iri"])
                if value not in {row["iri"] for row in options["records"]}:
                    self.editor.fail(422, "WRONG_TYPE", f"The value for {field} does not match its declared range.")
                shape = self.editor.schema(field)
                if NS + "Guide" not in shape["domains"]:
                    self.editor.fail(422, "WRONG_DOMAIN", f"Property {field} is not declared for Guide.")
                objects.append((NS + field, {"type": "uri", "value": value}))
            values = [(NS + "title", title), (NS + "publishedOn", published)]
            for field in ("isDownloadable", "isPrintable", "supportsLargeText"):
                value = payload[field]
                if value == "": continue
                try: values.append((NS + field, literal(value, XSD + "boolean")))
                except ValueError as exc: self.editor.fail(400, "INVALID_VALUE", str(exc))
            for predicate, value in values:
                shape = self.editor.schema(predicate.split("#")[-1])
                if shape["ranges"] != [value["datatype"]] or NS + "Guide" not in shape["domains"]:
                    self.editor.fail(422, "WRONG_SCHEMA", f"Check the model declaration for {predicate}.")
            # Readable slug plus UUID; guard checks BOTH incoming and outgoing
            # uses so an existing dangling reference cannot be reused.
            slug = re.sub(r"[^A-Za-z0-9]+", "-", title["value"]).strip("-")[:45] or "Guide"
            iri = NS + slug + "-" + uuid4().hex
            if self.app.fuseki.ask(f"ASK {{ {{ <{iri}> ?p ?o }} UNION {{ ?s ?p <{iri}> }} }}"):
                self.editor.fail(409, "IRI_COLLISION", "The generated identifier already exists. Nothing was written; save again to generate a new identifier.")
            triples = f"<{iri}> a <{NS}Guide>, <http://www.w3.org/2002/07/owl#NamedIndividual>; <http://www.w3.org/2000/01/rdf-schema#label> {self.editor.term(title)} .\n"
            triples += "\n".join(f"<{iri}> <{predicate}> {self.editor.term(value)} ." for predicate, value in [*objects, *values])
            before = self.app.counts()["individuals"]
            query = f"INSERT {{ {triples} }} WHERE {{ FILTER NOT EXISTS {{ <{iri}> ?p ?o }} FILTER NOT EXISTS {{ ?s ?q <{iri}> }} }}"
            try:
                self.app.fuseki.update(query)
                if not self.app.fuseki.ask(f"ASK {{ {triples} }}"):
                    self.editor.fail(409, "CREATE_NOT_VERIFIED", "The new guide could not be verified. Reload the graph before retrying.")
                after = self.app.counts()["individuals"]
            except self.editor.upstream_error:
                self.editor.fail(503, "CREATE_UNCERTAIN", f"The save response was interrupted. Check for this IRI before retrying: {iri}")
            return {"ok": True, "iri": iri, "triple": triples, "beforeIndividuals": before, "afterIndividuals": after,
                    "message": f"Created {title['value']}. The new record is stored in Fuseki and available in matching dropdowns and CQ results."}

    def link(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {"guide", "person", "revision"}
                or not all(isinstance(value, str) for value in payload.values())):
            self.editor.fail(400, "INVALID_REQUEST", "Choose an existing guide and person.")
        # Both form dropdowns use property-IRI range lookup. The subject uses
        # the inverse property's declared range, without asserting its inverse.
        guides = self.range_options(NS + "isTargetAudienceOf")["records"]
        if payload["guide"] not in {row["iri"] for row in guides}:
            self.editor.fail(422, "WRONG_TYPE", "Choose a Guide from the inverse property's range.")
        result = self.editor.save({"cq": "CQ4", "field": "isIntendedFor", "subject": payload["guide"], "action": "add", "value": payload["person"], "revision": payload["revision"]})
        return {**result, "message": "The guide-audience link is stored. Only Guide isIntendedFor Person is asserted; the inverse is left to OWL reasoning." if result["changed"] else result["message"]}


PREFIX = f"""PREFIX dc: <{NS}>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
"""
DEMO_TITLES = {"CQ1": "daily-care guide", "CQ2": "reviewed guide", "CQ3": "NSW support service",
               "CQ4": "printable guide", "CQ5": "anonymous-search guide", "CQ6": "large-text guide",
               "CQ7": "Chinese guide", "CQ8": "Chinese-speaking care centre", "CQ9": "symptom comparison guide"}


class DemoCreator:
    """Guided, user-entered examples verified by the actual CQ query."""
    def __init__(self, app):
        self.app, self.editor = app, app.editor

    def need(self, condition, message):
        if not condition:
            self.editor.fail(422, "DEMO_NOT_READY", message + " Check the current data or reset to the original data.")

    def select(self, body):
        return self.app.fuseki.select(PREFIX + body)

    def form(self, cq):
        if cq not in DEMO_TITLES:
            self.editor.fail(404, "UNKNOWN_CQ", "Choose CQ1 to CQ9.")
        fields, context, guards, choice_guards = [], [], [], {}
        def text_field(key, label, example, hint, kind="text", **constraints):
            fields.append({"id": key, "label": label, "type": kind, "required": True,
                           "example": example, "hint": hint, **constraints})
        def options_field(key, label, options, hint, preferred=None):
            self.need(options, f"No matching choice is available for {label}.")
            example = next((row["label"] for row in options if row["value"] == preferred), options[0]["label"])
            fields.append({"id": key, "label": label, "type": "select", "required": True,
                           "example": example, "hint": hint, "options": options})
        def record_field(key, label, cls, hint, preferred=None, pattern="", allowed=None):
            rows = self.select(f"SELECT DISTINCT ?iri WHERE {{ ?iri rdf:type/rdfs:subClassOf* dc:{cls} . {pattern} FILTER(isIRI(?iri)) }} ORDER BY ?iri")
            iris = sorted({row["iri"] for row in rows if row["iri"].startswith(NS) and (allowed is None or row["iri"] in allowed)})
            for iri in iris:
                self.editor.validate(iri, label)
            options_field(key, label, [{"value": iri, "label": iri.removeprefix(NS)} for iri in iris], hint,
                          NS + preferred if preferred else None)
            choice_guards[key] = {iri: f"<{iri}> rdf:type/rdfs:subClassOf* dc:{cls} . " + pattern.replace("?iri", f"<{iri}>") for iri in iris}
        def boolean_field(key, label, expected, hint):
            options_field(key, label, [{"value": expected, "label": "True" if expected == "true" else "False"}], hint)
        def number_field(key, label, example, hint, maximum=50000):
            text_field(key, label, example, hint, "number", min=0, max=maximum, step="any")
        kind = ("SupportService" if cq == "CQ3" else "CareCentre" if cq == "CQ8"
                else "SymptomComparisonGuide" if cq == "CQ9" else "Guide")
        text_field("name", "Service name" if cq == "CQ3" else "Care centre name" if cq == "CQ8" else "Guide title",
                   f"My {cq} demo", "Enter the name you want to see in the answer.", maxLength=150)
        # Form 3A asks for a date only when the selected CQ uses it (CQ6).
        # The general Task 1 create form keeps its own publication-date field.
        if cq == "CQ6":
            text_field("publishedOn", "Publication date", date.today().isoformat(),
                       "Enter your demo publication date. CQ6 sorts newest first.", "date")
        condition_patterns = {}
        if cq in {"CQ1", "CQ2", "CQ3"}:
            path = "dc:SarahCarer rdf:type/rdfs:subClassOf* dc:Carer; dc:caresFor ?adult . ?adult rdf:type/rdfs:subClassOf* dc:OlderAdult; dc:hasCondition ?condition . ?condition rdf:type/rdfs:subClassOf* dc:HealthCondition ."
            if cq == "CQ3":
                path += " FILTER(?adult = dc:MaryOlderAdult)"
            rows = self.select("SELECT DISTINCT ?adult ?condition WHERE { " + path + " } ORDER BY ?adult ?condition")
            self.need(rows, "Sarah's recorded care relationship and condition are missing" + (" for Mary" if cq == "CQ3" else "") + ".")
            for row in rows:
                self.editor.validate(row["adult"], "older adult")
                self.editor.validate(row["condition"], "condition")
                condition_patterns.setdefault(row["condition"], path.replace("?adult", f"<{row['adult']}>").replace("?condition", f"<{row['condition']}>"))
            context.append("This question uses Sarah's recorded care relationship" + (" with Mary." if cq == "CQ3" else "."))
        if cq in {"CQ1", "CQ2", "CQ4", "CQ9"}:
            record_field("audience", "Audience", "Carer", "Choose SarahCarer for this demo.", "SarahCarer",
                         allowed={NS + "SarahCarer"} if cq == "CQ9" else None)
        if cq in {"CQ1", "CQ2", "CQ3"}:
            mapping = {NS + "AlzheimersDementia": NS + "AlzheimerType", NS + "LewyBodyDementia": NS + "LewyBodyType", NS + "VascularDementia": NS + "VascularType"}
            if cq == "CQ2":
                condition_patterns = {iri: pattern for iri, pattern in condition_patterns.items() if iri in mapping}
            options_field("condition", "Condition", [{"value": iri, "label": iri.removeprefix(NS)} for iri in condition_patterns],
                          "Choose the condition already recorded for the older adult in Sarah's scenario.", NS + "AlzheimersDementia")
            choice_guards["condition"] = condition_patterns
        if cq == "CQ1":
            record_field("dailyTask", "Daily task", "DailyTaskStruggling", "Choose a daily-care difficulty, for example BathingDifficulty.", "BathingDifficulty")
            text_field("readingLevel", "Reading level", "Easy (classroom example)", "Type a reading level for your fictional guide.", maxLength=150)
        elif cq == "CQ2":
            record_field("dementiaType", "Dementia type", "DementiaType", "Match the condition: AlzheimersDementia → AlzheimerType; LewyBodyDementia → LewyBodyType; VascularDementia → VascularType.", "AlzheimerType", allowed={mapping[iri] for iri in condition_patterns})
            record_field("reviewer", "Reviewer", "HealthcareProfessional", "Choose an existing reviewer, for example DrLee.", "DrLee")
        elif cq == "CQ3":
            record_field("location", "Location", "Location", "Choose NSW because CQ3 asks about NSW.", "NSW", allowed={NS + "NSW"})
            text_field("phone", "Phone / demo contact", "DEMO ONLY - no telephone service", "For a fictional service, use the example rather than a real phone number.", maxLength=150)
            boolean_field("reliable", "Recorded reliable", "true", "Choose True to include this fictional service in the reliable-service answer.")
            text_field("evidence", "Assessment source", "Fictional classroom assessment", "Enter the source supporting your demo reliability claim.", maxLength=500)
            number_field("distance", "Distance from Mary (km)", "1", "Enter the demo distance from Mary; results are ranked by distance.")
        elif cq == "CQ4":
            record_field("format", "Document format", "DocumentFormat", "Choose a format, for example PDF.", "PDF")
            boolean_field("printable", "Can be printed", "true", "Choose True so the guide matches CQ4's print requirement.")
        elif cq in {"CQ5", "CQ6"}:
            self.need(self.app.fuseki.ask(PREFIX + "ASK { dc:AlzheimersDementia rdf:type/rdfs:subClassOf* dc:HealthCondition }"), "The Alzheimer's condition is missing.")
            guards.append("dc:AlzheimersDementia rdf:type/rdfs:subClassOf* dc:HealthCondition .")
            record_field("symptom", "Alzheimer's symptom", "Symptom", "Choose a symptom already linked to Alzheimer's, for example MemoryLoss.", "MemoryLoss", "dc:AlzheimersDementia dc:hasSymptom ?iri .")
            if cq == "CQ5":
                text_field("policyName", "New privacy policy name", "My demo privacy policy", "Enter a name for this guide's own new policy.", maxLength=150)
                boolean_field("personalInformation", "Personal information required", "false", "Choose False to demonstrate searching without personal information.")
                text_field("evidence", "Policy source", "Fictional classroom policy: no personal information required", "Enter the demo policy source supporting this answer.", maxLength=500)
            else:
                record_field("audience", "Audience", "OlderAdult", "Choose an older-adult audience, for example MaryOlderAdult.", "MaryOlderAdult")
                record_field("dementiaType", "Dementia type", "DementiaType", "Choose AlzheimerType for this Alzheimer's question.", "AlzheimerType", allowed={NS + "AlzheimerType"})
                boolean_field("largeText", "Supports large text", "true", "Choose True so the guide meets CQ6's large-text requirement.")
                self.need(self.app.fuseki.ask(PREFIX + "ASK { ?adult rdf:type/rdfs:subClassOf* dc:OlderAdult; dc:hasCondition dc:AlzheimersDementia }"), "No older adult has the Alzheimer's condition in the current graph.")
                guards.append("?cq6Adult rdf:type/rdfs:subClassOf* dc:OlderAdult; dc:hasCondition dc:AlzheimersDementia .")
        elif cq == "CQ7":
            record_field("audience", "Audience", "OlderAdult", "Choose an older-adult audience, for example MaryOlderAdult.", "MaryOlderAdult")
            record_field("topic", "Topic", "HealthCondition", "Choose AlzheimersDementia because CQ7 asks for Alzheimer's guides.", "AlzheimersDementia", allowed={NS + "AlzheimersDementia"})
            record_field("language", "Language", "Language", "Choose Chinese because CQ7 asks for Chinese guides.", "Chinese", allowed={NS + "Chinese"})
        elif cq == "CQ8":
            pattern = "dc:ChenOlderAdult rdf:type/rdfs:subClassOf* dc:OlderAdult; dc:isLocatedIn dc:Victoria ."
            self.need(self.app.fuseki.ask(PREFIX + f"ASK {{ {pattern} }}"), "Chen is not recorded in Victoria.")
            guards.append(pattern)
            record_field("location", "Location", "Location", "Choose Victoria because CQ8 uses Chen's location.", "Victoria", allowed={NS + "Victoria"})
            text_field("staffName", "New staff member name", "Demo staff member", "Enter the name of a fictional staff member at this new centre.", maxLength=150)
            record_field("language", "Staff language", "Language", "Choose Chinese to match CQ8's language requirement.", "Chinese", allowed={NS + "Chinese"})
            radius_terms = self.editor.values(NS + "ChenOlderAdult", NS + "nearbyRadiusKm")
            if not radius_terms:
                number_field("radius", "Chen's nearby radius (km)", "10", "No radius is recorded. Enter how far counts as nearby for this demo.")
                guards.append("FILTER NOT EXISTS { dc:ChenOlderAdult dc:nearbyRadiusKm ?existingRadius }")
                number_field("distance", "Distance from Chen (km)", "1", "Enter a distance no greater than the nearby radius above.")
            else:
                from cq_assessments import number
                radii = [number(term.get("value")) for term in radius_terms]
                self.need(len(radii) == 1 and radii[0] is not None, "Chen's radius has conflicting or invalid values.")
                radius = radii[0]
                expected = self.editor.term(radius_terms[0])
                guards.append(f"dc:ChenOlderAdult dc:nearbyRadiusKm {expected} . FILTER NOT EXISTS {{ dc:ChenOlderAdult dc:nearbyRadiusKm ?otherRadius FILTER(!sameTerm(?otherRadius, {expected})) }}")
                context.append(f"Chen's existing nearby radius is {radius} km. It will be kept.")
                number_field("distance", "Distance from Chen (km)", str(min(Decimal(1), radius)), f"Enter a distance no greater than {radius} km.", float(radius))
        elif cq == "CQ9":
            record_field("symptom1", "First symptom", "Symptom", "Choose the first symptom, for example MemoryLoss.", "MemoryLoss")
            record_field("symptom2", "Second symptom", "Symptom", "Choose a different symptom, for example Confusion.", "Confusion")
        return {"cq": cq, "title": "Add a " + DEMO_TITLES[cq], "summary": "Enter the fields below. Examples show what to enter.",
                "ready": True, "formVersion": 2, "fields": fields, "context": context,
                "_kind": kind, "_guards": guards, "_choiceGuards": choice_guards}

    def plan(self, cq):
        try:
            return {key: value for key, value in self.form(cq).items() if not key.startswith("_")}
        except self.editor.error as exc:
            if exc.code != "DEMO_NOT_READY":
                raise
            return {"cq": cq, "title": "Add a record", "summary": "", "fields": [], "context": [], "formVersion": 2,
                    "ready": False, "reason": exc.message}

    def validate_values(self, form, values):
        fields = {field["id"]: field for field in form["fields"]}
        if not isinstance(values, dict) or set(values) != set(fields):
            self.editor.fail(400, "INVALID_FIELDS", "Fill every field shown for this question; do not add other fields.")
        validated = {}
        for key, field in fields.items():
            raw = values[key]
            if not isinstance(raw, str) or not raw.strip() or len(raw) > field.get("maxLength", 1500):
                self.editor.fail(400, "INVALID_VALUE", f"Enter a valid {field['label']}.")
            if field["type"] == "select":
                if raw not in {option["value"] for option in field["options"]}:
                    self.editor.fail(400, "INVALID_VALUE", f"Choose {field['label']} from the shown options.")
                validated[key] = raw
            else:
                datatype = "date" if field["type"] == "date" else "decimal" if field["type"] == "number" else "string"
                try:
                    parsed = literal(raw, XSD + datatype)["value"]
                except ValueError as exc:
                    self.editor.fail(400, "INVALID_VALUE", f"{field['label']}: {exc}")
                if datatype == "decimal" and not Decimal(str(field.get("min", 0))) <= Decimal(parsed) <= Decimal(str(field.get("max", 50000))):
                    self.editor.fail(400, "INVALID_VALUE", f"{field['label']} must be between {field.get('min', 0)} and {field.get('max', 50000)}.")
                validated[key] = parsed
        cq = form["cq"]
        if cq == "CQ2":
            mapping = {NS + "AlzheimersDementia": NS + "AlzheimerType", NS + "LewyBodyDementia": NS + "LewyBodyType", NS + "VascularDementia": NS + "VascularType"}
            if mapping.get(validated["condition"]) != validated["dementiaType"]:
                self.editor.fail(400, "INVALID_VALUE", "Choose the dementia type matching the selected condition.")
        if cq == "CQ8" and "radius" in validated and Decimal(validated["distance"]) > Decimal(validated["radius"]):
            self.editor.fail(400, "INVALID_VALUE", "Distance from Chen must be no greater than the nearby radius.")
        if cq == "CQ9" and validated["symptom1"] == validated["symptom2"]:
            self.editor.fail(400, "INVALID_VALUE", "Choose two different symptoms.")
        return validated

    def build(self, cq, token, values, form=None):
        form = form or self.form(cq)
        values = self.validate_values(form, values)
        guards, records, extra = list(form["_guards"]), {}, []
        for field, options in form["_choiceGuards"].items():
            guards.append(options[values[field]])
        main = NS + f"Demo-{cq}-" + token
        label = values["name"]
        kind = form["_kind"]
        records[main] = {"class": kind, "label": label, "values": []}
        def value(subject, field, raw, datatype=None):
            term = literal(raw, XSD + datatype) if datatype else {"type": "uri", "value": raw}
            records[subject]["values"].append((field, term))
        if kind in {"Guide", "SymptomComparisonGuide"}:
            value(main, "title", label, "string")
        if cq == "CQ6":
            value(main, "publishedOn", values["publishedOn"], "date")
        for input_key, property_key in {"audience": "isIntendedFor", "reviewer": "isReviewedBy", "format": "isAvailableInFormat",
                                         "dailyTask": "isRelatedTo", "topic": "isRelatedTo", "dementiaType": "isRelatedTo"}.items():
            if input_key in values:
                value(main, property_key, values[input_key])
        if "condition" in values:
            value(main, "supportsCondition" if cq == "CQ3" else "addresses", values["condition"])
        if cq == "CQ1":
            value(main, "readingLevel", values["readingLevel"], "string")
        elif cq == "CQ3":
            value(main, "isLocatedIn", values["location"])
            for key, prop, datatype in (("phone", "phoneNumber", "string"), ("reliable", "isReliable", "boolean"),
                                         ("evidence", "reliabilityEvidence", "string"), ("distance", "distanceKm", "decimal")):
                value(main, prop, values[key], datatype)
        elif cq == "CQ4":
            value(main, "isPrintable", values["printable"], "boolean")
        elif cq in {"CQ5", "CQ6"}:
            value(main, "describes", values["symptom"])
            if cq == "CQ5":
                policy = NS + f"Demo-{cq}-Policy-" + token
                records[policy] = {"class": "PrivacyPolicy", "label": values["policyName"], "values": []}
                value(main, "isRelatedTo", values["symptom"])
                value(main, "isGovernedBy", policy)
                value(policy, "requiresPersonalInformationForSearch", values["personalInformation"], "boolean")
                value(policy, "searchRequirementEvidence", values["evidence"], "string")
            else:
                value(main, "supportsLargeText", values["largeText"], "boolean")
        elif cq == "CQ7":
            value(main, "isAvailableIn", values["language"])
        elif cq == "CQ8":
            staff = NS + f"Demo-{cq}-Staff-" + token
            records[staff] = {"class": "StaffMember", "label": values["staffName"], "values": []}
            value(main, "isLocatedIn", values["location"])
            value(main, "hasStaffMember", staff)
            value(main, "distanceKm", values["distance"], "decimal")
            value(staff, "speaksLanguage", values["language"])
            if "radius" in values:
                extra.append((NS + "ChenOlderAdult", "nearbyRadiusKm", literal(values["radius"], XSD + "decimal")))
        elif cq == "CQ9":
            value(main, "isRelatedTo", values["symptom1"])
            value(main, "isRelatedTo", values["symptom2"])

        # Check actual ontology domains/ranges, including each new companion.
        def check_value(subject, field, term):
            shape = self.editor.schema(field)
            subject_type = records[subject]["class"] if subject in records else "OlderAdult"
            self.need(any(self.app.fuseki.ask(PREFIX + f"ASK {{ dc:{subject_type} rdfs:subClassOf* <{domain}> }}") for domain in shape["domains"]), f"The model does not allow {field} on {subject_type}.")
            if term["type"] == "uri":
                target = term["value"]
                if target in records:
                    valid = any(self.app.fuseki.ask(PREFIX + f"ASK {{ dc:{records[target]['class']} rdfs:subClassOf* <{allowed}> }}") for allowed in shape["ranges"])
                else:
                    valid = target in {item["iri"] for item in self.editor.choices(shape["ranges"])}
                self.need(valid, f"The model does not allow the chosen value for {field}.")
            else:
                self.need(shape["ranges"] == [term["datatype"]], f"The datatype for {field} has changed.")
        triples = []
        for iri, record in records.items():
            self.need(self.app.fuseki.ask(PREFIX + f"ASK {{ dc:{record['class']} a owl:Class }}"), f"The class {record['class']} is missing.")
            triples.append(f"<{iri}> a dc:{record['class']}, owl:NamedIndividual; rdfs:label {self.editor.term(literal(record['label'], XSD + 'string'))}; rdfs:comment \"Fictional classroom demo data.\" .")
            for field, term in record["values"]:
                check_value(iri, field, term)
                triples.append(f"<{iri}> <{FIELDS[field]['iri']}> {self.editor.term(term)} .")
        for subject, field, term in extra:
            check_value(subject, field, term)
            triples.append(f"<{subject}> <{FIELDS[field]['iri']}> {self.editor.term(term)} .")
        return {"cq": cq, "recordIri": main, "recordLabel": label, "createdIndividuals": len(records),
                "ownedIris": list(records), "triples": "\n".join(triples), "guards": "\n".join(guards),
                "form": {key: val for key, val in form.items() if not key.startswith("_")}}

    def receipt(self, token):
        return self.app.backup_directory / ("quick-add-" + token + ".json")

    @staticmethod
    def plan_hash(plan):
        return hashlib.sha256(json.dumps(plan, sort_keys=True).encode("utf-8")).hexdigest()

    def reset_stamp(self):
        backup = self.app.database_demo.read_backup()
        return backup[0]["createdAt"] if backup else None

    def add(self, payload):
        if (not isinstance(payload, dict) or set(payload) != {"cq", "requestId", "values"}
                or not isinstance(payload["cq"], str) or payload["cq"] not in DEMO_TITLES
                or not isinstance(payload["values"], dict)
                or not all(isinstance(key, str) and isinstance(value, str) for key, value in payload["values"].items())
                or not isinstance(payload["requestId"], str)
                or not re.fullmatch(r"(?:[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", payload["requestId"])):
            self.editor.fail(400, "INVALID_REQUEST", "Enter the fields for this CQ and provide a valid request ID.")
        cq, token = payload["cq"], payload["requestId"].replace("-", "")
        input_hash = self.plan_hash(payload["values"])
        path = self.receipt(token)
        with self.app.save_lock:
            try:
                retry_saved = path.exists()
                if retry_saved:
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if saved["dataset"] != self.app.fuseki.dataset_url or saved["plan"]["cq"] != cq:
                        self.editor.fail(409, "DEMO_REQUEST_CONFLICT", "This request ID belongs to another demo. Reload the page.")
                    if saved.get("inputHash") != input_hash:
                        self.editor.fail(409, "DEMO_REQUEST_CONFLICT", "This request ID was used with different entered values. Start a new save.")
                    plan = saved["plan"]
                    if (saved.get("planHash") != self.plan_hash(plan)
                            or plan["recordIri"] != NS + f"Demo-{cq}-" + token):
                        self.editor.fail(409, "INVALID_DEMO_RECEIPT", "The saved demo receipt changed. Reload the page to start a new demo action.")
                    if saved.get("resetStamp") != self.reset_stamp():
                        self.editor.fail(409, "DEMO_WAS_RESET", "The database was reset after this request. Reload the page to start a new demo action.")
                    self.validate_values(plan["form"], payload["values"])
                else:
                    plan = self.build(cq, token, payload["values"])
                    saved = {"dataset": self.app.fuseki.dataset_url, "createdAt": datetime.now(timezone.utc).isoformat(),
                             "plan": plan, "planHash": self.plan_hash(plan), "inputHash": input_hash, "resetStamp": self.reset_stamp()}
                    self.app.backup_directory.mkdir(parents=True, exist_ok=True)
                    with path.open("x", encoding="utf-8") as stream:
                        stream.write(json.dumps(saved, indent=2))
                        stream.flush()
                        os.fsync(stream.fileno())
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.editor.fail(503, "DEMO_RECEIPT_UNAVAILABLE", "The demo recovery receipt could not be saved or read. No new request was sent.")
            ask = PREFIX + "ASK { " + plan["triples"] + " }"
            try:
                if not self.app.fuseki.ask(ask):
                    # Verify already-saved requests unchanged. Before replaying
                    # an interrupted INSERT, validate it against today's form
                    # and RDF so old dates or retired properties are not written.
                    if retry_saved:
                        try:
                            current_plan = self.build(cq, token, payload["values"])
                        except self.editor.error as exc:
                            if exc.code == "INVALID_FIELDS":
                                self.editor.fail(409, "GUIDED_FORM_CHANGED", "The required fields changed. Reload the form and save again.")
                            raise
                        if current_plan["triples"] != plan["triples"]:
                            self.editor.fail(409, "GUIDED_FORM_CHANGED", "The form's RDF mapping changed. Reload its fields and save again.")
                    # Never overwrite a partial/newer edit or reapply a reset
                    # operation. Check both outgoing and incoming uses of all IRIs.
                    guards = " ".join(f"FILTER NOT EXISTS {{ <{iri}> ?p ?o }} FILTER NOT EXISTS {{ ?s ?p <{iri}> }}" for iri in plan["ownedIris"])
                    self.app.fuseki.update(PREFIX + f"INSERT {{ {plan['triples']} }} WHERE {{ {plan['guards']} {guards} }}")
                    if not self.app.fuseki.ask(ask):
                        self.editor.fail(409, "DEMO_CHANGED", "The data changed while adding this example. Reload the page and try again.")
                answer = self.app.competency_question(cq)
                # Scope the actual CQ to the new IRI: user-entered labels may repeat.
                variable = "service" if cq == "CQ3" else "centreRecord" if cq == "CQ8" else "guideRecord"
                scoped_query, replacements = re.subn(r"\bWHERE\s*\{", lambda match: match[0] + f" VALUES ?{variable} {{ <{plan['recordIri']}> }} ", answer["query"], count=1, flags=re.IGNORECASE)
                if replacements != 1:
                    self.editor.fail(409, "DEMO_NOT_MATCHED", "The query structure changed; refresh and verify the saved record.")
                matching = self.app.fuseki.select(scoped_query)
                if cq in {"CQ3", "CQ5", "CQ8"}:
                    from cq_assessments import assess
                    matching = assess(cq, matching)["rows"]
                if cq == "CQ3":
                    matching = [row for row in matching if row.get("assessment") == "Recorded reliable, distance supplied"]
                elif cq == "CQ5":
                    matching = [row for row in matching if row.get("anonymousSearch") == "Yes, according to recorded policy"]
                elif cq == "CQ8":
                    matching = [row for row in matching if row.get("nearby") == "Within chosen radius"]
                if not matching:
                    self.editor.fail(409, "DEMO_NOT_MATCHED", "The record was saved, but the query no longer matches it. Refresh the answer; do not add the same example again.")
                return {"ok": True, "cq": cq, "message": f"Added {plan['recordLabel']}. Verified in {cq} results.",
                        "recordIri": plan["recordIri"], "recordLabel": plan["recordLabel"],
                        "createdIndividuals": plan["createdIndividuals"], "counts": self.app.counts(), "answer": answer}
            except self.editor.upstream_error:
                self.editor.fail(503, "DEMO_UNCERTAIN", "The response was interrupted. Retry this same demo action to check it safely; a duplicate record will not be created.")
