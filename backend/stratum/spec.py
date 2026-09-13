"""GraphSpec — the layered contract the Studio UI edits and the compiler lowers.

A graph is an ordered list of layers. Each layer is a *dependency level*:
nodes in a layer only consume outputs of nodes in earlier layers, which makes
every spec a DAG by construction (loops live *inside* a loop layer).

Layer kinds
  stage   every node runs concurrently once its dependencies finish.
          1 node = a sequential step, N nodes = a parallel fan-out.
  router  one classifier agent picks exactly one route; each route targets a
          node in the NEXT layer (exclusive branches).
  loop    [generator, critic] repeat until the critic answers PASS or
          max_iterations is reached.
  gate    a human node pauses the run (ADK RequestInput) for approve/reject.
"""

from __future__ import annotations

import keyword
import re
from typing import Literal

from pydantic import BaseModel, Field

LayerKind = Literal["stage", "router", "loop", "gate"]
NodeKind = Literal["agent", "code", "human"]
CodeOp = Literal["merge", "passthrough"]

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class Route(BaseModel):
    label: str
    target: str  # node id in the next layer
    when: str = ""  # natural-language condition shown to the router model


class AgentNode(BaseModel):
    id: str
    name: str
    kind: NodeKind = "agent"
    instruction: str = ""
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    op: CodeOp = "merge"
    depends_on: list[str] | None = Field(default=None, alias="dependsOn")
    routes: list[Route] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class Layer(BaseModel):
    id: str
    title: str
    kind: LayerKind = "stage"
    nodes: list[AgentNode] = Field(default_factory=list)
    max_iterations: int = Field(default=3, alias="maxIterations", ge=1, le=10)

    model_config = {"populate_by_name": True}


class GraphSpec(BaseModel):
    id: str
    name: str
    description: str = ""
    model: str = "gemini-2.5-flash"
    layers: list[Layer] = Field(default_factory=list)


class Diagnostic(BaseModel):
    level: Literal["error", "warning"]
    message: str
    layer_id: str | None = Field(default=None, serialization_alias="layerId")
    node_id: str | None = Field(default=None, serialization_alias="nodeId")


def validate_spec(spec: GraphSpec) -> list[Diagnostic]:
    out: list[Diagnostic] = []

    def err(msg: str, layer: Layer | None = None, node: AgentNode | None = None,
            level: Literal["error", "warning"] = "error"):
        out.append(Diagnostic(level=level, message=msg,
                              layer_id=layer.id if layer else None,
                              node_id=node.id if node else None))

    if not spec.layers:
        err("Add at least one layer.")
        return out

    layer_of: dict[str, int] = {}
    names: dict[str, str] = {}
    for li, layer in enumerate(spec.layers):
        if not layer.nodes:
            err(f"Layer '{layer.title}' is empty.", layer)
        for n in layer.nodes:
            layer_of[n.id] = li
            if not _IDENT.match(n.name) or keyword.iskeyword(n.name):
                err(f"'{n.name}' must be a lowercase python identifier.", layer, n)
            if n.name in names.values():
                err(f"Duplicate node name '{n.name}'.", layer, n)
            names[n.id] = n.name
            if n.kind == "human" and layer.kind != "gate":
                err("Human nodes only belong in a gate layer.", layer, n)

        if layer.kind == "router":
            if len(layer.nodes) != 1:
                err("A router layer holds exactly one classifier agent.", layer)
            elif len(layer.nodes[0].routes) < 2:
                err("A router needs at least two routes.", layer, layer.nodes[0])
            if li == len(spec.layers) - 1:
                err("A router must be followed by a layer of branch targets.", layer)
        if layer.kind == "loop" and len(layer.nodes) != 2:
            err("A loop layer holds exactly two agents: generator then critic.", layer)
        if layer.kind == "gate" and (len(layer.nodes) != 1 or layer.nodes[0].kind != "human"):
            err("A gate layer holds exactly one human node.", layer)

    for li, layer in enumerate(spec.layers):
        for n in layer.nodes:
            for dep in n.depends_on or []:
                if dep not in layer_of:
                    err(f"'{n.name}' depends on a node that no longer exists.", layer, n)
                elif layer_of[dep] >= li:
                    err(f"'{n.name}' can only depend on earlier layers.", layer, n)
            if layer.kind == "router":
                for r in n.routes:
                    if layer_of.get(r.target) != li + 1:
                        err(f"Route '{r.label}' must target a node in the next layer.", layer, n)

        if li > 0 and spec.layers[li - 1].kind == "router" and spec.layers[li - 1].nodes:
            if layer.kind != "stage":
                err("The layer after a router must be a stage of branch targets.", layer)
            targets = {r.target for r in spec.layers[li - 1].nodes[0].routes}
            for n in layer.nodes:
                if n.id not in targets:
                    err(f"'{n.name}' is unreachable — no route targets it.", layer, n)

    return out
