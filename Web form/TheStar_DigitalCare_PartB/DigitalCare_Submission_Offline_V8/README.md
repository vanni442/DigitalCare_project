# DigitalCare Part B — RDF V8

Create records, connect records and edit data to see CQ1–CQ9 answers change. HTML/CSS/JavaScript provide the frontend; Python validates submissions and runs SPARQL against Apache Jena Fuseki. Property definitions and initial facts come from the supplied RDF.

## Run offline

Windows 10/11 x64 and a browser are required. Python, Java, Fuseki and the RDF parser are bundled. No installation or internet download is needed.

1. Extract the complete ZIP into a writable folder.
2. Double-click start.cmd. It opens this copy's website automatically. The default address is http://127.0.0.1:8770/; use the address printed by the launcher if it selects another port.
3. Run stop.cmd to stop this copy. Closing the browser does not stop its servers.

Do not open web/index.html directly. A fresh copy automatically selects unused ports if another project is running, and saves them in config.json. Each copy uses its own database and RDF source. Restarting the same copy reuses its own services. The supplied V8 has 13 Guides, 3 Healthcare professionals and 3 Review links. Website additions persist across restarts.

## Use the website

1. Create guide: enter its details and choose existing linked records. Save creates a new Guide.
2. Link audience: select an existing Guide and person. Save adds one relationship, without creating another Guide or person.
3. Edit & see results: choose CQ1–CQ9, enter the guided fields and save. Publication date appears only for CQ6 in these guided forms. Advanced editing supports amendment/retraction and Undo.
4. Export & verify: Export RDF downloads the current graph as RDF/XML (.rdf), including website edits. Export TTL downloads the same data in Turtle. Open either in Protégé, or inspect the record's IRI and property values. Reset to original data restores the currently configured RDF after saving a full recovery snapshot.

## Two directions

- Website to RDF: save a record or change, then click Export RDF. The downloaded file contains the current database, not an unchanged copy of the original source.
- RDF to website: expand Source file to edit under Export & verify. Edit that exact file, save, then click Sync RDF at the top. Counts, record dropdowns and answers refresh. Editing an unrelated copy in Downloads does not change the project source.

Website saves update Fuseki; they do not automatically overwrite the source/reset baseline. If you want an exported graph to become the source, save the downloaded RDF/XML over the configured source file, then click Sync RDF. Reset will then use that revised source, including the exported records.

## Edit the RDF and refresh

The source is model/DigitalCare_TheStar_ass2_v8.rdf, selected by model_file in config.json.

1. Edit that file and save as UTF-8 RDF/XML.
2. Click Sync RDF to refresh the counts, dropdowns and answers. Reloading the page or restarting also synchronizes changes.
3. The application validates the RDF and synchronizes it to Fuseki. A changed checksum no longer blocks startup.

Changes are compared with the last successfully imported source. For a source field that changed (the same subject and property), the current RDF values take precedence. Unchanged source fields and unrelated website-created records are preserved. To restore only the current source, use Reset to original data.

Invalid RDF leaves the database intact. Correct the file and refresh. A reset snapshot from an earlier source revision is retained on disk but cannot be restored through the ordinary restore button.

To select a differently named RDF, place it in model/ and update model_file in config.json. The data-directory and dataset names retain v7 for compatibility; they do not select the model version. The current version, source file path and checksum are reported at /api/health on this copy's website.

## Model and mapping

The project uses RDF V8 and mapping table v3. V8 uses distanceKm for both CQ3 and CQ8, replacing the two old distance property names. Its distances use the scenario described in the model; the app does not calculate geographical distances.

Property types, domains and ranges come from Fuseki. Python contains UI mappings and queries, not fallback ontology definitions or invented instance values. Form examples are hints; only submitted values are saved. Forms do not assert defined-class membership or write both directions of an inverse relationship.

## Code path for review

- model_source.py parses the configured RDF and derives its version, counts, schema check and checksum. No fixed checksum is required.
- source_sync.py compares source revisions, preserves unrelated captured data and performs a guarded atomic update. Validation and staging happen before live data changes.
- manage.py and server.py synchronize at startup and before API requests. Reset loads the same current source.
- local_services.py verifies project path, process identity and port ownership before the launcher reuses a local service. A fresh extraction never imports into another copy's database.
- editor_catalog.py maps supported UI fields to property IRIs.
- data_editor.py and queries/editor_shape.rq obtain domains, ranges and datatypes from Fuseki, including explicit inverse-property declarations.
- partb.py validates create/link/guided-entry submissions and saves their values.
- queries/cq1.rq through cq9.rq and cq_assessments.py query live facts and interpret evidence.

## Verify

After starting, run verify.cmd. This compares current RDF declarations with Fuseki without changing data. The checksum describes the file selected in config.json.

For a visible source-edit test, change a sample Guide title in the RDF, save and refresh its CQ answer. The answer and exported Turtle should reflect the title. Restore the title and refresh to reverse the test. Export Turtle to inspect complete subject and property IRIs.

## Local data and troubleshooting

Fuseki defaults to http://127.0.0.1:3032/digitalcare_submission_v7; config.json records this copy's actual dataset URL and website port. Persistent data stays under data/v7/. Source-sync state keeps the last imported source and one pre-sync recovery graph. Full demo-reset backups are separate. The clean ZIP excludes local databases, logs and test records.

- Invalid RDF: correct the file and refresh; no checksum approval is required.
- Port in use: a fresh copy chooses free ports automatically. For a copy with saved data, if another service occupies its saved Fuseki port, stop that other service and restart this copy. The launcher preserves the original database and URL-bound recovery history.
- Old page: check the address and press Ctrl+F5.
- No result: complete the guided fields; a record must meet all CQ conditions.
- Existing database but missing sync state: recover its last imported source snapshot from that installation. A clean extraction instead starts a separate database; it never borrows another project's snapshot. Do not delete database/state files to bypass the check.
- Other startup failure: inspect logs/app.log and logs/fuseki.log.
