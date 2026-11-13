"""The settings mypy is given: the repository's own, without what would run its code."""

import configparser
from pathlib import Path

import pytest

from forja.core.adapters import CommandPlan, Operation, OperationRequest
from forja.languages.python import PythonAdapter, mypy_config
from forja.languages.python.mypy_config import MypyConfig


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    (root / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    return root


def adapter(tmp_path: Path) -> PythonAdapter:
    return PythonAdapter(python=("py",), ruff=("rf",), mypy=("mp",), cache_dir=tmp_path / "cache")


def types_plan(tmp_path: Path, project: Path) -> CommandPlan:
    plan = adapter(tmp_path).plan(Operation.TYPES, OperationRequest(root=project, files=("a.py",)))
    assert isinstance(plan, CommandPlan)
    return plan


def config_of(plan: CommandPlan) -> str:
    (option,) = [part for part in plan.argv if part.startswith("--config-file=")]
    return option.removeprefix("--config-file=")


def settings_of(project: Path) -> MypyConfig:
    found = mypy_config.read(project)
    assert found is not None
    return found


def test_a_project_without_mypy_settings_has_none(project: Path) -> None:
    assert mypy_config.read(project) is None


def test_the_settings_of_an_ini_file_are_read_with_its_sections_for_modules(project: Path) -> None:
    (project / "mypy.ini").write_text(
        "[mypy]\nstrict = True\n\n[mypy-legacy.*]\nignore_errors = True\n\n[other]\nx = 1\n",
        encoding="utf-8",
    )

    found = settings_of(project)

    assert found.source == "mypy.ini"
    assert found.sections == {
        "mypy": {"strict": "True"},
        "mypy-legacy.*": {"ignore_errors": "True"},
    }
    assert not found.plugins


def test_the_settings_of_pyproject_become_the_same_sections(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        "[tool.mypy]\nstrict = true\nwarn_unused_ignores = false\n"
        'disable_error_code = ["misc", "override"]\nexclude = ["^a/", "^b/"]\n'
        'python_version = "3.12"\n\n'
        '[[tool.mypy.overrides]]\nmodule = ["legacy.*", "old"]\nignore_errors = true\n\n'
        '[[tool.mypy.overrides]]\nmodule = "one"\ndisallow_untyped_defs = true\n',
        encoding="utf-8",
    )

    found = settings_of(project)

    assert found.source == "pyproject.toml"
    assert found.sections == {
        "mypy": {
            "strict": "True",
            "warn_unused_ignores": "False",
            "disable_error_code": "misc, override",
            "exclude": "^a/\n^b/",
            "python_version": "3.12",
        },
        "mypy-legacy.*,old": {"ignore_errors": "True"},
        "mypy-one": {"disallow_untyped_defs": "True"},
    }


def test_the_first_file_mypy_would_read_is_the_one_read(project: Path) -> None:
    (project / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    (project / "setup.cfg").write_text("[mypy]\nstrict = False\n", encoding="utf-8")
    assert settings_of(project).source == "pyproject.toml"

    (project / ".mypy.ini").write_text("[mypy]\npretty = True\n", encoding="utf-8")
    assert settings_of(project).source == ".mypy.ini"

    (project / "mypy.ini").write_text("[mypy]\npretty = False\n", encoding="utf-8")
    assert settings_of(project).source == "mypy.ini"


def test_a_setup_cfg_without_a_mypy_section_is_passed_over(project: Path) -> None:
    (project / "setup.cfg").write_text("[metadata]\nname = x\n", encoding="utf-8")

    assert mypy_config.read(project) is None


@pytest.mark.parametrize("text", ["[mypy\nbroken", "[other]\nx = 1\n"])
def test_a_mypy_ini_that_holds_no_settings_ends_the_search(project: Path, text: str) -> None:
    (project / "mypy.ini").write_text(text, encoding="utf-8")
    (project / "setup.cfg").write_text("[mypy]\nstrict = True\n", encoding="utf-8")

    assert mypy_config.read(project) is None


def test_a_pyproject_that_cannot_be_read_is_passed_over(project: Path) -> None:
    (project / "pyproject.toml").write_text("[tool.mypy\n", encoding="utf-8")
    (project / "setup.cfg").write_text("[mypy]\nstrict = True\n", encoding="utf-8")

    assert settings_of(project).source == "setup.cfg"


def test_the_copy_leaves_out_the_plugins_and_what_writes_a_report(project: Path) -> None:
    (project / "mypy.ini").write_text(
        "[mypy]\nstrict = True\nplugins = evil, pydantic.mypy\nhtml_report = out\n"
        "junit_xml = out.xml\nmypy_path = $MYPY_CONFIG_FILE_DIR/stubs\n"
        "exclude = (?x)(\n    ^one\\.py$\n    | ^two\\.py$\n  )\n",
        encoding="utf-8",
    )
    found = settings_of(project)
    assert found.plugins

    text = found.text(project)

    assert text is not None
    assert "plugins" not in text and "report" not in text and "junit" not in text
    assert "strict = True" in text
    assert f"mypy_path = {project.as_posix()}/stubs" in text
    assert "exclude = (?x)(\n    ^one\\.py$\n    | ^two\\.py$\n    )" in text


def test_plugins_given_as_nothing_are_no_plugins(project: Path) -> None:
    (project / "mypy.ini").write_text("[mypy]\nplugins =\n", encoding="utf-8")

    assert not settings_of(project).plugins


def test_mypy_is_given_a_copy_of_the_settings_outside_the_project(
    tmp_path: Path, project: Path
) -> None:
    (project / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict = true\nplugins = ["evil"]\n', encoding="utf-8"
    )

    plan = types_plan(tmp_path, project)

    copy = Path(config_of(plan))
    assert copy.parent == tmp_path / "cache" / "mypy"
    assert copy.read_text(encoding="utf-8") == "[mypy]\nstrict = True\n"
    assert plan.config_files == ("pyproject.toml",)
    assert config_of(types_plan(tmp_path, project)) == str(copy)  # the same settings, one file


def test_without_settings_mypy_runs_with_its_defaults(tmp_path: Path, project: Path) -> None:
    plan = types_plan(tmp_path, project)

    assert config_of(plan) == ""
    assert plan.config_files == ()


def test_types_cannot_be_measured_when_the_settings_load_plugins(
    tmp_path: Path, project: Path
) -> None:
    python = adapter(tmp_path)
    assert python.unmeasurable(Operation.TYPES, project) is None

    (project / "mypy.ini").write_text("[mypy]\nstrict = True\n", encoding="utf-8")
    assert python.unmeasurable(Operation.TYPES, project) is None

    (project / "mypy.ini").write_text("[mypy]\nplugins = pydantic.mypy\n", encoding="utf-8")
    assert python.unmeasurable(Operation.LINT, project) is None
    assert python.unmeasurable(Operation.TYPES, project) == (
        "the mypy settings in mypy.ini load plugins, which are code of the repository "
        "the Forja does not run"
    )


@pytest.mark.parametrize(
    "toml",
    [
        'Plugins = ["evil"]\n',  # mypy's reader of INI files takes the name without its case
        '" plugins " = ["evil"]\n',
        'HTML_Report = "out"\n',
    ],
)
def test_an_option_is_known_by_its_name_whatever_its_case(project: Path, toml: str) -> None:
    (project / "pyproject.toml").write_text(f"[tool.mypy]\nstrict = true\n{toml}", encoding="utf-8")

    found = settings_of(project)

    assert found.text(project) == "[mypy]\nstrict = True\n"
    assert found.plugins == ("lugins" in toml)


@pytest.mark.parametrize(
    "toml",
    [
        'python_version = "3.12\\rplugins = evil"\n',  # a line of its own, for a reader of text
        'python_version = "3.12\\u2028plugins = evil"\n',
        '"strict\\nplugins" = "evil"\n',
        '"strict = True\\nplugins" = "evil"\n',
        '"a b" = "c"\n',
        '[[tool.mypy.overrides]]\nmodule = "a]\\n[mypy]\\nplugins = evil\\n[mypy-b"\nx = true\n',
        '[[tool.mypy.overrides]]\nmodule = "a]"\nx = true\n',
    ],
)
def test_settings_that_could_add_a_line_to_the_copy_are_not_copied(
    tmp_path: Path, project: Path, toml: str
) -> None:
    (project / "pyproject.toml").write_text(f"[tool.mypy]\nstrict = true\n{toml}", encoding="utf-8")

    found = settings_of(project)

    assert found.text(project) is None
    assert found.write(project, tmp_path / "cache") is None
    plan = types_plan(tmp_path, project)
    assert config_of(plan) == "" and plan.config_files == ()
    assert adapter(tmp_path).unmeasurable(Operation.TYPES, project) == (
        "the mypy settings in pyproject.toml have a name or a value the Forja cannot copy "
        "without the risk of adding a setting of its own"
    )


def test_a_value_of_several_lines_stays_one_value_in_the_copy(project: Path) -> None:
    (project / "pyproject.toml").write_text(
        '[tool.mypy]\npython_version = "3.12\\n\\nplugins = evil\\n[mypy-x]\\nplugins = evil"\n',
        encoding="utf-8",
    )

    text = settings_of(project).text(project)

    assert text is not None
    reader = configparser.RawConfigParser()
    reader.read_string(text)
    assert reader.sections() == ["mypy"] and reader.options("mypy") == ["python_version"]


def test_two_different_settings_are_two_copies_in_the_same_directory(
    tmp_path: Path, project: Path
) -> None:
    cache = tmp_path / "cache"
    (project / "mypy.ini").write_text("[mypy]\nstrict = True\n", encoding="utf-8")
    first = settings_of(project).write(project, cache)
    (project / "mypy.ini").write_text("[mypy]\nstrict = False\n", encoding="utf-8")
    second = settings_of(project).write(project, cache)

    assert first is not None and second is not None and first != second
    assert first.parent == second.parent
    assert first.read_text(encoding="utf-8") == "[mypy]\nstrict = True\n"
    assert second.read_text(encoding="utf-8") == "[mypy]\nstrict = False\n"


def test_an_override_that_is_no_table_and_one_without_a_module_are_left_out(
    project: Path,
) -> None:
    (project / "pyproject.toml").write_text(
        '[tool.mypy]\nstrict = true\noverrides = ["text", {ignore_errors = true}]\n',
        encoding="utf-8",
    )

    assert settings_of(project).sections == {"mypy": {"strict": "True"}}
