"""Stratum API — templates, validate, compile (IR + Python), run (SSE), resume (HITL), script, chat."""

from __future__ import annotations

from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from google.adk import __version__ as adk_version
from pydantic import BaseModel, Field

load_dotenv()

from stratum import chat, codegen, script  # noqa: E402
from stratum.ir import diagnose, lower  # noqa: E402
from stratum.runs import RUNS, create_run, gemini_available, resume_message, stream, user_message  # noqa: E402
from stratum.spec import Diagnostic, GraphSpec  # noqa: E402
from stratum.templates import TEMPLATES  # noqa: E402

app = FastAPI(title="Stratum", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:4200"],
                   allow_methods=["*"], allow_headers=["*"])

Mode = Literal["graph", "dynamic"]
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class CompileRequest(BaseModel):
    spec: GraphSpec
    mode: Mode = "graph"


class RunRequest(BaseModel):
    spec: GraphSpec
    mode: Mode = "graph"
    engine: Literal["mock", "gemini"] = "mock"
    input: str = Field(min_length=1, max_length=8000)


class ResumeRequest(BaseModel):
    interrupt_id: str = Field(alias="interruptId")
    response: str = Field(min_length=1, max_length=2000)


class ScriptParseRequest(BaseModel):
    text: str = Field(max_length=20000)
    base: GraphSpec | None = None


class ScriptFormatRequest(BaseModel):
    spec: GraphSpec
    compact: bool = False


class ChatRequest(BaseModel):
    messages: list[chat.ChatMessage] = Field(min_length=1, max_length=60)
    spec: GraphSpec | None = None
    engine: Literal["auto", "parser"] = "auto"


def _dump(diags: list[Diagnostic]) -> list[dict]:
    return [d.model_dump(by_alias=True) for d in diags]


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "adk": adk_version, "engines": {"mock": True, "gemini": gemini_available()}}


@app.get("/api/templates")
def templates() -> list[dict]:
    return [t.model_dump(by_alias=True) for t in TEMPLATES]


@app.post("/api/compile")
def compile_spec(req: CompileRequest) -> dict:
    diags = diagnose(req.spec)
    if any(d.level == "error" for d in diags):
        return {"diagnostics": _dump(diags), "ir": None, "code": None}
    ir = lower(req.spec)
    code = codegen.dynamic(req.spec) if req.mode == "dynamic" else codegen.graph(req.spec, ir)
    return {"diagnostics": _dump(diags), "ir": ir.to_json(), "code": code}


@app.post("/api/script/parse")
def parse_script(req: ScriptParseRequest) -> dict:
    try:
        spec = script.parse(req.text, req.base)
    except script.ScriptError as e:
        return {"spec": None, "error": {"message": e.message, "line": e.line, "col": e.col, "pos": e.pos}}
    return {"spec": spec.model_dump(by_alias=True), "error": None,
            "diagnostics": _dump(diagnose(spec)), "summary": script.summarize(spec)}


@app.post("/api/script/format")
def format_script(req: ScriptFormatRequest) -> dict:
    return {"text": script.format(req.spec, req.compact)}


@app.post("/api/chat")
async def chat_turn(req: ChatRequest) -> dict:
    return await chat.respond(req.messages, req.spec, req.engine, gemini_available())


@app.post("/api/runs")
async def start_run(req: RunRequest) -> StreamingResponse:
    diags = diagnose(req.spec)
    if any(d.level == "error" for d in diags):
        raise HTTPException(422, detail={"diagnostics": _dump(diags)})
    if req.engine == "gemini" and not gemini_available():
        raise HTTPException(400, detail="Set GOOGLE_API_KEY (or GEMINI_API_KEY) in backend/.env to use Gemini.")
    run = await create_run(req.spec, req.mode, req.engine, req.input)
    return StreamingResponse(stream(run, user_message(req.input)),
                             media_type="text/event-stream", headers=SSE_HEADERS)


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest) -> StreamingResponse:
    run = RUNS.get(run_id)
    if run is None:
        raise HTTPException(404, detail="Run not found (the server may have restarted).")
    return StreamingResponse(stream(run, resume_message(req.interrupt_id, req.response), resume=True),
                             media_type="text/event-stream", headers=SSE_HEADERS)
