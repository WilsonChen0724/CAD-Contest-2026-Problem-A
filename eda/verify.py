from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from copy import deepcopy
from itertools import product
from pathlib import Path
from typing import Any, Protocol

from eda.design import Design, Gate
from eda.graph import rebuild_graph
from parser.verilog_writer import write_verilog
from parser.yosys_tools import quote_yosys_path, run_yosys_script


ABC_CEC_TIMEOUT = 60.0
ABC_YOSYS_TIMEOUT = 60.0
ABC_FULL_DESIGN_GATE_LIMIT = 50000

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
    abc_result = _check_boolean_equivalence_with_abc(target_expr, user_expr)
    solver_result = _prove_no_counterexample(engine, target_expr != user_expr)
    if solver_result.get("ok") and abc_result is not None and abc_result.get("ok"):
        return {"ok": True, "engine": "abc+z3", "counterexample": None}
    if not solver_result.get("ok") and solver_result.get("counterexample") is not None:
        return solver_result
    if abc_result is not None and solver_result.get("counterexample") is None:
        return abc_result
    return solver_result


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

    # Preparing a full-design BLIF can dominate the request budget on large
    # netlists. The expression solver works directly on the parsed graph.
    if max(len(before.gates), len(after.gates)) <= ABC_FULL_DESIGN_GATE_LIMIT:
        abc_result = _check_design_equivalence_with_abc(before, after, selected_outputs)
        if abc_result is not None:
            return abc_result

    return _check_design_equivalence_with_expr_solver(before, after, selected_outputs)


def _check_design_equivalence_with_expr_solver(before: Design, after: Design, selected_outputs: list[str]) -> dict:
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


def _check_design_equivalence_with_abc(before: Design, after: Design, selected_outputs: list[str]) -> dict | None:
    try:
        before_view = _abc_combinational_boundary_view(before, selected_outputs, "before")
        after_view = _abc_combinational_boundary_view(after, selected_outputs, "after")
        completed = _run_abc_cec(before_view, after_view)
    except Exception:
        return None

    text = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    if completed.returncode == 0 and _abc_reports_equivalent(text):
        return {"ok": True, "engine": "abc", "outputs": selected_outputs, "failures": {}}
    if _abc_reports_not_equivalent(text):
        return {
            "ok": False,
            "engine": "abc",
            "outputs": selected_outputs,
            "failures": {
                output: {
                    "ok": False,
                    "engine": "abc",
                    "counterexample": None,
                    "reason": "ABC CEC reported a mismatch.",
                }
                for output in selected_outputs
            },
            "reason": _trim_tool_output(text),
        }
    return None


def _abc_combinational_boundary_view(design: Design, selected_outputs: list[str], label: str) -> Design:
    """Build the same combinational-boundary model used by the expression checker."""
    view = deepcopy(design)
    view.module_name = f"abc_{label}"
    selected = set(selected_outputs)
    state_qs = {dff.q for dff in view.dffs.values()}
    view.dffs = {}

    for q in sorted(state_qs):
        if q in selected:
            state_input = _unique_abc_state_input(view, q)
            _replace_gate_input_net(view, q, state_input)
            view.inputs.add(state_input)
            view.wires.discard(state_input)
            if not _net_has_gate_driver(view, q):
                gate_name = view.make_unique_gate_name(f"ABC_state_{q}_buf")
                view.add_gate(Gate(name=gate_name, type="buf", inputs=[state_input], output=q))
        else:
            view.inputs.add(q)
            view.wires.discard(q)

    view.outputs = selected
    rebuild_graph(view)
    return view


def _unique_abc_state_input(design: Design, net: str) -> str:
    base = "__abc_state_" + "".join(char if char.isalnum() or char in "_$" else "_" for char in net)
    candidate = base
    index = 0
    used = design.all_nets() | set(design.gates) | set(design.dffs)
    while candidate in used:
        index += 1
        candidate = f"{base}_{index}"
    return candidate


def _replace_gate_input_net(design: Design, old: str, new: str) -> None:
    for gate in design.gates.values():
        gate.inputs = [new if item == old else item for item in gate.inputs]


def _net_has_gate_driver(design: Design, net: str) -> bool:
    return any(gate.output == net for gate in design.gates.values())


def _run_abc_cec(before: Design, after: Design) -> subprocess.CompletedProcess[str]:
    abc, env = _resolve_abc()
    with tempfile.TemporaryDirectory() as tmp:
        work_dir = Path(tmp)
        before_v = work_dir / "before.v"
        after_v = work_dir / "after.v"
        before_blif = work_dir / "before.blif"
        after_blif = work_dir / "after.blif"
        write_verilog(before, before_v)
        write_verilog(after, after_v)
        _write_abc_blif(before_v, before.module_name, before_blif)
        _write_abc_blif(after_v, after.module_name, after_blif)
        command = [str(abc), "-c", f"cec {before_blif.as_posix()} {after_blif.as_posix()}"]
        try:
            return subprocess.run(
                command,
                cwd=work_dir,
                env=env,
                text=True,
                capture_output=True,
                timeout=ABC_CEC_TIMEOUT,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, str) else ""
            stderr = exc.stderr if isinstance(exc.stderr, str) else ""
            message = f"ABC CEC timed out after {ABC_CEC_TIMEOUT:.1f} seconds."
            return subprocess.CompletedProcess(command, 124, stdout, (stderr + "\n" + message).strip())


def _write_abc_blif(verilog_path: Path, top: str, blif_path: Path) -> None:
    script = "\n".join(
        [
            f"read_verilog -noopt {quote_yosys_path(verilog_path)}",
            f"hierarchy -check -top {top}",
            "proc",
            "opt",
            "techmap",
            "opt",
            f"write_blif {quote_yosys_path(blif_path)}",
        ]
    )
    completed = run_yosys_script(script, timeout=ABC_YOSYS_TIMEOUT)
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(f"Yosys failed to prepare ABC BLIF: {message}")


def _resolve_abc() -> tuple[Path | str, dict[str, str]]:
    env = os.environ.copy()
    repo_root = Path(__file__).resolve().parents[1]
    suite_root = repo_root / "third_party" / "yosys" / "oss-cad-suite"
    bin_dir = suite_root / "bin"
    system = platform.system().lower()

    candidates = ["yosys-abc.exe", "abc.exe"] if system == "windows" else ["yosys-abc", "abc"]
    for name in candidates:
        local_abc = bin_dir / name
        if local_abc.exists():
            env = env.copy()
            env["PATH"] = os.pathsep.join([str(bin_dir), env.get("PATH", "")])
            if system == "windows":
                env["PATH"] = os.pathsep.join([str(suite_root / "lib"), env["PATH"]])
                env.setdefault("YOSYSHQ_ROOT", str(suite_root) + os.sep)
            return local_abc, env

    for name in candidates:
        system_abc = shutil.which(name)
        if system_abc:
            return system_abc, env

    raise RuntimeError("ABC was not found. Install OSS CAD Suite or add yosys-abc/abc to PATH.")


def _abc_reports_equivalent(text: str) -> bool:
    lowered = text.lower()
    return "networks are equivalent" in lowered or "circuits are equivalent" in lowered


def _abc_reports_not_equivalent(text: str) -> bool:
    lowered = text.lower()
    mismatch_markers = [
        "networks are not equivalent",
        "circuits are not equivalent",
        "not equivalent",
        "not equiv",
        "cex",
        "counter-example",
        "counterexample",
    ]
    return any(marker in lowered for marker in mismatch_markers) and not _abc_reports_equivalent(text)


def _trim_tool_output(text: str, limit: int = 800) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def _check_boolean_equivalence_with_abc(left: "_ExprNode", right: "_ExprNode") -> dict | None:
    try:
        common_inputs = left.vars() | right.vars()
        output = _abc_output_name(common_inputs)
        left_design = _expr_to_abc_design(left, output, "expr_left", inputs=common_inputs)
        right_design = _expr_to_abc_design(right, output, "expr_right", inputs=common_inputs)
        completed = _run_abc_cec(left_design, right_design)
    except Exception:
        return None

    text = "\n".join(part for part in [completed.stdout, completed.stderr] if part).strip()
    if completed.returncode == 0 and _abc_reports_equivalent(text):
        return {"ok": True, "engine": "abc", "counterexample": None}
    if _abc_reports_not_equivalent(text):
        return {
            "ok": False,
            "engine": "abc",
            "counterexample": None,
            "reason": _trim_tool_output(text) or "ABC CEC reported a mismatch.",
        }
    return None


def _expr_to_abc_design(
    expr: "_ExprNode",
    output: str,
    module_name: str,
    *,
    inputs: set[str] | None = None,
) -> Design:
    design = Design(
        module_name=module_name,
        inputs=set(expr.vars()) if inputs is None else set(inputs),
        outputs={output},
    )
    counter = [0]
    expr_net = _emit_expr_node(design, expr, counter, memo={})
    if expr_net != output:
        gate_name = design.make_unique_gate_name("ABC_expr_out")
        design.add_gate(Gate(name=gate_name, type="buf", inputs=[expr_net], output=output))
    rebuild_graph(design)
    return design


def _emit_expr_node(design: Design, expr: "_ExprNode", counter: list[int], memo: dict[int, str]) -> str:
    if isinstance(expr, _Var):
        return expr.name
    if isinstance(expr, _Const):
        return "1'b1" if expr.value else "1'b0"
    expr_id = id(expr)
    if expr_id in memo:
        return memo[expr_id]
    if isinstance(expr, _Not):
        input_net = _emit_expr_node(design, expr.operand, counter, memo)
        output_net = _add_expr_gate(design, "not", [input_net], counter)
        memo[expr_id] = output_net
        return output_net
    if isinstance(expr, _Binary):
        if expr.op == "->":
            left_net = _emit_expr_node(design, expr.left, counter, memo)
            right_net = _emit_expr_node(design, expr.right, counter, memo)
            not_left = _add_expr_gate(design, "not", [left_net], counter)
            output_net = _add_expr_gate(design, "or", [not_left, right_net], counter)
            memo[expr_id] = output_net
            return output_net
        gate_type = {"&": "and", "|": "or", "^": "xor"}.get(expr.op)
        if gate_type is None:
            raise ValueError(f"Unsupported Boolean operator for ABC: {expr.op}")
        left_net = _emit_expr_node(design, expr.left, counter, memo)
        right_net = _emit_expr_node(design, expr.right, counter, memo)
        output_net = _add_expr_gate(design, gate_type, [left_net, right_net], counter)
        memo[expr_id] = output_net
        return output_net
    raise ValueError(f"Unsupported Boolean expression node: {type(expr).__name__}")


def _add_expr_gate(design: Design, gate_type: str, inputs: list[str], counter: list[int]) -> str:
    counter[0] += 1
    output = design.make_unique_wire_name(f"__abc_expr_n{counter[0]}")
    gate_name = design.make_unique_gate_name(f"ABC_expr_{counter[0]}")
    design.add_gate(Gate(name=gate_name, type=gate_type, inputs=inputs, output=output))
    return output


def _abc_output_name(used: set[str]) -> str:
    output = "__abc_equiv_out"
    index = 0
    while output in used:
        index += 1
        output = f"__abc_equiv_out_{index}"
    return output


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
