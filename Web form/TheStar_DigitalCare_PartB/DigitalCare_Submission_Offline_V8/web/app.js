"use strict";

(() => {
  const byId = (id) => document.getElementById(id);
  const ui = {
    refresh: byId("refresh-overview"),
    notice: byId("page-notice"), connection: byId("connection"), export: byId("export-link"), exportRdf: byId("export-rdf-link")
  };
  const state = { loading: false, refreshPending: false, editorBusy: false, demoBusy: false, demoReady: false, canRestore: false, databaseBusy: false, databaseReady: false, canRestoreDatabase: false, ready: false };
  const databaseNoticeKey = "digitalcare:database-reset-notice";

  let cqSequence = 0;
  let cqCatalogLoaded = false;

  class ApiError extends Error {
    constructor(message, status = 0, code = "network_error") {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function request(path, options = {}) {
    const {timeoutMs = 20000, ...fetchOptions} = options;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(path, {
        ...fetchOptions, signal: controller.signal, cache: "no-store",
        headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || data?.ok === false) {
        throw new ApiError(data?.message || `The server could not complete this request (HTTP ${response.status}).`, response.status, data?.error);
      }
      if (data === null) throw new ApiError("The server returned an unreadable response. Check the service and refresh the data.", response.status, "invalid_response");
      return data;
    } catch (error) {
      if (error instanceof ApiError) throw error;
      if (options.method === "POST") throw new ApiError("The response was interrupted. The database may already have changed. Refresh the data before trying again. Any reset backup is retained.", 0, "change_uncertain");
      if (error.name === "AbortError") throw new ApiError("The request took too long. Check that Fuseki is running, then refresh the data.", 0, "timeout");
      throw new ApiError("The data service is unavailable. Start the application and Fuseki, then refresh the data.");
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function showNotice(element, message, kind = "error") {
    element.className = `notice notice-${kind}`;
    element.textContent = message;
    element.hidden = false;
  }

  function setConnection(label, status) {
    byId("connection-label").textContent = label;
    ui.connection.dataset.state = status;
  }

  function setExportEnabled(enabled) {
    for (const link of [ui.export, ui.exportRdf]) {
      link.setAttribute("aria-disabled", String(!enabled));
      link.tabIndex = enabled ? 0 : -1;
    }
  }

  function updateControls() {
    const locked = state.loading || state.demoBusy || state.editorBusy || state.databaseBusy;
    window.dispatchEvent(new CustomEvent("digitalcare:form-busy", {detail: !!(state.loading || state.demoBusy || state.databaseBusy)}));
    if (cqCatalogLoaded) byId("cq-select").disabled = state.editorBusy || !!state.databaseBusy;
    if (state.databaseBusy) byId("cq-refresh").disabled = true;
    ui.refresh.disabled = locked;
    ui.refresh.textContent = state.loading ? "Syncing…" : "Sync RDF";
    ui.refresh.setAttribute("aria-busy", String(state.loading));
    byId("reset-reviews").disabled = locked || !state.ready || !state.demoReady;
    byId("restore-reviews").disabled = locked || !state.ready || !state.canRestore;
    byId("reset-database").disabled = locked || !state.databaseReady;
    byId("restore-database").disabled = locked || !state.databaseReady || !state.canRestoreDatabase;
    byId("reset-database").textContent = state.databaseBusy === "reset" ? "Resetting…" : "Reset to original data";
    byId("restore-database").textContent = state.databaseBusy === "restore" ? "Restoring…" : "Restore previous data";
    byId("reset-reviews").textContent = state.demoBusy === "reset" ? "Resetting…" : "Reset review links";
    byId("restore-reviews").textContent = state.demoBusy === "restore" ? "Restoring…" : "Restore previous links";
  }

  function setCounts(counts = {}) {
    for (const [id, key] of [["count-guides", "guides"], ["count-reviewers", "reviewers"], ["count-links", "reviewLinks"]]) {
      byId(id).textContent = typeof counts[key] === "number" ? counts[key].toLocaleString() : "—";
    }
  }

  function renderLinks(data) {
    if (!Array.isArray(data.links)) throw new ApiError("The saved-link response is incomplete.", 0, "invalid_response");
    byId("links-body").replaceChildren();
    for (const link of data.links) {
      const row = document.createElement("tr");
      for (const [label, iri] of [[link.guideLabel, link.guide], [link.reviewerLabel, link.reviewer]]) {
        const cell = document.createElement("td");
        const name = document.createElement("span");
        name.textContent = label || iri;
        const identifier = document.createElement("small");
        identifier.textContent = iri;
        cell.append(name, identifier);
        row.append(cell);
      }
      byId("links-body").append(row);
    }
    byId("links-status").textContent = data.links.length ? `${data.links.length} saved review ${data.links.length === 1 ? "link" : "links"}.` : "No review links saved.";
    byId("links-table-wrap").hidden = data.links.length === 0;
  }

  function renderEvidence(data) {
    for (const [id, key] of [["guides-query", "guides"], ["reviewers-query", "reviewers"], ["links-query", "links"], ["update-query", "updateTemplate"]]) {
      byId(id).textContent = data.queries?.[key] || "Query not provided by the server.";
    }
  }

  function clearCQ(message) {
    byId("cq-result-count").textContent = "—";
    byId("cq-status").textContent = message;
    byId("cq-table-wrap").hidden = true;
    byId("cq-answer-box").hidden = true;
    byId("cq-answer-items").replaceChildren();
    byId("cq-answer-note").textContent = "";
    byId("cq-evidence").hidden = true;
    byId("cq-body").replaceChildren();
    byId("cq-notes").replaceChildren();
    byId("cq-query").textContent = "Query not loaded.";
    for (const id of ["cq-source", "cq-source-path", "cq-source-gap", "cq-scenario"]) byId(id).textContent = "";
  }

// Turn the live query response into an answer; never add facts to the graph.
function describeCQ(data) {
  const rows = data.rows || [];
  const unique = (values) => [...new Set(values.filter(Boolean))];
  const readable = (value) => String(value || "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/Alzheimers/g, "Alzheimer's");
  const join = (values) => {
    const names = unique(values);
    return names.length < 2 ? (names[0] || "") : names.length === 2
      ? names.join(" and ") : `${names.slice(0, -1).join(", ")}, and ${names.at(-1)}`;
  };
  const name = (row, key) => readable(row[key]);
  const names = (key, subset = rows) => join(subset.map((row) => name(row, key)));
  const list = (value) => join(String(value || "").split(/;\s*/).map(readable));
  const flag = (value) => {
    const tokens = unique(String(value ?? "").split(/\s*[;|]\s*/).map((x) => x.trim().toLowerCase()));
    if (tokens.length && tokens.every((x) => x === "true" || x === "1")) return true;
    if (tokens.length && tokens.every((x) => x === "false" || x === "0")) return false;
    return null;
  };
  const item = (title, text) => ({title, text});
  const empty = {
    CQ1: "No carer guide in the current data matches the condition and a daily-care difficulty.",
    CQ2: "No professionally reviewed carer guide currently matches the type of dementia in this scenario.",
    CQ3: "No matching NSW support service has a phone number recorded.",
    CQ4: "No carer guide is currently recorded as downloadable or printable with an available format.",
    CQ5: "Check the search privacy policy to see whether personal information is required.",
    CQ6: "No dated Alzheimer's symptom guide for older adults is currently recorded as supporting large text.",
    CQ7: "No Alzheimer's guide for older adults is currently recorded as available in Chinese.",
    CQ8: "No care centre in Victoria with Chinese-speaking staff currently matches Chen's scenario.",
    CQ9: "No guide for Sarah currently covers at least two recorded symptoms."
  };
  const result = {lead: "", items: [], note: "", tone: "answer"};
  if (!rows.length) return {...result, lead: empty[data.id] || "No matching records are currently available.", tone: "empty"};
  switch (data.id) {
    case "CQ1": {
      const condition = unique(rows.map((r) => r.condition)).length === 1 ? ` related to ${names("condition")}` : "";
      result.lead = `${names("guide")} ${unique(rows.map((r) => r.guide)).length === 1 ? "is a matching guide" : "are matching guides"} for carers dealing with daily-care difficulties${condition}.`;
      result.items = rows.map((r) => item(name(r, "guide"), `For carers supporting someone with ${name(r, "condition")}; covers ${name(r, "difficulty").toLowerCase()}${r.readingLevel ? `. Recorded reading level: ${r.readingLevel}` : ""}.`));
      if (rows.some((r) => !r.readingLevel)) result.note = "Reading ease is not recorded for every match.";
      else result.note = "Reading levels are recorded descriptions, not an independent readability assessment.";
      break;
    }
    case "CQ2":
      result.lead = `${names("guide")} ${unique(rows.map((r) => r.guide)).length === 1 ? "has" : "have"} recorded healthcare-professional reviews and ${unique(rows.map((r) => r.guide)).length === 1 ? "matches" : "match"} the carer audience and dementia type.`;
      result.items = rows.map((r) => item(name(r, "guide"), `Reviewed by ${name(r, "reviewer")}; addresses ${name(r, "condition")}.`));
      break;
    case "CQ3": {
      const qualified = rows.filter((r) => r.assessment === "Recorded reliable, distance supplied");
      const unknown = rows.filter((r) => r.assessment === "Missing or conflicting assessment data");
      if (qualified.length) {
        const distance = Math.min(...qualified.map((r) => Number(r.distanceKm)));
        const nearest = qualified.filter((r) => Number(r.distanceKm) === distance);
        result.lead = `${qualified.length} NSW support ${qualified.length === 1 ? "service has" : "services have"} a recorded reliability assessment, phone number and distance. Nearest among these: ${names("serviceName", nearest)} (${distance} km from Mary).`;
        if (unknown.length) result.note = "Other listed services lack enough information to compare reliability and distance.";
      } else {
        result.lead = `${data.metrics?.serviceCount ?? rows.length} NSW support ${rows.length === 1 ? "service has" : "services have"} phone numbers for help with the condition.`;
        result.note = unknown.length ? "A nearest reliable service cannot yet be identified from the recorded assessments and distances." : "None is recorded as reliable in the available assessments.";
      }
      result.items = rows.map((r) => item(name(r, "serviceName"), `Phone: ${r.phone}${r.assessment === "Recorded reliable, distance supplied" ? ` · Recorded reliable · ${r.distanceKm} km from Mary` : r.assessment === "Recorded not reliable" ? " · Recorded as not reliable" : " · Reliability or distance unconfirmed"}.`));
      break;
    }
    case "CQ4": {
      result.items = rows.map((r) => {
        const download = flag(r.downloadable), print = flag(r.printable);
        const action = download === true && print === true ? "Download or print" : download === true ? "Download" : print === true ? "Print" : "Download/print status needs review";
        return item(name(r, "guide"), `${action} · Format: ${list(r.format)}.`);
      });
      const descriptions = unique(rows.map((r) => {
        const download = flag(r.downloadable), print = flag(r.printable);
        const action = download === true && print === true ? "can be downloaded or printed" : download === true ? "can be downloaded" : print === true ? "can be printed" : "has conflicting download/print values";
        return `${name(r, "guide")} ${action}.`;
      }));
      result.lead = descriptions.join(" ");
      if (rows.some((r) => flag(r.downloadable) === null && r.downloadable || flag(r.printable) === null && r.printable)) result.note = "Some capability values conflict; only unambiguous capabilities are stated above. Phone offline use has not been verified.";
      else result.note = rows.some((r) => !r.downloadURL) ? "Download links are not recorded for every guide; phone offline use has not been verified." : "Phone offline use has not been verified.";
      break;
    }
    case "CQ5": {
      const permitted = rows.filter((r) => r.anonymousSearch?.startsWith("Yes,"));
      const required = rows.filter((r) => r.anonymousSearch?.startsWith("No,"));
      const unknown = rows.filter((r) => !r.anonymousSearch || r.anonymousSearch.startsWith("Unknown"));
      const overlap = permitted.some((r) => [...required, ...unknown].some((other) => other.guide === r.guide));
      if (overlap) result.lead = "Anonymous search is permitted by some recorded policies, but there is no single confirmed answer for the affected guide. The policy-specific outcomes are listed below.";
      else if (permitted.length) result.lead = `Yes, for ${names("guide", permitted)}, according to the recorded privacy ${unique(permitted.map((r) => r.policy)).length === 1 ? "policy" : "policies"}: personal information is not required for search.`;
      else if (required.length && !unknown.length) result.lead = `No, for the listed ${rows.length === 1 ? "guide" : "guides"}: the recorded privacy policies require personal information for search.`;
      else if (required.length) result.lead = `Personal information is required for ${names("guide", required)}. Check the privacy policies for the other listed guides before searching.`;
      else result.lead = `For ${names("guide")}, check the privacy policy to see whether personal information is required for search.`;
      result.items = rows.map((r) => item(name(r, "guide"), `${name(r, "symptom")} · ${name(r, "policy")} · ${r.anonymousSearch?.startsWith("Yes,") ? "Personal information not required" : r.anonymousSearch?.startsWith("No,") ? "Personal information required" : "Check the privacy policy before searching"}.`));
      if (permitted.length && (required.length || unknown.length)) result.note = "The answer differs by guide and policy; the details below show each recorded outcome.";
      break;
    }
    case "CQ6": {
      // The query orders xsd:date values newest first; preserve its ordering.
      const latest = rows[0].publishedOn;
      const newest = rows.filter((r) => r.publishedOn === latest);
      result.lead = `${names("guide", newest)} ${unique(newest.map((r) => r.guide)).length === 1 ? "is the most recently published matching Alzheimer's symptom guide" : "are the most recently published matching Alzheimer's symptom guides"} (${latest}), with large-text support for older readers.`;
      result.items = rows.map((r) => item(name(r, "guide"), `${name(r, "symptom")} · Published ${r.publishedOn} · Large text supported.`));
      break;
    }
    case "CQ7":
      result.lead = `${names("guide")} ${unique(rows.map((r) => r.guide)).length === 1 ? "is" : "are"} recorded as available in Chinese, about Alzheimer's dementia and intended for older adults.`;
      result.items = rows.map((r) => item(name(r, "guide"), `Chinese · Alzheimer's dementia · Older-adult audience: ${name(r, "audience")}.`));
      break;
    case "CQ8": {
      const within = rows.filter((r) => r.nearby === "Within chosen radius");
      const unknown = rows.filter((r) => r.nearby === "Unknown");
      if (within.length) result.lead = `Yes. ${names("centre", within)} ${unique(within.map((r) => r.centreRecord || r.centre)).length === 1 ? "has" : "have"} Chinese-speaking staff in Victoria and ${unique(within.map((r) => r.centreRecord || r.centre)).length === 1 ? "is" : "are"} within Chen's chosen distance.`;
      else if (!unknown.length) result.lead = `The listed centres have Chinese-speaking staff in Victoria, but none is within Chen's chosen distance.`;
      else result.lead = `${names("centre")} ${unique(rows.map((r) => r.centreRecord || r.centre)).length === 1 ? "has" : "have"} Chinese-speaking staff in Victoria. A nearby match for Chen is not yet confirmed.`;
      result.items = rows.map((r) => item(name(r, "centre"), `${name(r, "staff")} speaks Chinese${r.nearby === "Unknown" ? "; proximity to Chen unconfirmed" : ` · ${r.distanceKm} km from Chen · ${r.nearby.toLowerCase()} (${r.radiusKm} km)`}.`));
      if (within.length && unknown.length) result.note = "Proximity is unconfirmed for some other listed centres.";
      break;
    }
    case "CQ9":
      result.lead = `Yes. ${names("guide")} ${unique(rows.map((r) => r.guide)).length === 1 ? "covers" : "cover"} multiple dementia symptoms and ${unique(rows.map((r) => r.guide)).length === 1 ? "is" : "are"} intended for Sarah.`;
      result.items = rows.map((r) => item(name(r, "guide"), `${r.symptomCount} linked symptoms: ${list(r.symptoms)}.`));
      break;
    default:
      result.lead = data.answer || "No answer is available.";
  }
  // Exact duplicate display sentences may arise from multiple evidence paths.
  result.items = result.items.filter((entry, index, all) => all.findIndex((other) => other.title === entry.title && other.text === entry.text) === index);
  return result;
}


  function renderCQ(data) {
    if (!Array.isArray(data.rows) || !Array.isArray(data.columns)) throw new ApiError("The question response is incomplete.");
    byId("cq-id").textContent = `${data.id} · ${data.title}`;
    byId("cq-question").textContent = data.question;
    byId("cq-result-count").textContent = `${data.rows.length} evidence ${data.rows.length === 1 ? "row" : "rows"}`;
    byId("cq-scenario").textContent = `Scenario: ${data.scenario}`;
    const answer = describeCQ(data);
    byId("cq-answer-status").textContent = `${data.id} answer`;
    byId("cq-answer").textContent = answer.lead;
    byId("cq-answer-box").className = `cq-direct-answer cq-direct-${answer.tone}`;
    byId("cq-answer-items").replaceChildren();
    for (const entry of answer.items) {
      const card = document.createElement("div");
      card.className = "cq-answer-item";
      const title = document.createElement("h4");
      title.textContent = entry.title;
      const detail = document.createElement("p");
      detail.textContent = entry.text;
      card.append(title, detail);
      byId("cq-answer-items").append(card);
    }
    byId("cq-answer-note").textContent = answer.note;
    byId("cq-answer-note").hidden = !answer.note;
    byId("cq-evidence").hidden = false;
    byId("cq-answer-box").hidden = false;
    byId("cq-status").textContent = `Updated ${new Date().toLocaleTimeString([], {hour:"2-digit", minute:"2-digit"})}`;
    byId("cq-head").replaceChildren();
    byId("cq-body").replaceChildren();
    for (const column of data.columns) {
      const th = document.createElement("th"); th.scope = "col"; th.textContent = column.label;
      byId("cq-head").append(th);
    }
    for (const result of data.rows) {
      const row = document.createElement("tr");
      for (const column of data.columns) {
        const cell = document.createElement("td"); cell.textContent = result[column.key] || "Not recorded"; row.append(cell);
      }
      byId("cq-body").append(row);
    }
    byId("cq-table-wrap").hidden = data.rows.length === 0;
    byId("cq-caption").textContent = `${data.id} results from the current graph`;
    for (const note of data.notes) {
      const li = document.createElement("li"); li.textContent = note; byId("cq-notes").append(li);
    }
    byId("cq-query-title").textContent = `View ${data.id} SPARQL`;
    byId("cq-query").textContent = data.query;
    byId("cq-source").textContent = `${data.sourceFile} → competencyQuestion annotation → ${data.sourceCell}. Persona: ${data.persona}. Requirement IDs: ${data.requirements}.`;
    byId("cq-source-path").textContent = `Worksheet path: ${data.sourcePath}`;
    byId("cq-source-gap").textContent = data.sourceGap === "[BLANK]" ? "The worksheet has no gap note for this question. Additional interpretation limits are listed above." : `Worksheet gap note: ${data.sourceGap}`;
  }

  async function refreshCQ() {
    if (state.databaseBusy) return;
    const sequence = ++cqSequence;
    clearCQ("Loading the selected question…");
    byId("cq-recovery").hidden = true;
    byId("cq-refresh").disabled = true;
    if (!cqCatalogLoaded) {
      byId("cq-select").replaceChildren(new Option("Loading questions…", "CQ2"));
      byId("cq-select").disabled = true;
    }
    try {
      if (!cqCatalogLoaded) {
        const catalog = await request("/api/cqs");
        if (sequence !== cqSequence) return;
        if (!Array.isArray(catalog.questions) || catalog.questions.length !== 9) throw new ApiError("The nine-question catalog is unavailable.");
        const selected = byId("cq-select").value;
        byId("cq-select").replaceChildren(...catalog.questions.map((q) => new Option(`${q.id} — ${q.title}`, q.id)));
        byId("cq-select").value = selected || "CQ2";
        byId("cq-select").disabled = false;
        cqCatalogLoaded = true;
      }
      const id = byId("cq-select").value;
      window.dispatchEvent(new CustomEvent("digitalcare:cq-selected", {detail:id}));
      byId("cq-id").textContent = id;
      byId("cq-question").textContent = `Loading ${id}…`;
      const data = await request(`/api/cqs/${encodeURIComponent(id)}`);
      if (sequence !== cqSequence) return;
      renderCQ(data);
    } catch (error) {
      if (sequence === cqSequence) {
        clearCQ(`Unable to load this answer: ${error.message}`);
        if (!cqCatalogLoaded) {
          byId("cq-select").replaceChildren(new Option("Questions unavailable", "CQ2"));
          byId("cq-select").disabled = true;
          byId("cq-question").textContent = "The question list could not be loaded.";
        }
        if (error.status === 404) {
          byId("cq-status").textContent = "This running backend does not provide the CQ1–CQ9 API. Open the updated application or restart it with start.cmd.";
          byId("cq-recovery").hidden = false;
        }
      }
    } finally {
      if (sequence === cqSequence) byId("cq-refresh").disabled = !!state.databaseBusy;
    }
  }

  function markEvidenceUnavailable(message) {
    byId("links-status").textContent = message;
    byId("links-table-wrap").hidden = true;
  }

  function renderDemoStatus(data) {
    state.demoReady = true;
    state.canRestore = data.canRestore === true;
    byId("demo-backup-status").textContent = data.canRestore
      ? `Recovery backup: ${data.backupCount} links, saved ${new Date(data.createdAt).toLocaleString()}. Restore adds these links back and keeps any newer links.`
      : "No reset backup yet. Your first reset will create one automatically.";
    updateControls();
  }

  async function refreshDemoStatus() {
    try {
      renderDemoStatus(await request("/api/demo/status"));
    } catch (error) {
      state.demoReady = false;
      state.canRestore = false;
      byId("demo-backup-status").textContent = `Demo controls unavailable: ${error.message}`;
      updateControls();
    }
  }

  async function refreshDatabaseStatus() {
    try {
      const data = await request("/api/demo/database-status");
      state.databaseReady = true;
      state.canRestoreDatabase = data.canRestore === true;
      const baseline = data.baseline || {};
      byId("database-baseline").textContent = typeof baseline.guides === "number"
        ? `Original data: ${baseline.guides} guides · ${baseline.reviewers} healthcare professionals · ${baseline.reviewLinks} review links.` : "";
      byId("database-backup-status").textContent = data.canRestore
        ? `Backup saved ${new Date(data.createdAt).toLocaleString()}. Restore replaces the current data with this backup.`
        : (data.message || "Your data is backed up before resetting. You can restore it here.");
    } catch (error) {
      state.databaseReady = false;
      state.canRestoreDatabase = false;
      byId("database-backup-status").textContent = `Reset controls unavailable: ${error.message}`;
    }
    updateControls();
  }

  function showDatabaseNoticeAfterReload() {
    try {
      const saved = JSON.parse(sessionStorage.getItem(databaseNoticeKey) || "null");
      sessionStorage.removeItem(databaseNoticeKey);
      if (!saved) return;
      showNotice(byId("database-feedback"), saved.message, "success");
      if (/^CQ[1-9]$/.test(saved.cq)) byId("cq-select").replaceChildren(new Option("Loading questions…", saved.cq));
    } catch (_) { /* The reset still works if browser storage is unavailable. */ }
  }

  async function runDatabaseAction(action) {
    if (state.loading || state.demoBusy || state.editorBusy || state.databaseBusy || !state.databaseReady) return;
    if (action === "restore" && !state.canRestoreDatabase) return;
    state.databaseBusy = action;
    ++cqSequence; // Ignore an answer that was read before the reset.
    updateControls();
    setExportEnabled(false);
    byId("main").inert = true;
    byId("main").setAttribute("aria-busy", "true");
    showNotice(byId("database-feedback"), action === "reset" ? "Saving a backup and restoring the original data…" : "Restoring your previous data…", "warning");
    let completed = false;
    try {
      const result = await request(`/api/demo/${action}-database`, {method: "POST", body: "{}", timeoutMs: 60000});
      window.dispatchEvent(new Event("digitalcare:database-reset"));
      const message = result.message || (action === "reset" ? "Current RDF source restored. Ready for your demo." : "Previous data restored.");
      clearCQ("Reloading the updated data…");
      for (const form of document.querySelectorAll("form")) form.reset();
      for (const id of ["create-feedback", "create-proof", "create-triple", "audience-feedback", "editor-feedback"]) byId(id).hidden = true;
      byId("editor-history").textContent = "";
      byId("editor-undo").disabled = true;
      try { sessionStorage.setItem(databaseNoticeKey, JSON.stringify({message, cq: byId("cq-select").value})); } catch (_) {}
      showNotice(byId("database-feedback"), message, "success");
      completed = true;
      // A fresh page clears local form values and undo state, and re-reads every
      // dropdown, answer and count from the restored graph. The hash is retained.
      window.location.reload();
    } catch (error) {
      const message = error.status === 0
        ? "The response was interrupted and the data may already have changed. Reload this page to check before trying again. Your recovery backup is retained."
        : error.message;
      showNotice(byId("database-feedback"), message, "error");
    } finally {
      if (!completed) {
        state.databaseBusy = false;
        byId("main").inert = false;
        byId("main").removeAttribute("aria-busy");
        updateControls();
        setExportEnabled(state.ready);
        // A lost response can follow a completed reset. Re-read the graph so
        // the overview never continues presenting pre-reset counts as current.
        await loadData();
        byId("cq-refresh").disabled = false;
        byId("database-feedback").focus({preventScroll: true});
      }
    }
  }

  async function runDemoAction(action) {
    if (state.loading || state.demoBusy || state.editorBusy || state.databaseBusy || !state.ready) return;
    state.demoBusy = action;
    byId("demo-feedback").hidden = true;
    updateControls();
    setExportEnabled(false);
    try {
      const result = await request(`/api/demo/${action}-reviews`, { method: "POST", body: "{}" });
      showNotice(byId("demo-feedback"), result.message, "success");
      setCounts(result.counts);
      renderDemoStatus(result.backup);
    } catch (error) {
      showNotice(byId("demo-feedback"), error.message, "error");
    } finally {
      state.demoBusy = false;
      await loadData();
      byId("demo-feedback").focus({ preventScroll: true });
    }
  }

  async function refreshEvidence(refreshQuestion = true) {
    const cqRefresh = refreshQuestion ? refreshCQ() : Promise.resolve();
    const results = await Promise.allSettled([request("/api/links"), request("/api/evidence")]);
    for (const [index, result] of results.entries()) {
      const statusId = "links-status";
      const tableId = "links-table-wrap";
      try {
        if (result.status === "rejected") throw result.reason;
        (index === 0 ? renderLinks : renderEvidence)(result.value);
      } catch (error) {
        if (index === 0) {
          byId(statusId).textContent = `Unable to refresh this evidence: ${error.message}`;
          byId(tableId).hidden = true;
        } else {
          for (const id of ["guides-query", "reviewers-query", "links-query", "update-query"]) {
            byId(id).textContent = `Unable to load this query: ${error.message}`;
          }
        }
      }
    }
    await cqRefresh;
  }

  async function loadData() {
    if (state.loading || state.demoBusy || state.editorBusy || state.databaseBusy) {
      state.refreshPending = true;
      return;
    }
    state.refreshPending = false;
    state.loading = true;
    state.ready = false;
    ui.notice.hidden = true;
    setExportEnabled(false);
    setConnection("Connecting to data", "loading");
    byId("updated-at").textContent = "Refreshing counts…";
    updateControls();
    // Query answers and control status alongside the current graph counts.
    const cqRefresh = refreshCQ();
    const demoRefresh = refreshDemoStatus();
    const databaseRefresh = refreshDatabaseStatus();
    try {
      const health = await request("/api/health");
      setCounts(health.counts);
      byId("source-rdf-path").textContent = health.modelSourcePath || health.modelSourceFilename;
      if (state.sourceHash && state.sourceHash !== health.modelSourceSha256) {
        window.dispatchEvent(new Event("digitalcare:refresh-records"));
      }
      state.sourceHash = health.modelSourceSha256;
      if (health.modelReady === false) {
        throw new ApiError(health.message || "The Digital Care model is not ready. Load the project model into the configured Fuseki dataset, then refresh the data.", 422, "model_not_ready");
      }
      state.ready = true;
      setConnection("Connected to Fuseki", "ready");
      setExportEnabled(true);
      byId("updated-at").textContent = `Updated ${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`;
      await refreshEvidence(false);
    } catch (error) {
      state.ready = false;
      setCounts();
      setConnection(error.code === "model_not_ready" ? "Dataset needs setup" : "Data unavailable", "offline");
      byId("updated-at").textContent = "Counts unavailable · Refresh to retry";
      showNotice(ui.notice, error.message);
      markEvidenceUnavailable("Evidence is unavailable until the dataset connection is restored.");
    } finally {
      await Promise.allSettled([cqRefresh, demoRefresh, databaseRefresh]);
      state.loading = false;
      updateControls();
      if (state.refreshPending) loadData();
    }
  }

  byId("cq-select").addEventListener("change", refreshCQ);
  byId("cq-refresh").addEventListener("click", refreshCQ);
  ui.refresh.addEventListener("click", async () => {
    await loadData();
    window.dispatchEvent(new Event("digitalcare:refresh-records"));
  });
  byId("reset-reviews").addEventListener("click", () => runDemoAction("reset"));
  byId("restore-reviews").addEventListener("click", () => runDemoAction("restore"));
  byId("reset-database").addEventListener("click", () => runDatabaseAction("reset"));
  byId("restore-database").addEventListener("click", () => runDatabaseAction("restore"));
  window.addEventListener("digitalcare:editing", (event) => {
    state.editorBusy = event.detail;
    updateControls();
    if (!state.editorBusy && state.refreshPending) loadData();
  });
  window.addEventListener("digitalcare:data-changed", loadData);
  for (const link of [ui.export, ui.exportRdf]) {
    link.addEventListener("click", (event) => {
      if (link.getAttribute("aria-disabled") === "true") event.preventDefault();
    });
  }
  document.querySelectorAll(".demo-nav a").forEach((link) => link.addEventListener("click", () => {
    const target = document.querySelector(link.getAttribute("href"));
    if (target?.tagName === "DETAILS") target.open = true;
  }));
  showDatabaseNoticeAfterReload();
  loadData();
})();
