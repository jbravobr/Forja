from pathlib import Path

import pytest

from forja.core.adapters import (
    CommandPlan,
    VersionProbe,
    build_spec,
    compute_config_hash,
    extract_version,
    read_output_lines,
)
from forja.core.adapters.common import fits_command_line
from forja.core.process import UnsafeCommand


def plan(root: Path, **changes: object) -> CommandPlan:
    fields: dict[str, object] = {"argv": ("tool", "--check"), "cwd": root}
    fields.update(changes)
    return CommandPlan(**fields)  # type: ignore[arg-type]


# -- CommandPlan -------------------------------------------------------------------------------


def test_a_plan_needs_a_program_made_of_strings(tmp_path: Path) -> None:
    for argv in ((), ("",)):
        with pytest.raises(ValueError, match="needs a program"):
            CommandPlan(argv, tmp_path)
    with pytest.raises(ValueError, match="must be a string"):
        CommandPlan(("tool", 3), tmp_path)  # type: ignore[arg-type]


def test_environment_names_and_config_files_are_validated(tmp_path: Path) -> None:
    for bad in ("", "A=B"):
        with pytest.raises(ValueError, match="variable name"):
            plan(tmp_path, env_allow=frozenset({bad}))
        with pytest.raises(ValueError, match="variable name"):
            plan(tmp_path, env_set={bad: "x"})
    for bad_path in ("/abs/file", "../outside", "C:/x"):
        with pytest.raises(ValueError):
            plan(tmp_path, config_files=(bad_path,))


# -- from plan to process ----------------------------------------------------------------------


def test_the_spec_has_a_filtered_environment_and_the_plans_own_values(tmp_path: Path) -> None:
    host = {"PATH": "/bin", "HOME": "/h", "GITHUB_TOKEN": "secret", "TOOL_KEY": "tool-secret"}
    package = tmp_path / "package.md"

    process_spec = build_spec(
        plan(
            tmp_path,
            env_allow=frozenset({"TOOL_KEY"}),
            env_set={"NO_COLOR": "1"},
            stdin_file=package,
        ),
        host,
        timeout=30,
    )

    assert process_spec.env == {
        "PATH": "/bin",
        "HOME": "/h",
        "TOOL_KEY": "tool-secret",
        "NO_COLOR": "1",
    }
    assert process_spec.stdin_file == package
    assert (process_spec.timeout, process_spec.cwd) == (30, tmp_path)
    assert process_spec.low_priority is False


def test_the_spec_is_of_low_priority_only_when_the_caller_asks(tmp_path: Path) -> None:
    assert build_spec(plan(tmp_path), {}, timeout=5, low_priority=True).low_priority is True
    assert build_spec(plan(tmp_path), {}, timeout=5).low_priority is False


def test_an_unsafe_plan_fails_when_the_spec_is_built(tmp_path: Path) -> None:
    with pytest.raises(UnsafeCommand):
        build_spec(plan(tmp_path, argv=("tool", "x" * 9000)), {}, timeout=5)


# -- the configuration hash --------------------------------------------------------------------


def test_the_hash_is_stable_and_follows_the_command_line(tmp_path: Path) -> None:
    base = compute_config_hash(plan(tmp_path), tmp_path)

    assert compute_config_hash(plan(tmp_path), tmp_path) == base
    assert compute_config_hash(plan(tmp_path, argv=("tool", "--fix")), tmp_path) != base
    assert compute_config_hash(plan(tmp_path, argv=("tool", "--check", "-v")), tmp_path) != base


def test_the_hash_follows_the_environment_the_adapter_declares(tmp_path: Path) -> None:
    base = compute_config_hash(plan(tmp_path), tmp_path)
    allowed = compute_config_hash(plan(tmp_path, env_allow=frozenset({"TOOL_KEY"})), tmp_path)
    set_a = compute_config_hash(plan(tmp_path, env_set={"A": "1", "B": "2"}), tmp_path)
    set_b = compute_config_hash(plan(tmp_path, env_set={"B": "2", "A": "1"}), tmp_path)

    assert allowed != base and set_a != base
    assert set_a == set_b  # the order of declaration does not matter
    assert compute_config_hash(plan(tmp_path, env_set={"A": "9", "B": "2"}), tmp_path) != set_a


def test_the_hash_follows_the_content_of_the_declared_config_files(tmp_path: Path) -> None:
    declared = plan(tmp_path, config_files=("tool.toml",))
    absent = compute_config_hash(declared, tmp_path)
    (tmp_path / "tool.toml").write_text("strict = true")
    present = compute_config_hash(declared, tmp_path)
    (tmp_path / "tool.toml").write_text("strict = false")
    edited = compute_config_hash(declared, tmp_path)

    assert len({absent, present, edited}) == 3
    (tmp_path / "other.toml").write_text("anything")
    assert compute_config_hash(declared, tmp_path) == edited  # undeclared files do not count


def test_the_hash_never_depends_on_values_of_host_variables(tmp_path: Path) -> None:
    declared = plan(tmp_path, env_allow=frozenset({"TOOL_KEY"}))

    assert "TOOL_KEY" in declared.env_allow
    assert compute_config_hash(declared, tmp_path) == compute_config_hash(declared, tmp_path)


# -- versions and output -----------------------------------------------------------------------


def test_a_version_pattern_needs_exactly_one_group(tmp_path: Path) -> None:
    for pattern in (r"\d+", r"(\d+)\.(\d+)"):
        with pytest.raises(ValueError, match="one capture group"):
            VersionProbe(plan(tmp_path), pattern)


def test_the_first_matching_line_gives_the_version(tmp_path: Path) -> None:
    probe = VersionProbe(plan(tmp_path), r"tool (\d+\.\d+\.\d+)")

    assert extract_version(probe, ["banner", "tool 1.2.3", "tool 9.9.9"]) == "1.2.3"
    assert extract_version(probe, ["nothing here"]) is None
    assert extract_version(probe, []) is None


def test_output_lines_lose_the_channel_prefix_and_survive_bad_files(tmp_path: Path) -> None:
    log = tmp_path / "op.log"
    log.write_bytes(b"[out] first\n[err] second\nno prefix\n[out] bad \xff byte\n")

    lines = read_output_lines(log)

    assert lines[:3] == ["first", "second", "no prefix"]
    assert lines[3].startswith("bad ")
    assert read_output_lines(tmp_path / "missing.log") == []


def test_a_command_fits_when_the_process_runner_would_take_it() -> None:
    assert fits_command_line(("tool", "a", "b")) is True
    assert fits_command_line(("tool", "x" * 8192)) is True
    assert fits_command_line(("tool", "x" * 8193)) is False  # one argument too large
    assert fits_command_line(("tool", *["x" * 99] * 299)) is True
    assert fits_command_line(("tool", *["x" * 99] * 300)) is False  # the whole of it too long
    assert fits_command_line(("tool.cmd", "^(a|b)$")) is False  # the shell would misread it
    assert fits_command_line(("tool.exe", "^(a|b)$")) is True
    assert fits_command_line(()) is False
