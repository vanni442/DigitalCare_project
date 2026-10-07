"""Nine source-backed, read-only competency questions over the current graph."""
import json
from pathlib import Path
from cq_assessments import assess

CATALOG = json.loads((Path(__file__).parent / "questions.json").read_text(encoding="utf-8"))
QUESTIONS = {item["id"]: item for item in CATALOG}


def answer(app, cq_id):
    definition = QUESTIONS[cq_id]
    source_filename = app.source_info()["filename"]
    query = app.queries.get(definition["queryFile"])
    rows = app.fuseki.select(query)
    count = len(rows)
    status = "Recorded matches" if count else "No recorded match"
    summary = f"{count} matching result row(s) in the current graph."
    if cq_id in {"CQ3", "CQ5", "CQ8"}:
        return {**definition, "query": query, **assess(cq_id, rows),
                "dataSource": "Current Fuseki default graph", "sourceFile": source_filename}
    if cq_id == "CQ6" and rows:
        latest = rows[0].get("publishedOn", "Not recorded")
        summary = f"{count} matching symptom-guide row(s), newest first. Latest recorded publication date: {latest}."
    elif cq_id == "CQ9":
        summary = (f"{count} guide(s) for Sarah have at least two distinct, explicitly linked symptom records."
                   if count else "No guide for Sarah currently has at least two recorded symptom links matching this query.")
    elif cq_id == "CQ2":
        summary = f"{count} matching guide-reviewer pair(s). Multiple reviewers can belong to the same guide."
    if not count and cq_id not in {"CQ3", "CQ5", "CQ8"}:
        summary += " Missing records do not establish that a resource does not exist outside this graph."
    return {**definition, "query": query, "rows": rows, "answerStatus": status, "answer": summary,
            "dataSource": "Current Fuseki default graph", "sourceFile": source_filename}
