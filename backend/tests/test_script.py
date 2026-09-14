"""Stratum Script: the user's own syntax, round-trips for every template, merge-with-base, errors."""

import asyncio
import json

import pytest

from stratum import chat, nl, script
from stratum.ir import diagnose
from stratum.runs import create_run, resume_message, stream, user_message
from stratum.templates import TEMPLATES

USER_EXAMPLE = "start @stage1, @stage2(agt 1,2,3,4),@stage3(combine), @stage4(abc,abc) @stage5(human)"
BY_ID = {t.id: t for t in TEMPLATES}


def _shape(spec):
    by_id = {n.id: n.name for l in spec.layers for n in l.nodes}
    return [
        (l.kind, l.max_iterations if l.kind == "loop" else None,
         [(n.name, n.kind, n.op if n.kind == "code" else None, n.instruction, n.model, tuple(n.tools),
           tuple(by_id[d] for d in n.depends_on) if n.depends_on is not None else None,
           tuple((r.label, by_id[r.target], r.when) for r in n.routes)) for n in l.nodes])
        for l in spec.layers
    ]


def _errors(spec):
    return [d.message for d in diagnose(spec) if d.level == "error"]


def test_user_example_parses_to_five_layers():
    spec = script.parse(USER_EXAMPLE)
    assert [l.kind for l in spec.layers] == ["stage", "stage", "stage", "stage", "gate"]
    assert [n.name for n in spec.layers[1].nodes] == ["agt_1", "agt_2", "agt_3", "agt_4"]
    assert spec.layers[2].nodes[0].kind == "code"
    assert [n.name for n in spec.layers[3].nodes] == ["abc", "abc_2"]
    assert spec.layers[4].nodes[0].kind == "human"
    assert not _errors(spec)


@pytest.mark.parametrize("mode", ["graph", "dynamic"])
def test_user_example_runs_on_adk(mode):
    spec = script.parse(USER_EXAMPLE)

    async def go():
        run = await create_run(spec, mode, "mock", "hello")
        events = [json.loads(c[6:]) async for c in stream(run, user_message("hello"))]
        gate = next(e for e in events if e["type"] == "interrupt")
        events += [json.loads(c[6:]) async for c in stream(run, resume_message(gate["interruptId"], "approve"), resume=True)]
        return events

    events = asyncio.run(go())
    ended = {e["node"] for e in events if e["type"] == "node_end" and not e["internal"]}
    assert {"stage1", "agt_1", "agt_4", "combine", "abc", "abc_2"} <= ended
    assert events[-1]["status"] == "completed"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_full_format_round_trips(template):
    text = script.format(template)
    again = script.parse(text)
    assert _shape(again) == _shape(template)
    assert script.format(again) == text


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda t: t.id)
def test_compact_edit_keeps_details_from_base(template):
    compact = script.format(template, compact=True)
    merged = script.parse(compact, base=template)
    assert _shape(merged) == _shape(template)
    assert [n.id for l in merged.layers for n in l.nodes] == [n.id for l in template.layers for n in l.nodes]


def test_route_loop_and_explicit_inputs():
    spec = script.parse('@triage route(billing: billing_agent "refunds", tech: tech_agent), '
                        '@reply(composer[pro] "Write the reply"), @draft loop(writer <- (triage, reply), editor) x2')
    kinds = [l.kind for l in spec.layers]
    assert kinds == ["router", "stage", "stage", "loop"]
    assert spec.layers[0].nodes[0].routes[0].when == "refunds"
    assert spec.layers[2].nodes[0].model == "gemini-2.5-pro"
    writer = spec.layers[3].nodes[0]
    names = {n.id: n.name for l in spec.layers for n in l.nodes}
    assert [names[d] for d in writer.depends_on] == ["triage", "composer"]
    assert spec.layers[3].max_iterations == 2
    assert not _errors(spec)


def test_layer_refs_are_forgiving():
    base = script.parse("@stage1(a), @stage2(b, c)")
    text = script.format(base)
    assert "@stage2(" in text
    for ref in ("stage2", "stage_2", "Stage2"):
        spec = script.parse(f"{text}\n@stage3(d <- {ref})", base)
        names = {n.id: n.name for l in spec.layers for n in l.nodes}
        assert [names[x] for x in spec.layers[2].nodes[0].depends_on] == ["b", "c"]


def test_nl_numbers_added_agents_sequentially():
    spec, _ = nl.edit(BY_ID["research-brief"], "add 2 more reviewers to stage 2")
    assert [n.name for n in spec.layers[1].nodes][-2:] == ["reviewer_1", "reviewer_2"]


def test_errors_point_at_the_problem():
    with pytest.raises(script.ScriptError) as e:
        script.parse("@a(b, ")
    assert e.value.line == 1 and "Missing ')'" in e.value.message
    with pytest.raises(script.ScriptError) as e:
        script.parse("@a(b, c\n@d(e)")
    assert e.value.line == 2 and "Missing ')'" in e.value.message
    with pytest.raises(script.ScriptError) as e:
        script.parse("@a(b)\n@c(d <- nowhere)")
    assert e.value.line == 2 and "nowhere" in e.value.message


def test_nl_builds_research_pipeline():
    spec = nl.build("Research a topic with 4 analysts, combine their findings, draft and edit in a loop, then I approve")
    assert [l.kind for l in spec.layers] == ["stage", "stage", "loop", "gate"]
    assert len(spec.layers[0].nodes) == 4 and spec.layers[1].nodes[0].kind == "code"
    assert not _errors(spec)


def test_nl_builds_router_pipeline():
    spec = nl.build("Triage tickets into billing, technical or general, then compose a reply and ask me before sending")
    assert [l.kind for l in spec.layers] == ["router", "stage", "stage", "gate"]
    assert [r.label for r in spec.layers[0].nodes[0].routes] == ["billing", "technical", "general"]
    assert not _errors(spec)


def test_nl_edits_existing_graph():
    base = BY_ID["research-brief"]
    spec, _ = nl.edit(base, "add 2 more reviewers to stage 2")
    assert len(spec.layers[1].nodes) == 5
    spec, _ = nl.edit(base, "add a human approval after stage 1")
    assert spec.layers[1].kind == "gate"
    spec, _ = nl.edit(base, "remove risk_analyst")
    assert "risk_analyst" not in {n.name for l in spec.layers for n in l.nodes}
    spec, _ = nl.edit(base, "use pro for synthesizer")
    assert spec.layers[2].nodes[0].model == "gemini-2.5-pro"
    assert nl.edit(base, "tell me a joke") is None


def test_chat_routes_script_and_prose():
    base = BY_ID["research-brief"]
    out = asyncio.run(chat.respond([chat.ChatMessage(role="user", content=USER_EXAMPLE)], None))
    assert out["engine"] == "script" and len(out["spec"]["layers"]) == 5
    out = asyncio.run(chat.respond([chat.ChatMessage(role="user", content="add 1 more analyst to stage 2")], base))
    assert out["engine"] == "parser" and len(out["spec"]["layers"][1]["nodes"]) == 4
    bad = asyncio.run(chat.respond([chat.ChatMessage(role="user", content="@a(b")], None))
    assert "error" in bad
