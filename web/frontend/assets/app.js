import { EvidenceView, provenanceText } from "./evidence-view.mjs";
import { defaultInterval, intervalConfig, domainSummary } from "./input-domains.mjs";
import { CCTTreeView } from "./cct-tree.mjs?v=4";
import { buildMetricCards, createRequestTokens } from "./metric-cards.mjs?v=3";
import {
  buildOutcomeErrorView,
  buildOutcomeView,
  describeStartFailure,
  isNoFailureLocalizationRejection,
} from "./analysis-outcome.mjs";
import {
  detectJavaClassName,
  extractJavaInputParameters,
  parseFSFUnits,
  serializeFSFUnits,
} from "./workspace-input.mjs";
import { PROGRAM_EXAMPLES } from "./program-examples.mjs";
import {
  isArchived,
  liveSucceededJobs,
  originLabel,
  validateSessionArchiveFile,
} from "./session-view.mjs";

const state = {
  workspaces: [],
  jobs: [],
  history: [],
  selectedWorkspaceId: null,
  selectedJobId: null,
  selectionRequestToken: 0,
  jobsRequestToken: 0,
  workspacesRequestToken: 0,
  historyRequestToken: 0,
  outcomeContextToken: 0,
  eventSource: null,
  cctTree: null,
  evidenceView: null,
  cctRequestToken: 0,
  metricsTokens: createRequestTokens(),
  outcomeTokens: createRequestTokens(),
  outcomeCache: new Map(),
  outcomePending: new Map(),
  activeFsfTInput: null,
  runTimer: null,
  monitoredJobId: null,
  monitorStartedAt: null,
  runActivityCount: 0,
  localizationOverlay: null,
  resultDisplayMode: "all",
  workspaceSource: null,
  activeSourceTab: "java",
  workspaceSourceRequestToken: 0,
  cscParameterCache: new Map(),
  cscConfigWorkspaceId: null,
  cscInputIntervals: {},
  cscIntervalBound: 200,
  cscExplicitIntervals: false,
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

document.addEventListener("DOMContentLoaded", async () => {
  state.cctTree = new CCTTreeView({
    panel: $("#cct-view-panel"),
    svg: $("#cct-canvas"),
    world: $("#cct-world"),
    links: $("#cct-links"),
    labels: $("#cct-edge-labels"),
    nodes: $("#cct-nodes"),
    inspector: $("#cct-inspector"),
    title: $("#cct-view-title"),
    subtitle: $("#cct-view-subtitle"),
    layoutSelect: $("#cct-layout-select"),
    fitButton: $("#cct-fit"),
    zoomInButton: $("#cct-zoom-in"),
    zoomOutButton: $("#cct-zoom-out"),
    rootButton: $("#cct-root"),
    nextFailureButton: $("#cct-next-failure"),
    failureBadge: $("#cct-failure-badge"),
    onOpenSource: ({ tab, line }) => openSourceDialog(tab, line),
  });
  state.evidenceView = new EvidenceView($("#evidence-summary"), (data) => {
    if (state.selectedJobId !== data.jobId || !state.cctTree.model) return;
    state.cctTree.selectNode(data.leafNodeId, data.testId);
    state.cctTree.focusNode(data.leafNodeId);
  }, (data) => {
    state.localizationOverlay = data?.overlay || null;
    state.cctTree?.setLocalizationOverlay(state.localizationOverlay);
  });
  bindNavigation();
  bindActions();
  await refreshAll();
  window.setInterval(refreshJobs, 1500);
});

function bindNavigation() {
  $$(".nav-item").forEach((button) => {
    button.addEventListener("click", () => {
      $$(".nav-item").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      showView(button.dataset.view);
    });
  });
  $$(".result-view-tab").forEach((button) => {
    button.addEventListener("click", () => selectLatestResult(button.dataset.resultKind));
  });
}

function bindActions() {
  $$('.file-picker input[type="file"]').forEach((input) => {
    const filename = input.parentElement.querySelector(".file-picker-name");
    input.addEventListener("change", () => {
      filename.textContent = input.files?.[0]?.name || "No file selected";
    });
    input.form?.addEventListener("reset", () => {
      filename.textContent = "No file selected";
    });
  });
  $("#toggle-import").addEventListener("click", () => {
    const form = $("#workspace-form");
    const opening = form.classList.contains("hidden");
    form.classList.toggle("hidden");
    if (opening) window.setTimeout(() => $("#java-source").focus(), 0);
  });
  $("#close-import").addEventListener("click", () => {
    $("#workspace-form").classList.add("hidden");
  });
  $("#open-history").addEventListener("click", openHistoryDialog);
  $("#history-list").addEventListener("click", (event) => {
    const button = event.target.closest("[data-import-history]");
    if (button) importHistory(button.dataset.importHistory);
  });
  $("#session-import-form").addEventListener("submit", importSessionArchive);
  $$(".input-mode-tab").forEach((button) => {
    button.addEventListener("click", () => setInputMode(button.dataset.inputMode));
  });
  $("#java-source").addEventListener("input", renderJavaSourceHints);
  $("#fsf-inline-file").addEventListener("change", importFSFFile);
  $("#add-fsf-row").addEventListener("click", () => addFSFRow());
  $("#fsf-rows").addEventListener("click", handleFSFRowAction);
  $("#fsf-rows").addEventListener("focusin", (event) => {
    if (event.target.matches("[data-fsf-t]")) state.activeFsfTInput = event.target;
  });
  addFSFRow();
  renderJavaSourceHints();
  setInputMode("paste");
  initializeProgramExamples();
  $("#program-example").addEventListener("change", renderProgramExampleDescription);
  $("#load-program-example").addEventListener("click", loadProgramExample);
  $("#workspace-form").addEventListener("submit", createWorkspace);
  $("#refresh-button")?.addEventListener("click", refreshAll);
  $("#nav-history-button")?.addEventListener("click", openHistoryDialog);
  $("#health-indicator")?.addEventListener("click", () => {
    $("#environment-dialog")?.showModal();
  });
  $("#run-csc").addEventListener("click", () => {
    if (latestWorkspaceJob("csc")) openCSCNewRunDialog();
    else openCSCSettingsDialog();
  });
  for (const select of [$("#csc-form").elements.mode, $("#csc-form").elements.strategy]) {
    select.addEventListener("change", updateCSCFieldVisibility);
  }
  updateCSCFieldVisibility();
  $("#reset-input-domains").addEventListener("click", () => {
    state.cscInputIntervals = {};
    state.cscIntervalBound = 200;
    state.cscExplicitIntervals = false;
    renderCSCInputDomains();
  });
  $("#csc-input-domains").addEventListener("input", (event) => {
    const control = event.target;
    if (!control.dataset.inputName) return;
    state.cscInputIntervals[control.dataset.inputName][control.dataset.endpoint] = control.value;
  });
  for (const selectId of ["tbfv-csc-job", "localization-tbfv-job"]) {
    $(`#${selectId}`).addEventListener("change", (event) => {
      renderDependencies();
      ensureOutcomeCached(event.target.value);
    });
  }
  $("#run-tbfv").addEventListener("click", () => openStageDialog("tbfv"));
  $("#run-localization").addEventListener("click", () => openStageDialog("localization"));
  $$('[data-open-stage]').forEach((button) => {
    button.addEventListener("click", () => openStageDialog(button.dataset.openStage));
  });
  $("#cct-maximize").addEventListener("click", toggleCCTMaximize);
  $("#cct-root").addEventListener("click", () => state.cctTree?.jumpToRoot());
  $("#cct-next-failure").addEventListener("click", () => state.cctTree?.jumpToNextFailure());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#cct-view-panel").classList.contains("is-maximized")) {
      setCCTMaximized(false);
    }
  });
  $("#confirm-csc").addEventListener("click", () => runFromDialog("csc", startCSC));
  $("#confirm-tbfv").addEventListener("click", () => runFromDialog("tbfv", startTBFV));
  $("#confirm-localization").addEventListener("click", () => runFromDialog("localization", startLocalization));
  $$('[data-close-dialog]').forEach((button) => {
    button.addEventListener("click", () => button.closest("dialog").close());
  });
  $$(".analysis-dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });
  $$(".method-help").forEach(bindMethodHelp);
  document.addEventListener("click", () => closeMethodHelp());
  $("#cancel-job").addEventListener("click", cancelSelectedJob);
  $$(".result-display-tab").forEach((tab) => {
    tab.addEventListener("click", () => setResultDisplayMode(tab.dataset.displayMode));
  });
  $("#view-program-source")?.addEventListener("click", () => openSourceDialog());
  $("#cct-view-source")?.addEventListener("click", openSourceFromCCT);
  $$(".source-tab-btn").forEach((tab) => {
    tab.addEventListener("click", () => switchSourceTab(tab.dataset.sourceTab));
  });
  $("#copy-source-btn")?.addEventListener("click", copyCurrentSource);
  document.addEventListener("cct:view-source", (event) => {
    openSourceDialog(event.detail?.tab || "java", event.detail?.line);
  });
}

function setResultDisplayMode(mode) {
  state.resultDisplayMode = mode;
  $$(".result-display-tab").forEach((tab) => {
    const active = tab.dataset.displayMode === mode;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  applyResultDisplayMode();
}

function applyResultDisplayMode() {
  const mode = state.resultDisplayMode || "all";
  const cct = $("#cct-view-panel");
  const outcome = $("#outcome-summary");
  const evidence = $("#evidence-summary");
  const metrics = $("#metrics-summary");

  cct?.classList.toggle("mode-suppressed", mode !== "all" && mode !== "tree");
  evidence?.classList.toggle("mode-suppressed", mode !== "all" && mode !== "evidence");
  outcome?.classList.toggle("mode-suppressed", mode !== "all" && mode !== "metrics");
  metrics?.classList.toggle("mode-suppressed", mode !== "all" && mode !== "metrics");
}

function bindMethodHelp(help) {
  const trigger = help.querySelector(".method-help-trigger");
  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const opening = !help.classList.contains("is-open");
    closeMethodHelp();
    help.classList.toggle("is-open", opening);
    trigger.setAttribute("aria-expanded", String(opening));
  });
  help.addEventListener("mouseleave", () => {
    if (document.activeElement === trigger) trigger.blur();
    closeMethodHelp(help);
  });
  trigger.addEventListener("blur", () => {
    window.setTimeout(() => {
      if (!help.matches(":hover") && !help.contains(document.activeElement)) closeMethodHelp(help);
    }, 0);
  });
}

function closeMethodHelp(target = null) {
  const helps = target ? [target] : $$(".method-help.is-open");
  helps.forEach((help) => {
    help.classList.remove("is-open");
    help.querySelector(".method-help-trigger").setAttribute("aria-expanded", "false");
  });
}

function openStageDialog(kind) {
  if (kind === "csc") {
    openCSCSettingsDialog();
    return;
  }
  if (kind === "localization") {
    const localizationGate = downstreamGate($("#localization-tbfv-job").value, "localization");
    const hasNoFailures = localizationGate.reasonCodes?.some((item) => item.code === "no_failed_verification")
      || localizationGate.detail?.includes("no_failed_verification")
      || localizationGate.detail?.includes("No verification check failed");
    if (hasNoFailures) {
      toast("Verification completed with no failed checks. Fault localization is not applicable.");
      return;
    }
  }
  const dialog = $(`#${kind}-dialog`);
  if (!dialog || dialog.open) return;
  dialog.showModal();
}

function latestWorkspaceJob(kind) {
  return state.jobs.find((job) => (
    job.workspace_id === state.selectedWorkspaceId && job.kind === kind
  )) || null;
}

function selectedStageJob(kind) {
  const selected = state.jobs.find((job) => job.id === state.selectedJobId);
  if (!selected || selected.workspace_id !== state.selectedWorkspaceId) {
    return latestWorkspaceJob(kind);
  }
  if (selected.kind === kind) return selected;
  if (kind === "tbfv" && selected.kind === "localization") {
    return state.jobs.find((job) => job.id === selected.config?.tbfv_job_id)
      || latestWorkspaceJob(kind);
  }
  if (kind === "csc") {
    const verification = selected.kind === "tbfv"
      ? selected
      : state.jobs.find((job) => job.id === selected.config?.tbfv_job_id);
    return state.jobs.find((job) => job.id === verification?.config?.csc_job_id)
      || latestWorkspaceJob(kind);
  }
  return latestWorkspaceJob(kind);
}

const CSC_MODE_NOTES = {
  expanded: "Explores condition sequences and re-explores loop branches pruned by an earlier occurrence of the same condition whenever a solution exists within the input range; obligations solvable only outside the range are reported as out-of-range.",
  original: "Covers condition sequences; a loop branch already covered by an earlier occurrence of the same condition is pruned from exploration.",
};

function updateCSCFieldVisibility() {
  const form = $("#csc-form");
  const expanded = form.elements.mode.value === "expanded";
  form.querySelector('[data-csc-field="input-domains"]').classList.toggle("hidden", !expanded);
  form.querySelectorAll("[data-input-name]").forEach((input) => {
    input.disabled = !expanded || form.dataset.readOnly === "true";
  });
  form.querySelector('[data-csc-field="workers"]').classList.toggle("hidden", form.elements.strategy.value !== "batch");
  const note = $("#csc-mode-note");
  if (note) note.textContent = CSC_MODE_NOTES[form.elements.mode.value] || "";
}

function applyCSCConfig(config = {}) {
  const form = $("#csc-form");
  form.reset();
  state.cscConfigWorkspaceId = state.selectedWorkspaceId;
  state.cscInputIntervals = structuredClone(config.input_intervals || {});
  state.cscIntervalBound = config.range_bound ?? 200;
  state.cscExplicitIntervals = config.input_intervals != null;
  for (const name of ["mode", "strategy", "workers", "max_iter", "bootstrap"]) {
    if (config[name] !== undefined && config[name] !== null && form.elements[name]) {
      form.elements[name].value = config[name];
    }
  }
  updateCSCFieldVisibility();
  renderCSCInputDomains();
}

async function ensureCSCParameters() {
  const workspaceId = state.selectedWorkspaceId;
  if (state.cscConfigWorkspaceId !== workspaceId) applyCSCConfig();
  if (!state.cscParameterCache.has(workspaceId)) {
    try {
      const data = await api(`/api/workspaces/${workspaceId}/source`);
      if (state.selectedWorkspaceId !== workspaceId) return false;
      if (!Array.isArray(data.input_parameters)) throw new Error("Input parameter detection is unavailable. Restart the backend.");
      state.cscParameterCache.set(workspaceId, data.input_parameters);
      renderCSCInputDomains();
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }
  return state.selectedWorkspaceId === workspaceId;
}

function renderCSCInputDomains() {
  const container = $("#csc-input-domains");
  const parameters = state.cscParameterCache.get(state.selectedWorkspaceId);
  const readOnly = $("#csc-form").dataset.readOnly === "true";
  container.replaceChildren();
  if (!parameters) {
    container.textContent = "Loading target function inputs…";
    return;
  }
  if (!parameters.length) {
    container.textContent = "No input parameters detected by the analysis engine.";
    return;
  }
  for (const { name, type } of parameters) {
    const defaults = defaultInterval(type, state.cscIntervalBound);
    const row = document.createElement("div");
    row.className = "input-domain-row";
    const title = document.createElement("strong");
    title.textContent = `${name} (${type})`;
    row.append(title);
    if (defaults) {
      const interval = state.cscInputIntervals[name] ||= { ...defaults };
      for (const [endpoint, labelText] of [["min", "Minimum"], ["max", "Maximum"]]) {
        const label = document.createElement("label");
        label.textContent = labelText;
        const input = document.createElement("input");
        input.type = "number";
        input.step = "1";
        input.min = type === "char" ? "0" : "-2147483648";
        input.max = type === "char" ? "65535" : "2147483647";
        input.required = true;
        input.value = interval[endpoint];
        input.dataset.inputName = name;
        input.dataset.endpoint = endpoint;
        input.setAttribute("aria-label", `${name} ${labelText.toLowerCase()}`);
        input.disabled = readOnly;
        label.append(input);
        row.append(label);
      }
    } else {
      const note = document.createElement("span");
      note.className = "input-domain-type-note";
      note.textContent = type === "boolean" || type === "bool"
        ? "Default: false or true"
        : "Existing type rules; custom intervals are supported for int and char.";
      row.append(note);
    }
    container.append(row);
  }
  updateCSCFieldVisibility();
}

function setCSCDialogMode({ readOnly, newRun = false, job = null }) {
  const form = $("#csc-form");
  form.dataset.readOnly = String(readOnly);
  form.querySelectorAll("input, select").forEach((control) => {
    control.disabled = readOnly;
  });
  $("#reset-input-domains").disabled = readOnly;
  $("#csc-readonly-note").classList.toggle("hidden", !readOnly);
  $("#csc-readonly-note span").textContent = job?.config?.restrict_input_domain
    ? "This historical run applied ranges to all inputs. Start a new run to use the corrected Boundary expansion rules."
    : "These values belong to an existing run and cannot be changed retroactively.";
  $("#confirm-csc").classList.toggle("hidden", readOnly);
  $("#csc-dialog-close").textContent = readOnly ? "Close" : "Cancel";
  $("#csc-dialog-step").textContent = readOnly ? "Saved run" : "Step 1";
  $("#csc-dialog-title").textContent = readOnly
    ? "Test Generation Configuration"
    : newRun ? "Configure a New Test Generation Run" : "Configure Test Generation";
  $("#csc-dialog-description").textContent = readOnly
    ? `${formatDate(job?.created_at)} · configuration used for this run`
    : newRun
      ? "Review or modify the parameters before creating a new run."
      : "Set the generation mode and strategy.";
  $("#confirm-csc").textContent = newRun ? "Start New Run" : "Generate Tests";
  updateCSCFieldVisibility();
}

async function openCSCSettingsDialog() {
  if (!currentWorkspace()) return;
  const job = selectedStageJob("csc");
  if (job?.config) applyCSCConfig(job.config);
  if (!await ensureCSCParameters()) return;
  setCSCDialogMode({ readOnly: Boolean(job), job });
  if (!$("#csc-dialog").open) $("#csc-dialog").showModal();
}

async function openCSCNewRunDialog() {
  const job = selectedStageJob("csc");
  if (job?.config) applyCSCConfig(job.config);
  if (!await ensureCSCParameters()) return;
  setCSCDialogMode({ readOnly: false, newRun: true });
  if (!$("#csc-dialog").open) $("#csc-dialog").showModal();
}

function toggleCCTMaximize() {
  setCCTMaximized(!$("#cct-view-panel").classList.contains("is-maximized"));
}

function setCCTMaximized(maximized) {
  const panel = $("#cct-view-panel");
  const button = $("#cct-maximize");
  panel.classList.toggle("is-maximized", maximized);
  document.body.classList.toggle("cct-maximized", maximized);
  button.setAttribute("aria-pressed", String(maximized));
  button.title = maximized ? "Exit expanded CCT workspace" : "Expand CCT workspace";
  button.querySelector(".cct-maximize-label").textContent = maximized ? "Exit" : "Expand";
  window.setTimeout(() => state.cctTree.fitView(), 80);
}

async function runFromDialog(kind, run) {
  const started = await run();
  if (started) $(`#${kind}-dialog`).close();
}

async function refreshAll() {
  await Promise.all([refreshHealth(), refreshWorkspaces()]);
  await refreshJobs();
}

async function refreshHealth() {
  try {
    const health = await api("/api/health");
    $("#health-dot").className = `status-dot ${health.status === "ok" ? "ok" : "bad"}`;
    $("#health-label").textContent = health.status === "ok" ? "Environment ready" : "Environment degraded";
    renderEnvironment(health);
  } catch (error) {
    $("#health-dot").className = "status-dot bad";
    $("#health-label").textContent = "Backend unavailable";
  }
}

async function refreshWorkspaces() {
  const token = ++state.workspacesRequestToken;
  const workspaces = await api("/api/workspaces");
  if (token !== state.workspacesRequestToken) return;
  state.workspaces = workspaces;
  if (
    state.selectedWorkspaceId
    && !state.workspaces.some((item) => item.id === state.selectedWorkspaceId)
  ) {
    state.selectedWorkspaceId = null;
  }
  renderWorkspace();
}

async function openHistoryDialog() {
  const dialog = $("#history-dialog");
  if (!dialog.open) dialog.showModal();
  const token = ++state.historyRequestToken;
  $("#history-list").innerHTML = '<div class="history-empty"><strong>Loading history…</strong></div>';
  try {
    const history = await api("/api/history");
    if (token !== state.historyRequestToken) return;
    state.history = history;
    renderHistory();
  } catch (error) {
    if (token !== state.historyRequestToken) return;
    $("#history-list").innerHTML = `<div class="history-empty is-error"><strong>History could not be loaded</strong><span>${escapeHtml(error.message)}</span></div>`;
  }
}

function renderHistory() {
  $("#history-count").textContent = `${state.history.length} saved ${state.history.length === 1 ? "run" : "runs"}`;
  $("#history-list").innerHTML = state.history.length
    ? state.history.map((record) => {
      const program = record.program;
      return `
        <article class="history-item">
          <span class="history-stage history-stage-${escapeHtml(record.stage)}">${escapeHtml(formatKind(record.stage))}</span>
          <div class="history-item-main">
            <strong>${escapeHtml(program?.class_name || "Program input unavailable")}</strong>
            <span>${formatDate(record.created_at)} · ${escapeHtml(record.status)} · <b class="history-origin ${record.archived ? "is-archived" : "is-live"}">${originLabel(record)}</b></span>
          </div>
          <div class="history-actions">
            ${record.exportable ? `<a class="icon-button history-download" href="/api/history/${record.id}/export" download title="Export portable session" aria-label="Export ${escapeHtml(formatKind(record.stage))} session">⇩</a>` : ""}
            <button class="secondary-button" type="button" data-import-history="${record.id}" ${record.restorable ? "" : "disabled"}>Restore</button>
          </div>
        </article>`;
    }).join("")
    : '<div class="history-empty"><strong>No historical runs</strong><span>Completed analyses will appear here.</span></div>';
}

async function importSessionArchive(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.elements.session_file.files?.[0];
  const validation = validateSessionArchiveFile(file);
  const status = $("#session-import-status");
  if (validation) {
    status.textContent = validation;
    form.elements.session_file.focus();
    return;
  }
  invalidateAnalysisRequests();
  const token = state.selectionRequestToken;
  const submit = form.querySelector('button[type="submit"]');
  submit.disabled = true;
  status.textContent = `Importing ${file.name}…`;
  try {
    const response = await fetch("/api/history/import", {
      method: "POST",
      body: new FormData(form),
    });
    if (!response.ok) throw new Error(await responseDetail(response));
    const imported = await response.json();
    await Promise.all([refreshWorkspaces(), refreshJobs()]);
    if (token !== state.selectionRequestToken) return;
    state.selectedWorkspaceId = imported.workspace.id;
    renderWorkspace();
    renderDependencies();
    restoreWorkspaceConfiguration(imported.workspace.id);
    form.reset();
    renderProgramExampleDescription();
    status.textContent = "";
    $("#history-dialog").close();
    await selectJob(imported.rootJobId);
    toast(`${imported.workspace.java_filename} restored as an archived result`);
  } catch (error) {
    status.textContent = error.message;
    toast(error.message, true);
  } finally {
    submit.disabled = false;
  }
}

async function importHistory(jobId) {
  invalidateAnalysisRequests();
  const token = state.selectionRequestToken;
  const button = $(`[data-import-history="${jobId}"]`);
  if (button) button.disabled = true;
  try {
    const restored = await api(`/api/history/${jobId}/restore`, { method: "POST" });
    if (token !== state.selectionRequestToken) return;
    if (!state.workspaces.some((workspace) => workspace.id === restored.program.id)) {
      await refreshWorkspaces();
    }
    if (token !== state.selectionRequestToken) return;
    state.selectedWorkspaceId = restored.program.id;
    renderWorkspace();
    renderDependencies();
    restoreWorkspaceConfiguration(restored.program.id);
    $("#history-dialog").close();
    await selectJob(restored.job.id);
    toast(`${restored.program.java_filename} restored from history`);
  } catch (error) {
    toast(error.message, true);
    if (button) button.disabled = false;
  }
}

function restoreWorkspaceConfiguration(workspaceId) {
  const latest = (kind) => state.jobs.find((job) => (
    job.workspace_id === workspaceId && job.kind === kind
  ));
  const csc = latest("csc")?.config;
  if (csc) applyCSCConfig(csc);
  const verification = latest("tbfv")?.config;
  if (verification) {
    $("#tbfv-csc-job").value = verification.csc_job_id || $("#tbfv-csc-job").value;
    $("#tbfv-render").checked = verification.render_cct !== false;
  }
  const localization = latest("localization")?.config;
  if (localization) {
    $("#localization-tbfv-job").value = localization.tbfv_job_id || $("#localization-tbfv-job").value;
    const publicMethods = new Set(["ochiai", "dstar", "op2"]);
    $("#localization-scoring").value = publicMethods.has(localization.scoring_strategy)
      ? localization.scoring_strategy
      : "ochiai";
  }
}

async function refreshJobs() {
  const requestToken = ++state.jobsRequestToken;
  const selectionToken = state.selectionRequestToken;
  const previous = state.jobs.find((job) => job.id === state.selectedJobId)?.status;
  const jobs = await api("/api/jobs");
  if (requestToken !== state.jobsRequestToken) return;
  state.jobs = jobs;
  const current = state.jobs.find((job) => job.id === state.selectedJobId)?.status;
  renderJobs();
  renderDependencies();
  if (previous !== current && state.selectedJobId && selectionToken === state.selectionRequestToken) {
    await selectJob(state.selectedJobId, false);
  }
}

async function createWorkspace(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const inputMode = $(".input-mode-tab.active").dataset.inputMode;
  if (inputMode === "paste" && !$("#java-source").value.trim()) {
    toast("Paste Java source before using this program", true);
    $("#java-source").focus();
    return;
  }
  if (inputMode === "upload" && !form.elements.java_file.files.length) {
    toast("Choose a Java file before using this program", true);
    form.elements.java_file.focus();
    return;
  }
  if (inputMode === "paste" && !prepareFSFSource()) return;
  try {
    invalidateAnalysisRequests();
    const token = state.selectionRequestToken;
    const response = await fetch("/api/workspaces", {
      method: "POST",
      body: new FormData(form),
    });
    if (!response.ok) {
      throw new Error(await responseDetail(response));
    }
    const workspace = await response.json();
    if (token !== state.selectionRequestToken) {
      await refreshWorkspaces();
      return;
    }
    state.selectedWorkspaceId = workspace.id;
    clearSelectedAnalysis();
    form.reset();
    resetFSFRows();
    setInputMode("paste");
    renderJavaSourceHints();
    form.classList.add("hidden");
    await refreshWorkspaces();
    renderDependencies();
    toast(`${workspace.java_filename} is ready for analysis`);
  } catch (error) {
    toast(error.message, true);
  }
}

function invalidateAnalysisRequests() {
  state.evidenceView?.clear();
  state.selectionRequestToken += 1;
  state.cctRequestToken += 1;
  state.metricsTokens.issue();
  state.outcomeTokens.issue();
  state.outcomeContextToken += 1;
  for (const request of state.outcomePending.values()) request.controller.abort();
  state.outcomePending.clear();
}

function clearSelectedAnalysis() {
  invalidateAnalysisRequests();
  state.selectedJobId = null;
  state.cctRequestToken += 1;
  resetMetricsSummary();
  resetOutcomeSummary();
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  state.cctTree.clear();
  setCCTMaximized(false);
  stopRunMonitor();
  $("#result-display-modes")?.classList.add("hidden");
  setResultDisplayMode("all");
  $("#cct-view-panel").classList.add("hidden");
  $("#selected-job-label").textContent = "Run an analysis or import one from saved history.";
  $("#job-status").className = "job-status empty";
  $("#job-status").textContent = "No result";
  $("#cancel-job").classList.add("hidden");
  showResultPlaceholder(
    "empty",
    "No analysis result selected",
    "Generate tests to create an interactive tree, or import a historical run.",
  );
}

function setInputMode(mode) {
  $$(".input-mode-tab").forEach((button) => {
    const active = button.dataset.inputMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $$(".input-mode-panel").forEach((panel) => {
    const active = panel.dataset.inputPanel === mode;
    panel.classList.toggle("hidden", !active);
    panel.querySelectorAll("input, textarea").forEach((field) => {
      field.disabled = !active;
    });
  });
}

function initializeProgramExamples() {
  const select = $("#program-example");
  select.replaceChildren();
  PROGRAM_EXAMPLES.forEach((example, index) => {
    const option = document.createElement("option");
    option.value = example.id;
    option.textContent = example.label;
    option.defaultSelected = index === 0;
    select.append(option);
  });
  $("#load-program-example").disabled = PROGRAM_EXAMPLES.length === 0;
  renderProgramExampleDescription();
}

function renderProgramExampleDescription() {
  const example = PROGRAM_EXAMPLES.find((item) => item.id === $("#program-example").value);
  $("#program-example-description").textContent = example?.description || "";
}

function loadProgramExample() {
  const example = PROGRAM_EXAMPLES.find((item) => item.id === $("#program-example").value);
  if (!example) return;
  const units = parseFSFUnits(example.fsfSource);
  setInputMode("paste");
  $("#java-source").value = example.javaSource;
  $("#fsf-source").value = "";
  $("#fsf-inline-file").value = "";
  const form = $("#workspace-form");
  form.elements.java_file.value = "";
  form.elements.fsf_file.value = "";
  $("#fsf-rows").replaceChildren();
  state.activeFsfTInput = null;
  units.forEach((unit) => addFSFRow(unit));
  $("#fsf-builder-summary").textContent = `${example.label.split(" — ")[0]} · ${units.length} FSF pairs loaded`;
  renderJavaSourceHints();
  $("#java-source").focus();
  toast("Example Java source and FSF loaded. Choose Use this program to begin.");
}

function renderJavaSourceHints() {
  renderDetectedClass();
  renderInputParameters();
}

function renderDetectedClass() {
  const hint = $("#detected-class");
  const source = $("#java-source").value;
  const className = detectJavaClassName(source);
  hint.className = `detected-class${source.trim() ? (className ? " is-detected" : " is-missing") : ""}`;
  hint.textContent = className
    ? `Detected: ${className}`
    : source.trim() ? "Class name not detected" : "Class name will be detected";
}

function renderInputParameters() {
  const source = $("#java-source").value;
  const parameters = extractJavaInputParameters(source);
  const container = $("#fsf-parameter-chips");
  container.replaceChildren();

  if (!parameters.length) {
    const empty = document.createElement("span");
    empty.className = "fsf-parameter-empty";
    empty.textContent = source.trim()
      ? "No analysis inputs detected"
      : "Paste Java code to detect inputs";
    container.append(empty);
    return;
  }

  parameters.forEach(({ name, type }) => {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "fsf-parameter-chip";
    chip.title = `Insert ${name} into T`;
    chip.innerHTML = `<code>${escapeHtml(name)}</code><small>${escapeHtml(type)}</small>`;
    chip.addEventListener("click", () => insertFSFVariable(name));
    container.append(chip);
  });
}

function addFSFRow(values = { t: "", d: "" }) {
  const row = document.createElement("div");
  row.className = "fsf-row";
  row.innerHTML = `
    <span class="fsf-row-index" aria-hidden="true"></span>
    <label class="fsf-row-field">
      <span>T</span>
      <input data-fsf-t type="text" spellcheck="false" placeholder="e.g. x > 0">
    </label>
    <label class="fsf-row-field">
      <span>D</span>
      <input data-fsf-d type="text" spellcheck="false" placeholder="e.g. return_value > 0">
    </label>
    <button class="fsf-remove-button" type="button" data-remove-fsf-row aria-label="Remove FSF pair">×</button>`;
  row.querySelector("[data-fsf-t]").value = values.t;
  row.querySelector("[data-fsf-d]").value = values.d;
  $("#fsf-rows").append(row);
  renumberFSFRows();
  row.querySelector("[data-fsf-t]").focus();
}

function handleFSFRowAction(event) {
  const removeButton = event.target.closest("[data-remove-fsf-row]");
  if (!removeButton) return;
  const rows = $$("#fsf-rows .fsf-row");
  if (rows.length === 1) {
    rows[0].querySelector("[data-fsf-t]").value = "";
    rows[0].querySelector("[data-fsf-d]").value = "";
    rows[0].querySelector("[data-fsf-t]").focus();
    return;
  }
  removeButton.closest(".fsf-row").remove();
  renumberFSFRows();
}

function renumberFSFRows() {
  $$("#fsf-rows .fsf-row").forEach((row, index) => {
    row.querySelector(".fsf-row-index").textContent = String(index + 1);
    row.querySelector("[data-remove-fsf-row]").ariaLabel = `Remove FSF pair ${index + 1}`;
  });
}

function resetFSFRows() {
  $("#fsf-rows").replaceChildren();
  state.activeFsfTInput = null;
  $("#fsf-builder-summary").textContent = "Each row defines one T/D pair.";
  $("#fsf-inline-file").value = "";
  addFSFRow();
}

async function importFSFFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  if (file.size > 2 * 1024 * 1024) {
    toast("FSF file exceeds the 2 MiB limit", true);
    event.target.value = "";
    return;
  }
  try {
    const units = parseFSFUnits(await file.text());
    $("#fsf-rows").replaceChildren();
    state.activeFsfTInput = null;
    units.forEach((unit) => addFSFRow(unit));
    $("#fsf-builder-summary").textContent = `${file.name} · ${units.length} ${units.length === 1 ? "pair" : "pairs"} imported`;
    toast(`${file.name} imported into the FSF editor`);
  } catch (error) {
    toast(`FSF import failed: ${error.message}`, true);
    event.target.value = "";
  }
}

function insertFSFVariable(name) {
  const input = state.activeFsfTInput?.isConnected
    ? state.activeFsfTInput
    : $("#fsf-rows [data-fsf-t]");
  if (!input) return;
  const start = input.selectionStart ?? input.value.length;
  const end = input.selectionEnd ?? start;
  input.setRangeText(name, start, end, "end");
  input.focus();
}

function prepareFSFSource() {
  const rows = $$("#fsf-rows .fsf-row");
  const units = [];
  for (let index = 0; index < rows.length; index += 1) {
    const tInput = rows[index].querySelector("[data-fsf-t]");
    const dInput = rows[index].querySelector("[data-fsf-d]");
    const t = tInput.value.trim();
    const d = dInput.value.trim();
    if (!t && !d) continue;
    if (!t || !d) {
      toast(`Complete both T and D in FSF pair ${index + 1}`, true);
      (t ? dInput : tInput).focus();
      return false;
    }
    units.push({ t, d });
  }
  $("#fsf-source").value = serializeFSFUnits(units);
  return true;
}

async function startCSC() {
  if (!requireWorkspace()) return false;
  if (!await ensureCSCParameters()) return false;
  setCSCDialogMode({ readOnly: false });
  if (!$("#csc-form").reportValidity()) return false;
  const data = Object.fromEntries(new FormData($("#csc-form")).entries());
  const payload = {
    workspace_id: state.selectedWorkspaceId,
    mode: data.mode,
    max_iter: Number(data.max_iter),
    strategy: data.strategy,
    workers: Number(data.workers),
    bootstrap: data.bootstrap || null,
    render_cct: true,
  };
  if (data.mode === "expanded") {
    try {
      Object.assign(payload, intervalConfig(
        state.cscParameterCache.get(state.selectedWorkspaceId), state.cscInputIntervals,
        { bound: state.cscIntervalBound, explicit: state.cscExplicitIntervals },
      ));
    } catch (error) {
      toast(error.message, true);
      return false;
    }
  }
  return startJob("/api/jobs/csc", payload);
}

async function startTBFV() {
  if (!requireWorkspace()) return false;
  const cscJobId = $("#tbfv-csc-job").value;
  if (!cscJobId) {
    toast("Select a completed CSC job", true);
    return false;
  }
  return startJob("/api/jobs/tbfv", {
    workspace_id: state.selectedWorkspaceId,
    csc_job_id: cscJobId,
    render_cct: $("#tbfv-render").checked,
  });
}

async function startLocalization() {
  if (!requireWorkspace()) return false;
  const tbfvJobId = $("#localization-tbfv-job").value;
  if (!tbfvJobId) {
    toast("Select a completed TBFV job", true);
    return false;
  }
  const localizationGate = downstreamGate(tbfvJobId, "localization");
  const hasNoFailures = localizationGate.reasonCodes?.some((item) => item.code === "no_failed_verification")
    || localizationGate.detail?.includes("no_failed_verification")
    || localizationGate.detail?.includes("No verification check failed");
  if (hasNoFailures) {
    toast("Verification completed with no failed checks. Fault localization is not applicable.");
    return false;
  }
  return startJob("/api/jobs/localization", {
    workspace_id: state.selectedWorkspaceId,
    tbfv_job_id: tbfvJobId,
    scoring_strategy: $("#localization-scoring").value,
  });
}

async function startJob(path, payload) {
  const token = state.selectionRequestToken;
  const workspaceId = state.selectedWorkspaceId;
  try {
    const job = await api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshJobs();
    if (token === state.selectionRequestToken && workspaceId === state.selectedWorkspaceId) await selectJob(job.id);
    toast(`${formatKind(job.kind)} task started`);
    return true;
  } catch (error) {
    const isNotApplicable = isNoFailureLocalizationRejection(error.message);
    toast(describeStartFailure(error.message), !isNotApplicable);
    return false;
  }
}

async function cancelSelectedJob() {
  if (!state.selectedJobId) return;
  const jobId = state.selectedJobId;
  const token = state.selectionRequestToken;
  try {
    await api(`/api/jobs/${jobId}/cancel`, { method: "POST" });
    if (token === state.selectionRequestToken && state.selectedJobId === jobId) await selectJob(jobId);
    toast("Task cancelled");
  } catch (error) {
    toast(error.message, true);
  }
}

async function selectJob(jobId, openPipeline = true) {
  invalidateAnalysisRequests();
  const selectionToken = state.selectionRequestToken;
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  const job = await api(`/api/jobs/${jobId}`);
  if (selectionToken !== state.selectionRequestToken) return;
  state.selectedJobId = jobId;
  if (state.workspaces.some((workspace) => workspace.id === job.workspace_id)) {
    state.selectedWorkspaceId = job.workspace_id;
    renderWorkspace();
    renderDependencies();
  }
  if (openPipeline) {
    showView("pipeline");
    $$(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.view === "pipeline");
    });
  }
  renderSelectedJob(job);
  await Promise.all([renderCCT(job), loadAnalysisMetrics(job), loadAnalysisOutcome(job), state.evidenceView?.load(job)]);
  if (selectionToken !== state.selectionRequestToken || state.selectedJobId !== job.id) return;
  connectJobEvents(job);
}

function selectLatestResult(kind) {
  const job = state.jobs.find((candidate) => (
    candidate.workspace_id === state.selectedWorkspaceId
    && candidate.kind === kind
    && candidate.status === "succeeded"
  ));
  if (job) selectJob(job.id, false);
}

function connectJobEvents(job) {
  if (state.eventSource) {
    state.eventSource.close();
    state.eventSource = null;
  }
  if (!["pending", "running"].includes(job.status)) return;
  const source = new EventSource(`/api/jobs/${job.id}/events`);
  state.eventSource = source;
  const selectionToken = state.selectionRequestToken;
  source.onmessage = async (event) => {
    if (state.eventSource !== source || selectionToken !== state.selectionRequestToken || state.selectedJobId !== job.id) return;
    const payload = JSON.parse(event.data);
    const liveJob = { ...job, ...payload };
    renderSelectedJob(liveJob);
    if (payload.log?.trim()) noteRunActivity();
    if (["succeeded", "failed", "cancelled"].includes(payload.status)) {
      source.close();
      await refreshJobs();
      if (state.eventSource !== source || selectionToken !== state.selectionRequestToken || state.selectedJobId !== job.id) return;
      await Promise.all([
        renderCCT(liveJob),
        loadAnalysisMetrics(liveJob),
        loadAnalysisOutcome(liveJob),
        state.evidenceView?.load(liveJob),
      ]);
    }
  };
}

function renderWorkspace() {
  const workspace = currentWorkspace();
  const summaryEl = $("#workspace-summary");
  if (summaryEl) {
    summaryEl.textContent = workspace
      ? `${workspace.java_filename}${workspace.fsf_filename ? ` · ${workspace.fsf_filename}` : " · no FSF"}${isArchived(workspace) ? " · archived input" : ""}`
      : "No program added";
  }
  const runCsc = $("#run-csc");
  if (runCsc) runCsc.disabled = !workspace;
  const viewBtn = $("#view-program-source");
  if (viewBtn) {
    viewBtn.classList.toggle("hidden", !workspace);
  }
  const cctViewBtn = $("#cct-view-source");
  if (cctViewBtn) {
    cctViewBtn.disabled = !workspace;
  }
  if (workspace) {
    loadWorkspaceSource(workspace.id);
  } else {
    state.workspaceSourceRequestToken++;
    state.workspaceSource = null;
    state.cctTree?.setWorkspaceSource(null);
  }
}

function renderJobs() {
  const table = $("#jobs-table");
  if (!table) return;
  const workspaceNames = new Map(state.workspaces.map((item) => [item.id, item.name]));
  table.innerHTML = state.jobs.length
    ? state.jobs.map((job) => `
      <tr data-job-id="${job.id}">
        <td>${escapeHtml(formatKind(job.kind))}</td>
        <td>${escapeHtml(workspaceNames.get(job.workspace_id) || job.workspace_id.slice(0, 8))}</td>
        <td><span class="status-pill ${job.status}">${escapeHtml(job.status)}</span></td>
        <td>${formatDate(job.created_at)}</td>
        <td>${duration(job)}</td>
      </tr>
    `).join("")
    : '<tr><td colspan="5" class="muted">No sessions</td></tr>';
  $$("#jobs-table tr[data-job-id]").forEach((row) => {
    row.addEventListener("click", () => selectJob(row.dataset.jobId));
  });
}

function renderDependencies() {
  const jobs = state.jobs.filter(
    (job) => job.workspace_id === state.selectedWorkspaceId && job.status === "succeeded"
  );
  const cscJobs = jobs.filter((job) => job.kind === "csc");
  const verificationJobs = jobs.filter((job) => job.kind === "tbfv");
  const localizationJobs = jobs.filter((job) => job.kind === "localization");
  const cscSources = liveSucceededJobs(state.jobs, state.selectedWorkspaceId, "csc");
  const verificationSources = liveSucceededJobs(state.jobs, state.selectedWorkspaceId, "tbfv");
  $("#run-csc").disabled = !currentWorkspace();
  setJobOptions($("#tbfv-csc-job"), cscSources, "Generated tests");
  setJobOptions(
    $("#localization-tbfv-job"),
    verificationSources,
    "Verification"
  );
  const generation = state.jobs.find((job) => job.id === $("#tbfv-csc-job").value);
  const verification = state.jobs.find((job) => job.id === $("#localization-tbfv-job").value);
  const upstream = state.jobs.find((job) => job.id === verification?.config?.csc_job_id);
  if ($("#tbfv-input-domain")) $("#tbfv-input-domain").textContent = generation
    ? `${domainSummary(generation.config)} Inherited from the selected generation run.` : "";
  if ($("#localization-input-domain")) $("#localization-input-domain").textContent = upstream
    ? `${domainSummary(upstream.config)} Uses all generated tests and their verification evidence.` : "";
  $("#tbfv-source").textContent = cscSources.length
    ? "Check the generated tests against the FSF."
    : cscJobs.length ? "Rerun Test Generation to create live inputs for verification." : "Complete Test Generation to unlock this step.";
  $("#localization-source").textContent = verificationSources.length
    ? "Rank suspicious source lines using verification evidence."
    : verificationJobs.length ? "Rerun Verification from live inputs before localizing again." : "Complete Verification to unlock this step.";

  const cscGate = downstreamGate($("#tbfv-csc-job").value, "tbfv");
  const localizationGate = downstreamGate($("#localization-tbfv-job").value, "localization");
  const hasNoFailures = localizationGate.reasonCodes?.some((item) => item.code === "no_failed_verification")
    || localizationGate.detail?.includes("no_failed_verification")
    || localizationGate.detail?.includes("No verification check failed");

  $("#localization-source").textContent = verificationSources.length
    ? (hasNoFailures
        ? "Verification completed with no failed checks. Fault localization is not applicable."
        : "Rank suspicious source lines using verification evidence.")
    : verificationJobs.length ? "Rerun Verification from live inputs before localizing again." : "Complete Verification to unlock this step.";

  const cscGateText = cscGate.detail
    || ($("#tbfv-csc-job").value ? "Reading the upstream conclusion…" : "Waiting for generated tests");
  const localizationGateText = hasNoFailures
    ? "Not applicable — no failed verification evidence"
    : (localizationGate.detail
        || ($("#localization-tbfv-job").value ? "Reading the upstream conclusion…" : "Waiting for verification"));

  setWorkflowStep("csc", Boolean(currentWorkspace()), cscJobs.length > 0, "Add a program to begin", "Ready to generate tests", conclusionStepText(cscJobs[0], "Tests generated"));
  setWorkflowStep("tbfv", cscGate.available, verificationJobs.length > 0, cscGateText, cscGateText, conclusionStepText(verificationJobs[0], "Verification"));
  setWorkflowStep("localization", localizationGate.available || hasNoFailures, localizationJobs.length > 0, localizationGateText, localizationGateText, conclusionStepText(localizationJobs[0], "Localization"));

  state.jobs
    .filter((job) => (
      job.workspace_id === state.selectedWorkspaceId
      && ["pending", "running"].includes(job.status)
    ))
    .forEach((job) => {
      const step = $(`[data-workflow-kind="${job.kind}"]`);
      step.classList.add("is-running");
      $(`#${job.kind}-step-state`).textContent = job.status === "pending" ? "Queued" : "In progress…";
      step.querySelector(".primary-button").disabled = true;
    });

  const availableByKind = { csc: cscJobs[0], tbfv: verificationJobs[0], localization: localizationJobs[0] };
  const selectedKind = state.jobs.find((job) => job.id === state.selectedJobId)?.kind;
  $$(".result-view-tab").forEach((button) => {
    button.classList.toggle("hidden", !availableByKind[button.dataset.resultKind]);
    const active = button.dataset.resultKind === selectedKind;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  $(".result-view-switcher").classList.toggle("hidden", !Object.values(availableByKind).some(Boolean));

  ensureOutcomeCached($("#tbfv-csc-job").value);
  ensureOutcomeCached($("#localization-tbfv-job").value);
}

function setWorkflowStep(kind, unlocked, completed, lockedText, readyText, completedText) {
  const step = $(`[data-workflow-kind="${kind}"]`);
  step.classList.remove("hidden");
  step.classList.remove("is-running");
  step.classList.toggle("is-locked", !unlocked);
  step.classList.toggle("is-ready", unlocked && !completed);
  step.classList.toggle("is-completed", completed);
  $(`#${kind}-step-state`).textContent = completed
    ? completedText
    : unlocked ? readyText : lockedText;
  step.querySelectorAll("select, input").forEach((control) => {
    control.disabled = !unlocked;
  });
  const configurationButton = step.querySelector(".configuration-button");
  if (kind === "csc") {
    configurationButton.classList.remove("hidden");
    configurationButton.disabled = !currentWorkspace();
  } else {
    configurationButton.classList.toggle("hidden", !completed);
    configurationButton.disabled = !completed;
  }
  const actionButton = step.querySelector(".primary-button");
  actionButton.disabled = !unlocked;
  actionButton.textContent = completed
    ? { csc: "Generate Again", tbfv: "Verify Again", localization: "Locate Again" }[kind]
    : { csc: "Generate Tests", tbfv: "Run Verification", localization: "Locate Faults" }[kind];
}

function setJobOptions(select, jobs, label) {
  const previous = select.value;
  select.innerHTML = jobs.length
    ? jobs.map((job) => (
      `<option value="${job.id}">${label} · ${formatDate(job.finished_at || job.created_at)}</option>`
    )).join("")
    : `<option value="">No completed ${label} tasks</option>`;
  if (jobs.some((job) => job.id === previous)) select.value = previous;
}

function renderSelectedJob(job) {
  const completed = ["succeeded", "failed", "cancelled"].includes(job.status);
  $("#selected-job-label").textContent = [
    formatKind(job.kind),
    isArchived(job) ? "Archived result" : "Live run",
    formatDate(job.created_at),
    completed && job.started_at ? duration(job) : null,
  ].filter(Boolean).join(" · ");
  const status = $("#job-status");
  status.className = `job-status ${isArchived(job) ? "archived" : job.status}`;
  status.textContent = isArchived(job) ? "Archived result" : ({
    pending: "Queued",
    running: "Running",
    succeeded: "Completed",
    failed: "Failed",
    cancelled: "Cancelled",
  }[job.status] || job.status);
  $("#cancel-job").classList.toggle(
    "hidden",
    !["pending", "running"].includes(job.status)
  );
  $$(".result-view-tab").forEach((button) => {
    const active = button.dataset.resultKind === job.kind;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (job.status === "succeeded") {
    $("#result-display-modes")?.classList.remove("hidden");
    const evidenceTab = $("#view-mode-evidence");
    if (evidenceTab) {
      evidenceTab.classList.toggle("hidden", job.kind !== "localization");
      if (job.kind !== "localization" && state.resultDisplayMode === "evidence") {
        setResultDisplayMode("all");
      }
    }
    applyResultDisplayMode();
    stopRunMonitor();
    showResultPlaceholder("loading", "Preparing the interactive tree", "Loading the CCT result…");
  } else if (job.status === "failed") {
    $("#result-display-modes")?.classList.add("hidden");
    stopRunMonitor();
    showResultPlaceholder(
      "failed",
      "Analysis could not be completed",
      job.error || "The analysis ended before a result was produced.",
    );
  } else if (job.status === "cancelled") {
    $("#result-display-modes")?.classList.add("hidden");
    stopRunMonitor();
    showResultPlaceholder("cancelled", "Analysis cancelled", "No result was produced for this run.");
  } else if (job.status === "pending") {
    $("#result-display-modes")?.classList.add("hidden");
    showResultPlaceholder("running", "Analysis queued", "The task will begin when a worker is available.");
    startRunMonitor(job);
  } else {
    $("#result-display-modes")?.classList.add("hidden");
    showResultPlaceholder("running", "Building the analysis result", "The interactive CCT will appear when the run completes.");
    startRunMonitor(job);
  }
}

function startRunMonitor(job) {
  if (state.monitoredJobId !== job.id) {
    state.monitoredJobId = job.id;
    state.runActivityCount = 0;
    state.monitorStartedAt = Date.parse(job.started_at || job.created_at) || Date.now();
    $("#run-activity").textContent = job.status === "pending"
      ? "Waiting for an analysis worker"
      : "Analysis worker active";
  } else if (job.started_at) {
    state.monitorStartedAt = Date.parse(job.started_at) || state.monitorStartedAt;
  }
  $("#run-monitor").classList.remove("hidden");
  updateRunElapsed();
  if (!state.runTimer) state.runTimer = window.setInterval(updateRunElapsed, 1000);
}

function noteRunActivity() {
  state.runActivityCount += 1;
  $("#run-activity").textContent = `${state.runActivityCount} runtime ${state.runActivityCount === 1 ? "update" : "updates"} received`;
}

function updateRunElapsed() {
  const elapsed = Math.max(0, Math.floor((Date.now() - (state.monitorStartedAt || Date.now())) / 1000));
  const minutes = Math.floor(elapsed / 60);
  const seconds = elapsed % 60;
  $("#run-elapsed").textContent = `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function stopRunMonitor() {
  if (state.runTimer) window.clearInterval(state.runTimer);
  state.runTimer = null;
  state.monitoredJobId = null;
  state.monitorStartedAt = null;
  state.runActivityCount = 0;
  $("#run-monitor")?.classList.add("hidden");
}

async function renderCCT(job) {
  const panel = $("#cct-view-panel");
  const requestToken = ++state.cctRequestToken;
  if (job.status !== "succeeded") {
    panel.classList.add("hidden");
    state.cctTree.clear();
    return;
  }

  try {
    const response = await fetch(`/api/jobs/${job.id}/cct-snapshot`);
    if (requestToken !== state.cctRequestToken || state.selectedJobId !== job.id) return;
    if (response.status === 404) {
      panel.classList.add("hidden");
      state.cctTree.clear();
      showResultPlaceholder(
        "empty",
        "No interactive CCT for this result",
        "This analysis completed without producing a CCT snapshot.",
      );
      return;
    }
    if (!response.ok) throw new Error(await responseDetail(response));
    const snapshot = await response.json();
    if (requestToken !== state.cctRequestToken || state.selectedJobId !== job.id) return;
    $("#result-placeholder").classList.add("hidden");
    panel.classList.remove("hidden");
    state.cctTree.setPresentationMode(job.kind === "localization" ? "localization" : "verification");
    state.cctTree.setSnapshot(snapshot);
    state.cctTree.setLocalizationOverlay(state.localizationOverlay);
    const evidence = state.evidenceView?.selectedDetail;
    if (evidence?.jobId === job.id) state.evidenceView.onPath(evidence);
  } catch (error) {
    if (requestToken !== state.cctRequestToken || state.selectedJobId !== job.id) return;
    panel.classList.add("hidden");
    state.cctTree.clear();
    showResultPlaceholder(
      "failed",
      "The CCT could not be displayed",
      error.message,
    );
  }
}

function resetMetricsSummary() {
  state.metricsTokens.issue();
  $("#metrics-summary").classList.add("hidden");
  $("#metrics-cards").replaceChildren();
}

async function loadAnalysisMetrics(job) {
  const token = state.metricsTokens.issue();
  $("#metrics-summary").classList.remove("hidden");
  if (job.status !== "succeeded") {
    if (job.status === "failed" || job.status === "cancelled") {
      renderMetricsMessage(
        "empty",
        "No metrics summary",
        "This run ended before metrics could be recorded.",
      );
    } else {
      renderMetricsMessage(
        "pending",
        "Metrics pending",
        "Metrics appear as soon as this run completes.",
      );
    }
    return;
  }

  renderMetricsMessage("loading", "Loading metrics…", "Reading the recorded analysis metrics.");
  try {
    const response = await fetch(`/api/jobs/${job.id}/metrics`);
    if (!isCurrentMetricsRequest(token, job)) return;
    if (response.status === 404) {
      renderMetricsMessage(
        "empty",
        "No metrics summary",
        "No metrics were recorded for this result.",
      );
      return;
    }
    if (!response.ok) throw new Error(await responseDetail(response));
    const document = await response.json();
    if (!isCurrentMetricsRequest(token, job)) return;
    renderMetrics(document);
  } catch (error) {
    if (!isCurrentMetricsRequest(token, job)) return;
    renderMetricsMessage("failed", "Metrics could not be loaded", error.message);
  }
}

function isCurrentMetricsRequest(token, job) {
  return state.metricsTokens.isCurrent(token) && state.selectedJobId === job.id;
}

function renderMetrics(document) {
  const container = $("#metrics-cards");
  container.replaceChildren();
  buildMetricCards(document).forEach((card) => container.append(buildMetricCard(card)));
}

function buildMetricCard(card) {
  const article = document.createElement("article");
  article.className = `metric-card tone-${card.tone}`;
  article.dataset.metricId = card.id;

  const header = document.createElement("header");
  header.className = "metric-card-header";

  const titleGroup = document.createElement("div");
  titleGroup.className = "metric-card-title-group";

  const title = document.createElement("h4");
  title.textContent = card.title;
  titleGroup.append(title);

  if (card.help) {
    const helpWrap = document.createElement("div");
    helpWrap.className = "metric-help-wrap";

    const helpBtn = document.createElement("button");
    helpBtn.type = "button";
    helpBtn.className = "metric-help-button";
    helpBtn.textContent = "?";
    helpBtn.setAttribute("aria-label", `Help for ${card.title}`);

    const popover = document.createElement("div");
    popover.className = "metric-help-popover";
    popover.setAttribute("role", "tooltip");

    const popTitle = document.createElement("strong");
    popTitle.className = "metric-help-title";
    popTitle.textContent = card.title;

    const popCalc = document.createElement("div");
    popCalc.className = "metric-help-calc";
    const calcLabel = document.createElement("span");
    calcLabel.className = "metric-help-section-label";
    calcLabel.textContent = "Calculation:";
    const calcCode = document.createElement("code");
    calcCode.textContent = card.help.calculation;
    popCalc.append(calcLabel, calcCode);

    const popDesc = document.createElement("p");
    popDesc.className = "metric-help-desc";
    popDesc.textContent = card.help.meaning;

    popover.append(popTitle, popCalc, popDesc);
    helpWrap.append(helpBtn, popover);

    helpBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      helpWrap.classList.toggle("is-active");
    });
    helpWrap.addEventListener("mouseleave", () => {
      helpWrap.classList.remove("is-active");
    });

    titleGroup.append(helpWrap);
  }

  const status = document.createElement("span");
  status.className = `metric-status tone-${card.tone}`;
  status.textContent = card.statusLabel;
  header.append(titleGroup, status);
  article.append(header);

  if (card.primary) {
    const primary = document.createElement("p");
    primary.className = "metric-primary";
    const value = document.createElement("span");
    value.className = "metric-value";
    value.textContent = card.primary.text;
    primary.append(value);
    if (card.primary.label) {
      const label = document.createElement("span");
      label.className = "metric-primary-label";
      label.textContent = card.primary.label;
      primary.append(label);
    }
    article.append(primary);
  }
  if (card.caption) {
    article.append(metricLine("metric-caption", card.caption));
  }
  if (card.details.length) {
    const list = document.createElement("dl");
    list.className = "metric-details";
    card.details.forEach((detail) => {
      const row = document.createElement("div");
      const term = document.createElement("dt");
      term.textContent = detail.label;
      const value = document.createElement("dd");
      value.textContent = detail.text;
      row.append(term, value);
      list.append(row);
    });
    article.append(list);
  }
  if (card.reason) {
    article.append(metricLine("metric-reason", card.reason.message || card.reason.code));
  }
  if (card.note) {
    article.append(metricLine("metric-note", card.note));
  }
  if (card.degradations.length) {
    article.append(metricLine(
      "metric-note is-degraded",
      card.degradations.map(describeDegradation).join(" · "),
    ));
  }
  if (card.source) {
    const source = card.source.sourceJobId.slice(0, 8);
    article.append(metricLine(
      "metric-source",
      [`Source: ${formatKind(card.source.stage)}`, source].filter(Boolean).join(" · "),
    ));
  }
  return article;
}

function metricLine(className, text) {
  const line = document.createElement("p");
  line.className = className;
  line.textContent = text;
  return line;
}

function describeDegradation(kind) {
  return {
    unregistered_metric: "Unregistered metric ID",
    unsupported_data_type: "Unsupported data type",
    renderer_error: "Metric could not be rendered",
  }[kind] || kind;
}

function renderMetricsMessage(tone, title, message) {
  const empty = document.createElement("div");
  empty.className = `metrics-empty is-${tone}`;
  const heading = document.createElement("strong");
  heading.textContent = title;
  const detail = document.createElement("span");
  detail.textContent = message;
  empty.append(heading, detail);
  $("#metrics-cards").replaceChildren(empty);
}

function resetOutcomeSummary() {
  state.outcomeTokens.issue();
  $("#outcome-summary").classList.add("hidden");
  $("#outcome-body").replaceChildren();
  setOutcomeBadge(null);
}

async function loadAnalysisOutcome(job) {
  const token = state.outcomeTokens.issue();
  $("#outcome-summary").classList.remove("hidden");

  if (job.status !== "succeeded") {
    renderOutcomeEntry({ state: "local", view: buildOutcomeErrorView({ job }) });
    return;
  }

  const cached = state.outcomeCache.get(job.id);
  if (cached) {
    renderOutcomeEntry(cached);
    return;
  }

  renderOutcomeNotice("loading", "Loading the conclusion…", "Reading the recorded analysis outcome.");
  const entry = await ensureOutcomeCached(job.id);
  if (!isCurrentOutcomeRequest(token, job)) return;
  renderOutcomeEntry(entry);
}

function isCurrentOutcomeRequest(token, job) {
  return state.outcomeTokens.isCurrent(token) && state.selectedJobId === job.id;
}

function cachedOutcomeEntry(jobId) {
  return jobId ? state.outcomeCache.get(jobId) || null : null;
}

function cachedNextStage(jobId, stage) {
  const entry = cachedOutcomeEntry(jobId);
  if (!entry || entry.state !== "ready") return null;
  return entry.view.nextStages.find((item) => item.stage === stage) || null;
}

function downstreamGate(jobId, stage) {
  const gate = cachedNextStage(jobId, stage);
  if (gate) return gate;
  if (!jobId) return { available: false, detail: null };
  const entry = cachedOutcomeEntry(jobId);
  return {
    available: false,
    detail: entry ? "Blocked: the upstream conclusion could not be read." : null,
  };
}

function conclusionStepText(job, base) {
  const entry = cachedOutcomeEntry(job?.id);
  const suffix = entry?.view?.badge?.label?.replace(/^Conclusion: /, "");
  return suffix ? `${base} · ${suffix}` : base;
}

function ensureOutcomeCached(jobId) {
  if (!jobId) return Promise.resolve(null);
  const cached = state.outcomeCache.get(jobId);
  if (cached) return Promise.resolve(cached);
  const pending = state.outcomePending.get(jobId);
  if (pending) return pending.promise;
  const contextToken = state.outcomeContextToken;
  const controller = new AbortController();
  const request = { controller, promise: null };
  request.promise = fetchOutcomeEntry(jobId, controller.signal).then((entry) => {
    // A cancelled request can still finish parsing, or a replacement request
    // for the same job can already exist. Neither may publish the old result.
    if (contextToken !== state.outcomeContextToken || controller.signal.aborted) return null;
    state.outcomeCache.set(jobId, entry);
    renderDependencies();
    return entry;
  }).finally(() => {
    if (state.outcomePending.get(jobId) === request) state.outcomePending.delete(jobId);
  });
  state.outcomePending.set(jobId, request);
  return request.promise;
}

async function fetchOutcomeEntry(jobId, signal) {
  const job = state.jobs.find((item) => item.id === jobId) || null;
  try {
    const response = await fetch(`/api/jobs/${jobId}/outcome`, { signal });
    if (response.ok) {
      const payload = await response.json();
      const view = buildOutcomeView({ outcome: payload.outcome, nextStages: payload.nextStages });
      return view
        ? { state: "ready", view: { ...view, provenance: provenanceText(payload.upstream || []) } }
        : { state: "failed", message: "The recorded conclusion is not a recognizable outcome document." };
    }
    const detail = await responseDetail(response);
    return { state: "error", view: buildOutcomeErrorView({ job, httpStatus: response.status, detail }) };
  } catch (error) {
    if (signal?.aborted) return null;
    return { state: "failed", message: error.message };
  }
}

function renderOutcomeEntry(entry) {
  if (!entry) return;
  if (entry.state === "ready") {
    renderOutcome(entry.view);
    return;
  }
  if (entry.view) {
    setOutcomeBadge(entry.view.badge);
    renderOutcomeNotice(entry.view.tone, entry.view.title, entry.view.message);
    return;
  }
  setOutcomeBadge(null);
  renderOutcomeNotice("failed", "The conclusion could not be loaded", entry.message || "The request failed.");
}

function setOutcomeBadge(badge) {
  const element = $("#outcome-badge");
  if (!badge) {
    element.className = "outcome-badge tone-muted hidden";
    element.textContent = "";
    return;
  }
  element.className = `outcome-badge tone-${badge.tone}`;
  element.textContent = badge.label;
}

function renderOutcome(view) {
  setOutcomeBadge(view.badge);
  const card = document.createElement("article");
  card.className = `outcome-card tone-${view.badge.tone}`;

  const header = document.createElement("header");
  header.className = "outcome-card-header";
  const titleGroup = document.createElement("div");
  titleGroup.className = "outcome-title-group";
  const title = document.createElement("h4");
  title.textContent = `${view.stageLabel} conclusion`;
  const meta = document.createElement("span");
  meta.className = "outcome-meta";
  meta.textContent = [
    view.jobId.slice(0, 8),
    view.generatedAt ? formatDate(view.generatedAt) : "",
  ].filter(Boolean).join(" · ");
  titleGroup.append(title, meta);
  const status = document.createElement("span");
  status.className = `outcome-status tone-${view.badge.tone}`;
  status.textContent = view.status.label;
  header.append(titleGroup, status);
  card.append(header);

  const headline = document.createElement("p");
  headline.className = "outcome-headline";
  headline.textContent = view.headline;
  card.append(headline);
  for (const text of view.provenance || []) {
    card.append(metricLine("outcome-provenance", text));
  }

  const facts = document.createElement("dl");
  facts.className = "outcome-facts";
  appendOutcomeFact(facts, "Completeness", view.completeness.text);
  view.summary.forEach((fact) => appendOutcomeFact(facts, fact.label, fact.text));
  if (facts.childElementCount) card.append(facts);

  if (view.reasons.length) {
    const reasons = document.createElement("ul");
    reasons.className = "outcome-reasons";
    view.reasons.forEach((reason) => {
      const item = document.createElement("li");
      item.textContent = reason.text;
      reasons.append(item);
    });
    card.append(reasons);
  }

  if (view.nextStages.length) {
    const next = document.createElement("div");
    next.className = "outcome-next-stages";
    view.nextStages.forEach((stage) => next.append(buildNextStage(stage)));
    card.append(next);
  }

  if (view.sources.length) {
    const sources = document.createElement("p");
    sources.className = "outcome-sources";
    sources.textContent = `Evidence: ${view.sources.map(describeOutcomeSource).join(" · ")}`;
    card.append(sources);
  }

  $("#outcome-body").replaceChildren(card);
}

function appendOutcomeFact(list, label, text) {
  const row = document.createElement("div");
  const term = document.createElement("dt");
  term.textContent = label;
  const value = document.createElement("dd");
  value.textContent = text;
  row.append(term, value);
  list.append(row);
}

function buildNextStage(stage) {
  const box = document.createElement("div");
  box.className = "outcome-next-stage";
  const head = document.createElement("div");
  head.className = "outcome-next-head";
  const label = document.createElement("span");
  label.className = "outcome-next-label";
  label.textContent = `Next: ${stage.label}`;
  const state = document.createElement("span");
  state.className = `outcome-next-status ${stage.available ? "is-available" : "is-blocked"}`;
  state.textContent = stage.statusLabel;
  head.append(label, state);
  const detail = document.createElement("p");
  detail.className = "outcome-next-detail";
  detail.textContent = stage.detail;
  box.append(head, detail);
  return box;
}

function describeOutcomeSource(source) {
  return [source.stageLabel, source.jobId.slice(0, 8), source.artifacts.join(", ")]
    .filter(Boolean)
    .join(" · ");
}

function renderOutcomeNotice(tone, title, message) {
  const notice = document.createElement("div");
  notice.className = `outcome-notice is-${tone}`;
  const heading = document.createElement("strong");
  heading.textContent = title;
  const detail = document.createElement("span");
  detail.textContent = message;
  notice.append(heading, detail);
  $("#outcome-body").replaceChildren(notice);
}

function showResultPlaceholder(tone, title, message) {
  const placeholder = $("#result-placeholder");
  placeholder.className = `result-placeholder is-${tone}`;
  placeholder.querySelector(".result-placeholder-mark").textContent = {
    loading: "···",
    running: "↻",
    failed: "!",
    cancelled: "×",
    empty: "CCT",
  }[tone] || "CCT";
  placeholder.querySelector("h3").textContent = title;
  placeholder.querySelector("p").textContent = message;
}

function renderEnvironment(health) {
  const entries = [
    ["Status", health.status],
    ["CSC engine", health.core_root],
    ["Runtime storage", health.runtime_root],
    ["Java", health.tools.java || "Not found"],
    ["Java compiler", health.tools.javac || "Not found"],
    ["Graphviz", health.tools.dot || "Not found"],
    ["Java bridge", health.bridge_built ? "Built" : "Not built"],
  ];
  $("#environment-list").innerHTML = entries
    .map(([key, value]) => `<dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value))}</dd>`)
    .join("");
}

function showView(view) {
  if (view === "sessions") {
    openHistoryDialog();
    return;
  }
  if (view === "environment") {
    $("#environment-dialog")?.showModal();
    return;
  }
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `${view}-view`));
  const titles = {
    pipeline: ["Analysis", "Generate tests, verify behavior, and locate faults"],
    sessions: ["Sessions", "Completed and in-progress analysis runs"],
    environment: ["Environment", "Core tools, dependencies, and storage"],
  };
  const titleEl = $("#view-title");
  if (titleEl) titleEl.textContent = titles[view]?.[0] || "";
  const contextEl = $("#view-context");
  if (contextEl) contextEl.textContent = titles[view]?.[1] || "";
}

function currentWorkspace() {
  return state.workspaces.find((item) => item.id === state.selectedWorkspaceId) || null;
}

function requireWorkspace() {
  if (currentWorkspace()) return true;
  toast("Add a program before starting the analysis", true);
  return false;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(await responseDetail(response));
  return response.json();
}

async function responseDetail(response) {
  try {
    const body = await response.json();
    return body.detail || `Request failed (${response.status})`;
  } catch {
    return `Request failed (${response.status})`;
  }
}

function toast(message, error = false) {
  const element = $("#toast");
  element.textContent = message;
  element.className = `toast show${error ? " error" : ""}`;
  window.setTimeout(() => element.classList.remove("show"), 2800);
}

function formatKind(kind) {
  return { csc: "Test Generation", tbfv: "Verification", localization: "Fault Localization" }[kind] || kind;
}

function formatDate(value) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("en-GB", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(value));
}

function duration(job) {
  if (!job.started_at) return "—";
  const end = job.finished_at ? new Date(job.finished_at) : new Date();
  const seconds = Math.max(0, Math.round((end - new Date(job.started_at)) / 1000));
  return `${seconds}s`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

async function loadWorkspaceSource(workspaceId) {
  const token = ++state.workspaceSourceRequestToken;
  try {
    const data = await api(`/api/workspaces/${workspaceId}/source`);
    if (token !== state.workspaceSourceRequestToken || state.selectedWorkspaceId !== workspaceId) return;
    state.workspaceSource = data;
    state.cctTree?.setWorkspaceSource(data);
    const dialog = $("#source-dialog");
    if (dialog && dialog.open) {
      renderSourceDialogContent();
    }
  } catch (err) {
    if (token !== state.workspaceSourceRequestToken) return;
    state.workspaceSource = null;
    state.cctTree?.setWorkspaceSource(null);
  }
}

function openSourceFromCCT() {
  let targetLine = null;
  if (state.cctTree?.model && state.cctTree?.selectedNodeId) {
    const node = state.cctTree.model.nodeById.get(state.cctTree.selectedNodeId);
    if (node?.kind === "condition" && node.condition?.line) {
      targetLine = Number(node.condition.line);
    }
  }
  openSourceDialog("java", targetLine);
}

function openSourceDialog(tab = "java", highlightLine = null) {
  const dialog = $("#source-dialog");
  if (!dialog) return;
  state.activeSourceTab = tab;
  renderSourceDialogContent(highlightLine);
  if (!dialog.open) {
    dialog.showModal();
  }
  if (highlightLine) {
    window.setTimeout(() => {
      const lineEl = $(`#source-code-viewer .source-line[data-line="${highlightLine}"]`);
      if (lineEl) {
        lineEl.scrollIntoView({ block: "center", behavior: "smooth" });
      }
    }, 60);
  }
}

function switchSourceTab(tab) {
  state.activeSourceTab = tab;
  renderSourceDialogContent();
}

function renderSourceDialogContent(highlightLine = null) {
  const data = state.workspaceSource;
  const isJava = state.activeSourceTab === "java";

  $$(".source-tab-btn").forEach((btn) => {
    const active = btn.dataset.sourceTab === state.activeSourceTab;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", String(active));
  });

  const javaNameEl = $("#source-tab-java-name");
  if (javaNameEl) {
    javaNameEl.textContent = data?.java_filename || "Example.java";
  }
  const fsfNameEl = $("#source-tab-fsf-name");
  if (fsfNameEl) {
    fsfNameEl.textContent = data?.fsf_filename || "None";
  }

  const viewer = $("#source-code-viewer");
  if (!viewer) return;

  const content = isJava ? (data?.java_source || "") : (data?.fsf_source || "");
  const filename = isJava ? (data?.java_filename || "Java Source") : (data?.fsf_filename || "FSF Specification");

  if (!content) {
    viewer.replaceChildren();
    const emptyMsg = document.createElement("p");
    emptyMsg.className = "source-empty-state";
    emptyMsg.textContent = isJava
      ? "No Java source code available for this program."
      : "No Formal Specification File (FSF) provided for this program.";
    viewer.appendChild(emptyMsg);
    const statsEl = $("#source-dialog-stats");
    if (statsEl) statsEl.textContent = "0 lines";
    return;
  }

  const lines = content.split(/\r?\n/);
  const frag = document.createDocumentFragment();

  lines.forEach((lineText, idx) => {
    const lineNum = idx + 1;
    const lineRow = document.createElement("div");
    lineRow.className = "source-line";
    lineRow.dataset.line = String(lineNum);
    if (isJava && highlightLine === lineNum) {
      lineRow.classList.add("highlighted");
    }

    const numSpan = document.createElement("span");
    numSpan.className = "source-line-num";
    numSpan.textContent = String(lineNum);

    const codeSpan = document.createElement("span");
    codeSpan.className = "source-line-code";
    codeSpan.textContent = lineText || " ";

    lineRow.appendChild(numSpan);
    lineRow.appendChild(codeSpan);
    frag.appendChild(lineRow);
  });

  viewer.replaceChildren(frag);

  const statsEl = $("#source-dialog-stats");
  if (statsEl) {
    statsEl.textContent = `${filename} · ${lines.length} line${lines.length === 1 ? "" : "s"}`;
  }
}

async function copyCurrentSource() {
  const data = state.workspaceSource;
  const isJava = state.activeSourceTab === "java";
  const content = isJava ? (data?.java_source || "") : (data?.fsf_source || "");
  if (!content) {
    toast("No content to copy", true);
    return;
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(content);
      toast(`Copied ${isJava ? "Java source" : "FSF"} to clipboard`);
    } else {
      throw new Error("Clipboard API unavailable");
    }
  } catch {
    const ta = document.createElement("textarea");
    ta.value = content;
    ta.style.position = "fixed";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try {
      document.execCommand("copy");
      toast(`Copied ${isJava ? "Java source" : "FSF"} to clipboard`);
    } catch {
      toast("Failed to copy to clipboard", true);
    } finally {
      document.body.removeChild(ta);
    }
  }
}
