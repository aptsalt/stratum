"""In-browser run simulator for the GitHub Pages demo.

Executes the lowered IR with ADK's edge semantics — a tuple fan-out runs concurrently,
a JoinNode waits for every predecessor, routed edges fire only for the emitted label,
back-edges loop — and emits the exact event protocol runs.py streams over SSE, so the
UI cannot tell the difference. Model calls use the same mock text engine. No ADK imports.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable

from .ir import code_output, critic_passed, is_approved, lower, mock_latency, mock_text, pick_route, text_of
from .spec import GraphSpec

Emit = Callable[[dict[str, Any]], None]
Ask = Callable[[dict[str, Any]], Awaitable[str]]


async def simulate(spec: GraphSpec, mode: str, user_input: str, emit: Emit, ask: Ask) -> None:
    ir = lower(spec)
    t0 = time.perf_counter()
    names = {n.name for layer in spec.layers for n in layer.nodes}
    out_edges: dict[str, list] = defaultdict(list)
    join_preds: dict[str, list[str]] = defaultdict(list)
    for e in ir.edges:
        out_edges[e.src].append(e)
        if ir.nodes[e.dst].role == "join":
            join_preds[e.dst].append(e.src)

    inbox: dict[str, dict[str, Any]] = defaultdict(dict)
    state: dict[str, Any] = {"user_input": user_input}
    counts: dict[str, int] = defaultdict(int)
    pending: set[asyncio.Future] = set()
    last: dict[str, Any] = {"output": None}

    def send(**event: Any) -> None:
        emit({**event, "t": round((time.perf_counter() - t0) * 1000)})

    def schedule(name: str, value: Any) -> None:
        pending.add(asyncio.ensure_future(step(name, value)))

    def deliver(src: str, value: Any, route: str | None) -> None:
        for e in out_edges[src]:
            if e.route is not None and e.route != route:
                continue
            if ir.nodes[e.dst].role == "join":
                box = inbox[e.dst]
                box[src] = value
                if all(p in box for p in join_preds[e.dst]):
                    inbox[e.dst] = {}
                    schedule(e.dst, box)
            else:
                schedule(e.dst, value)

    async def step(name: str, value: Any) -> None:
        n = ir.nodes[name]
        route: str | None = None
        if n.role in ("agent", "code", "human_ask"):
            counts[name] += 1
            send(type="node_start", node=name, iteration=counts[name])

        if n.role == "agent":
            await asyncio.sleep(mock_latency(name))
            output: Any = mock_text(n.spec, n.layer, value, counts[name], user_input)  # type: ignore[arg-type]
            state[name] = output
        elif n.role == "code":
            await asyncio.sleep(0.05)
            output = code_output(n.spec, value)  # type: ignore[arg-type]
            state[name] = text_of(output)
        elif n.role == "join":
            output = value
        elif n.role == "router_fn":
            route = pick_route(text_of(value), n.extra["labels"])
            vals = {k: state.get(k) for k in n.extra["context"]}
            output = next(iter(vals.values())) if len(vals) == 1 else vals
        elif n.role == "loop_gate":
            key = f"_iter_{name}"
            it = int(state.get(key, 0)) + 1
            draft = state.get(n.extra["generator"])
            if critic_passed(text_of(value)) or it >= n.extra["max"]:
                output, route, state[key] = draft, "done", 0
            else:
                output, route, state[key] = {"draft": draft, "feedback": text_of(value)}, "revise", it
        elif n.role == "human_ask":
            state[f"_gate_{name}"] = text_of(value)
            interrupt = {"interruptId": uuid.uuid4().hex[:12], "node": name,
                         "message": (n.spec.instruction if n.spec else "") or "Approve to continue?",
                         "payload": {"preview": text_of(value)[:1500]}}
            send(type="interrupt", **interrupt)
            output = await ask(interrupt)
        elif n.role == "human_decide":
            gate = n.extra["ask"]
            if is_approved(value):
                output, route = state.get(f"_gate_{gate}"), "approved"
            else:
                output, route = f"Rejected at gate '{gate}'.", "rejected"
        else:
            raise ValueError(f"unknown role {n.role}")

        internal = name not in names
        send(type="node_end", node=name, iteration=max(1, counts[name]), internal=internal,
             route=route, output=text_of(output)[:6000], tokens=None)
        if not internal or route == "rejected":
            last["output"] = output
        deliver(name, output, route)

    send(type="run_start", runId=uuid.uuid4().hex[:10], mode=mode, engine="demo")
    deliver("START", user_input, None)
    while pending:
        done, _ = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            pending.discard(task)
            exc = task.exception()
            if exc is not None:
                send(type="error", message=f"{type(exc).__name__}: {exc}")
    send(type="run_end", status="completed", output=text_of(last["output"])[:8000], tokens=0)
