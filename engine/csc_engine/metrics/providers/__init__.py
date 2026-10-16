"""Built-in providers backed by artifacts already produced by the tool."""

from ..registry import MetricRegistry
from .cct_scale import CCTScaleMetricProvider
from .coverage import BranchCoverageMetricProvider, ConditionCoverageMetricProvider
from .obligations import CSCObligationMetricProvider
from .runtime import AnalysisTimeMetricProvider
from .solver import SolverOutcomesMetricProvider
from .test_suite import TestSuiteMetricProvider
from .verification import VerificationResultsMetricProvider


DEFAULT_METRIC_PROVIDERS = (
    BranchCoverageMetricProvider(),
    ConditionCoverageMetricProvider(),
    CSCObligationMetricProvider(),
    TestSuiteMetricProvider(),
    VerificationResultsMetricProvider(),
    SolverOutcomesMetricProvider(),
    AnalysisTimeMetricProvider(),
    CCTScaleMetricProvider(),
)


def create_default_metric_registry() -> MetricRegistry:
    # Version 2 added the four M3 providers to the M2 four-provider directory;
    # version 3 raises verification.results to definitionVersion 2 (unknown).
    return MetricRegistry(DEFAULT_METRIC_PROVIDERS, version=3)


__all__ = [
    "AnalysisTimeMetricProvider",
    "BranchCoverageMetricProvider",
    "CCTScaleMetricProvider",
    "ConditionCoverageMetricProvider",
    "CSCObligationMetricProvider",
    "DEFAULT_METRIC_PROVIDERS",
    "TestSuiteMetricProvider",
    "SolverOutcomesMetricProvider",
    "VerificationResultsMetricProvider",
    "create_default_metric_registry",
]
