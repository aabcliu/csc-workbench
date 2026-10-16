const STAGE_LABELS = {
  csc: "Test Generation",
  tbfv: "Verification",
  localization: "Fault Localization",
};

const REASON_TEXT = {
  source_artifact_missing: "A required source artifact was not found.",
  source_artifact_invalid: "A source artifact exists but is not valid evidence.",
  solver_timeout: "The solver timed out before deciding some checks.",
  solver_unknown: "The solver could not decide some checks.",
  unsupported_expression: "Some expressions are outside the supported fragment.",
  missing_trace: "Some checks have no executable trace to replay.",
  untrusted_trace: "Some recorded traces were not trusted.",
  compile_error: "The run stopped at a compile error.",
  analysis_error: "The run stopped at an internal analysis error.",
  iteration_limit: "The search stopped at the configured iteration limit.",
  no_progress: "The search stopped after repeated iterations without progress.",
  unresolved_obligations: "Some explored obligations remain unresolved.",
  no_executable_tests: "No executable trace-backed test was produced.",
  no_applicable_scenario: "No FSF scenario applies to the generated tests.",
  no_failed_verification: "No verification check failed, so there is nothing to locate.",
  no_localization_candidate: "Failed checks were not tied back to a suspicious location.",
};

const WARNING_TEXT = {
  upstream_partial: "The upstream conclusion is partial.",
  incomplete_evidence: "Locating uses incomplete verification evidence.",
};

const COMPLETENESS_VIEWS = {
  complete: {
    label: "Complete",
    text: "Complete — every applicable case was decided.",
  },
  partial: {
    label: "Partial",
    text: "Partial — results exist, but some evidence is missing or unresolved.",
  },
  unknown: {
    label: "Unknown",
    text: "Unknown — the stage could not establish how much evidence was covered.",
  },
};

const STATUS_VIEWS = {
  csc: {
    completed: {
      label: "Completed",
      tone: "ok",
      headline: "Every explored obligation was decided without unresolved cases.",
    },
    partial: {
      label: "Partial",
      tone: "warn",
      headline:
        "Some results are available and can be viewed, but the exploration is incomplete: read the reasons below before relying on them.",
    },
    unknown: {
      label: "Unknown",
      tone: "muted",
      headline: "No usable conclusion was produced for this run.",
    },
  },
  tbfv: {
    pass: {
      label: "Pass",
      tone: "ok",
      headline:
        "Every applicable scenario over the generated tests passed its checks. This does not mean the program is correct: it means no checked applicable path violated the FSF.",
    },
    fail: {
      label: "Fail",
      tone: "warn",
      headline:
        "At least one checked applicable path violated the FSF. Fault localization can rank suspicious locations from this evidence.",
    },
    unknown: {
      label: "Unknown",
      tone: "muted",
      headline:
        "The stage could not establish a definitive pass or fail. Some checks may have reliable results; review the counts and reasons for the unresolved checks.",
    },
    unsupported: {
      label: "Unsupported",
      tone: "muted",
      headline:
        "Some necessary checks use expressions outside the supported fragment. Other checks may have reliable results, but the stage cannot establish a pass/fail conclusion.",
    },
  },
  localization: {
    available: {
      label: "Available",
      tone: "ok",
      headline: "Suspicious locations were ranked from the failed checks.",
    },
    unavailable: {
      label: "Unavailable",
      tone: "muted",
      headline: "No ranked suspicious locations were produced for this run.",
    },
  },
};

const LOCALIZATION_DETAILS = {
  no_failed_verification:
    "No verification check failed, so there is no failure evidence to locate.",
  no_localization_candidate:
    "Failed checks were found, but none could be tied back to a suspicious location.",
  upstream_unknown:
    "The upstream verification conclusion is unknown or incomplete, so there is no trusted evidence to locate from.",
  artifact_problem:
    "The evidence needed for localization is missing or invalid.",
};

const SUMMARY_LABELS = {
  stopReason: "Stop reason",
  obligations: "Obligations explored",
  concrete: "Covered by concrete tests",
  unsat: "Proven UNSAT",
  ancestorStopped: "Stopped by CSC ancestor rule",
  rangeExcluded: "Outside input range",
  unresolved: "Unresolved obligations",
  truncatedLeaves: "Truncated leaves",
  solverUnknownCalls: "Solver unknowns",
  solverTimeoutCalls: "Solver timeouts",
  executableTraceBackedTests: "Executable trace-backed tests",
  testcases: "Test cases",
  fsfUnits: "FSF units",
  passed: "Passed",
  failed: "Failed",
  unknown: "Unknown",
  unsupported: "Unsupported",
  skipped: "Skipped",
  skippedScenarioUnmatched: "Skipped · scenario not matched",
  skippedMissingTrace: "Skipped · missing trace",
  skippedUntrustedTrace: "Skipped · untrusted trace",
  candidateCount: "Candidate locations",
};

const SUMMARY_ORDER = [
  "stopReason",
  "obligations",
  "unresolved",
  "truncatedLeaves",
  "solverUnknownCalls",
  "solverTimeoutCalls",
  "executableTraceBackedTests",
  "testcases",
  "fsfUnits",
  "passed",
  "failed",
  "unknown",
  "unsupported",
  "skipped",
  "skippedScenarioUnmatched",
  "skippedMissingTrace",
  "skippedUntrustedTrace",
  "candidateCount",
];

export function buildOutcomeView({ outcome, nextStages } = {}) {
  if (!outcome || typeof outcome !== "object") return null;
  const stage = typeof outcome.stage === "string" ? outcome.stage : "";
  const conclusion = isObject(outcome.conclusion) ? outcome.conclusion : {};
  const status = typeof conclusion.status === "string" ? conclusion.status : "";
  const stageViews = STATUS_VIEWS[stage] || {};
  const statusView = stageViews[status] || {
    label: humanize(status) || "Unknown",
    tone: "muted",
    headline: "This run recorded a conclusion outside the known status vocabulary.",
  };
  const completenessKey =
    typeof conclusion.completeness === "string" ? conclusion.completeness : "";
  const completenessView = COMPLETENESS_VIEWS[completenessKey] || {
    label: humanize(completenessKey) || "Unknown",
    text: "Unknown — the recorded completeness is outside the known vocabulary.",
  };

  return {
    stage,
    stageLabel: stageLabelFor(stage),
    jobId: typeof outcome.jobId === "string" ? outcome.jobId : "",
    generatedAt: typeof outcome.generatedAt === "string" ? outcome.generatedAt : "",
    status: { key: status, label: statusView.label, tone: statusView.tone },
    headline: headlineFor(stage, status, statusView, conclusion),
    badge: {
      label: `Conclusion: ${statusView.label}`,
      tone: statusView.tone,
    },
    completeness: {
      key: completenessKey,
      label: completenessView.label,
      text: completenessView.text,
    },
    reasons: describeCodes(conclusion.reasonCodes, REASON_TEXT),
    summary: describeSummary(outcome.summary),
    nextStages: describeNextStages(nextStages),
    sources: describeSources(outcome.sources),
  };
}

export function buildOutcomeErrorView({ job, httpStatus, detail } = {}) {
  const jobStatus = typeof job?.status === "string" ? job.status : "";
  const warnings = Array.isArray(job?.finalization_warnings)
    ? job.finalization_warnings
    : [];
  const finalizationFailed = warnings.some(
    (warning) => warning?.component === "outcome",
  );

  if (httpStatus === 409) {
    return errorView(
      "error",
      "invalid",
      "The recorded conclusion is invalid",
      "The stored conclusion artifact failed contract validation and cannot be trusted.",
      detail,
    );
  }
  if (jobStatus === "pending" || jobStatus === "running") {
    return errorView(
      "pending",
      "pending",
      "Conclusion pending",
      "The conclusion appears after this run finishes.",
    );
  }
  if (jobStatus === "failed" || jobStatus === "cancelled") {
    return errorView(
      "muted",
      "not recorded",
      "This run produced no conclusion",
      "The run ended before a conclusion could be recorded.",
    );
  }
  if (finalizationFailed) {
    return errorView(
      "warn",
      "finalization failed",
      "The conclusion could not be finalized",
      "Recording the conclusion failed for this run. The rest of the result is unaffected; the job log holds the details.",
      detail,
    );
  }
  return errorView(
    "muted",
    "not recorded",
    "Analysis conclusion was not recorded by this version",
    "This run was recorded before conclusions existed. Its CCT and metrics are still available.",
  );
}

export function describeStartFailure(message) {
  const text = typeof message === "string" ? message : "";
  if (!text) return "The task could not be started.";
  if (isNoFailureLocalizationRejection(text)) {
    return "Verification completed with no failed checks. Fault localization is not applicable.";
  }
  const separator = text.indexOf(": ");
  if (separator === -1) return text;
  const codes = text.slice(separator + 2).split(",").map((code) => code.trim());
  const descriptions = codes.map((code) => REASON_TEXT[code]);
  if (!codes.length || descriptions.some((description) => !description)) return text;
  return `${text.slice(0, separator + 1)} ${descriptions.join(" ")}`;
}

export function isNoFailureLocalizationRejection(message) {
  const text = typeof message === "string" ? message : "";
  return text.includes("no_failed_verification")
    || text.includes("No verification check failed");
}

function headlineFor(stage, status, statusView, conclusion) {
  if (stage === "tbfv" && status === "pass" && conclusion.completeness === "partial") {
    return "The applicable scenarios with reliable results passed their checks, but some necessary checks could not be completed. This does not mean the program is correct; review the missing or untrusted trace evidence below.";
  }
  if (stage === "localization" && status === "unavailable") {
    const detail = LOCALIZATION_DETAILS[localizationReason(conclusion)];
    return `${statusView.headline} ${detail}`;
  }
  return statusView.headline;
}

function localizationReason(conclusion) {
  const codes = Array.isArray(conclusion.reasonCodes) ? conclusion.reasonCodes : [];
  if (conclusion.completeness === "unknown") {
    return codes.some((code) => code === "source_artifact_missing" || code === "source_artifact_invalid")
      ? "artifact_problem"
      : "upstream_unknown";
  }
  if (codes.includes("no_localization_candidate")) return "no_localization_candidate";
  if (codes.includes("no_failed_verification")) return "no_failed_verification";
  return "artifact_problem";
}

function describeCodes(codes, vocabulary) {
  if (!Array.isArray(codes)) return [];
  return codes
    .filter((code) => typeof code === "string" && code)
    .map((code) => ({
      code,
      text: vocabulary[code] || `${humanize(code) || code}.`,
    }));
}

function describeNextStages(nextStages) {
  if (!Array.isArray(nextStages)) return [];
  return nextStages
    .filter((entry) => isObject(entry) && typeof entry.stage === "string")
    .map((entry) => {
      const available = entry.available === true;
      const reasonCodes = describeCodes(entry.reasonCodes, REASON_TEXT);
      const warningCodes = describeCodes(entry.warningCodes, WARNING_TEXT);
      return {
        stage: entry.stage,
        label: stageLabelFor(entry.stage),
        available,
        statusLabel: available ? "Available" : "Blocked",
        reasonCodes,
        warningCodes,
        detail: available
          ? nextStageReadyDetail(warningCodes)
          : `Blocked: ${reasonCodes.map((item) => item.text).join(" ")}`.trim(),
      };
    });
}

function nextStageReadyDetail(warningCodes) {
  if (!warningCodes.length) return "Ready to start from this conclusion.";
  return `Can start, with warnings: ${warningCodes.map((item) => item.text).join(" ")}`;
}

function describeSummary(summary) {
  if (!isObject(summary)) return [];
  const facts = [];
  if (typeof summary.stopReason === "string" && summary.stopReason) {
    facts.push({
      label: SUMMARY_LABELS.stopReason,
      text: humanize(summary.stopReason),
    });
  }
  const obligations = isObject(summary.obligations) ? summary.obligations : null;
  if (obligations) {
    if (isNumber(obligations.total)) {
      facts.push({ label: SUMMARY_LABELS.obligations, text: String(obligations.total) });
    }
    for (const key of ["concrete", "unsat", "ancestorStopped", "rangeExcluded"]) {
      if (isNumber(obligations[key])) {
        facts.push({ label: SUMMARY_LABELS[key], text: String(obligations[key]) });
      }
    }
    if (isNumber(obligations.unresolved)) {
      facts.push({ label: SUMMARY_LABELS.unresolved, text: String(obligations.unresolved) });
    }
  }
  for (const key of SUMMARY_ORDER) {
    if (key === "stopReason" || key === "obligations" || key === "unresolved") continue;
    if (!isNumber(summary[key])) continue;
    facts.push({ label: SUMMARY_LABELS[key] || humanize(key), text: String(summary[key]) });
  }
  return facts;
}

function describeSources(sources) {
  if (!Array.isArray(sources)) return [];
  return sources
    .filter((source) => isObject(source))
    .map((source) => ({
      jobId: typeof source.jobId === "string" ? source.jobId : "",
      stageLabel: stageLabelFor(typeof source.stage === "string" ? source.stage : ""),
      artifacts: Array.isArray(source.artifacts)
        ? source.artifacts.filter((artifact) => typeof artifact === "string")
        : [],
    }));
}

function errorView(tone, badgeLabel, title, message, detail = null) {
  const extra = typeof detail === "string" && detail.trim() ? detail : null;
  return {
    tone,
    badge: { label: `Conclusion: ${badgeLabel}`, tone },
    title,
    message: extra ? `${message} (${extra})` : message,
  };
}

function stageLabelFor(stage) {
  return STAGE_LABELS[stage] || humanize(stage) || "Unknown stage";
}

function isObject(value) {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function humanize(value) {
  if (typeof value !== "string" || !value) return "";
  const words = value.replace(/_/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}
