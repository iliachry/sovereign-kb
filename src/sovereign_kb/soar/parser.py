"""Recursive-descent tokenizer and parser for the Soar Production Language (`sp { ... }`)."""

from __future__ import annotations

from typing import Union

from .ast_nodes import (
    AttrValueTest,
    Condition,
    ConjunctiveTest,
    DisjunctionTest,
    EqualityTest,
    PreferenceSpec,
    Production,
    RelationalTest,
    RHSAction,
    RHSFuncCall,
    RHSFunctionStandalone,
    RHSValue,
    Symbol,
    Test,
    Var,
)

RELATIONAL_OPS = {"<>", "<=", ">=", "<=>", "<", ">", "="}
PREF_CHARS = {"+", "-", "!", "~", ">", "<", "="}


class SoarSyntaxError(ValueError):
    """Raised when `.soar` source code contains invalid production syntax."""


def tokenize_soar(source: str) -> list[str]:
    """Tokenize `.soar` source into a flat list of lexical tokens."""
    tokens: list[str] = []
    i = 0
    n = len(source)

    while i < n:
        ch = source[i]
        # Whitespace
        if ch.isspace():
            i += 1
            continue
        # Line comment
        if ch == "#":
            while i < n and source[i] != "\n":
                i += 1
            continue
        # Double-quoted string
        if ch == '"':
            start = i
            i += 1
            while i < n and source[i] != '"':
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            i += 1  # closing quote
            tokens.append(source[start:i])
            continue
        # Pipe-delimited string |...|
        if ch == "|":
            start = i
            i += 1
            while i < n and source[i] != "|":
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                else:
                    i += 1
            i += 1
            tokens.append(source[start:i])
            continue
        # Arrow -->
        if source.startswith("-->", i):
            tokens.append("-->")
            i += 3
            continue
        # Multi-character operators & delimiters
        if source.startswith("<=>", i):
            tokens.append("<=>")
            i += 3
            continue
        if source[i : i + 2] in ("<<", ">>", "<>", "<=", ">="):
            tokens.append(source[i : i + 2])
            i += 2
            continue
        # Variable <var>
        if ch == "<":
            # Check if it's a variable <name> vs relational '<'
            end = i + 1
            while end < n and (source[end].isalnum() or source[end] in ("-", "_", "*")):
                end += 1
            if end < n and source[end] == ">" and end > i + 1:
                tokens.append(source[i : end + 1])
                i = end + 1
                continue
            tokens.append("<")
            i += 1
            continue
        # Single-char delimiters
        if ch in "{}()":
            tokens.append(ch)
            i += 1
            continue
        # Negation prefix `-(` or `-^`
        if ch == "-" and i + 1 < n and source[i + 1] in ("(", "^"):
            tokens.append("-")
            i += 1
            continue
        # Caret attribute prefix `^attr`
        if ch == "^":
            end = i + 1
            while end < n and not source[end].isspace() and source[end] not in "{}()":
                end += 1
            tokens.append(source[i:end])
            i = end
            continue
        # Atom / symbol / number / preference
        end = i
        while end < n and not source[end].isspace() and source[end] not in '{}()"|':
            if source.startswith("-->", end):
                break
            end += 1
        tokens.append(source[i:end])
        i = end

    return tokens


def _parse_atom(tok: str) -> Symbol:
    """Convert a lexical token into a `Var`, `int`, `float`, or `str`."""
    if tok.startswith("<") and tok.endswith(">") and len(tok) > 2:
        return Var(tok[1:-1])
    if (tok.startswith('"') and tok.endswith('"')) or (tok.startswith("|") and tok.endswith("|")):
        return tok[1:-1]
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    return tok


class SoarParser:
    """Parser for Soar production files (`sp { ... }`)."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.tokens = tokenize_soar(source)
        self.pos = 0

    def peek(self, offset: int = 0) -> str | None:
        idx = self.pos + offset
        if 0 <= idx < len(self.tokens):
            return self.tokens[idx]
        return None

    def consume(self, expected: str | None = None) -> str:
        tok = self.peek()
        if tok is None:
            raise SoarSyntaxError(f"Unexpected end of input (expected {expected!r})")
        if expected is not None and tok != expected:
            raise SoarSyntaxError(f"Expected {expected!r}, got {tok!r} near token #{self.pos}")
        self.pos += 1
        return tok

    def parse_all(self) -> list[Production]:
        productions: list[Production] = []
        while self.peek() is not None:
            tok = self.peek()
            if tok == "sp":
                productions.append(self.parse_production())
            else:
                raise SoarSyntaxError(f"Expected 'sp' at top level, got {tok!r}")
        return productions

    def parse_production(self) -> Production:
        self.consume("sp")
        self.consume("{")
        name = self.consume()
        doc = ""
        support_flag: str | None = None
        is_chunk = name.startswith("chunk*")

        # Optional docstring and flags (:o-support, :i-support, :chunk)
        while self.peek() is not None:
            nxt = self.peek()
            assert nxt is not None
            if nxt.startswith('"') or nxt.startswith("|"):
                doc = str(_parse_atom(self.consume()))
            elif nxt.startswith(":"):
                flag = self.consume()
                if flag in (":o-support", ":i-support"):
                    support_flag = flag
                elif flag == ":chunk":
                    is_chunk = True
            else:
                break

        conditions: list[Condition] = []
        while self.peek() is not None and self.peek() != "-->":
            conditions.extend(self.parse_condition_expanded())

        self.consume("-->")

        actions: list[Union[RHSAction, RHSFunctionStandalone]] = []
        while self.peek() is not None and self.peek() != "}":
            actions.extend(self.parse_rhs_item())

        self.consume("}")
        prod = Production(
            name=name,
            conditions=conditions,
            actions=actions,
            doc=doc,
            support_flag=support_flag,
            is_chunk=is_chunk,
        )
        prod.source_text = format_production(prod)
        return prod

    def parse_condition(self) -> Condition:
        negated = False
        if self.peek() == "-":
            self.consume("-")
            negated = True

        self.consume("(")
        is_state = False
        if self.peek() == "state":
            self.consume("state")
            is_state = True

        id_tok = self.consume()
        id_sym = _parse_atom(id_tok)
        if not isinstance(id_sym, Var):
            id_sym = Var(str(id_sym))

        attr_tests: list[AttrValueTest] = []

        # Support standalone relational test between two identifiers: (<b1> <> <b2>)
        if self.peek() in RELATIONAL_OPS:
            op = self.consume()
            target = _parse_atom(self.consume())
            attr_tests.append(
                AttrValueTest(
                    attr="__self__",
                    value_test=RelationalTest(op=op, target=target),
                    negated=False,
                )
            )

        while self.peek() is not None and self.peek() != ")":
            attr_neg = False
            if self.peek() == "-":
                self.consume("-")
                attr_neg = True

            attr_tok = self.consume()
            if not attr_tok.startswith("^"):
                raise SoarSyntaxError(f"Expected attribute starting with '^' inside condition, got {attr_tok!r}")
            attr_raw = attr_tok[1:]
            attr_sym = _parse_atom(attr_raw)

            # Parse value test (or empty attribute existence check before next ^attr or `)`)
            acceptable_pref = False
            if self.peek() == ")" or (self.peek() or "").startswith("^") or self.peek() == "-":
                val_test: Test = EqualityTest(Var(f"_anon_{self.pos}"))
            else:
                val_test = self.parse_value_test()
                if self.peek() == "+":
                    self.consume("+")
                    acceptable_pref = True
                # Support inline trailing relational predicate after variable binding, e.g. `^contents <c> < 5`
                extra_tests: list[Test] = [val_test]
                while self.peek() in RELATIONAL_OPS:
                    op = self.consume()
                    rhs_target = _parse_atom(self.consume())
                    extra_tests.append(RelationalTest(op=op, target=rhs_target))
                if len(extra_tests) > 1:
                    val_test = ConjunctiveTest(tuple(extra_tests))

            attr_tests.append(
                AttrValueTest(
                    attr=attr_sym,
                    value_test=val_test,
                    negated=attr_neg,
                    acceptable_pref=acceptable_pref,
                )
            )

        self.consume(")")
        return Condition(id_var=id_sym, attr_tests=attr_tests, is_state=is_state, negated=negated)

    def parse_condition_expanded(self) -> list[Condition]:
        """Parse a condition and expand any dot-notation attributes (e.g. `^io.input-link.val`) into chained conditions."""
        base = self.parse_condition()
        extra_conds: list[Condition] = []
        new_attr_tests: list[AttrValueTest] = []

        for at in base.attr_tests:
            if isinstance(at.attr, str) and "." in at.attr:
                parts = at.attr.split(".")
                cur_var = Var(f"_dot_{self.pos}_{parts[0]}")
                new_attr_tests.append(
                    AttrValueTest(
                        attr=parts[0],
                        value_test=EqualityTest(cur_var),
                        negated=False,
                    )
                )
                for mid in parts[1:-1]:
                    nxt_var = Var(f"_dot_{self.pos}_{mid}")
                    extra_conds.append(
                        Condition(
                            id_var=cur_var,
                            attr_tests=[AttrValueTest(attr=mid, value_test=EqualityTest(nxt_var))],
                            is_state=False,
                            negated=False,
                        )
                    )
                    cur_var = nxt_var
                extra_conds.append(
                    Condition(
                        id_var=cur_var,
                        attr_tests=[
                            AttrValueTest(
                                attr=parts[-1],
                                value_test=at.value_test,
                                negated=at.negated,
                                acceptable_pref=at.acceptable_pref,
                            )
                        ],
                        is_state=False,
                        negated=base.negated,
                    )
                )
            else:
                new_attr_tests.append(at)

        base.attr_tests = new_attr_tests
        if base.negated and extra_conds:
            base.negated = False
        return [base] + extra_conds

    def parse_value_test(self) -> Test:
        tok = self.peek()
        if tok == "{":
            self.consume("{")
            subtests: list[Test] = []
            while self.peek() is not None and self.peek() != "}":
                subtests.append(self.parse_single_test())
            self.consume("}")
            return ConjunctiveTest(tuple(subtests))
        return self.parse_single_test()

    def parse_single_test(self) -> Test:
        tok = self.peek()
        if tok == "<<":
            self.consume("<<")
            opts: list[Union[str, int, float]] = []
            while self.peek() is not None and self.peek() != ">>":
                atom = _parse_atom(self.consume())
                if isinstance(atom, Var):
                    opts.append(atom.name)
                else:
                    opts.append(atom)
            self.consume(">>")
            return DisjunctionTest(tuple(opts))
        if tok in RELATIONAL_OPS:
            op = self.consume()
            target = _parse_atom(self.consume())
            if op == "=":
                return EqualityTest(target=target)
            return RelationalTest(op=op, target=target)
        target = _parse_atom(self.consume())
        return EqualityTest(target=target)

    def parse_rhs_item(self) -> list[Union[RHSAction, RHSFunctionStandalone]]:
        self.consume("(")
        first = self.peek()
        if first in ("write", "halt", "crlf"):
            call = self._parse_func_body()
            self.consume(")")
            return [RHSFunctionStandalone(call=call)]

        id_sym = _parse_atom(self.consume())
        actions: list[Union[RHSAction, RHSFunctionStandalone]] = []

        while self.peek() is not None and self.peek() != ")":
            attr_tok = self.consume()
            if not attr_tok.startswith("^"):
                raise SoarSyntaxError(f"Expected '^attr' in RHS action, got {attr_tok!r}")
            attr_sym = _parse_atom(attr_tok[1:])
            rhs_val = self.parse_rhs_value()

            prefs: list[PreferenceSpec] = []
            is_removal = False

            while self.peek() is not None and self.peek() != ")" and not (self.peek() or "").startswith("^"):
                ptok = self.consume()
                if ptok not in PREF_CHARS:
                    raise SoarSyntaxError(f"Unexpected preference token {ptok!r} in RHS action")
                referent: RHSValue | None = None
                if ptok in (">", "<", "="):
                    nxt = self.peek()
                    if nxt is not None and nxt != ")" and not nxt.startswith("^") and nxt not in PREF_CHARS:
                        referent = self.parse_rhs_value()
                if ptok == "-" and not prefs:
                    is_removal = True
                prefs.append(PreferenceSpec(pref_type=ptok, referent=referent))

            if not prefs:
                prefs.append(PreferenceSpec(pref_type="+"))

            actions.append(
                RHSAction(
                    id_sym=id_sym,
                    attr=attr_sym,
                    value=rhs_val,
                    preferences=prefs,
                    is_removal=is_removal,
                )
            )

        self.consume(")")
        return actions

    def parse_rhs_value(self) -> RHSValue:
        if self.peek() == "(":
            self.consume("(")
            call = self._parse_func_body()
            self.consume(")")
            return call
        return _parse_atom(self.consume())

    def _parse_func_body(self) -> RHSFuncCall:
        func_name = self.consume()
        args: list[Union[Symbol, RHSFuncCall]] = []
        while self.peek() is not None and self.peek() != ")":
            if self.peek() == "(":
                self.consume("(")
                args.append(self._parse_func_body())
                self.consume(")")
            else:
                args.append(_parse_atom(self.consume()))
        return RHSFuncCall(func_name=func_name, args=args)


def _needs_pipe_quotes(s: str) -> bool:
    if not s or any(ch.isspace() or ch in '{}()^"|<>' for ch in s):
        return True
    try:
        int(s)
        return True
    except ValueError:
        pass
    try:
        float(s)
        return True
    except ValueError:
        pass
    return False


def _fmt_sym(sym: Symbol | RHSFuncCall) -> str:
    if isinstance(sym, Var):
        return f"<{sym.name}>"
    if isinstance(sym, RHSFuncCall):
        inner = " ".join(_fmt_sym(a) for a in sym.args)
        return f"({sym.func_name}{(' ' + inner) if inner else ''})"
    if isinstance(sym, str) and _needs_pipe_quotes(sym):
        return f"|{sym}|"
    return str(sym)


def _fmt_test(t: Test) -> str:
    if isinstance(t, EqualityTest):
        return _fmt_sym(t.target)
    if isinstance(t, RelationalTest):
        return f"{t.op} {_fmt_sym(t.target)}"
    if isinstance(t, DisjunctionTest):
        opts = " ".join(str(o) for o in t.options)
        return f"<< {opts} >>"
    if isinstance(t, ConjunctiveTest):
        inner = " ".join(_fmt_test(sub) for sub in t.tests)
        return f"{{ {inner} }}"
    return str(t)


def format_production(prod: Production) -> str:
    """Format a `Production` AST back into canonical `.soar` source code."""
    lines = [f"sp {{{prod.name}"]
    if prod.doc:
        lines.append(f'    "{prod.doc}"')
    if prod.is_chunk:
        lines.append("    :chunk")
    if prod.support_flag:
        lines.append(f"    {prod.support_flag}")
    for cond in prod.conditions:
        neg_prefix = "-" if cond.negated else ""
        state_prefix = "state " if cond.is_state else ""
        if len(cond.attr_tests) == 1 and cond.attr_tests[0].attr == "__self__":
            lines.append(
                f"    {neg_prefix}({state_prefix}{_fmt_sym(cond.id_var)} {_fmt_test(cond.attr_tests[0].value_test)})"
            )
            continue
        attrs_str = " ".join(
            f"{'-' if at.negated else ''}^{_fmt_sym(at.attr)} {_fmt_test(at.value_test)}{' +' if at.acceptable_pref else ''}"
            for at in cond.attr_tests
        )
        lines.append(f"    {neg_prefix}({state_prefix}{_fmt_sym(cond.id_var)} {attrs_str})")
    lines.append("-->")
    for act in prod.actions:
        if isinstance(act, RHSFunctionStandalone):
            lines.append(f"    {_fmt_sym(act.call)}")
        else:
            prefs_str = " ".join(
                f"{p.pref_type}{(' ' + _fmt_sym(p.referent)) if p.referent is not None else ''}"
                for p in act.preferences
            )
            lines.append(f"    ({_fmt_sym(act.id_sym)} ^{_fmt_sym(act.attr)} {_fmt_sym(act.value)} {prefs_str})")
    lines.append("}")
    return "\n".join(lines)


def parse_soar(source: str) -> list[Production]:
    """Parse `.soar` text into a list of `Production` objects."""
    return SoarParser(source).parse_all()
