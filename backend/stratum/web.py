"""JSON-in / JSON-out entry points for the browser build (Pyodide), mirroring the HTTP API."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from . import chat, codegen, script
from .ir import diagnose, lower
from .sim import simulate
from .spec import Diagnostic, GraphSpec
from .templates import TEMPLATES


def _spec(raw: str | None) -> GraphSpec | None:
    return GraphSpec.model_validate_json(raw) if raw else None


def _diags(diags: list[Diagnostic]) -> list[dict[str, Any]]:
    return [d.model_dump(by_alias=True) for d in diags]


def templates() -> str:
    return json.dumps([t.model_dump(by_alias=True) for t in TEMPLATES])


def compile_spec(spec_json: str, mode: str) -> str:
    spec = _spec(spec_json)
    assert spec is not None
    diags = diagnose(spec)
    if any(d.level == "error" for d in diags):
        return json.dumps({"diagnostics": _diags(diags), "ir": None, "code": None})
    ir = lower(spec)
    code = codegen.dynamic(spec) if mode == "dynamic" else codegen.graph(spec, ir)
    return json.dumps({"diagnostics": _diags(diags), "ir": ir.to_json(), "code": code})


def parse_script(text: str, base_json: str) -> str:
    try:
        spec = script.parse(text, _spec(base_json))
    except script.ScriptError as e:
        return json.dumps({"spec": None, "error": {"message": e.message, "line": e.line, "col": e.col, "pos": e.pos}})
    return json.dumps({"spec": spec.model_dump(by_alias=True), "error": None,
                       "diagnostics": _diags(diagnose(spec)), "summary": script.summarize(spec)})


def format_script(spec_json: str, compact: bool) -> str:
    spec = _spec(spec_json)
    assert spec is not None
    return json.dumps({"text": script.format(spec, bool(compact))})


async def chat_turn(messages_json: str, spec_json: str) -> str:
    messages = [chat.ChatMessage.model_validate(m) for m in json.loads(messages_json)]
    return json.dumps(await chat.respond(messages, _spec(spec_json), "parser", False), default=str)


async def run(spec_json: str, mode: str, user_input: str, emit: Callable[[str], None],
              ask: Callable[[str], Awaitable[Any]]) -> None:
    spec = _spec(spec_json)
    assert spec is not None

    async def ask_human(interrupt: dict[str, Any]) -> str:
        return str(await ask(json.dumps(interrupt)))

    await simulate(spec, mode, user_input, lambda e: emit(json.dumps(e, default=str)), ask_human)
