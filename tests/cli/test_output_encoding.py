"""A redirected output in a narrow code page must not stop the run at a character it cannot hold."""

import io
import sys

import pytest

from forja.cli.console import lenient_output


def narrow() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_an_arrow_in_a_cp1252_output_is_replaced_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, err = narrow(), narrow()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    lenient_output()
    out.write("a → b")
    err.write("c → d")
    out.flush()
    err.flush()

    assert out.buffer.getvalue() == b"a ? b"  # type: ignore[attr-defined]
    assert err.buffer.getvalue() == b"c ? d"  # type: ignore[attr-defined]


def test_an_output_that_cannot_be_reconfigured_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Plain:
        def write(self, text: str) -> int:
            return len(text)

    out, err = Plain(), Plain()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    lenient_output()

    assert sys.stdout is out
    assert sys.stderr is err
