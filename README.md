# DigitalCare — Ontology and Semantic Web Application

**Table of Contents**

- [Problem](#1-problem)
- [Data Source](#2-data-source)
- [Steps](#3-steps)
- [Result](#4-result)

## 1. Problem

Older adults and carers may need dementia information that matches several needs at once: a particular condition, daily care difficulty, language, reading preference, or location. Storing these details as disconnected records makes it difficult to identify relevant guides and support services consistently.

DigitalCare explores how an ontology and RDF knowledge graph can connect this information and answer questions involving several relationships. Two personas guide the project: **Sarah**, an unpaid carer seeking support for her mother, and **Chen**, an older adult exploring accessible information and Chinese-language support.

The project addresses these information needs:

- Find relevant daily-care guides and professionally reviewed dementia information.
- Identify guides with recorded language, large-text, download, or print capabilities.
- Explore support services by condition and location, using recorded reliability and distance information.
- Examine privacy-policy requirements and compare multiple dementia symptoms.

The outcome is an academic ontology and offline web prototype for creating, connecting, querying, and editing semantic data. The sample information is not a verified clinical or emergency service.

## 2. Data Source

The project uses its supplied ontology, modelling worksheets, and sample records. Unlike a sales-analysis project, its main data source is a structured RDF knowledge graph rather than a transaction spreadsheet.

| Source | Purpose |
| --- | --- |
| [DigitalCare_TheStar_ASS2_v8.rdf][ontology] | Standalone V8 ontology containing classes, properties, axioms, and sample individuals |
| [Model worksheet][worksheet] | Supporting documentation for the model and competency questions |
| [Axiom table][axioms] | Documentation of ontology rules and restrictions |
| [Mapping table][mapping] | Documentation connecting web-form fields with the model |
| [Application model][app-model] | RDF/XML source loaded by the offline application by default |
| [Graph after data entry][export] | Supplied Turtle snapshot containing captured data |

The graph connects people, conditions, symptoms, guides, services, locations, languages, formats, and privacy policies. Examples include `caresFor`, `hasCondition`, `addresses`, `isReviewedBy`, `isIntendedFor`, and `isLocatedIn`.

The ontology namespace is:

```text
http://uow.edu.au/isit906/thestar/digitalcare#
```

The standalone ontology and the application's configured model are separate files. The app loads the source selected by `model_file` in its `config.json`. Sample contacts, reviewer links, distances, and policy evidence are recorded scenario data rather than independently verified real-world facts.

## 3. Steps

1. **Define the information needs and competency questions.**
   Translate the Sarah and Chen scenarios into nine competency questions, covering guide relevance, professional review, support services, accessibility, language, privacy, and symptom comparison.

2. **Build the RDF/OWL ontology.**
   Model the main entities as classes and connect their instances through object and datatype properties. Include property domains and ranges, inverse-property declarations, disjointness, and restrictions. For example, `HealthCondition` and `Symptom` are disjoint, while the `caresFor` and `hasCondition` property chain supports `caresForSomeoneWith`.

3. **Load the graph into Apache Jena Fuseki.**
   Use the bundled Fuseki 6.2.0 service and persistent TDB2 storage to make the graph available for SPARQL queries and updates. The configured ontology is V8, while some dataset and storage names retain `v7` for compatibility.

4. **Develop and connect the web forms.**
   Use HTML, CSS, and JavaScript for the interface and Python for API handling and validation. Forms allow users to create guides, link existing records, and edit supported properties. Property declarations are read from the loaded ontology; form hints are not automatically inserted as facts.

5. **Implement the competency queries and evidence assessments.**
   Use SPARQL joins, optional values, subclass paths, aggregation, and ordering to retrieve matching records. Python assessment logic interprets recorded reliability, privacy, and proximity evidence while preserving unknown or conflicting values.

6. **Support synchronisation, export, and verification.**
   Save website changes to the live graph and export the current data as RDF/XML or Turtle. Synchronise edits from the configured RDF source, and provide a read-only verification script to compare source declarations with Fuseki. Supported editing operations also include undo and recovery controls.

**To run the prototype:**

1. Extract the complete project on Windows 10 or 11, 64-bit.
2. Open `Web form/TheStar_DigitalCare_PartB/DigitalCare_Submission_Offline_V8/`.
3. Double-click `start.cmd`. The bundled Python, Java, and Fuseki runtimes start the services and open the browser.
4. Use the address printed by the launcher; the default is [http://127.0.0.1:8770/](http://127.0.0.1:8770/).
5. Run `verify.cmd` after startup to compare source and live schema declarations, and run `stop.cmd` when finished.

Do not open `web/index.html` directly because the forms require the backend and database. Detailed operating instructions are available in the [application README][app-readme].

## 4. Result

The project provides an OWL ontology and an offline web application that connects data entry with live competency-question results. Users can create a record, add or edit relationships, and inspect how the graph's answers change.

| Question | Supported analysis |
| --- | --- |
| CQ1 | Find daily-task guides relevant to Sarah's care scenario and display recorded reading levels. |
| CQ2 | Find condition-matching dementia guides for carers with recorded healthcare-professional reviewers. |
| CQ3 | Find NSW support services with phone numbers and assess reliability and proximity where sufficient values are recorded. |
| CQ4 | Find carer guides with a document format and a true download or print flag. |
| CQ5 | Connect Alzheimer's symptoms, guides, and privacy policies to assess recorded personal-information requirements. |
| CQ6 | Find qualifying symptom guides for older adults with large-text support and sort publication dates newest first. |
| CQ7 | Find Alzheimer's guides for older adults with explicitly recorded Chinese availability. |
| CQ8 | Identify Victorian care centres with Chinese-speaking staff and assess distance against Chen's recorded radius. |
| CQ9 | Find guides intended for Sarah that link to at least two distinct typed symptoms. |

**Main outcomes:**

- **Connected information retrieval:** multi-relationship queries combine details about people, conditions, guides, and services.
- **Editable semantic data:** users can create, link, amend, and retract supported records and relationships through forms.
- **Visible handling of incomplete evidence:** missing reliability, privacy, and distance values remain unknown instead of being treated as false.
- **Portable graph outputs:** RDF/XML and Turtle exports contain the current live data, including website edits.
- **Traceable source updates:** synchronisation compares source revisions, applies changed source fields, and preserves unrelated website-created records.

**Current limitations:** results depend on the graph's recorded facts. CQ1 displays reading levels but does not enforce a Plain English filter, and CQ6 returns qualifying guides newest first rather than limiting the answer to one row. Distances are entered scenario values, and recorded accessibility or language capabilities do not prove that a usable document exists. The app queries explicit relationships and subclass paths without configuring a full OWL reasoner.

Website saves do not automatically overwrite the source RDF. To change the baseline, deliberately update the configured source and synchronise it. Reset restores that current source baseline after creating a recovery snapshot.

**Project outputs:**

- [V8 ontology][ontology]
- [Model worksheet][worksheet]
- [Axiom documentation][axioms]
- [Web-form mapping][mapping]
- [Offline application][app]
- [SPARQL query files][queries]
- [Graph snapshot after data entry][export]

[ontology]: Ontology%20generation/DigitalCare_TheStar_ASS2_v8.rdf
[worksheet]: Ontology%20generation/ISIT906_TheStar_DigitalCare_Model_Worksheet_ASS2%20updated.xlsx
[axioms]: Ontology%20generation/TheStar_Axiom%20table.xlsx
[mapping]: Web%20form/TheStar_Mapping%20table.xlsx
[app-model]: Web%20form/TheStar_DigitalCare_PartB/DigitalCare_Submission_Offline_V8/model/DigitalCare_TheStar_ass2_v8.rdf
[app]: Web%20form/TheStar_DigitalCare_PartB/DigitalCare_Submission_Offline_V8/
[app-readme]: Web%20form/TheStar_DigitalCare_PartB/DigitalCare_Submission_Offline_V8/README.md
[queries]: Web%20form/TheStar_DigitalCare_PartB/DigitalCare_Submission_Offline_V8/queries/
[export]: Web%20form/TheStar_DigitalCare_PartB/TheStar_DigitalCare_after_data_entry.ttl
