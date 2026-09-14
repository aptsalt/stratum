"""Build live ADK 2.x objects from the lowered IR.

  GraphSpec ──ir.lower()──▶ IR (nodes + edges) ──build_graph()───▶ google.adk.Workflow(edges=…)
      │                                        └──codegen.graph()──▶ agent.py (graph mode)
      └──────────────── build_dynamic() ──▶ Workflow(START → @node orchestrator using ctx.run_node)
                         codegen.dynamic() ─▶ agent.py (dynamic mode)

Two engines share one runtime: `mock` swaps each LlmAgent for a FunctionNode
that fakes latency + text (so the REAL ADK graph engine — fan-out, JoinNode,
routes, back-edges, RequestInput — runs offline); `gemini` builds real
LlmAgents. Everything ADK-free lives in ir.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Literal

from google.adk import Context, Event, Workflow
from google.adk.agents import LlmAgent
from google.adk.events import RequestInput
from google.adk.workflow import FunctionNode, JoinNode, node

from .ir import (
    IRNode,
    CompileError,
    code_output,
    critic_passed,
    group_edges,
    instruction_for,
    is_approved,
    lower,
    mock_latency,
    mock_text,
    pick_route,
    text_of,
    wf_name,
)
from .spec import AgentNode, GraphSpec, Layer

Engine = Literal["mock", "gemini"]

__all__ = ["Engine", "Hooks", "build_graph", "build_dynamic", "lower", "text_of"]


class Hooks:
    """Per-run side channel: node starts + call counts feed the SSE stream."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]):
        self._emit = emit
        self._counts: dict[str, int] = {}

    def start(self, name: str) -> int:
        self._counts[name] = self._counts.get(name, 0) + 1
        self._emit({"type": "node_start", "node": name, "iteration": self._counts[name]})
        return self._counts[name]


# ─────────────────────────────── node builders ───────────────────────────────

def _mock_agent(n: AgentNode, layer: Layer, hooks: Hooks) -> FunctionNode:
    async def run(ctx: Context, node_input: Any = None):
        iteration = hooks.start(n.name)
        await asyncio.sleep(mock_latency(n.name))
        text = mock_text(n, layer, node_input, iteration, ctx.state.get("user_input", ""))
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
        out = code_output(n, node_input)
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
        router = ir_node.spec.name if ir_node.spec else ir_node.name

        async def route(ctx: Context, node_input: Any = None):
            label = pick_route(text_of(node_input), x["labels"])
            ctx_vals = {k: ctx.state.get(k) for k in x["context"]}
            payload = next(iter(ctx_vals.values())) if len(ctx_vals) == 1 else ctx_vals
            return Event(output=payload, route=label, state={f"_route_{router}": label})
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
    return Workflow(name=wf_name(spec), edges=edges)


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

    return Workflow(name=wf_name(spec), edges=[("START", orchestrator)])
