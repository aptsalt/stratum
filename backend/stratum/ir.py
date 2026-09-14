"""Pure graph lowering: GraphSpec → IR, validation, shared helpers and the mock text engine.

No ADK imports on purpose — this module (with spec, script, nl, codegen, chat, sim, web)
also runs in the browser under Pyodide for the GitHub Pages demo.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .spec import AgentNode, Diagnostic, GraphSpec, Layer, validate_spec

Role = Literal["agent", "code", "human_ask", "human_decide", "router_fn", "loop_gate", "join"]
Branch = tuple[str, str] | None  # (router name, route label)

APPROVE_WORDS = ("approve", "approved", "yes", "y", "ok", "ship", "lgtm")


class CompileError(ValueError):
    pass


@dataclass
class IRNode:
    name: str
    role: Role
    spec: AgentNode | None = None
    layer: Layer | None = None
    internal: bool = False
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class IREdge:
    src: str  # "START" or IR node name
    dst: str
    route: str | None = None


@dataclass
class IR:
    nodes: dict[str, IRNode]
    edges: list[IREdge]

    def to_json(self) -> dict[str, Any]:
        return {
            "nodes": [{"name": n.name, "role": n.role, "internal": n.internal,
                       "sourceId": n.spec.id if n.spec else None} for n in self.nodes.values()],
            "edges": [{"src": e.src, "dst": e.dst, "route": e.route} for e in self.edges],
        }


@dataclass
class _Pred:
    src: str
    route: str | None
    branch: Branch = None


# ─────────────────────────────── lowering ───────────────────────────────

def lower(spec: GraphSpec) -> IR:
    nodes: dict[str, IRNode] = {}
    edges: list[IREdge] = []
    exit_of: dict[str, _Pred] = {}
    prev_exits: list[_Pred] = []
    pending_routes: dict[str, _Pred] = {}

    def add(n: IRNode) -> None:
        nodes[n.name] = n

    def connect(preds: list[_Pred], target: str) -> Branch:
        """Wire preds → target. Returns the branch the target inherits."""
        if not preds:
            edges.append(IREdge("START", target))
            return None
        if len(preds) == 1:
            p = preds[0]
            edges.append(IREdge(p.src, target, p.route))
            return p.branch

        branches = {p.branch for p in preds}
        if None not in branches and len({b[0] for b in branches}) == 1:  # type: ignore[index]
            labels = [b[1] for b in branches]  # type: ignore[index]
            if len(branches) == 1:  # all on the same branch → they all fire → join
                return _join(preds, target, next(iter(branches)))
            if len(labels) == len(preds):  # one pred per exclusive branch → converge
                for p in preds:
                    edges.append(IREdge(p.src, target, p.route))
                return None
            raise CompileError(f"'{target}' mixes parallel and exclusive branch inputs.")
        if len(branches) > 1 and any(b is not None for b in branches):
            raise CompileError(
                f"'{target}' joins a router branch with nodes that always run — the join would wait forever."
            )
        return _join(preds, target, None)

    def _join(preds: list[_Pred], target: str, branch: Branch) -> Branch:
        j = f"join_{target}"
        add(IRNode(j, "join", internal=True))
        for p in preds:
            edges.append(IREdge(p.src, j, p.route))
        edges.append(IREdge(j, target))
        return branch

    def preds_for(n: AgentNode, default: list[_Pred]) -> list[_Pred]:
        if n.id in pending_routes:
            return [pending_routes[n.id]]
        if n.depends_on is None:
            return default
        try:
            return [exit_of[d] for d in n.depends_on]
        except KeyError as e:
            raise CompileError(f"'{n.name}' depends on an unknown node.") from e

    def context_keys(preds: list[_Pred]) -> list[str]:
        keys = [p.src for p in preds if nodes.get(p.src) and not nodes[p.src].internal]
        return keys or ["user_input"]

    for layer in spec.layers:
        default = prev_exits
        routes_in = pending_routes
        if layer.kind == "stage":
            cur: list[_Pred] = []
            for n in layer.nodes:
                add(IRNode(n.name, "code" if n.kind == "code" else "agent", n, layer))
                branch = connect(preds_for(n, default), n.name)
                exit_of[n.id] = _Pred(n.name, None, branch)
                cur.append(exit_of[n.id])
            prev_exits = cur

        elif layer.kind == "router":
            n = layer.nodes[0]
            preds = preds_for(n, default)
            add(IRNode(n.name, "agent", n, layer, extra={"router": True}))
            branch = connect(preds, n.name)
            if branch is not None:
                raise CompileError("Nested routers are not supported yet — flatten into one router.")
            rf = f"route_{n.name}"
            add(IRNode(rf, "router_fn", n, layer, internal=True, extra={
                "labels": [r.label for r in n.routes], "context": context_keys(preds)}))
            edges.append(IREdge(n.name, rf))
            exit_of[n.id] = _Pred(n.name, None)
            pending_routes = {r.target: _Pred(rf, r.label, (n.name, r.label)) for r in n.routes}
            prev_exits = []
            continue

        elif layer.kind == "loop":
            gen, critic = layer.nodes
            gate = f"loop_{gen.name}"
            add(IRNode(gen.name, "agent", gen, layer, extra={"generator": True}))
            add(IRNode(critic.name, "agent", critic, layer, extra={"critic": True}))
            add(IRNode(gate, "loop_gate", None, layer, internal=True, extra={
                "generator": gen.name, "max": layer.max_iterations}))
            branch = connect(preds_for(gen, default), gen.name)
            edges += [IREdge(gen.name, critic.name), IREdge(critic.name, gate),
                      IREdge(gate, gen.name, "revise")]
            exit_of[gen.id] = exit_of[critic.id] = _Pred(gate, "done", branch)
            prev_exits = [exit_of[gen.id]]

        elif layer.kind == "gate":
            h = layer.nodes[0]
            decide = f"decide_{h.name}"
            add(IRNode(h.name, "human_ask", h, layer))
            add(IRNode(decide, "human_decide", h, layer, internal=True, extra={"ask": h.name}))
            branch = connect(preds_for(h, default), h.name)
            edges.append(IREdge(h.name, decide))
            exit_of[h.id] = _Pred(decide, "approved", branch)
            prev_exits = [exit_of[h.id]]

        if routes_in is pending_routes:
            pending_routes = {}

    return IR(nodes, edges)


def group_edges(ir: IR) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """Plain edges grouped by source (fan-out tuples) and routed edges as {label: dsts}."""
    plain: dict[str, list[str]] = {}
    routed: dict[str, dict[str, list[str]]] = {}
    for e in ir.edges:
        if e.route is None:
            plain.setdefault(e.src, []).append(e.dst)
        else:
            routed.setdefault(e.src, {}).setdefault(e.route, []).append(e.dst)
    return plain, routed


def diagnose(spec: GraphSpec) -> list[Diagnostic]:
    """Validation rules + whatever lowering rejects (e.g. joins that would deadlock)."""
    diags = validate_spec(spec)
    if not any(d.level == "error" for d in diags):
        try:
            lower(spec)
        except CompileError as e:
            diags.append(Diagnostic(level="error", message=str(e)))
    return diags


def wf_name(spec: GraphSpec) -> str:
    return re.sub(r"[^a-z0-9_]", "_", spec.name.lower()).strip("_") or "stratum_graph"


# ─────────────────────────────── shared helpers ───────────────────────────────

def text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    parts = getattr(value, "parts", None)
    if parts:
        return "".join(p.text or "" for p in parts if getattr(p, "text", None))
    if isinstance(value, (dict, list)):
        return json.dumps(value, default=str, indent=1)
    return str(value)


def pick_route(text: str, labels: list[str]) -> str:
    low = text.lower()
    hits = [(low.find(lbl.lower()), lbl) for lbl in labels if lbl.lower() in low]
    return min(hits)[1] if hits else labels[0]


def is_approved(response: Any) -> bool:
    if isinstance(response, dict):
        response = response.get("result", response.get("decision", ""))
    return text_of(response).strip().lower().split(" ")[0] in APPROVE_WORDS


def critic_passed(text: str) -> bool:
    return text.strip().upper().startswith("PASS")


def instruction_for(n: AgentNode, layer: Layer) -> str:
    base = n.instruction.strip() or f"You are {n.name.replace('_', ' ')}."
    if layer.kind == "router":
        opts = "\n".join(f"- {r.label}: {r.when}" for r in n.routes)
        return f"{base}\n\nReply with exactly one label on the first line, then one sentence why.\n{opts}"
    if layer.kind == "loop" and layer.nodes and layer.nodes[-1].id == n.id:
        return f"{base}\n\nStart your reply with PASS if the draft meets the bar, otherwise REVISE: and concrete feedback."
    if layer.kind == "loop":
        return f"{base}\n\nIf the input contains feedback on a previous draft, revise that draft."
    return base


def code_output(n: AgentNode, node_input: Any) -> Any:
    """What a zero-token code node produces: markdown merge of its inputs, or pass-through."""
    if n.op == "passthrough":
        return node_input
    if isinstance(node_input, dict):
        return "\n\n".join(f"## {k}\n{text_of(v)}" for k, v in node_input.items())
    return text_of(node_input)


# ─────────────────────────────── mock text engine ───────────────────────────────

def seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest()[:8], 16)


def mock_latency(name: str) -> float:
    return 0.45 + (seed(name) % 900) / 1000


def _mock_router_label(n: AgentNode, user_input: str) -> str:
    words = set(re.findall(r"[a-z]{3,}", user_input.lower()))
    scored = [(len(words & set(re.findall(r"[a-z]{3,}", f"{r.label} {r.when}".lower()))), -i, r.label)
              for i, r in enumerate(n.routes)]
    return max(scored)[2]


def mock_text(n: AgentNode, layer: Layer, node_input: Any, iteration: int, user_input: str) -> str:
    if layer.kind == "router":
        label = _mock_router_label(n, user_input)
        return f"{label}\nClassified the request as '{label}' from its wording."
    if layer.kind == "loop" and layer.nodes[-1].id == n.id:
        return ("PASS — the draft is clear, sourced and on-brief." if iteration >= 2
                else "REVISE: tighten the opening, add one concrete example, cite the upstream findings.")
    upstream = list(node_input.keys()) if isinstance(node_input, dict) else []
    goal = (n.instruction.strip().split(".")[0] or n.name.replace("_", " "))[:120]
    lines = [f"{goal}."]
    if upstream:
        lines.append(f"Synthesised {len(upstream)} upstream inputs: {', '.join(upstream)}.")
    elif user_input:
        lines.append(f"Worked from the request: “{user_input[:90]}”.")
    if layer.kind == "loop":
        lines.append(f"Draft revision {iteration}" + (" — applied critic feedback." if iteration > 1 else "."))
    facts = ["3 key findings extracted", "2 risks flagged", "confidence 0.82", "5 sources cross-checked",
             "1 open question logged", "latency budget respected"]
    s = seed(n.name, str(iteration))
    lines.append(f"Result: {facts[s % len(facts)]}; {facts[(s // 7) % len(facts)]}.")
    return " ".join(lines)
