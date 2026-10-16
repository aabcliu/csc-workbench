"""CCT size metric derived from stable CCT statistics."""

from __future__ import annotations

from ..context import MetricContext
from ..registry import MetricObservation, MetricSpec
from .common import (
    artifact_or_unavailable,
    measurement,
    require_count,
    require_object,
    source_or_unavailable,
)


class CCTScaleMetricProvider:
    spec = MetricSpec(
        id="cct.scale",
        definition_version=1,
        data_type="core.vector",
        data_version=1,
        producer="cct-scale-metrics",
        producer_version="1",
        stages=("csc", "tbfv", "localization"),
    )

    def collect(self, context: MetricContext) -> MetricObservation:
        source, unavailable = source_or_unavailable(context, "csc", "CCT Scale")
        if unavailable is not None:
            return unavailable
        assert source is not None
        payload, unavailable = artifact_or_unavailable(
            context, source, "cct_stats.json", "CCT Scale"
        )
        if unavailable is not None:
            return unavailable
        cct = require_object(require_object(payload, "cct_stats").get("cct"), "cct_stats.cct")
        total = require_count(cct.get("total_nodes"), "cct.total_nodes")
        conditions = require_count(cct.get("internal_nodes"), "cct.internal_nodes")
        leaves = require_count(cct.get("leaf_nodes"), "cct.leaf_nodes")
        max_depth = require_count(cct.get("max_depth"), "cct.max_depth")
        if conditions + leaves != total:
            raise ValueError("CCT node counts do not satisfy internal_nodes + leaf_nodes = total_nodes")
        materialized_edges = max(0, total - 1)
        unallocated_edges = 2 * conditions - materialized_edges
        if unallocated_edges < 0:
            raise ValueError("CCT edge counts are inconsistent with a binary tree")

        return MetricObservation(
            status="available",
            data={
                "primaryKey": "total_nodes",
                "measurements": [
                    measurement("total_nodes", "Nodes", total, "node"),
                    measurement("condition_nodes", "Conditions", conditions, "node"),
                    measurement("leaf_nodes", "Leaves", leaves, "node"),
                    measurement("materialized_edges", "Materialized edges", materialized_edges, "edge"),
                    measurement("unallocated_edges", "Unallocated edges", unallocated_edges, "edge"),
                    measurement("max_depth", "Maximum depth", max_depth, "level"),
                ],
            },
            source_job_id=source.job_id,
            source_stage=source.stage,
            artifacts=(("cct_stats.json", "source"),),
        )
