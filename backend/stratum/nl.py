"""Offline natural-language → GraphSpec. A deterministic phrase parser used when no
Gemini key is configured (and as the fallback if the model call fails).

Build:  "Research a topic with 4 analysts, combine their findings, draft and edit in a loop, then I approve"
Edit:   "add 2 more reviewers to stage 2" · "add a human approval after stage 3" · "remove risk_analyst"
        "rename writer to drafter" · "use pro for synthesizer" · "set loops to 4 rounds"
"""

from __future__ import annotations

import re
import uuid

from .script import _ident, _pretty, _singular
from .spec import AgentNode, GraphSpec, Layer, Route

NUMS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10}
AGENT_NOUN = {
    "plan": "planner", "research": "researcher", "write": "writer", "draft": "drafter", "review": "reviewer",
    "summarize": "summarizer", "summarise": "summarizer", "synthesize": "synthesizer", "synthesise": "synthesizer",
    "analyze": "analyst", "analyse": "analyst", "classify": "classifier", "translate": "translator",
    "search": "searcher", "extract": "extractor", "check": "checker", "verify": "verifier", "test": "tester",
    "code": "coder", "edit": "editor", "critique": "critic", "score": "scorer", "rank": "ranker",
    "compose": "composer", "reply": "responder", "respond": "responder", "answer": "responder",
    "generate": "generator", "design": "designer", "fix": "fixer", "find": "finder", "evaluate": "evaluator",
    "judge": "judge", "label": "labeler", "parse": "parser", "ingest": "ingestor", "scrape": "scraper",
    "report": "reporter", "explain": "explainer", "outline": "outliner", "brainstorm": "brainstormer",
    "decide": "decider", "fetch": "fetcher", "read": "reader", "audit": "auditor", "price": "pricer",
    "forecast": "forecaster", "debug": "debugger", "optimize": "optimizer", "format": "formatter",
}
CLAUSE_VERBS = set(AGENT_NOUN) | {"combine", "merge", "aggregate", "consolidate", "collect", "gather", "route",
                                  "triage", "loop", "approve", "ask", "get", "send", "have", "let", "finally", "i", "we"}
STOP = {"a", "an", "the", "and", "or", "of", "to", "for", "with", "in", "on", "by", "then", "it", "its", "their",
        "them", "this", "that", "some", "all", "each", "every", "into", "from", "using", "use", "run", "do", "have",
        "get", "make", "let", "should", "will", "we", "i", "me", "my", "our", "please", "step", "stage", "layer",
        "agents", "agent", "parallel", "different", "independent", "separate", "multiple", "several", "who", "which"}
GENERIC = {"agent", "worker", "bot", "model", "llm", "assistant", "expert", "specialist"}

_NUM = r"(\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten)"
_GATE = re.compile(r"\b(approv\w*|sign[\s-]?off|human|ask me|ask (?:a|the) (?:human|manager|reviewer|lead)|my (?:ok|okay|approval)"
                   r"|confirm with|permission|i (?:review|check|confirm|decide|approve))\b")
_ROUTER = re.compile(r"\b(route|routes|triage|classify|categori[sz]e|dispatch|depending on|based on)\b")
_LOOP = re.compile(r"\b(loop|iterat\w*|until|refine|revis\w*|critic|polish)\b")
_MERGE = re.compile(r"\b(combine|merge|aggregate|consolidate|collect|gather)\b")


def _nid() -> str:
    return f"n_{uuid.uuid4().hex[:6]}"


def _lid() -> str:
    return f"l_{uuid.uuid4().hex[:6]}"


def _num(s: str) -> int:
    return int(s) if s.isdigit() else NUMS.get(s.lower(), 1)


def _cap(s: str) -> str:
    s = s.strip(" .,")
    return s[:1].upper() + s[1:] if s else s


class _Names:
    def __init__(self, taken: set[str] | None = None):
        self.taken = set(taken or ())

    def __call__(self, base: str) -> str:
        base = _ident(base)
        n, k = base, 2
        while n in self.taken:
            n, k = f"{base}_{k}", k + 1
        self.taken.add(n)
        return n


def _agent(names: _Names, base: str, instruction: str = "", kind: str = "agent") -> AgentNode:
    return AgentNode(id=_nid(), name=names(base), kind=kind, instruction=instruction)  # type: ignore[arg-type]


# ─────────────────────────────── build ───────────────────────────────

def split_clauses(text: str) -> list[str]:
    strong = re.split(
        r"\s*(?:->|→|;|\n+|\.\s+|,?\s+(?:and\s+)?then\s+|,?\s+after that\s*,?\s*|,?\s+finally\s*,?\s*"
        r"|\s+and\s+(?=(?:ask|get|wait|require|let|have)\s))\s*",
        text.strip(), flags=re.I)
    out: list[str] = []
    for piece in strong:
        segs = re.split(r",\s*(?:and\s+)?", piece)
        cur = segs[0]
        for s in segs[1:]:
            first = s.split()[0].lower() if s.split() else ""
            if first in CLAUSE_VERBS:
                out.append(cur)
                cur = s
            else:
                cur += ", " + s
        out.append(cur)
    return [c.strip(" .,") for c in out if c.strip(" .,")]


def _first_verb_noun(low: str) -> str | None:
    for w in re.findall(r"[a-z]+", low):
        if w in AGENT_NOUN:
            return AGENT_NOUN[w]
    return None


def _stage(clause: str, names: _Names) -> Layer:
    low = clause.lower()
    verb_noun = _first_verb_noun(low)
    nodes: list[AgentNode] = []
    title = ""

    count = re.search(rf"\b{_NUM}\s+((?:[a-z]+\s+){{0,2}}?[a-z]+s)\b", low)
    listing = re.search(r"\b(?:with|using|including|via|:)\s+(.+)$", low)
    if count and _num(count.group(1)) > 1:
        words = [w for w in count.group(2).split() if w not in STOP]
        base = _singular(words[-1]) if words else "agent"
        if base in GENERIC:
            base = verb_noun or "agent"
        stem = "_".join(words[:-1] + [base]) if words and base not in GENERIC else base
        n = _num(count.group(1))
        nodes = [_agent(names, f"{stem}_{k}", f"{_cap(clause)} — perspective {k} of {n}.") for k in range(1, n + 1)]
        title = _pretty(stem) + "s"
    elif listing and re.search(r",|\band\b|&|\bor\b", listing.group(1)):
        for part in re.split(r",\s*|\s+and\s+|\s+&\s+|\s+or\s+", listing.group(1)):
            words = [w for w in re.findall(r"[a-z]+", part) if w not in STOP][:3]
            if words:
                nodes.append(_agent(names, "_".join(words), f"{_cap(clause)}. Your focus: {' '.join(words)}."))
        title = _cap(clause.split()[0]) if clause.split() else "Stage"
    if not nodes:
        base = verb_noun or next((w for w in re.findall(r"[a-z]+", low) if w not in STOP), "agent") + "_agent"
        nodes = [_agent(names, base, _cap(clause) + ".")]
        title = _cap(clause.split()[0]) if clause.split() else "Stage"
    return Layer(id=_lid(), title=title or "Stage", kind="stage", nodes=nodes)


def _router(clause: str, names: _Names) -> list[Layer]:
    low = clause.lower()
    labels: list[str] = []
    m = re.search(r"\b(?:into|to|between|among|as|by)\s+(.+)$", low)
    if m:
        for part in re.split(r",\s*|\s+or\s+|\s+and\s+|/", m.group(1)):
            words = [w for w in re.findall(r"[a-z]+", part) if w not in STOP][:2]
            if words:
                labels.append(_ident("_".join(words)))
    labels = list(dict.fromkeys(labels))
    if len(labels) < 2:
        labels = ["general", "specialist"]
    router = _agent(names, "triage" if "triage" in low else "router", _cap(clause) + ".")
    branches = Layer(id=_lid(), title="Branches", kind="stage")
    for label in labels:
        t = _agent(names, f"{label}_agent", f"Handle {label.replace('_', ' ')} requests end to end.")
        branches.nodes.append(t)
        router.routes.append(Route(label=label, target=t.id, when=label.replace("_", " ")))
    return [Layer(id=_lid(), title="Triage" if "triage" in low else "Route", kind="router", nodes=[router]), branches]


def _loop(clause: str, names: _Names) -> Layer:
    low = clause.lower()
    gen = next((AGENT_NOUN[w] for w in re.findall(r"[a-z]+", low) if w in ("write", "draft", "generate", "code", "compose", "design")), "writer")
    critic = "editor" if "edit" in low else "reviewer" if "review" in low else "critic"
    m = re.search(rf"\b(?:up to\s+)?{_NUM}\s*(?:x|times|rounds|iterations|passes)\b", low)
    rounds = min(10, max(1, _num(m.group(1)))) if m else 3
    return Layer(id=_lid(), title="Refine loop", kind="loop", maxIterations=rounds, nodes=[
        _agent(names, gen, f"{_cap(clause)} — produce the draft, and revise it whenever the {critic} sends feedback."),
        _agent(names, critic, "Review the draft for clarity, accuracy and completeness. Reply PASS only when it is ready."),
    ])


def _gate(clause: str, names: _Names) -> Layer:
    words = clause.split()
    question = "Approve to continue?" if len(words) <= 3 else _cap(clause).rstrip("?.") + "?"
    return Layer(id=_lid(), title="Approval", kind="gate", nodes=[_agent(names, "approval", question, "human")])


def _merge(clause: str, names: _Names) -> Layer:
    verb = next((w for w in re.findall(r"[a-z]+", clause.lower()) if _MERGE.match(w)), "combine")
    return Layer(id=_lid(), title=_pretty(verb), kind="stage",
                 nodes=[_agent(names, verb, "Deterministic merge — no model call.", "code")])


def build(text: str) -> GraphSpec:
    names = _Names()
    layers: list[Layer] = []
    for clause in split_clauses(text):
        low = clause.lower()
        if _GATE.search(low):
            layers.append(_gate(clause, names))
        elif _ROUTER.search(low):
            layers += _router(clause, names)
        elif _LOOP.search(low) or (re.search(r"\b(draft|write)\b", low) and re.search(r"\b(edit|review|critique)\b", low)):
            layers.append(_loop(clause, names))
        elif _MERGE.search(low) and not re.search(r"\b(summari[sz]e|synthesi[sz]e|analy[sz]e)\b", low):
            layers.append(_merge(clause, names))
        else:
            layers.append(_stage(clause, names))
    first = split_clauses(text)[0] if text.strip() else "New graph"
    name = _cap(" ".join(first.split()[:6]))[:48] or "New graph"
    return GraphSpec(id=f"chat-{uuid.uuid4().hex[:6]}", name=name, description=_cap(text)[:240], layers=layers)


# ─────────────────────────────── edit ───────────────────────────────

def _layer_index(spec: GraphSpec, ref: str) -> int | None:
    ref = ref.strip().lower().rstrip(".")
    if ref in ("end", "last", "the end", "last layer", "last stage"):
        return len(spec.layers) - 1
    if ref in ("start", "first", "beginning", "the start"):
        return 0
    m = re.search(r"(?:stage|layer|step|level)\s*#?(\d+)", ref) or re.fullmatch(r"#?(\d+)", ref)
    if m:
        i = int(m.group(1)) - 1
        return i if 0 <= i < len(spec.layers) else None
    key = re.sub(r"[^a-z0-9]", "", ref)
    for i, l in enumerate(spec.layers):
        if re.sub(r"[^a-z0-9]", "", l.title.lower()) == key:
            return i
    for i, l in enumerate(spec.layers):
        if any(n.name == _ident(ref) for n in l.nodes):
            return i
    return None


def _tidy(spec: GraphSpec) -> None:
    spec.layers = [l for l in spec.layers if l.nodes]
    ids = {n.id for l in spec.layers for n in l.nodes}
    for l in spec.layers:
        for n in l.nodes:
            n.routes = [r for r in n.routes if r.target in ids]
            if n.depends_on is not None:
                n.depends_on = [d for d in n.depends_on if d in ids]


def edit(spec: GraphSpec, text: str) -> tuple[GraphSpec, str] | None:
    s = spec.model_copy(deep=True)
    t = text.strip().rstrip(".!")
    low = t.lower()
    names = _Names({n.name for l in s.layers for n in l.nodes})

    m = re.match(rf"^(?:add|include|insert|put)\s+(?:{_NUM}\s+)?(?:more\s+)?(.+?)\s+(?:to|in|into|inside)\s+(?:the\s+)?(.+)$", low)
    if m and not re.match(r"^(?:add|insert|put)\s+(?:an?\s+)?(?:human|approval|sign|gate|merge|combine|loop)", low):
        i = _layer_index(s, m.group(3))
        if i is None:
            return None
        layer = s.layers[i]
        if layer.kind != "stage":
            return s, f"Layer {i + 1} ({layer.title}) is a {layer.kind} layer — agents can only be added to stages."
        n = _num(m.group(1)) if m.group(1) else 1
        words = [w for w in re.findall(r"[a-z]+", m.group(2)) if w not in STOP][:3] or ["agent"]
        base = "_".join(words[:-1] + [_singular(words[-1])])
        added = [_agent(names, f"{base}_{k}" if n > 1 else base, "") for k in range(1, n + 1)]
        layer.nodes += added
        return s, f"Added {', '.join(a.name for a in added)} to layer {i + 1} ({layer.title}) — they run in parallel with the rest."

    m = re.match(r"^(?:add|insert|put)\s+(?:an?\s+)?(human(?:\s+\w+)?|approval|sign[\s-]?off|gate|merge|combine|(?:review\s+|refine\s+)?loop|stage)\b.*?(?:\b(after|before)\s+(.+))?$", low)
    if m:
        what = m.group(1)
        at = len(s.layers)
        if m.group(2):
            i = _layer_index(s, m.group(3))
            if i is None:
                return None
            at = i + 1 if m.group(2) == "after" else i
            if m.group(2) == "after" and s.layers[i].kind == "router":
                at += 1
        layer = (_gate("approve", names) if re.match(r"human|approval|sign|gate", what)
                 else _merge(what, names) if what in ("merge", "combine")
                 else _loop(what, names) if "loop" in what
                 else Layer(id=_lid(), title="New stage", kind="stage", nodes=[_agent(names, "agent")]))
        s.layers.insert(at, layer)
        return s, f"Inserted a {layer.kind} layer “{layer.title}” at position {at + 1}."

    m = re.match(r"^(?:remove|delete|drop)\s+(?:the\s+)?(.+)$", low)
    if m:
        target = m.group(1).strip()
        node_name = _ident(target)
        for l in s.layers:
            hit = next((n for n in l.nodes if n.name == node_name), None)
            if hit:
                if l.kind == "stage" and len(l.nodes) > 1:
                    l.nodes.remove(hit)
                    _tidy(s)
                    return s, f"Removed {hit.name} from {l.title}."
                idx = s.layers.index(l)
                del s.layers[idx: idx + (2 if l.kind == "router" else 1)]
                _tidy(s)
                return s, f"Removed layer “{l.title}” (it only held {hit.name})."
        i = _layer_index(s, target)
        if i is None:
            return None
        gone = s.layers[i]
        del s.layers[i: i + (2 if gone.kind == "router" else 1)]
        _tidy(s)
        return s, f"Removed layer {i + 1} (“{gone.title}”)."

    m = re.match(r"^rename\s+(\S+)\s+(?:to|as)\s+(\S+)$", low)
    if m:
        old, new = _ident(m.group(1)), _ident(m.group(2))
        for l in s.layers:
            for n in l.nodes:
                if n.name == old:
                    n.name = names(new)
                    return s, f"Renamed {old} → {n.name}."
        i = _layer_index(s, m.group(1))
        if i is not None:
            s.layers[i].title = _pretty(new)
            return s, f"Renamed layer {i + 1} to “{s.layers[i].title}”."
        return None

    m = re.search(r"\b(?:use|switch(?:\s+to)?|set|make\s+(?:it|everything))\s+(?:gemini[\s-]*)?(?:2\.5[\s-]*)?(pro|flash[\s-]lite|flash|lite)\b(?:\s+(?:for|on)\s+(\S+))?", low)
    if m:
        model = {"pro": "gemini-2.5-pro", "flash": "gemini-2.5-flash"}.get(m.group(1), "gemini-2.5-flash-lite")
        if m.group(2):
            for l in s.layers:
                for n in l.nodes:
                    if n.name == _ident(m.group(2)):
                        n.model = model
                        return s, f"{n.name} now runs on {model}."
            return None
        s.model = model
        return s, f"Default model is now {model}."

    m = re.search(rf"\b{_NUM}\s*(?:rounds|iterations|times|passes)\b", low)
    if m and re.search(r"\b(loop|loops|rounds|iterations)\b", low) and any(l.kind == "loop" for l in s.layers):
        n = min(10, max(1, _num(m.group(1))))
        for l in s.layers:
            if l.kind == "loop":
                l.max_iterations = n
        return s, f"Loops now run at most {n} rounds."

    return None
