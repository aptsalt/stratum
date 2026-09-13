"""Built-in starter graphs shown in the Studio gallery."""

from __future__ import annotations

from .spec import GraphSpec


def _agent(nid: str, instruction: str, **kw) -> dict:
    return {"id": f"n_{nid}", "name": nid, "kind": "agent", "instruction": instruction, **kw}


RESEARCH_BRIEF = {
    "id": "research-brief",
    "name": "Research Brief",
    "description": "Plan → parallel specialists → synthesis → write/edit loop → human sign-off.",
    "layers": [
        {"id": "l_plan", "title": "Plan", "kind": "stage", "nodes": [
            _agent("planner", "Break the request into 3 research questions and a target audience."),
        ]},
        {"id": "l_research", "title": "Parallel research", "kind": "stage", "nodes": [
            _agent("web_researcher", "Answer the research questions with current, sourced facts.", tools=["google_search"]),
            _agent("market_analyst", "Size the market, name the key players and recent moves."),
            _agent("risk_analyst", "List the top regulatory, technical and adoption risks."),
        ]},
        {"id": "l_synth", "title": "Synthesis", "kind": "stage", "nodes": [
            _agent("synthesizer", "Merge all findings into one structured outline with evidence."),
        ]},
        {"id": "l_write", "title": "Write & edit loop", "kind": "loop", "maxIterations": 3, "nodes": [
            _agent("writer", "Write a crisp 300-word brief from the outline and the original plan.",
                   dependsOn=["n_synthesizer", "n_planner"]),
            _agent("editor", "Review the brief for clarity, evidence and tone."),
        ]},
        {"id": "l_signoff", "title": "Sign-off", "kind": "gate", "nodes": [
            {"id": "n_publish_approval", "name": "publish_approval", "kind": "human",
             "instruction": "Approve this brief for publishing?"},
        ]},
    ],
}

SUPPORT_TRIAGE = {
    "id": "support-triage",
    "name": "Support Triage",
    "description": "A router sends each ticket to exactly one specialist, then a composer drafts the reply.",
    "layers": [
        {"id": "l_triage", "title": "Triage", "kind": "router", "nodes": [
            _agent("triage", "Classify the customer ticket.", routes=[
                {"label": "billing", "target": "n_billing_agent", "when": "invoices, refunds, charges, payment"},
                {"label": "technical", "target": "n_tech_agent", "when": "errors, bugs, outage, login, api"},
                {"label": "general", "target": "n_general_agent", "when": "anything else"},
            ]),
        ]},
        {"id": "l_specialists", "title": "Specialists", "kind": "stage", "nodes": [
            _agent("billing_agent", "Resolve the billing issue using account policy."),
            _agent("tech_agent", "Diagnose the technical issue and give exact next steps."),
            _agent("general_agent", "Answer the question helpfully and briefly."),
        ]},
        {"id": "l_compose", "title": "Compose reply", "kind": "stage", "nodes": [
            _agent("reply_composer", "Turn the specialist answer into a warm, on-brand customer reply."),
        ]},
        {"id": "l_send", "title": "Send approval", "kind": "gate", "nodes": [
            {"id": "n_send_approval", "name": "send_approval", "kind": "human",
             "instruction": "Send this reply to the customer?"},
        ]},
    ],
}

CODE_REVIEW = {
    "id": "code-review-swarm",
    "name": "Code Review Swarm",
    "description": "Four reviewers fan out in parallel; a zero-cost code node merges; one agent rules.",
    "layers": [
        {"id": "l_read", "title": "Read diff", "kind": "stage", "nodes": [
            _agent("diff_reader", "Summarise what the diff changes and which files matter."),
        ]},
        {"id": "l_review", "title": "Parallel reviewers", "kind": "stage", "nodes": [
            _agent("security_reviewer", "Find injection, authz and secret-handling issues."),
            _agent("perf_reviewer", "Find N+1s, hot loops and unnecessary allocations."),
            _agent("style_reviewer", "Flag naming, dead code and readability problems."),
            _agent("test_reviewer", "Identify untested branches and missing edge cases."),
        ]},
        {"id": "l_merge", "title": "Merge findings", "kind": "stage", "nodes": [
            {"id": "n_findings", "name": "findings", "kind": "code", "op": "merge",
             "instruction": "Deterministic merge — no model call."},
        ]},
        {"id": "l_verdict", "title": "Verdict", "kind": "stage", "nodes": [
            _agent("verdict_writer", "Rank the findings and decide approve / request changes.",
                   dependsOn=["n_findings", "n_diff_reader"]),
        ]},
    ],
}

BLANK = {
    "id": "blank",
    "name": "Untitled graph",
    "description": "Start from a single agent.",
    "layers": [
        {"id": "l_1", "title": "Stage 1", "kind": "stage", "nodes": [
            _agent("assistant", "Help the user with their request."),
        ]},
    ],
}

TEMPLATES = [GraphSpec.model_validate(t) for t in (RESEARCH_BRIEF, SUPPORT_TRIAGE, CODE_REVIEW, BLANK)]
