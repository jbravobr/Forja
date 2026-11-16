"""What the policy may say about each check, and what it says when left alone."""

from forja.core.gates import InconclusivePolicy, Mode
from forja.core.policy.registry import CheckRegistry, CheckSpec

BLOCK = InconclusivePolicy.BLOCK
PREMISE = InconclusivePolicy.PREMISE


def default_registry() -> CheckRegistry:
    """The default checks. A tool that cannot run blocks the task, except complexity,
    whose gap is declared as an assumption because its limit is not calibrated yet."""
    registry = CheckRegistry()
    for spec in (
        CheckSpec("scope", 1, Mode.BLOCKING, BLOCK, limits=frozenset({"max_files", "max_lines"})),
        CheckSpec("freeze", 1, Mode.BLOCKING, BLOCK),
        CheckSpec("secrets", 2, Mode.BLOCKING, BLOCK),
        CheckSpec("dependency_install", 2, Mode.BLOCKING, BLOCK),
        CheckSpec("format", 3, Mode.BLOCKING, BLOCK, commands=frozenset({"format"})),
        CheckSpec("lint", 3, Mode.BLOCKING, BLOCK, commands=frozenset({"lint"})),
        CheckSpec("types", 3, Mode.OBSERVATION, PREMISE, commands=frozenset({"types"})),
        CheckSpec("build", 4, Mode.BLOCKING, BLOCK, commands=frozenset({"build"})),
        CheckSpec("tests", 5, Mode.BLOCKING, BLOCK, commands=frozenset({"test"})),
        CheckSpec("red_green", 5, Mode.BLOCKING, BLOCK, commands=frozenset({"test"})),
        CheckSpec("e2e", 6, Mode.OBSERVATION, BLOCK),
        CheckSpec("test_data", 6, Mode.OBSERVATION, PREMISE),
        CheckSpec("properties", 6, Mode.OBSERVATION, PREMISE),
        CheckSpec(
            "mutation",
            7,
            Mode.OBSERVATION,
            PREMISE,
            thresholds=frozenset({"min_score"}),
            limits=frozenset({"workers", "line_tests"}),
        ),
        CheckSpec("complexity", 8, Mode.BLOCKING, PREMISE, thresholds=frozenset({"max_ccn"})),
        CheckSpec("crap", 8, Mode.OBSERVATION, PREMISE, thresholds=frozenset({"max_crap"})),
        CheckSpec("pass_through", 8, Mode.OBSERVATION, PREMISE),
        CheckSpec(
            "module_depth",
            8,
            Mode.OBSERVATION,
            PREMISE,
            thresholds=frozenset({"max_interface_ratio", "min_statements"}),
        ),
        CheckSpec(
            "duplication",
            8,
            Mode.OBSERVATION,
            PREMISE,
            limits=frozenset({"min_tokens", "min_lines"}),
        ),
        CheckSpec("dependency_cycles", 9, Mode.OBSERVATION, BLOCK),
        CheckSpec("layer_rules", 9, Mode.OBSERVATION, BLOCK),
        CheckSpec(
            "package_metrics",
            9,
            Mode.OBSERVATION,
            PREMISE,
            thresholds=frozenset({"max_distance", "min_types"}),
        ),
        CheckSpec("sast", 10, Mode.OBSERVATION, BLOCK),
        CheckSpec("dependency_audit", 10, Mode.OBSERVATION, BLOCK),
        CheckSpec(
            "new_dependency",
            10,
            Mode.OBSERVATION,
            PREMISE,
            thresholds=frozenset({"min_age_days", "min_downloads"}),
        ),
        CheckSpec(
            "differential",
            11,
            Mode.OBSERVATION,
            PREMISE,
            thresholds=frozenset({"max_time_ratio", "max_memory_ratio", "runs"}),
        ),
        CheckSpec("voice", 12, Mode.OBSERVATION, PREMISE, limits=frozenset({"max_dashes"})),
        CheckSpec("comments", 12, Mode.OBSERVATION, PREMISE),
        CheckSpec("citations", 13, Mode.OBSERVATION, BLOCK),
        CheckSpec("traceability", 13, Mode.OBSERVATION, BLOCK),
        CheckSpec("change_record", 14, Mode.OBSERVATION, BLOCK),
        CheckSpec("readme", 14, Mode.OBSERVATION, PREMISE),
        CheckSpec("doc_drift", 14, Mode.OBSERVATION, PREMISE),
        CheckSpec("adr", 14, Mode.OBSERVATION, PREMISE),
    ):
        registry.register(spec)
    return registry
