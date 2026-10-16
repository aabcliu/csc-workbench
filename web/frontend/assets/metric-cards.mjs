const METRIC_ID_PATTERN = /^[a-z][a-z0-9_.-]*$/;
const DATA_TYPE_PATTERN = /^[a-z][a-z0-9_.-]*\.[a-z][a-z0-9_.-]*$/;

const STATUS_VIEWS = {
  available: { label: "Available", tone: "ok" },
  partial: { label: "Partial", tone: "warn" },
  pending: { label: "Pending", tone: "muted" },
  not_applicable: { label: "Not applicable", tone: "muted" },
  unavailable: { label: "Unavailable", tone: "muted" },
  unsupported: { label: "Unsupported", tone: "muted" },
  error: { label: "Error", tone: "error" },
};

const UNIT_LABELS = {
  count: ["", ""],
  branch_outcome: ["branch outcome", "branch outcomes"],
  condition_outcome: ["condition outcome", "condition outcomes"],
  csc_obligation: ["CSC obligation", "CSC obligations"],
  obligation: ["obligation", "obligations"],
  active_feasible_obligation: ["active feasible obligation", "active feasible obligations"],
  test_record: ["test record", "test records"],
  input: ["input", "inputs"],
  solver_call: ["solver call", "solver calls"],
  fsf_unit: ["FSF unit", "FSF units"],
  verification_check: ["check", "checks"],
  verification_count: ["verification", "verifications"],
  node: ["node", "nodes"],
  edge: ["edge", "edges"],
  level: ["level", "levels"],
};

const cardRegistry = new Map();
const suppressedMetricIds = new Set();
const rendererRegistry = new Map();

export function registerMetricCard(definition) {
  const card = normalizeCardDefinition(definition);
  if (cardRegistry.has(card.metricId)) {
    throw new Error(`Metric card already registered: ${card.metricId}`);
  }
  suppressedMetricIds.delete(card.metricId);
  cardRegistry.set(card.metricId, card);
  return card;
}

export function unregisterMetricCard(metricId) {
  if (!cardRegistry.delete(metricId)) return false;
  suppressedMetricIds.add(metricId);
  return true;
}

export function registerMetricRenderer(dataType, dataVersion, renderer) {
  if (typeof dataType !== "string" || !DATA_TYPE_PATTERN.test(dataType)) {
    throw new Error(`Invalid metric data type: ${dataType}`);
  }
  if (!Number.isInteger(dataVersion) || dataVersion < 1) {
    throw new Error(`Invalid metric data version: ${dataVersion}`);
  }
  if (typeof renderer !== "function") {
    throw new Error("Metric renderer must be a function");
  }
  const key = rendererKey(dataType, dataVersion);
  if (rendererRegistry.has(key)) {
    throw new Error(`Metric renderer already registered: ${key}`);
  }
  rendererRegistry.set(key, renderer);
  return renderer;
}

export function unregisterMetricRenderer(dataType, dataVersion) {
  return rendererRegistry.delete(rendererKey(dataType, dataVersion));
}

export function buildMetricCards(document) {
  const metrics =
    document && typeof document === "object" && Array.isArray(document.metrics)
      ? document.metrics
      : [];
  const cards = [];
  metrics.forEach((record) => {
    if (!record || typeof record !== "object") return;
    if (suppressedMetricIds.has(record.id)) return;
    cards.push(buildMetricCard(record));
  });
  return cards.sort((left, right) => left.order - right.order);
}

export function createRequestTokens() {
  let current = 0;
  return {
    issue() {
      current += 1;
      return current;
    },
    isCurrent(token) {
      return token === current;
    },
  };
}

export function formatUnitValue(value, unit) {
  if (!isFiniteNumber(value)) return "—";
  if (unit === "millisecond") return formatDuration(value);
  const noun = unitNoun(unit, value);
  const amount = formatNumber(value);
  return noun ? `${amount} ${noun}` : amount;
}

export function formatRatioPercent(numerator, denominator) {
  if (!isFiniteNumber(numerator) || !isFiniteNumber(denominator)) return null;
  if (denominator <= 0 || numerator < 0 || numerator > denominator) return null;
  return `${formatNumber(Math.round((numerator / denominator) * 1000) / 10)}%`;
}

function buildMetricCard(record) {
  const metricId = typeof record.id === "string" ? record.id : "";
  const definition = cardRegistry.get(metricId) || null;
  const degradations = [];
  if (!definition) degradations.push("unregistered_metric");

  const status = typeof record.status === "string" ? record.status : "";
  const statusView = STATUS_VIEWS[status] || { label: humanize(status) || "Unknown", tone: "muted" };
  const renderable =
    (status === "available" || status === "partial") &&
    record.data !== null &&
    typeof record.data === "object";

  let rendered = emptyRendering();
  if (renderable) {
    const renderer = rendererRegistry.get(
      rendererKey(record.dataType, record.dataVersion),
    );
    if (renderer) {
      try {
        rendered = renderer(record, { definition });
      } catch {
        degradations.push("renderer_error");
        rendered = emptyRendering();
      }
    } else {
      degradations.push("unsupported_data_type");
    }
  }

  return {
    id: metricId,
    title: definition ? definition.title : metricId || "Unknown metric",
    order: definition ? definition.order : Number.MAX_SAFE_INTEGER,
    status,
    statusLabel: statusView.label,
    tone: statusView.tone,
    primary: rendered.primary,
    caption: rendered.caption,
    details: rendered.details,
    reason: normalizeReason(record.reason),
    source: normalizeScope(record.scope),
    note: definition && definition.note ? definition.note : null,
    help: definition && definition.help ? definition.help : null,
    degradations,
  };
}

function normalizeCardDefinition(definition) {
  if (!definition || typeof definition !== "object") {
    throw new Error("Metric card definition must be an object");
  }
  if (typeof definition.metricId !== "string" || !METRIC_ID_PATTERN.test(definition.metricId)) {
    throw new Error(`Invalid metric card ID: ${definition.metricId}`);
  }
  if (typeof definition.title !== "string" || !definition.title.trim()) {
    throw new Error(`Metric card ${definition.metricId} needs a title`);
  }
  if (!Number.isFinite(definition.order)) {
    throw new Error(`Metric card ${definition.metricId} needs a numeric order`);
  }
  return Object.freeze({
    metricId: definition.metricId,
    title: definition.title,
    order: definition.order,
    primaryKey: typeof definition.primaryKey === "string" ? definition.primaryKey : null,
    primaryLabel:
      typeof definition.primaryLabel === "string" ? definition.primaryLabel : null,
    note: typeof definition.note === "string" ? definition.note : null,
    hideComponentUnits: Boolean(definition.hideComponentUnits),
    help: definition.help && typeof definition.help === "object" ? {
      calculation: typeof definition.help.calculation === "string" ? definition.help.calculation : "",
      meaning: typeof definition.help.meaning === "string" ? definition.help.meaning : "",
    } : null,
  });
}

function rendererKey(dataType, dataVersion) {
  return `${dataType}@${dataVersion}`;
}

function emptyRendering() {
  return { primary: null, caption: null, details: [] };
}

function renderScalar(record, context) {
  const data = record.data;
  const definition = context.definition || {};
  return {
    primary: {
      label: definition.primaryLabel || null,
      text: formatUnitValue(data.value, data.unit),
    },
    caption: null,
    details: renderComponents(data.components),
  };
}

function renderRatio(record, context) {
  const data = record.data;
  const definition = context.definition || {};
  const percent = formatRatioPercent(data.numerator, data.denominator);
  const caption = `${formatNumber(data.numerator)} / ${formatNumber(data.denominator)}${unitSuffix(
    data.unit,
    data.denominator,
  )}`;
  const hideUnits = Boolean(definition.hideComponentUnits);
  return {
    primary: { label: definition.primaryLabel || null, text: percent || "—" },
    caption,
    details: renderComponents(data.components, hideUnits),
  };
}

function renderVector(record, context) {
  const data = record.data;
  const definition = context.definition || {};
  const measurements = Array.isArray(data.measurements) ? data.measurements : [];
  const primaryKey = definition.primaryKey || data.primaryKey;
  const primaryMeasurement =
    measurements.find((measurement) => measurement?.key === primaryKey) || measurements[0];
  if (!primaryMeasurement) return emptyRendering();
  return {
    primary: {
      label: measurementLabel(primaryMeasurement.key, primaryMeasurement.label),
      text: formatUnitValue(primaryMeasurement.value, primaryMeasurement.unit),
    },
    caption: null,
    details: measurements
      .filter((measurement) => measurement !== primaryMeasurement)
      .map((item) => renderMeasurement(item, false)),
  };
}

function renderComponents(components, hideUnits = false) {
  return Array.isArray(components) ? components.map((c) => renderMeasurement(c, hideUnits)) : [];
}

function renderMeasurement(measurement, hideUnits = false) {
  const key = typeof measurement?.key === "string" ? measurement.key : "";
  const shouldHide = hideUnits === true;
  const text = shouldHide
    ? formatNumber(measurement?.value)
    : formatUnitValue(measurement?.value, measurement?.unit);
  return {
    key,
    label: measurementLabel(key, measurement?.label),
    text,
  };
}

function measurementLabel(key, label) {
  if (typeof label === "string" && label.trim()) return label;
  return humanize(key);
}

function normalizeReason(reason) {
  if (!reason || typeof reason !== "object") return null;
  return {
    code: typeof reason.code === "string" ? reason.code : "",
    message: typeof reason.message === "string" ? reason.message : "",
  };
}

function normalizeScope(scope) {
  if (!scope || typeof scope !== "object") return null;
  return {
    sourceJobId: typeof scope.sourceJobId === "string" ? scope.sourceJobId : "",
    stage: typeof scope.stage === "string" ? scope.stage : "",
  };
}

function formatDuration(milliseconds) {
  if (milliseconds < 1000) return `${formatNumber(milliseconds)} ms`;
  if (milliseconds < 60000) return `${formatNumber(Math.round(milliseconds / 100) / 10)} s`;
  const minutes = Math.floor(milliseconds / 60000);
  const seconds = Math.round((milliseconds % 60000) / 1000);
  return seconds ? `${minutes} min ${seconds} s` : `${minutes} min`;
}

function formatNumber(value) {
  if (!isFiniteNumber(value)) return "—";
  return String(Math.round(value * 10) / 10);
}

function unitNoun(unit, value) {
  if (typeof unit !== "string" || !unit) return "";
  const labels = UNIT_LABELS[unit];
  if (labels) return value === 1 ? labels[0] : labels[1];
  const words = unit.trim().replace(/_/g, " ");
  if (!words) return "";
  return value === 1 ? words : `${words}s`;
}

function unitSuffix(unit, value) {
  const noun = unitNoun(unit, value);
  return noun ? ` ${noun}` : "";
}

function humanize(value) {
  if (typeof value !== "string" || !value) return "";
  const words = value.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

registerMetricCard({
  metricId: "coverage.branch",
  title: "Branch Coverage",
  order: 10,
  help: {
    calculation: "covered_branches / total_branches",
    meaning: "Percentage of control-flow branch outcomes (true and false) exercised by the generated test suite.",
  },
});
registerMetricCard({
  metricId: "coverage.condition",
  title: "Condition Coverage",
  order: 20,
  help: {
    calculation: "covered_conditions / total_conditions",
    meaning: "Percentage of atomic boolean condition outcomes evaluated to both true and false across all decisions.",
  },
});
registerMetricCard({
  metricId: "csc.obligations",
  title: "Concrete CSC Branch Coverage",
  order: 30,
  hideComponentUnits: true,
  help: {
    calculation: "covered / (covered + uncovered)",
    meaning: "Share of currently identified active branches exercised by concrete tests. This ratio excludes X-marked, range-excluded, and truncated branches; it does not establish exploration completeness. See the CSC outcome for proven UNSAT, ancestor-stopped, and unresolved counts.",
  },
});
registerMetricCard({
  metricId: "tests.suite",
  title: "Test Suite",
  order: 40,
  primaryKey: "executable_records",
  help: {
    calculation: "count(executable_records)",
    meaning: "Total count of synthesized executable test cases covering explored paths and boundary conditions.",
  },
});
registerMetricCard({
  metricId: "verification.results",
  title: "Verification Results",
  order: 50,
  primaryKey: "passed",
  help: {
    calculation: "checked = passed + failed; skipped, unsupported, and unknown are reported separately",
    meaning: "Formal verification results for FSF checks over generated test cases, including checks that were skipped, unsupported, or inconclusive (solver unknown/timeout).",
  },
});
registerMetricCard({
  metricId: "solver.outcomes",
  title: "Solver Outcomes",
  order: 60,
  primaryKey: "calls",
  help: {
    calculation: "calls = SAT + UNSAT + unknown; timeouts are a subset of unknown",
    meaning: "SMT solver outcomes produced by the source stage shown on this card; downstream stages reuse that stage value instead of accumulating it again.",
  },
});
registerMetricCard({
  metricId: "runtime.analysis",
  title: "Analysis Time",
  order: 70,
  primaryKey: "wall_total",
  help: {
    calculation: "sum(stage_elapsed_time)",
    meaning: "Total elapsed wall-clock time spent in test generation, verification, and fault localization.",
  },
});
registerMetricCard({
  metricId: "cct.scale",
  title: "CCT Scale",
  order: 80,
  primaryKey: "total_nodes",
  help: {
    calculation: "nodes, materialized edges, and maximum depth reported as separate measurements",
    meaning: "Size and structural complexity metrics of the constructed Condition Case Tree model.",
  },
});

registerMetricRenderer("core.scalar", 1, renderScalar);
registerMetricRenderer("core.ratio", 1, renderRatio);
registerMetricRenderer("core.vector", 1, renderVector);
