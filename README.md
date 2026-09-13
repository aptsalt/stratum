# Stratum

**A visual studio for Google ADK 2.x agent graphs.** Stack agents in layers — sequential steps, parallel fan-outs, routers, refine loops, human gates — wire any agent to any earlier layer's output, then compile the graph to a real ADK `Workflow` and watch it run live.

- **Layered canvas.** Each layer is a dependency level. One agent = a step, several = a parallel fan-out. Drag agents between stages, insert layers anywhere, undo/redo everything.
- **Explicit data wiring.** By default an agent consumes the previous layer. Hand-pick inputs to consume *any* earlier layer (skip-level edges). Stratum inserts an ADK `JoinNode` wherever an agent has more than one upstream.
- **Two compile targets.** *Graph workflow* — static `Workflow(edges=[…])`. *Dynamic workflow* — one `@node` orchestrator using `ctx.run_node` + `asyncio.gather`. Same spec, same validation.
- **Runs on the real ADK runtime.** The *mock* engine keeps ADK's graph engine (fan-out, joins, routes, back-edges, `RequestInput`) and stubs only the model calls — no key needed. The *Gemini* engine builds real `LlmAgent`s.
- **Live execution view.** Streaming node states on the canvas, a Gantt timeline where concurrent agents overlap, the raw SSE event stream, and an approve/reject card when a human gate pauses the run.
- **Honest export.** Download `agent.py` for either mode — generated from the same IR that ran — and serve it with `adk web`.

![layers](https://img.shields.io/badge/ADK-2.9-7c6cff) ![angular](https://img.shields.io/badge/Angular-21%20zoneless-38d3c3) ![fastapi](https://img.shields.io/badge/FastAPI-SSE-36d399)

## Quick start

```bash
# backend (Python 3.11+)
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # macOS/Linux: .venv/bin/python
.venv/Scripts/python -m uvicorn app:app --port 8000

# frontend (Node 20+) — in a second terminal
cd frontend
npm install
npm start                                                   # http://localhost:4200
```

Optional: copy `backend/.env.example` to `backend/.env` and set `GOOGLE_API_KEY` to enable the Gemini engine.

Tests: `cd backend && .venv/Scripts/python -m pytest -q tests` — every template runs through the real ADK engine in both modes (parallel overlap, router exclusivity, loop rounds, HITL approve/reject, code export).

## The layer model

| Layer | Meaning | ADK 2.x construct |
|---|---|---|
| **Stage** | Every agent runs once its inputs are ready. 1 = step, N = parallel | `(src, (a, b, c))` fan-out, `JoinNode` for multi-input agents |
| **Router** | A classifier picks exactly one route; each route targets an agent in the next layer | `(route_fn, {"billing": a, "technical": b})` with `Event(route=…)` |
| **Loop** | Generator ⇄ critic until the critic says `PASS` or the round cap | back-edge `(loop_gate, {"revise": generator, "done": next})` |
| **Human gate** | Pauses for approve / reject | node yielding `RequestInput`; resume with an `adk_request_input` function response |

Plus **code nodes** — deterministic `FunctionNode`s (merge / pass-through) that cost zero tokens.

## Three ways to build: Graph · Chat · Script

The studio's center area has three views over the same graph (<kbd>Alt</kbd>+<kbd>1/2/3</kbd>):

- **Graph** — the visual layered canvas.
- **Chat** — describe the graph in English (*"research with 4 analysts, combine, draft and edit in a loop, then I approve"*), edit it (*"add 2 more reviewers to stage 2"*, *"remove risk_analyst"*, *"use pro for synthesizer"*), or paste Stratum Script. Every reply is a **proposal** with a mini preview and a +/− diff; nothing touches the canvas until you click *Apply*. Uses Gemini when a key is set, otherwise a deterministic offline phrase parser.
- **Script** — the graph as text, synced both ways with the canvas, with highlighting, `@` autocomplete, live preview and line/column errors.

**Stratum Script** — one `@layer(...)` per layer:

```text
start @stage1, @stage2(agt 1,2,3,4), @stage3(combine), @stage4(abc, abc) @stage5(human)

@triage "Classify the ticket" route(billing: billing_agent "refunds", tech: tech_agent "Fix it" if "errors")
@draft loop(writer <- (planner, research), editor) x3
@verdict(judge[pro, search] "Rank the findings" <- (research, combine))
```

| Syntax | Meaning |
|---|---|
| `@name(a, b, c)` | stage — parallel agents |
| `agt 1,2,3,4` · `4 analysts` | numbered agents `agt_1…agt_4` · `analyst_1…analyst_4` |
| `combine` / `merge` | zero-token code node |
| `human` / `approve` | human gate |
| `route(label: agent "when")` | router; branches become the next layer |
| `loop(generator, critic) xN` | refine loop |
| `name <- x` · `name <- (x, y)` | explicit inputs (node or layer, forgiving about `stage2`/`stage_2`) |
| `name[pro, search] "text"` | model · tools · instruction |

Parsing **merges with the current graph by name**, so a compact script (structure only) never wipes instructions, models or ids you set on the canvas.

## Repo layout

```
backend/
  app.py                 FastAPI: /api/templates /api/compile /api/runs (SSE) /api/runs/{id}/resume
  stratum/spec.py        GraphSpec (Pydantic) + validation rules
  stratum/compiler.py    spec → IR → live ADK objects (graph or dynamic), mock + Gemini engines
  stratum/codegen.py     IR → standalone agent.py (graph or dynamic)
  stratum/runs.py        ADK Runner + node-start side channel → one ordered SSE stream, HITL resume
  stratum/templates.py   starter graphs
  stratum/script.py      Stratum Script parser + formatter (merge-with-base)
  stratum/nl.py          offline English → graph builder and edit commands
  stratum/chat.py        chat turn → proposal (script / Gemini / offline parser)
  tests/                 end-to-end runtime tests
frontend/src/app/
  core/                  models, fetch/SSE client, GraphStore (undo/redo, normalize), RunStore
  features/studio/       canvas (SVG edges), inspector, run dock, command palette
  features/learn/        "How ADK graph workflows work" guide
docs/ARCHITECTURE.md     design, data flow and the three meanings of "dynamic"
```

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full design.
