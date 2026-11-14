"""A place in a Python file where one small change can be made."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Site:
    line: int  # 1-based line of the first byte of the token
    start: int  # byte offsets (UTF-8) of the token in the file, end exclusive
    end: int
    original: str
    mutant: str  # empty when the change deletes the token
    category: str
    function: str  # qualified name of the innermost function that holds the site
    # The line its statement starts on. A coverage report may mark only that line of a statement
    # that spans several, so a site counts as covered when either line is (as in the mutator).
    anchor_line: int = 0

    def __post_init__(self) -> None:
        if self.anchor_line == 0:
            object.__setattr__(self, "anchor_line", self.line)

    @property
    def lines(self) -> tuple[int, ...]:
        """The lines whose coverage says a test reaches the site."""
        return (self.line,) if self.anchor_line == self.line else (self.line, self.anchor_line)

    @property
    def description(self) -> str:
        """The change as the mutation report prints it: ``<= -> <`` or ``delete -``."""
        if self.mutant == "":
            return f"delete {self.original}"
        return f"{self.original} -> {self.mutant}"
