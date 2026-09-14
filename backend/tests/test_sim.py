"""The browser simulator (GitHub Pages demo) must behave like the real ADK runs and never need ADK."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

from stratum import script, web
from stratum.sim import simulate
from stratum.templates import TEMPLATES

BY_ID = {t.id: t for t in TEMPLATES}


def _sim(spec, text, decision="approve"):
    events: list[dict] = []

    async def ask(_interrupt):
        return decision

    asyncio.run(simulate(spec, "graph", text, events.append, ask))
    return events


def _ended(events):
    return {e["node"] for e in events if e["type"] == "node_end" and not e["internal"]}


def test_research_brief_parallel_loop_gate():
    events = _sim(BY_ID["research-brief"], "on-device LLMs")
    assert {"planner", "web_researcher", "market_analyst", "risk_analyst", "synthesizer", "writer", "editor",
            "publish_approval"} <= _ended(events)
    starts = {e["node"]: e["t"] for e in events if e["type"] == "node_start" and e["iteration"] == 1}
    ends = {e["node"]: e["t"] for e in events if e["type"] == "node_end"}
    par = ["web_researcher", "market_analyst", "risk_analyst"]
    assert max(starts[n] for n in par) < min(ends[n] for n in par)
    assert any(e["type"] == "node_start" and e["node"] == "writer" and e["iteration"] == 2 for e in events)
    assert any(e["type"] == "interrupt" for e in events)
    assert events[-1]["type"] == "run_end" and events[-1]["status"] == "completed"


def test_router_and_reject():
    events = _sim(BY_ID["support-triage"], "double charged on my invoice, refund please", decision="reject")
    ran = _ended(events)
    assert "billing_agent" in ran and "tech_agent" not in ran
    assert "Rejected" in events[-1]["output"]


def test_user_script_runs():
    spec = script.parse("start @stage1, @stage2(agt 1,2,3,4), @stage3(combine), @stage4(abc,abc) @stage5(human)")
    assert {"agt_1", "agt_4", "combine", "abc_2", "stage5_approval"} <= _ended(_sim(spec, "hi"))


def test_web_bridge_round_trips_json():
    specs = json.loads(web.templates())
    compiled = json.loads(web.compile_spec(json.dumps(specs[0]), "dynamic"))
    assert "orchestrator" in compiled["code"]
    parsed = json.loads(web.parse_script("@a(b, c)", ""))
    assert parsed["error"] is None and len(parsed["spec"]["layers"]) == 1
    reply = json.loads(asyncio.run(web.chat_turn(json.dumps([{"role": "user", "content": "@a(b)"}]), "")))
    assert reply["engine"] == "script"


def test_browser_modules_do_not_import_adk():
    code = ("import sys, stratum.web; bad = [m for m in sys.modules if m.startswith('google')]; "
            "assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parent.parent,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
