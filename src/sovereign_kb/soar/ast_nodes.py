"""AST data structures for the Soar Production Language (`sp { ... }`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union


@dataclass(frozen=True)
class Var:
    """A Soar pattern variable such as `<s>`, `<o>`, or `<block1>`."""

    name: str

    def __repr__(self) -> str:
        return f"<{self.name}>"


Symbol = Union[Var, str, int, float]


@dataclass(frozen=True)
class EqualityTest:
    """Matches when the WME value equals or binds `target`."""

    target: Symbol


@dataclass(frozen=True)
class RelationalTest:
    """Matches when `wme_val <op> resolved(target)` holds (`<>`, `<`, `>`, `<=`, `>=`, `<=>`)."""

    op: str
    target: Symbol


@dataclass(frozen=True)
class DisjunctionTest:
    """Matches when the WME value is in `options` (`<< a b c >>`)."""

    options: tuple[Union[str, int, float], ...]


@dataclass(frozen=True)
class ConjunctiveTest:
    """Matches when all constituent `tests` succeed (`{ <> <b1> <b2> }`)."""

    tests: tuple[Test, ...]


Test = Union[EqualityTest, RelationalTest, DisjunctionTest, ConjunctiveTest]


@dataclass
class AttrValueTest:
    """A single `^attr <value_test> [+] ` (or negated `-^attr <value_test>`) inside a condition."""

    attr: Symbol
    value_test: Test
    negated: bool = False
    acceptable_pref: bool = False


@dataclass
class Condition:
    """A single parenthesized LHS condition pattern `([state] <id> ^attr val ...)`."""

    id_var: Var
    attr_tests: list[AttrValueTest] = field(default_factory=list)
    is_state: bool = False
    negated: bool = False


@dataclass
class RHSFuncCall:
    """An RHS function invocation such as `(+ <x> 1)`, `(min <a> <b>)`, `(write ...)`, or `(halt)`."""

    func_name: str
    args: list[Union[Symbol, RHSFuncCall]] = field(default_factory=list)


RHSValue = Union[Symbol, RHSFuncCall]


@dataclass
class PreferenceSpec:
    """A preference assertion on an RHS action: `+`, `-`, `!`, `~`, `>`, `<`, `=`."""

    pref_type: str  # '+', '-', '!', '~', '>', '<', '='
    referent: RHSValue | None = None


@dataclass
class RHSAction:
    """An RHS WME creation, removal (`-`), or operator preference assertion."""

    id_sym: Symbol
    attr: Symbol
    value: RHSValue
    preferences: list[PreferenceSpec] = field(default_factory=list)
    is_removal: bool = False


@dataclass
class RHSFunctionStandalone:
    """A standalone RHS function call such as `(write (crlf) |Solved!|)` or `(halt)`."""

    call: RHSFuncCall


@dataclass
class Production:
    """A compiled Soar production rule (`sp { ... }`)."""

    name: str
    conditions: list[Condition]
    actions: list[Union[RHSAction, RHSFunctionStandalone]]
    doc: str = ""
    support_flag: str | None = None  # ':i-support', ':o-support', or None (auto-inferred)
    is_chunk: bool = False
    source_text: str = ""
