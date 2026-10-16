"""Branch and atomic-condition coverage providers."""

from __future__ import annotations

from ..context import MetricContext
from ..registry import MetricObservation, MetricSpec
from .common import (
    artifact_or_unavailable,
    measurement,
    require_array,
    require_count,
    require_object,
    source_or_unavailable,
)


class _CoverageMetricProvider:
    catalog_key: str
    hits_key: str
    unit: str
    label: str

    def collect(self, context: MetricContext) -> MetricObservation:
        source, unavailable = source_or_unavailable(context, "csc", self.label)
        if unavailable is not None:
            return unavailable
        assert source is not None
        payload, unavailable = artifact_or_unavailable(
            context, source, "coverage.facts.v1.json", self.label
        )
        if unavailable is not None:
            return unavailable
        fact = require_object(payload, "coverage facts")
        if fact.get("format") != "coverage.facts" or fact.get("version") != 1:
            raise ValueError("unsupported coverage facts format/version")
        catalog = require_array(fact.get(self.catalog_key), self.catalog_key)
        hits = require_object(fact.get(self.hits_key), self.hits_key)
        ids = []
        for index, entry in enumerate(catalog):
            record = require_object(entry, f"{self.catalog_key}[{index}]")
            probe_id = record.get("id")
            if not isinstance(probe_id, str) or not probe_id:
                raise ValueError(f"{self.catalog_key}[{index}].id must be a string")
            ids.append(probe_id)
        if len(ids) != len(set(ids)) or set(hits) != set(ids):
            raise ValueError("coverage catalog and hit IDs disagree")
        denominator = 2 * len(ids)
        covered = 0
        for probe_id in ids:
            outcomes = require_object(hits[probe_id], f"hits[{probe_id}]")
            if set(outcomes) != {"false", "true"}:
                raise ValueError(f"hits[{probe_id}] must contain false and true")
            covered += int(require_count(outcomes["false"], f"hits[{probe_id}].false") > 0)
            covered += int(require_count(outcomes["true"], f"hits[{probe_id}].true") > 0)
        if denominator == 0:
            return MetricObservation(
                status="not_applicable",
                source_job_id=source.job_id,
                source_stage=source.stage,
                artifacts=(("coverage.facts.v1.json", "source"),),
            )
        return MetricObservation(
            status="available" if fact.get("complete") is True else "partial",
            data={
                "numerator": covered,
                "denominator": denominator,
                "unit": self.unit,
                "components": [measurement("uncovered", "Uncovered", denominator - covered, self.unit)],
            },
            reason_code=None if fact.get("complete") is True else "facts_incomplete",
            reason_message=None if fact.get("complete") is True else "Coverage facts are incomplete",
            source_job_id=source.job_id,
            source_stage=source.stage,
            artifacts=(("coverage.facts.v1.json", "source"),),
            details_artifact="coverage.facts.v1.json",
        )


class BranchCoverageMetricProvider(_CoverageMetricProvider):
    spec = MetricSpec(
        id="coverage.branch", definition_version=1, data_type="core.ratio", data_version=1,
        producer="java-coverage", producer_version="1", stages=("csc", "tbfv", "localization"),
    )
    catalog_key = "decisions"
    hits_key = "branchHits"
    unit = "branch_outcome"
    label = "Branch Coverage"


class ConditionCoverageMetricProvider(_CoverageMetricProvider):
    spec = MetricSpec(
        id="coverage.condition", definition_version=1, data_type="core.ratio", data_version=1,
        producer="java-coverage", producer_version="1", stages=("csc", "tbfv", "localization"),
    )
    catalog_key = "conditions"
    hits_key = "conditionHits"
    unit = "condition_outcome"
    label = "Condition Coverage"

