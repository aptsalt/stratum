"""Chat → graph. Three paths, one output shape (a *proposal* the UI previews before applying):

  1. the message contains Stratum Script (@layer …)  → parsed deterministically
  2. Gemini is configured                              → model writes Stratum Script, parser validates it
  3. otherwise                                         → offline phrase parser (nl.py): build or edit
"""

from __future__ import annotations

import os
from typing import Any, Literal

from pydantic import BaseModel

from . import nl, script
from .ir import diagnose
from .spec import GraphSpec

GEMINI_RULES = """
Rules:
- Always return the COMPLETE graph in `script` (never a diff). When editing, keep existing layer and agent names.
- Every agent gets a short, specific instruction in quotes.
- Layer names are short snake_case words (research, combine, draft, signoff).
- Use combine/merge for zero-token merging, loop(writer, critic) xN for refinement, route(label: agent "when") for branching,
  and a final @signoff(human "question") when the user wants to approve something.
- `reply` is 1–2 friendly sentences describing what you built or changed.
""".strip()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    script: str | None = None


class _GeminiOut(BaseModel):
    reply: str
    script: str
    name: str = ""


def _proposal(spec: GraphSpec, reply: str, engine: str) -> dict[str, Any]:
    return {
        "reply": reply,
        "engine": engine,
        "spec": spec.model_dump(by_alias=True),
        "script": script.format(spec),
        "summary": script.summarize(spec),
        "diagnostics": [d.model_dump(by_alias=True) for d in diagnose(spec)],
    }


async def respond(messages: list[ChatMessage], spec: GraphSpec | None, engine: str = "auto",
                  gemini_ready: bool = False) -> dict[str, Any]:
    text = messages[-1].content.strip()
    base = spec if spec and spec.layers else None

    if "@" in text:
        try:
            parsed = script.parse(text, base)
        except script.ScriptError as e:
            return {"reply": f"That script doesn't parse: {e.message} (line {e.line}, col {e.col}).",
                    "engine": "script", "error": {"message": e.message, "line": e.line, "col": e.col}}
        parsed.name = base.name if base else "Scripted graph"
        return _proposal(parsed, f"Parsed your script → {script.summarize(parsed)}.", "script")

    note = ""
    if engine == "auto" and gemini_ready:
        try:
            return await _gemini(messages, base)
        except Exception as e:  # fall back to the offline parser, but say so
            note = f" (Gemini failed: {type(e).__name__} — used the offline parser instead.)"

    edited = nl.edit(base, text) if base else None
    if edited:
        new, what = edited
        return _proposal(new, what + note, "parser")
    built = nl.build(text)
    hint = "" if gemini_ready else " Offline parser — add GOOGLE_API_KEY for free-form requests."
    return _proposal(built, f"Here's a graph for that: {script.summarize(built)}.{hint}{note}", "parser")


async def _gemini(messages: list[ChatMessage], base: GraphSpec | None) -> dict[str, Any]:
    from google import genai
    from google.genai import types

    system = (
        "You design layered agent graphs for Google ADK, written in Stratum Script.\n\n"
        f"{script.__doc__}\n\n{GEMINI_RULES}\n\nCurrent graph:\n"
        + (script.format(base) if base else "(empty — build a new one)")
    )
    history = [
        types.Content(role="user" if m.role == "user" else "model",
                      parts=[types.Part(text=m.content + (f"\n\n{m.script}" if m.script else ""))])
        for m in messages[-12:]
    ]
    client = genai.Client()
    model = os.environ.get("STRATUM_CHAT_MODEL", "gemini-2.5-flash")
    config = types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                         response_schema=_GeminiOut)

    for _ in range(2):
        resp = await client.aio.models.generate_content(model=model, contents=history, config=config)
        out = resp.parsed if isinstance(resp.parsed, _GeminiOut) else _GeminiOut.model_validate_json(resp.text or "{}")
        try:
            parsed = script.parse(out.script, base)
        except script.ScriptError as e:
            history += [types.Content(role="model", parts=[types.Part(text=out.script)]),
                        types.Content(role="user", parts=[types.Part(text=f"That script failed to parse: {e}. Return the corrected full script.")])]
            continue
        parsed.name = out.name or (base.name if base else "Chat graph")
        return _proposal(parsed, out.reply, "gemini")
    raise ValueError("model returned an unparseable script twice")
