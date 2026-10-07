"""Editable fields mapped to the nine supplied questions, with English labels."""
NS = "http://uow.edu.au/isit906/thestar/digitalcare#"
XSD = "http://www.w3.org/2001/XMLSchema#"

LABELS = {
    "title": "Guide title", "isTargetAudienceOf": "Inverse audience property (range lookup only)",
    "addresses": "Condition addressed by a guide", "caresFor": "Person cared for",
    "hasCondition": "Older adult's condition", "isReviewedBy": "Guide reviewer",
    "isIntendedFor": "Guide audience", "isRelatedTo": "Guide topic or symptom",
    "readingLevel": "Guide reading level", "supportsCondition": "Condition supported by a service",
    "isLocatedIn": "Location of a person, service or centre", "phoneNumber": "Service phone number",
    "isDownloadable": "Guide can be downloaded", "isPrintable": "Guide can be printed",
    "isAvailableInFormat": "Guide document format", "downloadURL": "Guide download address",
    "isGovernedBy": "Guide privacy policy", "describes": "Symptom described by a guide",
    "hasSymptom": "Symptom associated with a condition", "supportsLargeText": "Guide supports large text",
    "publishedOn": "Guide publication date", "isAvailableIn": "Guide language",
    "speaksLanguage": "Language spoken by a staff member", "hasStaffMember": "Care-centre staff member",
}
# Presentation labels and an edit allowlist only. Types, domains and ranges are
# read from the current RDF loaded into Fuseki, including assessment fields.
LABELS.update({
    "isReliable": "Service recorded as reliable",
    "reliabilityEvidence": "Source for the reliability assessment",
    "distanceKm": "Distance from the persona's location (km)",
    "requiresPersonalInformationForSearch": "Policy requires personal information to search",
    "searchRequirementEvidence": "Source for the search requirement",
    "nearbyRadiusKm": "Older adult's chosen nearby radius (km)",
})
FIELDS = {key: {"id": key, "label": label, "iri": NS + key}
          for key, label in LABELS.items()}
CQ_FIELDS = {
    "CQ1": ["readingLevel", "isRelatedTo", "addresses", "isIntendedFor", "caresFor", "hasCondition"],
    "CQ2": ["isReviewedBy", "addresses", "isRelatedTo", "isIntendedFor", "caresFor", "hasCondition"],
    "CQ3": ["phoneNumber", "supportsCondition", "isLocatedIn", "isReliable", "reliabilityEvidence", "distanceKm", "caresFor", "hasCondition"],
    "CQ4": ["isPrintable", "isDownloadable", "isAvailableInFormat", "isIntendedFor", "downloadURL"],
    "CQ5": ["requiresPersonalInformationForSearch", "searchRequirementEvidence", "isGovernedBy", "describes", "hasSymptom"],
    "CQ6": ["supportsLargeText", "publishedOn", "describes", "isIntendedFor", "isRelatedTo", "addresses", "hasCondition", "hasSymptom"],
    "CQ7": ["isAvailableIn", "isIntendedFor", "isRelatedTo", "addresses"],
    "CQ8": ["speaksLanguage", "hasStaffMember", "isLocatedIn", "distanceKm", "nearbyRadiusKm"],
    "CQ9": ["isRelatedTo", "isIntendedFor"],
}
TIPS = {
    "CQ1": "Try changing DailyCareGuide's reading level. To add a matching guide, record its condition, carer audience and a DailyTaskStruggling topic.",
    "CQ2": "Add DailyCareGuide -> DrPatel as a reviewer. A guide must also match Sarah's cared-for adult's condition, a dementia type and a carer audience.",
    "CQ3": "Edit a NSW service's phone number or supported condition. Record reliability, its source and distance from Mary to support counting and ranking reliable services.",
    "CQ4": "Set CarerCommunicationGuide's printable flag to False, then undo. A guide needs a carer audience, a format and at least one True download/print flag.",
    "CQ5": "Choose DigitalCarePrivacyPolicy. Record whether personal information is required and a supporting source. Unknown is different from False.",
    "CQ6": "Set SymptomsGuide's large-text support to False, then undo. You can also edit its publication date to change the newest-first results.",
    "CQ7": "Add Chinese to EnglishAlzheimersGuide's languages, then undo. Chinese availability must be recorded alongside an older-adult audience and Alzheimer's topic.",
    "CQ8": "Edit LiChen's spoken languages or the centre's staff. To assess near, enter a centre's distance from Chen and ChenOlderAdult's chosen nearby radius.",
    "CQ9": "Add a Symptom topic to a guide intended for SarahCarer. Two distinct symptoms are required; a dementia-type topic is not counted as a symptom.",
}
DEFAULTS = {"CQ1": "DailyCareGuide", "CQ2": "DailyCareGuide", "CQ3": "CarerHelpNSW", "CQ4": "CarerCommunicationGuide",
            "CQ5": "DigitalCarePrivacyPolicy", "CQ6": "SymptomsGuide", "CQ7": "EnglishAlzheimersGuide", "CQ8": "LiChen", "CQ9": "EarlySignsComparisonGuide"}
