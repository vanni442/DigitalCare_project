"""Interpret additional recorded evidence without treating missing values as false."""
from decimal import Decimal, InvalidOperation


def number(value):
    try:
        result = Decimal(value)
        return result if result.is_finite() and 0 <= result <= 50000 else None
    except (InvalidOperation, TypeError):
        return None


def boolean(value):
    values = set(str(value).split("|"))
    if values and values <= {"true", "1"}: return True
    if values and values <= {"false", "0"}: return False
    return None


def assess(cq, rows):
    if cq == "CQ3":
        qualified, unknown = [], 0
        for row in rows:
            reliable = boolean(row.get("reliable", ""))
            distance = number(row.get("distanceKm", ""))
            evidence = bool(row.get("reliabilityEvidence", "").strip())
            eligible = reliable is True and evidence and distance is not None
            row["assessment"] = "Recorded reliable, distance supplied" if eligible else "Recorded not reliable" if reliable is False and evidence else "Missing or conflicting assessment data"
            if eligible: qualified.append(row)
            elif not (reliable is False and evidence): unknown += 1
        qualified.sort(key=lambda row: number(row["distanceKm"]))
        ordered = qualified + [row for row in rows if row not in qualified]
        summary = f"{len(rows)} condition-matching support service(s) in NSW have a recorded phone number. {len(qualified)} have a recorded reliable assessment, a source and distance from Mary."
        if qualified:
            names = [row["serviceName"] for row in qualified if number(row["distanceKm"]) == number(qualified[0]["distanceKm"])]
            summary += f" Nearest among those recorded services: {', '.join(names)} ({qualified[0]['distanceKm']} km)."
        if unknown: summary += f" {unknown} service(s) lack sufficient assessment/distance data; they could change the ranking."
        incomplete = bool(unknown or not rows)
        return {"rows": ordered, "answer": summary, "answerStatus": "Incomplete answer" if incomplete else "Answer from recorded assessments", "incomplete": incomplete,
                "metrics": {"serviceCount": len(rows), "qualifiedCount": len(qualified), "unknownCount": unknown}}
    if cq == "CQ5":
        yes, no, unknown = 0, 0, 0
        for row in rows:
            requires = boolean(row.get("requiresInfo", ""))
            evidence = bool(row.get("searchEvidence", "").strip())
            row["anonymousSearch"] = "Yes, according to recorded policy" if requires is False and evidence else "No, recorded policy requires information" if requires is True and evidence else "Unknown - requirement or source not recorded"
            if requires is False and evidence: yes += 1
            elif requires is True and evidence: no += 1
            else: unknown += 1
        incomplete = bool(unknown or not rows)
        summary = f"{len(rows)} symptom-guide-policy result row(s): {yes} permit searching without personal information, {no} require it, according to recorded policies."
        if incomplete: summary += " Requirements or supporting sources are not recorded for all matches; unknown does not mean False."
        return {"rows": rows, "answer": summary, "answerStatus": "Unknown: anonymous search" if incomplete else "Answer from recorded policies", "incomplete": incomplete}
    if cq == "CQ8":
        nearby, unknown = set(), set()
        for row in rows:
            distance, radius = number(row.get("distanceKm", "")), number(row.get("radiusKm", ""))
            key = row.get("centreRecord", row.get("centre", ""))
            row["nearby"] = "Unknown" if distance is None or radius is None else "Within chosen radius" if distance <= radius else "Outside chosen radius"
            if row["nearby"] == "Unknown": unknown.add(key)
            elif distance <= radius: nearby.add(key)
        rows.sort(key=lambda row: (number(row.get("distanceKm", "")) is None, number(row.get("distanceKm", "")) or Decimal(0), row.get("centre", "")))
        incomplete = bool(unknown or not rows)
        summary = f"{len(nearby)} recorded care centre(s) in Victoria with Chinese-speaking staff are within Chen's chosen radius."
        if unknown: summary += " Distance from Chen is unknown or the chosen radius is missing for some matches."
        if not rows: summary += " No centre-staff matches are recorded for this scenario. This does not prove no such centre exists."
        return {"rows": rows, "answer": summary, "answerStatus": "Incomplete answer" if incomplete else "Answer using the chosen radius", "incomplete": incomplete}
    return {}
