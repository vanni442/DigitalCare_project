"use strict";
(() => {
  const el = (id) => document.getElementById(id);
  const NS = "http://uow.edu.au/isit906/thestar/digitalcare#";
  const state = { cq: "", catalog: null, shape: null, revision: null, sequence: 0, currentSequence: 0,
    busy: false, outsideBusy: false, loading: false, captureLoading: false, canUndo: false, audienceRevision: null,
    demoPlan: null, demoSequence: 0, demoLoading: false, demoBusy: false };
  const pendingKey = "digitalcare:pending-guided-records-v2";
  const draftKey = "digitalcare:guided-drafts-v2";
  let pendingDemo = {}, guidedDrafts = {};
  const isValues = (value) => value && typeof value === "object" && !Array.isArray(value)
    && Object.values(value).every((item) => typeof item === "string");
  try {
    const saved = JSON.parse(sessionStorage.getItem(pendingKey) || "{}");
    for (const [cq, pending] of Object.entries(saved)) {
      if (/^CQ[1-9]$/.test(cq) && pending && /^[a-f0-9-]{32,36}$/i.test(pending.requestId) && isValues(pending.values)) pendingDemo[cq] = pending;
    }
    const drafts = JSON.parse(sessionStorage.getItem(draftKey) || "{}");
    for (const [cq, values] of Object.entries(drafts)) if (/^CQ[1-9]$/.test(cq) && isValues(values)) guidedDrafts[cq] = values;
  } catch (_) { /* A retry still retains its request ID in this page. */ }
  function rememberPending() {
    try { sessionStorage.setItem(pendingKey, JSON.stringify(pendingDemo)); } catch (_) {}
  }
  function rememberDrafts() {
    try { sessionStorage.setItem(draftKey, JSON.stringify(guidedDrafts)); } catch (_) {}
  }

  function guidedStatus() {
    if (pendingDemo[state.cq]) {
      el("demo-add-status").textContent = "Save not yet confirmed. Your entries are locked until you retry this same save.";
    } else if (state.demoPlan?.ready) {
      const required = [...el("demo-add-fields").querySelectorAll("[required]")];
      const filled = required.filter((node) => node.value.trim()).length;
      el("demo-add-status").textContent = `${filled} of ${required.length} required fields entered. Examples are suggestions; you enter the values.`;
    }
  }

  function renderGuidedFields(plan, cq) {
    // Drafts follow the current server form. Keep an uncertain request intact
    // so its original ID and values can still verify a previously saved record.
    if (plan.ready) {
      const fieldIds = new Set((plan.fields || []).map((field) => field.id));
      guidedDrafts[cq] = Object.fromEntries(Object.entries(guidedDrafts[cq] || {}).filter(([key]) => fieldIds.has(key)));
      rememberDrafts();
    }
    const values = pendingDemo[cq]?.values || guidedDrafts[cq] || {};
    el("demo-add-fields").replaceChildren();
    for (const field of plan.fields || []) {
      const group = document.createElement("div"); group.className = "field guided-entry-field";
      const label = document.createElement("label");
      label.htmlFor = `guided-${field.id}`; label.textContent = field.label;
      const node = document.createElement(field.type === "select" ? "select" : "input");
      node.id = label.htmlFor; node.name = field.id; node.required = !!field.required;
      if (field.type === "select") {
        node.append(new Option("Choose…", ""));
        for (const option of field.options || []) node.append(new Option(option.label, option.value));
        if (values[field.id] && !(field.options || []).some((option) => option.value === values[field.id])) {
          const missing = new Option(`${values[field.id].split("#").pop()} (unavailable)`, values[field.id]);
          missing.disabled = true; node.append(missing);
        }
      } else {
        node.type = ["text", "date", "number"].includes(field.type) ? field.type : "text";
        node.placeholder = field.example || "";
        for (const attr of ["min", "max", "step", "maxLength"]) if (field[attr] !== undefined) node[attr] = field[attr];
      }
      node.value = values[field.id] || "";
      const hint = document.createElement("p"); hint.className = "guided-field-hint"; hint.id = `${node.id}-hint`;
      hint.textContent = [field.example ? `Example: ${field.example}.` : "", field.hint || ""].filter(Boolean).join(" ");
      node.setAttribute("aria-describedby", hint.id);
      const validate = () => node.setCustomValidity(field.type === "select" && node.value
        && !(field.options || []).some((option) => option.value === node.value) ? "Choose an available record." : "");
      validate();
      const change = () => {
        if (pendingDemo[cq]) return;
        guidedDrafts[cq] = {...guidedDrafts[cq], [field.id]: node.value};
        rememberDrafts(); validate(); guidedStatus();
      };
      node.addEventListener("input", change); node.addEventListener("change", change);
      group.append(label, node); if (hint.textContent) group.append(hint);
      el("demo-add-fields").append(group);
    }
  }

  async function api(path, body) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(path, { method: body === undefined ? "GET" : "POST", cache: "no-store", signal: controller.signal,
        headers: { Accept: "application/json", ...(body === undefined ? {} : {"Content-Type":"application/json"}) },
        ...(body === undefined ? {} : {body:JSON.stringify(body)}) });
      let data;
      try { data = await response.json(); }
      catch (_) { const error = new Error("The server response could not be read."); error.uncertain = body !== undefined; throw error; }
      if (!response.ok) { const error = new Error(data.message || `Request failed (${response.status}).`); error.status = response.status; error.code = data.error || data.code; throw error; }
      return data;
    } catch (error) {
      if (error.name === "AbortError" || error instanceof TypeError) {
        const interrupted = new Error(body === undefined
          ? "The data service is unavailable. Reload the form when the server is ready."
          : "The response was interrupted. Data may already have changed. Reload current values before retrying.");
        interrupted.uncertain = body !== undefined; throw interrupted;
      }
      throw error;
    } finally { clearTimeout(timeout); }
  }

  function notice(id, text, success = false) {
    const node = el(id); node.textContent = text; node.className = `notice notice-${success ? "success" : "error"}`; node.hidden = false;
  }
  function options(node, records, placeholder, preferred = "") {
    node.replaceChildren(new Option(placeholder,""), ...records.map((r) => new Option(r.label || r.iri.split("#").pop(),r.iri)));
    if (records.some((r) => r.iri === preferred)) node.value = preferred;
  }
  function display(values) { return values.length ? values.map((v) => v.type === "uri" ? v.value.split("#").pop() : v.value).join("; ") : "Not recorded"; }
  function controls() {
    const locked = state.busy || state.outsideBusy;
    for (const id of ["editor-field","editor-subject","editor-action","editor-reload"]) el(id).disabled = locked || state.loading;
    for (const id of ["editor-value","editor-text"]) el(id).disabled = locked || state.loading || !state.revision;
    el("editor-save").disabled = locked || state.loading || !state.revision || !el("editor-subject").value;
    el("editor-undo").disabled = locked || !state.canUndo;
    el("capture-reload").disabled = locked || state.captureLoading;
    for (const node of document.querySelectorAll(".partb-forms input, .partb-forms select")) node.disabled = locked || state.captureLoading || node.dataset.failed === "true";
    el("create-guide-button").disabled = locked || state.captureLoading || Array.from(document.querySelectorAll("#create-guide-form [data-range-property]")).some((node) => node.dataset.failed === "true");
    el("audience-link-button").disabled = locked || state.captureLoading || !state.audienceRevision || !el("audience-person").value;
    el("editor-save").textContent = state.busy ? "Saving…" : "Save data change";
    el("demo-add-button").disabled = locked || state.demoLoading || (!state.demoPlan?.ready && !pendingDemo[state.cq]);
    el("demo-add-button").textContent = state.demoBusy ? "Saving record…" : pendingDemo[state.cq] ? "Retry save safely" : "Save record →";
    el("demo-plan-reload").disabled = locked || state.demoLoading;
    for (const node of el("demo-add-fields").querySelectorAll("input, select")) node.disabled = locked || state.demoLoading || !!pendingDemo[state.cq];
  }

  async function loadDemoPlan() {
    const version = ++state.demoSequence;
    const cq = state.cq;
    if (!cq) return;
    state.demoPlan = null; state.demoLoading = true; controls();
    el("demo-add-title").textContent = `Add a record for ${cq}`;
    el("demo-add-summary").textContent = "Loading the fields for this question…";
    el("demo-add-fields").replaceChildren();
    el("demo-add-context").hidden = true;
    el("demo-add-status").textContent = "Checking the current database…";
    el("demo-plan-reload").hidden = true;
    try {
      const plan = await api(`/api/demo/plan/${encodeURIComponent(cq)}`);
      if (version !== state.demoSequence || cq !== state.cq) return;
      if (plan.formVersion !== 2) throw new Error("Restart the updated website, then reload these fields.");
      state.demoPlan = plan;
      el("demo-add-title").textContent = plan.title || `Add a record for ${cq}`;
      el("demo-add-summary").textContent = plan.summary || "Enter each value using the examples, then save to see the answer change.";
      el("demo-add-context").textContent = (plan.context || []).join(" ");
      el("demo-add-context").hidden = !el("demo-add-context").textContent;
      renderGuidedFields(plan, cq);
      el("demo-add-status").textContent = plan.ready
        ? "Enter your data using the examples below each field."
        : plan.reason || "This question needs supporting records before a demo can be added.";
      guidedStatus();
      el("demo-plan-reload").hidden = !!plan.ready;
    } catch (error) {
      if (version !== state.demoSequence || cq !== state.cq) return;
      el("demo-add-summary").textContent = "The entry fields could not be loaded.";
      el("demo-add-status").textContent = error.status === 404
        ? "Restart the updated website, then reload these fields."
        : error.message;
      el("demo-plan-reload").hidden = false;
    } finally {
      if (version === state.demoSequence) { state.demoLoading = false; controls(); }
    }
  }

  async function addDemoRecord() {
    if (state.busy || state.outsideBusy || state.demoLoading || (!state.demoPlan?.ready && !pendingDemo[state.cq])) return;
    const cq = state.cq;
    if (!pendingDemo[cq] && !el("demo-add-form").reportValidity()) return;
    if (!pendingDemo[cq]) {
      const values = Object.fromEntries([...el("demo-add-fields").querySelectorAll("input, select")].map((node) => [node.name, node.value]));
      guidedDrafts[cq] = {...guidedDrafts[cq], ...values}; rememberDrafts();
      pendingDemo[cq] = {requestId:crypto.randomUUID(), values}; rememberPending();
    }
    const {requestId, values} = pendingDemo[cq];
    state.busy = true; state.demoBusy = true; controls();
    window.dispatchEvent(new CustomEvent("digitalcare:editing", {detail:true}));
    el("demo-add-feedback").hidden = true;
    let result = null;
    try {
      result = await api("/api/demo/add-record", {cq, requestId, values});
      if (!result.ok) throw new Error(result.message || "The record could not be added.");
      delete pendingDemo[cq]; rememberPending();
      if (cq === state.cq) {
        notice("demo-add-feedback", `${result.recordLabel || "Your record"} saved. See the updated answer on the right.`, true);
        document.querySelector(".cq-results").classList.add("demo-result-updated");
        setTimeout(() => document.querySelector(".cq-results").classList.remove("demo-result-updated"), 8000);
      }
    } catch (error) {
      // A saved record can fail the final match check (409 DEMO_NOT_MATCHED).
      // Keep that ID, and any uncertain transport/server failure, to prevent duplicates.
      const rejected = error.status >= 400 && error.status < 500
        && ![408,429].includes(error.status) && error.code !== "DEMO_NOT_MATCHED";
      if (rejected) { delete pendingDemo[cq]; rememberPending(); }
      let message;
      if (error.code === "DEMO_WAS_RESET") message = "The database was reset. Check your entries, then save a new record.";
      else if (error.code === "GUIDED_FORM_CHANGED") message = "The form has been updated. Your entries are kept; check the fields and save again.";
      else if (["INVALID_DEMO_RECEIPT", "DEMO_RECEIPT_INVALID"].includes(error.code)) message = "The previous request can no longer be reused. Your entries are kept; check them and save again.";
      else if (rejected) message = `${error.message} Your entries are kept; correct them and save again.`;
      else if (error.uncertain) message = "The response was interrupted. Retry save safely checks the same entries without creating a duplicate.";
      else message = `${error.message} Click Retry save safely to check this same record.`;
      if (cq === state.cq) notice("demo-add-feedback", message);
    } finally {
      state.busy = false; state.demoBusy = false;
      window.dispatchEvent(new CustomEvent("digitalcare:editing", {detail:false}));
      window.dispatchEvent(new Event("digitalcare:data-changed"));
      await Promise.all([captureChoices(), state.catalog ? loadField() : selectQuestion(cq), history(), loadDemoPlan()]);
      controls();
      if (cq === state.cq) el("demo-add-feedback").focus({preventScroll:true});
    }
  }
  async function history() {
    try { const value = await api("/api/editor/status"); state.canUndo = value.canUndo; el("editor-history").textContent = value.message; }
    catch (error) { state.canUndo = false; el("editor-history").textContent = error.message; }
    controls();
  }
  async function current() {
    const version = ++state.currentSequence;
    state.revision = null; el("editor-current").textContent = "Loading…"; controls();
    if (!el("editor-subject").value || !state.shape) { el("editor-current").textContent = "Choose an existing record."; return; }
    try {
      const query = new URLSearchParams({cq:state.cq,field:el("editor-field").value,subject:el("editor-subject").value});
      const result = await api(`/api/editor/current?${query}`);
      if (version !== state.currentSequence) return;
      state.revision = result.revision; el("editor-current").textContent = display(result.values);
      if (state.shape.kind === "literal") {
        const value = result.values.length === 1 ? result.values[0].value : "";
        el("editor-text").value = value;
        el("editor-value").value = ["true","false"].includes(value) ? value : "";
      }
    } catch (error) { if (version === state.currentSequence) el("editor-current").textContent = error.message; }
    finally { if (version === state.currentSequence) controls(); }
  }
  function valueMode() {
    const shape = state.shape;
    const isSelect = shape?.kind === "record" || shape?.ranges?.[0].endsWith("#boolean");
    const clear = el("editor-action").value === "clear";
    el("editor-value").hidden = !isSelect || clear;
    el("editor-text").hidden = !!isSelect || clear;
    el("editor-value").required = !!isSelect && !clear;
    el("editor-text").required = !isSelect && !clear;
    el("editor-value-field").hidden = clear;
    el("editor-value-field").querySelector("label").htmlFor = isSelect ? "editor-value" : "editor-text";
  }
  async function loadField() {
    const version = ++state.sequence; ++state.currentSequence;
    state.loading = true; state.revision = null; controls();
    const field = el("editor-field").value;
    try {
      const data = await api(`/api/editor/options?${new URLSearchParams({cq:state.cq,field})}`);
      if (version !== state.sequence) return;
      state.shape = data.field;
      const old = el("editor-subject").value;
      options(el("editor-subject"), data.subjects, "Choose an existing record", data.subjects.some((r) => r.iri === old) ? old : state.catalog.defaultSubject);
      if (!el("editor-subject").value && data.subjects.length) el("editor-subject").value = data.subjects[0].iri;
      el("editor-help").textContent = data.field.help;
      el("editor-mapping").textContent = `${data.field.iri} | Domain: ${data.field.domains.join(", ")} | Range: ${data.field.ranges.join(", ")}. Schema read from the current RDF graph, including explicit inverse-property axioms. Changes write one direction only; no individual is deleted.`;
      const isRecord = data.field.kind === "record";
      const actions = isRecord ? [["add","Add relationship"],["remove","Remove selected relationship"],["replace","Replace all values of this relationship"]] : [["set","Save value"],["clear","Clear value (leave unknown)"]];
      el("editor-action").replaceChildren(...actions.map(([value,label]) => new Option(label,value)));
      if (isRecord) options(el("editor-value"),data.objects,"Choose a linked record");
      else el("editor-value").replaceChildren(new Option("Choose True or False",""),new Option("True","true"),new Option("False","false"));
      const datatype = data.field.ranges[0].split("#").pop();
      el("editor-text").type = datatype === "date" ? "date" : datatype === "decimal" ? "number" : datatype === "anyURI" ? "url" : "text";
      el("editor-text").min = "0"; el("editor-text").max = "50000"; el("editor-text").step = "any";
      valueMode();
      await current();
    } catch (error) {
      if (version === state.sequence) { state.shape = null; el("editor-current").textContent = error.message; notice("editor-feedback",error.message); }
    } finally { if (version === state.sequence) {state.loading = false; controls();} }
  }
  async function selectQuestion(cq) {
    if (cq === state.cq && state.catalog) { loadDemoPlan(); return; }
    const version = ++state.sequence; ++state.currentSequence;
    state.cq = cq; state.catalog = null; state.revision = null; state.loading = true; controls();
    el("editor-feedback").hidden = true;
    el("editor-advanced").open = false;
    el("demo-add-feedback").hidden = true;
    document.querySelector(".cq-results").classList.remove("demo-result-updated");
    loadDemoPlan();
    try {
      const catalog = await api(`/api/editor/catalog/${encodeURIComponent(cq)}`);
      if (version !== state.sequence) return;
      state.catalog = catalog; el("editor-title").textContent = `Edit ${cq} data`; el("editor-tip").textContent = catalog.tip;
      el("editor-field").replaceChildren(...catalog.fields.map((field) => new Option(field.label,field.id)));
      el("editor-subject").replaceChildren();
      await loadField();
    } catch (error) { if (version === state.sequence) { state.loading = false; notice("editor-feedback",error.message); controls(); } }
  }
  async function mutate(path, body, feedback) {
    if (state.busy || state.outsideBusy) return null;
    state.busy = true; controls(); window.dispatchEvent(new CustomEvent("digitalcare:editing",{detail:true}));
    try {
      const result = await api(path,body); notice(feedback,result.message,true); return result;
    } catch (error) { notice(feedback,error.message); return null; }
    finally {
      state.busy = false; window.dispatchEvent(new CustomEvent("digitalcare:editing",{detail:false}));
      window.dispatchEvent(new Event("digitalcare:data-changed"));
      await Promise.all([current(),history(),loadAudienceCurrent()]); controls();
      el(feedback).focus({preventScroll:true});
    }
  }

  // Assignment requirement: callers pass PROPERTY IRIs, never class names.
  async function fillDropdownFromRange(select, propertyIri) {
    const old = select.value;
    try {
      const data = await api(`/api/partb/range?${new URLSearchParams({property:propertyIri})}`);
      options(select,data.records,select.required ? "Choose an existing record" : "Not recorded",old);
      select.dataset.failed = "false";
    } catch (error) {
      select.replaceChildren(new Option("Unavailable - see message below", "")); select.dataset.failed = "true";
      throw error;
    }
  }
  async function captureChoices() {
    if (state.busy) return;
    state.captureLoading = true; el("capture-load-status").hidden = false; el("capture-load-status").textContent = "Loading records…"; controls();
    const results = await Promise.allSettled(Array.from(document.querySelectorAll("[data-range-property]")).map((node) => fillDropdownFromRange(node,NS + node.dataset.rangeProperty)));
    const errors = results.filter((r) => r.status === "rejected").map((r) => r.reason.message);
    el("capture-load-status").textContent = errors.length ? [...new Set(errors)].join(" ") : "";
    el("capture-load-status").hidden = errors.length === 0;
    state.captureLoading = false; await loadAudienceCurrent(); controls();
  }
  let audienceSequence = 0;
  async function loadAudienceCurrent() {
    const version = ++audienceSequence;
    state.audienceRevision = null;
    const subject = el("audience-guide").value;
    if (!subject) {el("audience-current").textContent = "Choose a guide."; controls(); return;}
    try {
      const result = await api(`/api/editor/current?${new URLSearchParams({cq:"CQ4",field:"isIntendedFor",subject})}`);
      if (version !== audienceSequence) return;
      state.audienceRevision = result.revision; el("audience-current").textContent = "Current audiences: " + display(result.values);
    } catch(error) { if (version === audienceSequence) el("audience-current").textContent = error.message; }
    finally {controls();}
  }

  el("cq-editor-form").addEventListener("submit",async(event) => {
    event.preventDefault(); if (!state.revision || !state.shape) return;
    const value = el("editor-action").value === "clear" ? "" : el("editor-text").hidden ? el("editor-value").value : el("editor-text").value;
    await mutate("/api/editor/change",{cq:state.cq,field:el("editor-field").value,subject:el("editor-subject").value,action:el("editor-action").value,value,revision:state.revision},"editor-feedback");
  });
  el("editor-field").addEventListener("change",loadField);
  el("editor-subject").addEventListener("change",current);
  el("editor-action").addEventListener("change",valueMode);
  el("editor-reload").addEventListener("click",() => {loadField(); history();});
  el("editor-undo").addEventListener("click",() => mutate("/api/editor/undo",{},"editor-feedback"));
  el("demo-add-form").addEventListener("submit", (event) => {event.preventDefault(); addDemoRecord();});
  el("demo-plan-reload").addEventListener("click", loadDemoPlan);
  el("capture-reload").addEventListener("click",captureChoices);
  el("audience-guide").addEventListener("change",loadAudienceCurrent);
  el("audience-person").addEventListener("change",controls);
  el("audience-link-form").addEventListener("submit",async(event) => {
    event.preventDefault(); if (!state.audienceRevision) return;
    await mutate("/api/partb/link",{guide:el("audience-guide").value,person:el("audience-person").value,revision:state.audienceRevision},"audience-feedback");
  });
  el("create-guide-form").addEventListener("submit",async(event) => {
    event.preventDefault(); const links = {};
    for(const node of document.querySelectorAll("#create-guide-form [data-range-property]")) if(node.value) links[node.dataset.rangeProperty] = node.value;
    const result = await mutate("/api/partb/create",{title:el("new-title").value,publishedOn:el("new-date").value,links,isDownloadable:el("new-downloadable").value,isPrintable:el("new-printable").value,supportsLargeText:el("new-large").value},"create-feedback");
    if(result) {el("create-triple").textContent = `New IRI: ${result.iri}\nNamed individuals: ${result.beforeIndividuals} -> ${result.afterIndividuals}\n\n${result.triple}`; el("create-triple").hidden = false; el("create-proof").hidden = false; notice("create-feedback", `Created ${el("new-title").value}. Ready to link.`, true); await captureChoices(); await loadField();}
  });
  window.addEventListener("digitalcare:cq-selected",(event) => selectQuestion(event.detail));
  window.addEventListener("digitalcare:refresh-records",() => {
    if (state.busy) return;
    captureChoices();
    if (state.catalog) loadField();
    history();
  });
  window.addEventListener("digitalcare:form-busy",(event) => {state.outsideBusy = event.detail; controls();});
  window.addEventListener("digitalcare:database-reset",() => {
    pendingDemo = {}; guidedDrafts = {}; rememberPending(); rememberDrafts(); ++state.demoSequence; state.demoPlan = null;
    el("demo-add-feedback").hidden = true; loadDemoPlan(); controls();
  });
  selectQuestion(el("cq-select").value || "CQ2"); history(); captureChoices();
})();
