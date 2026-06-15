from __future__ import annotations

from itertools import product
from typing import Any, Protocol

from eda.design import Design
from eda.graph import rebuild_graph

# todo
# 1. check_duplicate_drivers
# 2. check_missing_drivers
# 3. check_primary_outputs_driven
# 4. check_gate_pin_count
# 5. check_no_unknown_gate_type
# 6. check_floating_inputs
# 7. check_combinational_loop
# 8. check_unused_wires
# 9. check_dangling_gates

def check_connectivity(design: Design) -> dict:
    """Check missing drivers and duplicate drivers."""
    rebuild_graph(design)
    net_drivers = _collect_net_drivers(design)
    duplicate_drivers = {
        net: drivers
        for net, drivers in sorted(net_drivers.items())
        if len(drivers) > 1
    }

    missing_drivers = []
    for net in design.all_nets():
        if net not in design.drivers and net not in design.inputs:
            missing_drivers.append(net)
    return {
        "ok": len(missing_drivers) == 0 and len(duplicate_drivers) == 0,
        "missing_drivers": sorted(missing_drivers),
        "duplicate_drivers": duplicate_drivers,
    }


def check_fanout(design: Design, max_fanout: int) -> dict:
    """Report nets whose fanout exceeds the given limit."""
    rebuild_graph(design)
    violations = {
        net: len(sinks)
        for net, sinks in design.fanouts.items()
        if len(sinks) > max_fanout
    }
    return {
        "ok": len(violations) == 0,
        "violations": violations,
    }


def check_depth(design: Design, src: str, dst: str, max_allowed_depth: int) -> dict:
    """Check whether the max logic depth from src to dst is within limit."""
    from eda.analysis import max_depth

    depth, path = max_depth(design, src, dst)
    return {
        "ok": depth <= max_allowed_depth,
        "depth": depth,
        "max_allowed_depth": max_allowed_depth,
        "path": path,
    }


def check_equivalence(design: Design, expr: str, target: str) -> dict:
    """Check whether a Boolean expression is equivalent to a target net."""
    engine = _BooleanEngine()
    target_expr = engine.net_expr(design, target)
    user_expr = engine.parse_expr(expr, design)
    return _prove_no_counterexample(engine, target_expr != user_expr)


def check_property(design: Design, target: str, property_text: str) -> dict:
    """Check whether a Boolean property holds for all assignments."""
    if target not in _tokenize_expr(property_text):
        raise ValueError(f'Property must reference target "{target}".')
    engine = _BooleanEngine()
    prop_expr = engine.parse_expr(property_text, design)
    return _prove_no_counterexample(engine, ~prop_expr)


def check_signal_symmetry(design: Design, target: str, input_a: str, input_b: str) -> dict:
    """Check whether target is unchanged when two inputs are swapped."""
    if input_a == input_b:
        raise ValueError("Symmetry check needs two distinct inputs.")
    engine = _BooleanEngine()
    target_expr = engine.net_expr(design, target)
    try:
        return _prove_symmetry_with_z3(target_expr, input_a, input_b)
    except ImportError:
        return _prove_symmetry_by_bruteforce(target_expr, input_a, input_b)


def check_design_equivalence(before: Design, after: Design, outputs: list[str] | None = None) -> dict:
    """Check whether two designs produce the same values on selected outputs."""
    selected_outputs = sorted(outputs if outputs is not None else before.outputs & after.outputs)
    if not selected_outputs:
        return {"ok": True, "engine": "none", "outputs": [], "failures": {}}

    failures: dict[str, dict] = {}
    engines: set[str] = set()
    for output in selected_outputs:
        before_engine = _BooleanEngine()
        after_engine = _BooleanEngine()
        before_expr = before_engine.net_expr(before, output)
        after_expr = after_engine.net_expr(after, output)
        result = _prove_no_counterexample(before_engine, before_expr != after_expr)
        engines.add(result.get("engine", "unknown"))
        if not result.get("ok", False):
            failures[output] = result

    return {
        "ok": len(failures) == 0,
        "engine": ",".join(sorted(engines)) if engines else "none",
        "outputs": selected_outputs,
        "failures": failures,
    }


def _collect_net_drivers(design: Design) -> dict[str, list[str]]:
    """Build a full driver list per net so duplicates are not hidden by graph maps."""
    drivers: dict[str, list[str]] = {}

    def add_driver(net: str, driver: str) -> None:
        drivers.setdefault(net, []).append(driver)

    for net in design.inputs:
        add_driver(net, f"PI:{net}")
    for gate in design.gates.values():
        add_driver(gate.output, f"GATE:{gate.name}")
    for dff in design.dffs.values():
        add_driver(dff.q, f"DFF:{dff.name}")

    return drivers


class _Expr(Protocol):
    def eval(self, values: dict[str, bool]) -> bool:
        ...

    def vars(self) -> set[str]:
        ...

    def z3(self, ctx: dict[str, Any]) -> Any:
        ...

    def __invert__(self) -> "_Expr":
        return _Not(self)

    def __and__(self, other: "_Expr") -> "_Expr":
        return _Binary("&", self, other)

    def __or__(self, other: "_Expr") -> "_Expr":
        return _Binary("|", self, other)

    def __xor__(self, other: "_Expr") -> "_Expr":
        return _Binary("^", self, other)

    def __ne__(self, other: object) -> "_Expr":  # type: ignore[override]
        if not isinstance(other, _ExprNode):
            return NotImplemented
        return _Binary("^", self, other)


class _ExprNode:
    def __invert__(self) -> "_ExprNode":
        return _Not(self)

    def __and__(self, other: "_ExprNode") -> "_ExprNode":
        return _Binary("&", self, other)

    def __or__(self, other: "_ExprNode") -> "_ExprNode":
        return _Binary("|", self, other)

    def __xor__(self, other: "_ExprNode") -> "_ExprNode":
        return _Binary("^", self, other)

    def __ne__(self, other: object) -> "_ExprNode":  # type: ignore[override]
        if not isinstance(other, _ExprNode):
            return NotImplemented
        return _Binary("^", self, other)


class _Var(_ExprNode):
    def __init__(self, name: str) -> None:
        self.name = name

    def eval(self, values: dict[str, bool]) -> bool:
        return values[self.name]

    def vars(self) -> set[str]:
        return {self.name}

    def z3(self, ctx: dict[str, Any]) -> Any:
        z3 = _import_z3()
        ctx.setdefault(self.name, z3.Bool(self.name))
        return ctx[self.name]


class _Const(_ExprNode):
    def __init__(self, value: bool) -> None:
        self.value = value

    def eval(self, values: dict[str, bool]) -> bool:
        del values
        return self.value

    def vars(self) -> set[str]:
        return set()

    def z3(self, ctx: dict[str, Any]) -> Any:
        del ctx
        return _import_z3().BoolVal(self.value)


class _Not(_ExprNode):
    def __init__(self, operand: _ExprNode) -> None:
        self.operand = operand

    def eval(self, values: dict[str, bool]) -> bool:
        return not self.operand.eval(values)

    def vars(self) -> set[str]:
        return self.operand.vars()

    def z3(self, ctx: dict[str, Any]) -> Any:
        return _import_z3().Not(self.operand.z3(ctx))


class _Binary(_ExprNode):
    def __init__(self, op: str, left: _ExprNode, right: _ExprNode) -> None:
        self.op = op
        self.left = left
        self.right = right

    def eval(self, values: dict[str, bool]) -> bool:
        left = self.left.eval(values)
        right = self.right.eval(values)
        if self.op == "&":
            return left and right
        if self.op == "|":
            return left or right
        if self.op == "^":
            return left != right
        if self.op == "->":
            return (not left) or right
        raise ValueError(f"Unknown Boolean operator: {self.op}")

    def vars(self) -> set[str]:
        return self.left.vars() | self.right.vars()

    def z3(self, ctx: dict[str, Any]) -> Any:
        z3 = _import_z3()
        left = self.left.z3(ctx)
        right = self.right.z3(ctx)
        if self.op == "&":
            return z3.And(left, right)
        if self.op == "|":
            return z3.Or(left, right)
        if self.op == "^":
            return left != right
        if self.op == "->":
            return z3.Implies(left, right)
        raise ValueError(f"Unknown Boolean operator: {self.op}")


class _BooleanEngine:
    def __init__(self) -> None:
        self._net_cache: dict[str, _ExprNode] = {}

    def net_expr(self, design: Design, net: str) -> _ExprNode:
        rebuild_graph(design)
        return self._net_expr(design, net, visiting=set())

    def parse_expr(self, text: str, design: Design | None = None) -> _ExprNode:
        resolver = None
        if design is not None:
            rebuild_graph(design)
            resolver = lambda name: self._net_expr(design, name, visiting=set())
        parser = _ExprParser(text, resolver=resolver)
        return parser.parse()

    def _net_expr(self, design: Design, net: str, visiting: set[str]) -> _ExprNode:
        if net in {"1'b1", "1"}:
            return _Const(True)
        if net in {"1'b0", "0"}:
            return _Const(False)
        if net in self._net_cache:
            return self._net_cache[net]
        if net in visiting:
            raise ValueError(f"Combinational loop detected at net {net}.")

        visiting.add(net)
        driver = design.drivers.get(net)
        if driver is None or driver.startswith("PI:") or driver.startswith("DFF:"):
            expr: _ExprNode = _Var(net)
        elif driver.startswith("CONST:"):
            expr = _Const(driver.split(":", 1)[1] in {"1'b1", "1"})
        elif driver.startswith("GATE:"):
            gate = design.gates[driver.split(":", 1)[1]]
            inputs = [self._net_expr(design, item, visiting) for item in gate.inputs]
            expr = _gate_expr(gate.type, inputs)
        else:
            raise ValueError(f"Unsupported driver for {net}: {driver}")
        visiting.remove(net)
        self._net_cache[net] = expr
        return expr


class _ExprParser:
    def __init__(self, text: str, resolver=None) -> None:
        self.tokens = _tokenize_expr(text)
        self.index = 0
        self.resolver = resolver

    def parse(self) -> _ExprNode:
        expr = self._parse_implication()
        if self._peek() is not None:
            raise ValueError(f"Unexpected token in Boolean expression: {self._peek()}")
        return expr

    def _parse_implication(self) -> _ExprNode:
        left = self._parse_or()
        if self._accept("->"):
            right = self._parse_implication()
            return _Binary("->", left, right)
        return left

    def _parse_or(self) -> _ExprNode:
        expr = self._parse_xor()
        while self._accept("|"):
            expr = _Binary("|", expr, self._parse_xor())
        return expr

    def _parse_xor(self) -> _ExprNode:
        expr = self._parse_and()
        while self._accept("^"):
            expr = _Binary("^", expr, self._parse_and())
        return expr

    def _parse_and(self) -> _ExprNode:
        expr = self._parse_unary()
        while self._accept("&"):
            expr = _Binary("&", expr, self._parse_unary())
        return expr

    def _parse_unary(self) -> _ExprNode:
        if self._accept("!") or self._accept("~"):
            return _Not(self._parse_unary())
        if self._accept("("):
            expr = self._parse_implication()
            self._expect(")")
            return expr
        token = self._peek()
        if token is None:
            raise ValueError("Unexpected end of Boolean expression.")
        self.index += 1
        if token in {"1'b1", "1"}:
            return _Const(True)
        if token in {"1'b0", "0"}:
            return _Const(False)
        if self.resolver is not None:
            return self.resolver(token)
        return _Var(token)

    def _accept(self, token: str) -> bool:
        if self._peek() == token:
            self.index += 1
            return True
        return False

    def _expect(self, token: str) -> None:
        if not self._accept(token):
            raise ValueError(f"Expected token {token} in Boolean expression.")

    def _peek(self) -> str | None:
        if self.index >= len(self.tokens):
            return None
        return self.tokens[self.index]


def _gate_expr(gate_type: str, inputs: list[_ExprNode]) -> _ExprNode:
    if gate_type == "buf":
        _expect_input_count(gate_type, inputs, 1)
        return inputs[0]
    if gate_type == "not":
        _expect_input_count(gate_type, inputs, 1)
        return ~inputs[0]
    if len(inputs) < 2:
        raise ValueError(f"Gate type {gate_type} expects at least two inputs.")
    expr = inputs[0]
    for item in inputs[1:]:
        if gate_type == "and":
            expr = expr & item
        elif gate_type == "or":
            expr = expr | item
        elif gate_type == "xor":
            expr = expr ^ item
        elif gate_type == "nand":
            expr = expr & item
        elif gate_type == "nor":
            expr = expr | item
        elif gate_type == "xnor":
            expr = expr ^ item
        else:
            raise ValueError(f"Unsupported gate type for formal check: {gate_type}")
    if gate_type in {"nand", "nor", "xnor"}:
        return ~expr
    return expr


def _expect_input_count(gate_type: str, inputs: list[_ExprNode], count: int) -> None:
    if len(inputs) != count:
        raise ValueError(f"Gate type {gate_type} expects {count} input(s).")


def _prove_no_counterexample(engine: _BooleanEngine, bad_condition: _ExprNode) -> dict:
    try:
        return _prove_with_z3(engine, bad_condition)
    except ImportError:
        return _prove_by_bruteforce(bad_condition)


def _prove_with_z3(engine: _BooleanEngine, bad_condition: _ExprNode) -> dict:
    del engine
    z3 = _import_z3()
    ctx: dict[str, Any] = {}
    solver = z3.Solver()
    solver.add(bad_condition.z3(ctx))
    outcome = solver.check()
    if outcome == z3.unsat:
        return {"ok": True, "engine": "z3", "counterexample": None}
    if outcome == z3.unknown:
        return {"ok": False, "engine": "z3", "counterexample": None, "reason": str(solver.reason_unknown())}
    model = solver.model()
    return {
        "ok": False,
        "engine": "z3",
        "counterexample": {
            name: bool(model.eval(var, model_completion=True))
            for name, var in sorted(ctx.items())
        },
    }


def _prove_by_bruteforce(bad_condition: _ExprNode) -> dict:
    variables = sorted(bad_condition.vars())
    if len(variables) > 12:
        return {
            "ok": False,
            "engine": "bruteforce",
            "counterexample": None,
            "reason": "z3-solver is not installed and brute-force fallback is limited to 12 variables.",
        }
    for values in product([False, True], repeat=len(variables)):
        assignment = dict(zip(variables, values))
        if bad_condition.eval(assignment):
            return {"ok": False, "engine": "bruteforce", "counterexample": assignment}
    return {"ok": True, "engine": "bruteforce", "counterexample": None}


def _prove_symmetry_with_z3(expr: _ExprNode, input_a: str, input_b: str) -> dict:
    z3 = _import_z3()
    ctx: dict[str, Any] = {}
    zexpr = expr.z3(ctx)
    a_var = ctx.setdefault(input_a, z3.Bool(input_a))
    b_var = ctx.setdefault(input_b, z3.Bool(input_b))
    expr_ab = z3.substitute(
        zexpr,
        (a_var, z3.BoolVal(False)),
        (b_var, z3.BoolVal(True)),
    )
    expr_ba = z3.substitute(
        zexpr,
        (a_var, z3.BoolVal(True)),
        (b_var, z3.BoolVal(False)),
    )
    solver = z3.Solver()
    solver.add(expr_ab != expr_ba)
    outcome = solver.check()
    if outcome == z3.unsat:
        return {"ok": True, "engine": "z3", "counterexample": None}
    if outcome == z3.unknown:
        return {"ok": False, "engine": "z3", "counterexample": None, "reason": str(solver.reason_unknown())}
    model = solver.model()
    counterexample = {
        name: bool(model.eval(var, model_completion=True))
        for name, var in sorted(ctx.items())
        if name not in {input_a, input_b}
    }
    counterexample[f"{input_a}/{input_b}"] = "0/1 vs 1/0"
    return {"ok": False, "engine": "z3", "counterexample": counterexample}


def _prove_symmetry_by_bruteforce(expr: _ExprNode, input_a: str, input_b: str) -> dict:
    variables = sorted(expr.vars() - {input_a, input_b})
    if len(variables) > 12:
        return {
            "ok": False,
            "engine": "bruteforce",
            "counterexample": None,
            "reason": "z3-solver is not installed and brute-force fallback is limited to 12 variables.",
        }
    for values in product([False, True], repeat=len(variables)):
        assignment = dict(zip(variables, values))
        first = dict(assignment)
        first[input_a] = False
        first[input_b] = True
        second = dict(assignment)
        second[input_a] = True
        second[input_b] = False
        if expr.eval(first) != expr.eval(second):
            assignment[f"{input_a}/{input_b}"] = "0/1 vs 1/0"
            return {"ok": False, "engine": "bruteforce", "counterexample": assignment}
    return {"ok": True, "engine": "bruteforce", "counterexample": None}


def _tokenize_expr(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if text.startswith("->", index):
            tokens.append("->")
            index += 2
            continue
        if char in "()!~&|^":
            tokens.append(char)
            index += 1
            continue
        if text.startswith("1'b0", index) or text.startswith("1'b1", index):
            tokens.append(text[index : index + 4])
            index += 4
            continue
        match = __import__("re").match(r"[A-Za-z_][A-Za-z0-9_$]*(?:\[[0-9]+\])?|[01]", text[index:])
        if not match:
            raise ValueError(f"Unexpected character in Boolean expression: {char}")
        tokens.append(match.group(0))
        index += len(match.group(0))
    return tokens


def _import_z3() -> Any:
    import z3  # type: ignore

    return z3
