"""End-to-end: every template compiles, runs on the real ADK engine (mock LLM), and exports."""

import asyncio
import json

import pytest

from stratum import codegen
from stratum.compiler import lower
from stratum.runs import create_run, resume_message, stream, user_message
from stratum.spec import validate_spec
from stratum.templates import TEMPLATES

BY_ID = {t.id: t for t in TEMPLATES}
MODES = ["graph", "dynamic"]


async def _collect(agen) -> list[dict]:
    return [json.loads(chunk[len("data: "):]) async for chunk in agen]


def _run(template_id: str, mode: str, text: str, decision: str = "approve") -> list[dict]:
    async def go():
        run = await create_run(BY_ID[template_id], mode, "mock", text)
        events = await _collect(stream(run, user_message(text)))
        end = events[-1]
        if end["status"] == "paused":
            gate = next(e for e in events if e["type"] == "interrupt")
            events += await _collect(stream(run, resume_message(gate["interruptId"], decision), resume=True))
        return events
    return asyncio.run(go())


def _ended(events: list[dict]) -> set[str]:
    return {e["node"] for e in events if e["type"] == "node_end" and not e["internal"]}


def test_templates_are_valid():
    for t in TEMPLATES:
        assert not [d for d in validate_spec(t) if d.level == "error"], t.id


def test_lowering_inserts_join_loop_and_gate():
    ir = lower(BY_ID["research-brief"])
    into_join = [e.src for e in ir.edges if e.dst == "join_synthesizer"]
    assert sorted(into_join) == ["market_analyst", "risk_analyst", "web_researcher"]
    assert any(e.src == "loop_writer" and e.dst == "writer" and e.route == "revise" for e in ir.edges)
    assert any(e.src == "decide_publish_approval" for e in ir.edges) is False  # last layer → run ends
    # skip-level dependency: writer consumes planner (layer 1) + synthesizer (layer 3)
    assert sorted(e.src for e in ir.edges if e.dst == "join_writer") == ["planner", "synthesizer"]


@pytest.mark.parametrize("mode", MODES)
def test_research_brief_runs_parallel_loop_and_gate(mode):
    events = _run("research-brief", mode, "State of on-device LLMs for banking apps")
    assert {"planner", "web_researcher", "market_analyst", "risk_analyst", "synthesizer",
            "writer", "editor"} <= _ended(events)
    starts = {e["node"]: e["t"] for e in events if e["type"] == "node_start" and e["iteration"] == 1}
    ends = {e["node"]: e["t"] for e in events if e["type"] == "node_end"}
    parallel = ["web_researcher", "market_analyst", "risk_analyst"]
    assert max(starts[n] for n in parallel) < min(ends[n] for n in parallel), "fan-out must overlap"
    assert any(e["type"] == "node_start" and e["node"] == "writer" and e["iteration"] == 2 for e in events)
    assert any(e["type"] == "interrupt" for e in events)
    assert events[-1]["status"] == "completed"


@pytest.mark.parametrize("mode", MODES)
def test_router_takes_exactly_one_branch(mode):
    events = _run("support-triage", mode, "I was double charged on my last invoice, need a refund")
    ran = _ended(events)
    assert "billing_agent" in ran and "tech_agent" not in ran and "general_agent" not in ran
    assert "reply_composer" in ran


@pytest.mark.parametrize("mode", MODES)
def test_reject_stops_the_run(mode):
    events = _run("support-triage", mode, "The API returns 500 errors on login", decision="reject")
    assert "tech_agent" in _ended(events)
    assert "Rejected" in events[-1]["output"]


@pytest.mark.parametrize("mode", MODES)
def test_code_review_merges_with_code_node(mode):
    events = _run("code-review-swarm", mode, "Review PR #42: adds token refresh")
    ran = _ended(events)
    assert {"findings", "verdict_writer"} <= ran
    findings = next(e for e in events if e["type"] == "node_end" and e["node"] == "findings")
    assert "## security_reviewer" in findings["output"]


@pytest.mark.parametrize("mode", MODES)
def test_exported_code_builds_a_workflow(mode):
    for t in TEMPLATES:
        src = codegen.dynamic(t) if mode == "dynamic" else codegen.graph(t)
        ns: dict = {}
        exec(compile(src, f"{t.id}.py", "exec"), ns)
        assert type(ns["root_agent"]).__name__ == "Workflow", t.id
