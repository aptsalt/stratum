"""Stratum Script — a one-line syntax for layered graphs.

    start @plan(planner), @research(agt 1,2,3,4), @stage3(combine), @review(abc, abc), @signoff(human)
    @triage "Classify the ticket" route(billing: billing_agent "refunds, invoices", tech: tech_agent "Fix it" if "errors")
    @draft loop(writer <- (planner, research), editor) x3
    @verdict(judge[pro] "Rank the findings" <- (research, combine))

Layers start with @name. Inside the parens:
    a, b, c           parallel agents          agt 1,2,3,4 → agt_1 … agt_4     4 analysts → analyst_1 … analyst_4
    name[tags]        pro | flash | lite | latest | search | merge | passthrough | human
    name "text"       instruction              name <- x   /   name <- (x, y)   explicit inputs (node or layer)
    combine / merge   zero-token code node     human / approve                  human gate
Layer keywords: route(label: target "when", …), loop(generator, critic) xN.

parse(text, base) merges with an existing graph by name: anything the text does not
say (instructions, models, ids, route conditions) is kept from `base`, so a compact
script can be edited without losing detail. format(spec) is the inverse.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from .spec import AgentNode, GraphSpec, Layer, Route

MODEL_TAGS = {
    "pro": "gemini-2.5-pro",
    "flash": "gemini-2.5-flash",
    "lite": "gemini-2.5-flash-lite",
    "flash-lite": "gemini-2.5-flash-lite",
    "latest": "gemini-flash-latest",
}
MODEL_SHORT = {v: k for k, v in MODEL_TAGS.items() if k != "flash-lite"}
CODE_WORDS = {"combine", "merge", "aggregate", "consolidate", "collect"}
HUMAN_WORDS = {"human", "approve", "approval", "approver", "signoff"}
KIND_WORDS = {"route": "router", "router": "router", "loop": "loop", "gate": "gate", "stage": "stage", "parallel": "stage"}
DEFAULT_CODE = "Deterministic merge — no model call."
DEFAULT_HUMAN = "Approve to continue?"

_TOKEN = re.compile(
    r"""(?P<ws>\s+)|(?P<str>"(?:[^"\\]|\\.)*")|(?P<larrow><-|←)|(?P<arrow>->|→|=>)|(?P<at>@)|(?P<num>\d+)"""
    r"""|(?P<ident>[A-Za-z_][A-Za-z0-9_\-]*)|(?P<punc>[()\[\],:;&+|×])"""
)


class ScriptError(ValueError):
    def __init__(self, message: str, pos: int, text: str):
        line = text.count("\n", 0, pos) + 1
        col = pos - (text.rfind("\n", 0, pos) + 1) + 1
        super().__init__(f"{message} (line {line}, col {col})")
        self.message, self.pos, self.line, self.col = message, pos, line, col


@dataclass
class _Tok:
    kind: str
    value: str
    pos: int


@dataclass
class _Item:
    name: str
    pos: int
    tags: list[str] = field(default_factory=list)
    instruction: str | None = None
    refs: list[str] | None = None
    label: str | None = None  # route items only
    when: str | None = None


@dataclass
class _RawLayer:
    ident: str
    pos: int
    kind: str | None = None
    note: str | None = None
    items: list[_Item] = field(default_factory=list)
    iterations: int | None = None


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _ident(s: str) -> str:
    s = re.sub(r"[^a-z0-9_]+", "_", s.lower()).strip("_")
    s = re.sub(r"^[^a-z_]+", "", s)
    return s[:63] or "agent"


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _pretty(ident: str) -> str:
    m = re.fullmatch(r"(stage|layer|step)_?(\d+)", ident)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}"
    return ident.replace("_", " ").strip().capitalize() or "Stage"


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:6]}"


# ─────────────────────────────── parsing ───────────────────────────────

class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks: list[_Tok] = []
        pos = 0
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m:
                raise ScriptError(f"Unexpected character '{text[pos]}'", pos, text)
            if m.lastgroup != "ws":
                self.toks.append(_Tok(m.lastgroup or "", m.group(), pos))
            pos = m.end()
        self.i = 0

    # token helpers
    def peek(self, k: int = 0) -> _Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def at(self, kind: str, value: str | None = None, k: int = 0) -> bool:
        t = self.peek(k)
        return t is not None and t.kind == kind and (value is None or t.value == value)

    def take(self) -> _Tok:
        t = self.toks[self.i]
        self.i += 1
        return t

    def expect(self, kind: str, value: str | None = None, what: str = "") -> _Tok:
        if not self.at(kind, value):
            t = self.peek()
            got = f"'{t.value}'" if t else "end of script"
            raise ScriptError(f"Expected {what or value or kind}, got {got}", t.pos if t else len(self.text), self.text)
        return self.take()

    def error(self, msg: str) -> ScriptError:
        t = self.peek()
        return ScriptError(msg, t.pos if t else len(self.text), self.text)

    # grammar
    def parse(self) -> list[_RawLayer]:
        layers: list[_RawLayer] = []
        while self.peek():
            t = self.peek()
            assert t
            if t.kind in ("arrow",) or (t.kind == "punc" and t.value in ",;"):
                self.take()
            elif t.kind == "ident" and t.value.lower() in ("start", "end", "then") and not self.at("punc", "(", 1):
                self.take()
            elif t.kind == "at" or (t.kind == "ident" and self.at("punc", "(", 1)):
                layers.append(self.layer())
            else:
                raise self.error(f"Expected a layer like @stage(…), got '{t.value}'")
        if not layers:
            raise ScriptError("Write at least one layer, e.g. @research(a, b)", 0, self.text)
        return layers

    def layer(self) -> _RawLayer:
        if self.at("at"):
            self.take()
        name = self.expect("ident", what="a layer name after @")
        raw = _RawLayer(ident=_ident(name.value), pos=name.pos)
        while True:  # "@x loop(…)" → keyword; "@loop(…)" → just a layer named loop
            if self.at("str"):
                raw.note = json.loads(self.take().value)
            elif self.at("ident") and self.peek().value.lower() in KIND_WORDS:  # type: ignore[union-attr]
                raw.kind = KIND_WORDS[self.take().value.lower()]
            elif self.at("ident") and self.peek().value.lower() in HUMAN_WORDS and not self.at("punc", "(", 1):  # type: ignore[union-attr]
                self.take()
                raw.kind = "gate"
            else:
                break
        if self.at("punc", "("):
            raw.items = self.route_items() if raw.kind == "router" else self.items()
        t = self.peek()
        if t and t.kind == "ident" and re.fullmatch(r"[xX]\d+", t.value):
            raw.iterations = int(self.take().value[1:])
        elif self.at("punc", "×") and self.at("num", k=1):
            self.take()
            raw.iterations = int(self.take().value)
        return raw

    def items(self) -> list[_Item]:
        self.expect("punc", "(")
        out: list[_Item] = []
        last_base: str | None = None
        while not self.at("punc", ")"):
            if not self.peek():
                raise self.error("Missing ')'")
            if self.at("at"):
                raise self.error("Missing ')' — close this layer before starting the next @layer")
            if self.at("punc", ","):
                self.take()
                continue
            start = self.peek()
            assert start
            if start.kind == "num":
                n = int(self.take().value)
                if self.at("ident"):  # "4 analysts"
                    words = self.words()
                    base = _ident(_singular(words[-1]))
                    group = [_Item(f"{base}_{k}", start.pos) for k in range(1, n + 1)]
                    self.suffix(group)
                    out += group
                    last_base = base
                    continue
                if last_base is None:
                    raise ScriptError("A bare number needs a name before it, e.g. agt 1, 2, 3", start.pos, self.text)
                item = _Item(f"{last_base}_{n}", start.pos)
            elif start.kind == "ident":
                words = self.words()
                if self.at("num"):  # "agt 1"
                    last_base = _ident("_".join(words))
                    item = _Item(f"{last_base}_{self.take().value}", start.pos)
                else:
                    item = _Item(_ident("_".join(words)), start.pos)
                    last_base = None
            else:
                raise self.error(f"Expected an agent name, got '{start.value}'")
            self.suffix([item])
            out.append(item)
        self.take()
        return out

    def words(self) -> list[str]:
        words = [self.take().value]
        while self.at("ident") and not re.fullmatch(r"[xX]\d+", self.peek().value):  # type: ignore[union-attr]
            words.append(self.take().value)
        return words

    def suffix(self, group: list[_Item]) -> None:
        """Optional [tags], "instruction" and <- refs, in any order."""
        while True:
            if self.at("punc", "["):
                self.take()
                tags: list[str] = []
                while not self.at("punc", "]"):
                    if self.at("punc", ","):
                        self.take()
                    elif self.at("ident"):
                        tags.append(self.take().value.lower())
                    else:
                        raise self.error("Expected a tag like pro, search or merge")
                self.take()
                for it in group:
                    it.tags += tags
            elif self.at("str"):
                text = json.loads(self.take().value)
                for it in group:
                    it.instruction = text
            elif self.at("larrow"):
                self.take()
                refs = self.refs()
                for it in group:
                    it.refs = refs
            else:
                return

    def refs(self) -> list[str]:
        if self.at("punc", "("):
            self.take()
            refs: list[str] = []
            while not self.at("punc", ")"):
                if self.at("punc", ","):
                    self.take()
                elif self.at("ident"):
                    refs.append(self.take().value)
                else:
                    raise self.error("Expected a node or layer name")
            self.take()
            return refs
        refs = [self.expect("ident", what="a node or layer name after <-").value]
        while self.at("punc") and self.peek().value in "&+|":  # type: ignore[union-attr]
            self.take()
            refs.append(self.expect("ident", what="a node or layer name").value)
        return refs

    def route_items(self) -> list[_Item]:
        self.expect("punc", "(")
        out: list[_Item] = []
        while not self.at("punc", ")"):
            if not self.peek():
                raise self.error("Missing ')'")
            if self.at("punc", ","):
                self.take()
                continue
            first = self.expect("ident", what="a route label")
            if self.at("punc", ":"):
                self.take()
                target = self.expect("ident", what="the agent this route runs")
                item = _Item(_ident(target.value), target.pos, label=_ident(first.value))
            else:
                name = _ident(first.value)
                item = _Item(name, first.pos, label=re.sub(r"_agent$", "", name))
            self.suffix([item])
            if self.at("ident", "if"):  # label: agent "instruction" if "condition"
                self.take()
                item.when = json.loads(self.expect("str", what='a quoted condition after if').value)
            else:  # a lone string on a route is the condition
                item.when, item.instruction = item.instruction, None
            out.append(item)
        self.take()
        return out


def parse(text: str, base: GraphSpec | None = None) -> GraphSpec:
    raw = _Parser(text).parse()
    return _build(raw, base, text)


def _build(raw: list[_RawLayer], base: GraphSpec | None, text: str) -> GraphSpec:
    base_layers = {(_key(l.title)): (i, l) for i, l in enumerate(base.layers)} if base else {}
    base_nodes = {n.name: n for l in base.layers for n in l.nodes} if base else {}
    used_base: set[str] = set()
    taken: set[str] = set()
    pending: list[tuple[AgentNode, list[str], _Item]] = []
    layers: list[Layer] = []
    idents: dict[str, Layer] = {}

    def uniq(name: str) -> str:
        n, k = name, 2
        while n in taken:
            n, k = f"{name}_{k}", k + 1
        taken.add(n)
        return n

    def mk(item: _Item, layer_kind: str, fallback_human: str | None = None) -> AgentNode:
        raw_name = item.name
        is_code = raw_name in CODE_WORDS and not item.tags or any(t in ("merge", "passthrough", "code") for t in item.tags)
        is_human = raw_name in HUMAN_WORDS or "human" in item.tags or layer_kind == "gate"
        name_hint = fallback_human if is_human and raw_name in HUMAN_WORDS and fallback_human else raw_name
        b = base_nodes.get(name_hint) if name_hint not in used_base else None
        if b:
            used_base.add(name_hint)
        name = uniq(_ident(name_hint))
        kind = "human" if is_human else "code" if is_code else (b.kind if b and b.kind != "human" else "agent")
        model = next((MODEL_TAGS.get(t, t) for t in item.tags if t in MODEL_TAGS or t.startswith("gemini")), None)
        tools = ["google_search"] if "search" in item.tags else (list(b.tools) if b else [])
        op = "passthrough" if "passthrough" in item.tags else (b.op if b and not is_code else "merge")
        default = DEFAULT_HUMAN if kind == "human" else DEFAULT_CODE if kind == "code" else ""
        node = AgentNode(
            id=b.id if b else _new_id("n"), name=name, kind=kind,
            instruction=item.instruction if item.instruction is not None else (b.instruction if b else default),
            model=model or (b.model if b else None), tools=tools, op=op,
        )
        if item.refs:
            pending.append((node, item.refs, item))
        return node

    for rl in raw:
        hit = base_layers.get(_key(rl.ident))
        bi, bl = hit if hit else (None, None)
        kind = rl.kind or ("gate" if rl.items and all(i.name in HUMAN_WORDS or "human" in i.tags for i in rl.items) else "stage")
        layer = Layer(id=bl.id if bl and bl.kind == kind else _new_id("l"), title=bl.title if bl else _pretty(rl.ident),
                      kind=kind, maxIterations=rl.iterations or (bl.max_iterations if bl else 3))
        idents[rl.ident] = layer

        if kind == "router":
            b_router = bl.nodes[0] if bl and bl.kind == "router" and bl.nodes else None
            router = mk(_Item(b_router.name if b_router else rl.ident, rl.pos, instruction=rl.note), "router")
            if router.instruction == "":
                router.instruction = "Classify the request."
            b_next = base.layers[bi + 1] if base and bi is not None and bi + 1 < len(base.layers) else None
            branch = Layer(id=b_next.id if b_next and b_router else _new_id("l"),
                           title=b_next.title if b_next and b_router else f"{layer.title} branches", kind="stage")
            old_when = {r.label: r.when for r in (b_router.routes if b_router else [])}
            if len(rl.items) < 2:
                raise ScriptError("A route needs at least two branches, e.g. route(a: x, b: y)", rl.pos, text)
            for it in rl.items:
                target = mk(_Item(it.name, it.pos, tags=it.tags, instruction=it.instruction), "stage")
                branch.nodes.append(target)
                router.routes.append(Route(label=it.label or target.name, target=target.id,
                                           when=it.when if it.when is not None else old_when.get(it.label or "", "")))
            layer.nodes = [router]
            layers += [layer, branch]
            idents.setdefault(_ident(branch.title), branch)
            continue

        items = rl.items or [_Item("human" if kind == "gate" else rl.ident, rl.pos, instruction=rl.note)]
        if kind == "loop" and len(items) == 1:
            items.append(_Item("critic", rl.pos))
        gate_name = (bl.nodes[0].name if bl and bl.kind == "gate" and bl.nodes
                     else f"{rl.ident}_approval" if not rl.ident.endswith(("approval", "gate")) else rl.ident)
        layer.nodes = [mk(it, kind, gate_name) for it in items]
        layers.append(layer)

    # refs are forgiving: "stage2", "stage_2" and "Stage 2" all name the same layer
    nodes_by_key = {_key(n.name): n for l in layers for n in l.nodes}
    layers_by_key = {**{_key(l.title): l for l in layers}, **{_key(k): l for k, l in idents.items()}}
    for node, refs, item in pending:
        deps: list[str] = []
        for ref in refs:
            k = _key(ref)
            if k in nodes_by_key:
                deps.append(nodes_by_key[k].id)
            elif k in layers_by_key:
                l = layers_by_key[k]
                deps += [n.id for n in (l.nodes[:1] if l.kind in ("loop", "router", "gate") else l.nodes)]
            else:
                raise ScriptError(f"Unknown input '{ref}' — use a node or layer name", item.pos, text)
        node.depends_on = deps

    return GraphSpec(
        id=base.id if base else f"script-{uuid.uuid4().hex[:6]}",
        name=base.name if base else "Scripted graph",
        description=base.description if base else "",
        model=base.model if base else "gemini-2.5-flash",
        layers=layers,
    )


# ─────────────────────────────── formatting ───────────────────────────────

def format(spec: GraphSpec, compact: bool = False) -> str:  # noqa: A001 — mirrors parse()
    layers = spec.layers
    by_id = {n.id: n for l in layers for n in l.nodes}
    seen: dict[str, int] = {}
    lines: list[str] = []

    def ident_for(layer: Layer) -> str:
        base = re.sub(r"_(\d+)$", r"\1", _ident(layer.title)) or "stage"  # "Stage 2" → @stage2
        seen[base] = seen.get(base, 0) + 1
        return base if seen[base] == 1 else f"{base}_{seen[base]}"

    def q(s: str) -> str:
        return json.dumps(s, ensure_ascii=False)

    def item(n: AgentNode, with_text: bool = True) -> str:
        tags: list[str] = []
        if n.kind == "code":
            tags.append(n.op)
        if n.kind == "human":
            tags.append("human")
        if n.model:
            tags.append(MODEL_SHORT.get(n.model, n.model))
        if "google_search" in n.tools:
            tags.append("search")
        s = n.name + (f"[{', '.join(tags)}]" if tags else "")
        default = DEFAULT_CODE if n.kind == "code" else ""
        if with_text and not compact and n.instruction and n.instruction != default:
            s += f" {q(n.instruction)}"
        if n.depends_on is not None:
            names = [by_id[d].name for d in n.depends_on if d in by_id]
            s += " <- " + (names[0] if len(names) == 1 else "(" + ", ".join(names) + ")")
        return s

    def wrap(head: str, parts: list[str], tail: str = "") -> str:
        one = f"{head}({', '.join(parts)}){tail}"
        if compact or len(parts) < 2 or len(one) <= 96:
            return one
        return f"{head}(\n  " + ",\n  ".join(parts) + f"\n){tail}"

    skip = -1
    for i, layer in enumerate(layers):
        if i == skip:
            continue
        ident = ident_for(layer)
        if layer.kind == "router" and layer.nodes:
            r = layer.nodes[0]
            skip = i + 1
            if i + 1 < len(layers):
                ident_for(layers[i + 1])
            head = f"@{ident}"
            if not compact and r.instruction and r.instruction != "Classify the request.":
                head += f" {q(r.instruction)}"
            parts = []
            for route in r.routes:
                t = by_id.get(route.target)
                if not t:
                    continue
                head_item = f"{route.label}: {item(t, with_text=False)}"
                if not compact and t.instruction:
                    parts.append(f"{head_item} {q(t.instruction)} if {q(route.when)}")
                else:
                    parts.append(head_item + (f" {q(route.when)}" if route.when else ""))
            lines.append(wrap(f"{head} route", parts))
        elif layer.kind == "loop":
            lines.append(wrap(f"@{ident} loop", [item(n) for n in layer.nodes], f" x{layer.max_iterations}"))
        else:
            lines.append(wrap(f"@{ident}", [item(n) for n in layer.nodes]))
    return (", " if compact else "\n").join(lines)


def summarize(spec: GraphSpec) -> str:
    parts = []
    for i, l in enumerate(spec.layers):
        if i > 0 and spec.layers[i - 1].kind == "router":
            continue
        names = [n.name for n in l.nodes]
        if l.kind == "router":
            branches = [r.label for r in l.nodes[0].routes] if l.nodes else []
            parts.append(f"{names[0] if names else 'router'} routes to one of {', '.join(branches)}")
        elif l.kind == "loop":
            parts.append(f"{' ⇄ '.join(names)} loop ≤{l.max_iterations}")
        elif l.kind == "gate":
            parts.append("human approval")
        elif len(names) > 1:
            parts.append(f"{len(names)} parallel ({', '.join(names)})")
        else:
            code = l.nodes and l.nodes[0].kind == "code"
            parts.append(f"{names[0]}{' (code, 0 tokens)' if code else ''}")
    return " → ".join(parts)
