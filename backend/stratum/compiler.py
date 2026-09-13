"""Lower a layered GraphSpec to an IR, then to live ADK 2.x objects.

  GraphSpec ──lower()──▶ IR (nodes + edges) ──build_graph()───▶ google.adk.Workflow(edges=…)
      │                                    └──codegen.graph()──▶ agent.py (graph mode)
      └──────────────── build_dynamic() ──▶ Workflow(START → @node orchestrator using ctx.run_node)
                         codegen.dynamic() ─▶ agent.py (dynamic mode)

Two engines share one runtime: `mock` swaps each LlmAgent for a FunctionNode
that fakes latency + text (so the REAL ADK graph engine — fan-out, JoinNode,
routes, back-edges, RequestInput — runs offline); `gemini` builds real
LlmAgents.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from google.adk import Context, Event, Workflow
from google.adk.agents import LlmAgent
from google.adk.events import RequestInput
from google.adk.workflow import FunctionNode, JoinNode, node

from .spec import AgentNode, Diagnostic, GraphSpec, Layer, validate_spec

Role = Literal["agent", "code", "human_ask", "human_decide", "router_fn", "loop_gate", "join"]
Engine = Literal["mock", "gemini"]
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
        if None not in branches and len({b[0] for b in branches}) == 1:
            labels = [b[1] for b in branches]
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


def diagnose(spec: GraphSpec) -> list[Diagnostic]:
    """Validation rules + whatever lowering rejects (e.g. joins that would deadlock)."""
    diags = validate_spec(spec)
    if not any(d.level == "error" for d in diags):
        try:
            lower(spec)
        except CompileError as e:
            diags.append(Diagnostic(level="error", message=str(e)))
    return diags


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


class Hooks:
    """Per-run side channel: node starts + call counts feed the SSE stream."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]):
        self._emit = emit
        self._counts: dict[str, int] = {}

    def start(self, name: str) -> int:
        self._counts[name] = self._counts.get(name, 0) + 1
        self._emit({"type": "node_start", "node": name, "iteration": self._counts[name]})
        return self._counts[name]


# ─────────────────────────────── mock engine ───────────────────────────────

def _seed(*parts: str) -> int:
    return int(hashlib.sha1("|".join(parts).encode()).hexdigest()[:8], 16)


def _mock_router_label(n: AgentNode, user_input: str) -> str:
    words = set(re.findall(r"[a-z]{3,}", user_input.lower()))
    scored = [(len(words & set(re.findall(r"[a-z]{3,}", f"{r.label} {r.when}".lower()))), -i, r.label)
              for i, r in enumerate(n.routes)]
    return max(scored)[2]


def _mock_text(n: AgentNode, layer: Layer, node_input: Any, iteration: int, user_input: str) -> str:
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
    s = _seed(n.name, str(iteration))
    lines.append(f"Result: {facts[s % len(facts)]}; {facts[(s // 7) % len(facts)]}.")
    return " ".join(lines)


def _mock_agent(n: AgentNode, layer: Layer, hooks: Hooks) -> FunctionNode:
    async def run(ctx: Context, node_input: Any = None):
        iteration = hooks.start(n.name)
        await asyncio.sleep(0.45 + (_seed(n.name) % 900) / 1000)
        text = _mock_text(n, layer, node_input, iteration, ctx.state.get("user_input", ""))
        return Event(output=text, state={n.name: text})

    return FunctionNode(func=run, name=n.name)


def _gemini_agent(n: AgentNode, layer: Layer, spec: GraphSpec, hooks: Hooks) -> LlmAgent:
    tools: list[Any] = []
    if "google_search" in n.tools:
        from google.adk.tools import google_search
        tools.append(google_search)

    def before(callback_context):  # noqa: ANN001 — ADK callback signature
        hooks.start(n.name)

    return LlmAgent(name=n.name, model=n.model or spec.model, mode="single_turn",
                    instruction=instruction_for(n, layer), output_key=n.name,
                    tools=tools, before_agent_callback=before)


def _code_node(n: AgentNode, hooks: Hooks) -> FunctionNode:
    async def run(node_input: Any = None):
        hooks.start(n.name)
        if n.op == "passthrough":
            out = node_input
        elif isinstance(node_input, dict):
            out = "\n\n".join(f"## {k}\n{text_of(v)}" for k, v in node_input.items())
        else:
            out = text_of(node_input)
        return Event(output=out, state={n.name: text_of(out)})

    return FunctionNode(func=run, name=n.name)


def _human_ask(n: AgentNode, hooks: Hooks):
    async def run(ctx: Context, node_input: Any = None):
        hooks.start(n.name)
        ctx.state[f"_gate_{n.name}"] = text_of(node_input)
        yield RequestInput(message=n.instruction or "Approve to continue?",
                           payload={"preview": text_of(node_input)[:1500]})

    return node(run, name=n.name, rerun_on_resume=False)


def build_agent_nodes(spec: GraphSpec, engine: Engine, hooks: Hooks) -> dict[str, Any]:
    objs: dict[str, Any] = {}
    for layer in spec.layers:
        for n in layer.nodes:
            if n.kind == "code":
                objs[n.name] = _code_node(n, hooks)
            elif n.kind == "human":
                objs[n.name] = _human_ask(n, hooks)
            elif engine == "gemini":
                objs[n.name] = _gemini_agent(n, layer, spec, hooks)
            else:
                objs[n.name] = _mock_agent(n, layer, hooks)
    return objs


# ─────────────────────────────── graph mode ───────────────────────────────

def _internal_node(ir_node: IRNode) -> Any:
    x = ir_node.extra
    if ir_node.role == "join":
        return JoinNode(name=ir_node.name)

    if ir_node.role == "router_fn":
        async def route(ctx: Context, node_input: Any = None):
            label = pick_route(text_of(node_input), x["labels"])
            ctx_vals = {k: ctx.state.get(k) for k in x["context"]}
            payload = next(iter(ctx_vals.values())) if len(ctx_vals) == 1 else ctx_vals
            return Event(output=payload, route=label, state={f"_route_{ir_node.spec.name}": label})
        return FunctionNode(func=route, name=ir_node.name)

    if ir_node.role == "loop_gate":
        key = f"_iter_{ir_node.name}"

        async def gate(ctx: Context, node_input: Any = None):
            it = int(ctx.state.get(key, 0)) + 1
            draft = ctx.state.get(x["generator"])
            verdict = text_of(node_input)
            if critic_passed(verdict) or it >= x["max"]:
                return Event(output=draft, route="done", state={key: 0})
            return Event(output={"draft": draft, "feedback": verdict}, route="revise", state={key: it})
        return FunctionNode(func=gate, name=ir_node.name)

    if ir_node.role == "human_decide":
        ask = x["ask"]

        async def decide(ctx: Context, node_input: Any = None):
            upstream = ctx.state.get(f"_gate_{ask}")
            if is_approved(node_input):
                return Event(output=upstream, route="approved")
            return Event(output=f"Rejected at gate '{ask}'.", route="rejected",
                         message=f"Run stopped: '{ask}' was rejected.")
        return FunctionNode(func=decide, name=ir_node.name)

    raise CompileError(f"No runtime for role {ir_node.role}")


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


def build_graph(spec: GraphSpec, engine: Engine, hooks: Hooks) -> Workflow:
    ir = lower(spec)
    objs = build_agent_nodes(spec, engine, hooks)
    for name, n in ir.nodes.items():
        if name not in objs:
            objs[name] = _internal_node(n)

    plain, routed = group_edges(ir)

    def ref(name: str) -> Any:
        return "START" if name == "START" else objs[name]

    def many(names: list[str]) -> Any:
        return ref(names[0]) if len(names) == 1 else tuple(ref(d) for d in names)

    edges: list[tuple] = [(ref(src), many(dsts)) for src, dsts in plain.items()]
    edges += [(ref(src), {lbl: many(d) for lbl, d in m.items()}) for src, m in routed.items()]
    return Workflow(name=_wf_name(spec), edges=edges)


def _wf_name(spec: GraphSpec) -> str:
    return re.sub(r"[^a-z0-9_]", "_", spec.name.lower()).strip("_") or "stratum_graph"


# ─────────────────────────────── dynamic mode ───────────────────────────────

def build_dynamic(spec: GraphSpec, engine: Engine, hooks: Hooks) -> Workflow:
    """One @node orchestrator walks the layers with ctx.run_node — ADK dynamic workflow."""
    lower(spec)  # same validity rules as graph mode
    objs = build_agent_nodes(spec, engine, hooks)
    by_id = {n.id: n for layer in spec.layers for n in layer.nodes}

    def bundle(outs: dict[str, Any], ids: list[str], user_input: Any) -> Any:
        vals = {by_id[i].name: outs[i] for i in ids if i in outs}
        if not ids:
            return user_input
        return next(iter(vals.values())) if len(vals) == 1 else vals

    @node(name="orchestrator", rerun_on_resume=True)
    async def orchestrator(ctx: Context, node_input: Any = None):
        user_input = ctx.state.get("user_input", text_of(node_input))
        outs: dict[str, Any] = {}
        prev: list[str] = []
        chosen: str | None = None
        last: Any = user_input

        for layer in spec.layers:
            if layer.kind == "stage":
                runnable = []
                for n in layer.nodes:
                    if chosen is not None and n.id != chosen:
                        continue
                    deps = prev if n.depends_on is None else n.depends_on
                    ready = [d for d in deps if d in outs]
                    if deps and (not ready or (n.depends_on is not None and len(ready) < len(deps))):
                        continue  # downstream of a branch that did not run
                    runnable.append((n, bundle(outs, ready, user_input)))
                results = await asyncio.gather(*(ctx.run_node(objs[n.name], inp) for n, inp in runnable))
                for (n, _), r in zip(runnable, results):
                    outs[n.id] = last = r
                prev, chosen = [n.id for n, _ in runnable], None

            elif layer.kind == "router":
                n = layer.nodes[0]
                deps = prev if n.depends_on is None else n.depends_on
                verdict = await ctx.run_node(objs[n.name], bundle(outs, deps, user_input))
                label = pick_route(text_of(verdict), [r.label for r in n.routes])
                chosen = next(r.target for r in n.routes if r.label == label)
                outs[n.id] = verdict
                # branch targets consume the router's own input, not its verdict
                prev = [d for d in deps if d in outs] if deps else []

            elif layer.kind == "loop":
                gen, critic = layer.nodes
                deps = prev if gen.depends_on is None else gen.depends_on
                payload = bundle(outs, deps, user_input)
                draft = None
                for _ in range(layer.max_iterations):
                    draft = await ctx.run_node(objs[gen.name], payload)
                    verdict = await ctx.run_node(objs[critic.name], draft)
                    if critic_passed(text_of(verdict)):
                        break
                    payload = {"draft": draft, "feedback": verdict}
                outs[gen.id] = outs[critic.id] = last = draft
                prev = [gen.id]

            elif layer.kind == "gate":
                h = layer.nodes[0]
                deps = prev if h.depends_on is None else h.depends_on
                upstream = bundle(outs, deps, user_input)
                decision = await ctx.run_node(objs[h.name], upstream)
                if not is_approved(decision):
                    return Event(output=f"Rejected at gate '{h.name}'.",
                                 message=f"Run stopped: '{h.name}' was rejected.")
                outs[h.id] = last = upstream
                prev = [h.id]

        return last

    return Workflow(name=_wf_name(spec), edges=[("START", orchestrator)])
