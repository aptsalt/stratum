"""Run manager — executes a compiled graph on the ADK Runner and streams SSE.

ADK only emits an event when a node *finishes*. Node starts come from the
compiler's Hooks side channel, so both land in one asyncio.Queue that the SSE
generator drains in order.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal

from google.adk.runners import InMemoryRunner
from google.genai import types

from .compiler import Engine, Hooks, build_dynamic, build_graph, text_of
from .spec import GraphSpec

Mode = Literal["graph", "dynamic"]
_END = object()
_USER = "studio"


def gemini_available() -> bool:
    if not os.environ.get("GOOGLE_API_KEY") and os.environ.get("GEMINI_API_KEY"):
        os.environ["GOOGLE_API_KEY"] = os.environ["GEMINI_API_KEY"]
    return bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GOOGLE_GENAI_USE_VERTEXAI"))


@dataclass
class Run:
    id: str
    spec: GraphSpec
    mode: Mode
    engine: Engine
    queue: asyncio.Queue
    t0: float
    names: set[str]
    wf_name: str = ""
    runner: InMemoryRunner | None = None
    session_id: str = ""
    invocation_id: str | None = None
    interrupt: dict[str, Any] | None = None
    last_output: Any = None
    tokens: dict[str, int] = field(default_factory=dict)

    def ms(self) -> int:
        return round((time.perf_counter() - self.t0) * 1000)

    def emit(self, item: dict[str, Any]) -> None:
        self.queue.put_nowait({**item, "t": self.ms()})


RUNS: dict[str, Run] = {}


async def create_run(spec: GraphSpec, mode: Mode, engine: Engine, user_input: str) -> Run:
    run = Run(id=uuid.uuid4().hex[:10], spec=spec, mode=mode, engine=engine,
              queue=asyncio.Queue(), t0=time.perf_counter(),
              names={n.name for layer in spec.layers for n in layer.nodes})
    hooks = Hooks(run.emit)
    workflow = (build_dynamic if mode == "dynamic" else build_graph)(spec, engine, hooks)
    run.wf_name = workflow.name
    run.runner = InMemoryRunner(agent=workflow, app_name="stratum")
    session = await run.runner.session_service.create_session(
        app_name="stratum", user_id=_USER, state={"user_input": user_input})
    run.session_id = session.id
    RUNS[run.id] = run
    return run


def user_message(text: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(text=text)])


def resume_message(interrupt_id: str, response: str) -> types.Content:
    return types.Content(role="user", parts=[types.Part(function_response=types.FunctionResponse(
        id=interrupt_id, name="adk_request_input", response={"result": response}))])


def _node_name(path: str) -> tuple[str, int]:
    seg = path.rsplit("/", 1)[-1]
    name, _, idx = seg.partition("@")
    return name, int(idx) if idx.isdigit() else 1


def _map_event(run: Run, ev: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    run.invocation_id = ev.invocation_id or run.invocation_id
    info = ev.node_info
    name, iteration = _node_name(info.path) if info and info.path else ("", 1)

    for fc in ev.get_function_calls() or []:
        if fc.name == "adk_request_input":
            args = fc.args or {}
            gate = name if name in run.names else next(
                (n for n in reversed(list(run.names)) if n in (info.path if info else "")), name)
            run.interrupt = {"interruptId": fc.id, "node": gate,
                             "message": args.get("message", "Approve to continue?"),
                             "payload": args.get("payload")}
            out.append({"type": "interrupt", **run.interrupt})

    if ev.usage_metadata and name:
        used = (ev.usage_metadata.prompt_token_count or 0) + (ev.usage_metadata.candidates_token_count or 0)
        run.tokens[name] = run.tokens.get(name, 0) + used

    if info is None or not name or name == run.wf_name:
        return out
    if name == "orchestrator":  # dynamic mode: the orchestrator's output IS the run result
        if ev.output is not None and info.output_for:
            run.last_output = ev.output
        return out
    value = ev.output
    if value is None and info.message_as_output and ev.content:
        value = text_of(ev.content)
    if value is not None and (info.output_for or info.message_as_output):
        route = ev.actions.route if ev.actions else None
        out.append({"type": "node_end", "node": name, "iteration": iteration,
                    "internal": name not in run.names, "route": route,
                    "output": text_of(value)[:6000], "tokens": run.tokens.get(name)})
        if name in run.names or route == "rejected":
            run.last_output = value
    return out


def _sse(item: dict[str, Any]) -> str:
    return f"data: {json.dumps(item, default=str)}\n\n"


async def stream(run: Run, message: types.Content, resume: bool = False) -> AsyncIterator[str]:
    run.interrupt = None
    assert run.runner is not None

    async def pump() -> None:
        try:
            async for ev in run.runner.run_async(
                user_id=_USER, session_id=run.session_id, new_message=message,
                invocation_id=run.invocation_id if resume else None,
            ):
                for item in _map_event(run, ev):
                    run.emit(item)
        except Exception as e:  # surfaced to the UI, never swallowed
            run.emit({"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            run.queue.put_nowait(_END)

    if not resume:
        yield _sse({"type": "run_start", "runId": run.id, "mode": run.mode,
                    "engine": run.engine, "t": run.ms()})
    else:
        yield _sse({"type": "run_resume", "runId": run.id, "t": run.ms()})

    task = asyncio.create_task(pump())
    while True:
        item = await run.queue.get()
        if item is _END:
            break
        yield _sse(item)
    await task

    status = "paused" if run.interrupt else "completed"
    yield _sse({"type": "run_end", "status": status, "t": run.ms(),
                "output": text_of(run.last_output)[:8000],
                "tokens": sum(run.tokens.values())})
