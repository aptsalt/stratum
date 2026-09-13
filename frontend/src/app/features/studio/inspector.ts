import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { GraphStore } from '../../core/graph-store';
import { AgentNode, LAYER_KINDS, Layer, LayerKind, MODELS, TOOLS } from '../../core/models';
import { RunStore } from '../../core/run-store';
import { UiStore } from '../../core/ui-store';

interface DepGroup {
  layer: Layer;
  index: number;
  nodes: AgentNode[];
}

/** Right-hand property panel: node, layer or graph — whichever is selected. */
@Component({
  selector: 'app-inspector',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './inspector.html',
  styleUrl: './inspector.scss',
})
export class InspectorComponent {
  readonly store = inject(GraphStore);
  readonly run = inject(RunStore);
  readonly ui = inject(UiStore);
  readonly kinds = LAYER_KINDS;
  readonly kindOrder: LayerKind[] = ['stage', 'router', 'loop', 'gate'];
  readonly models = MODELS;
  readonly tools = TOOLS;

  readonly hit = this.store.selectedNode;
  readonly layerSel = this.store.selectedLayer;

  readonly routed = computed(() => {
    const h = this.hit();
    if (!h) return null;
    const prev = this.store.spec().layers[h.layerIndex - 1];
    return prev?.kind === 'router' ? (prev.nodes[0]?.routes.find((r) => r.target === h.node.id) ?? null) : null;
  });

  readonly takesInputs = computed(() => {
    const h = this.hit();
    if (!h || h.layerIndex === 0 || this.routed()) return false;
    return h.layer.kind === 'stage' || h.layer.nodes[0]?.id === h.node.id;
  });

  readonly depGroups = computed<DepGroup[]>(() => {
    const h = this.hit();
    if (!h) return [];
    return this.store
      .spec()
      .layers.slice(0, h.layerIndex)
      .map((layer, index) => ({ layer, index, nodes: layer.nodes.filter((n) => layer.kind !== 'loop' || n.id === layer.nodes[0]?.id) }))
      .filter((g) => g.layer.kind !== 'router' || true);
  });

  readonly activeDeps = computed(() => {
    const h = this.hit();
    if (!h) return new Set<string>();
    return new Set(h.node.dependsOn ?? this.store.defaultDeps(h.node.id));
  });

  readonly nextLayerNodes = computed(() => {
    const h = this.hit();
    return h ? (this.store.spec().layers[h.layerIndex + 1]?.nodes ?? []) : [];
  });

  readonly nodeTitle = computed(() => {
    const h = this.hit();
    if (!h) return '';
    if (h.node.kind === 'code') return 'Code node';
    if (h.node.kind === 'human') return 'Human gate';
    if (h.layer.kind === 'router') return 'Router agent';
    if (h.layer.kind === 'loop') return h.layer.nodes[0]?.id === h.node.id ? 'Loop generator' : 'Loop critic';
    return 'LLM agent';
  });

  readonly adkHint = computed(() => {
    const h = this.hit();
    if (!h) return '';
    const n = h.node;
    if (n.kind === 'code') return `FunctionNode(func=${n.name}) — plain Python, zero tokens, runs in milliseconds.`;
    if (n.kind === 'human') return `@node ${n.name} yields RequestInput(message=…) → the Runner pauses until you answer.`;
    return `Agent(name="${n.name}", mode="single_turn", output_key="${n.name}", instruction=…)`;
  });

  readonly joinHint = computed(() => {
    const h = this.hit();
    if (!h || !this.store.joins().has(h.node.name)) return '';
    const names = [...this.activeDeps()].map((id) => this.store.index().get(id)?.node.name).filter(Boolean);
    return `JoinNode("join_${h.node.name}") waits for ${names.join(', ')} and hands this agent one dict keyed by name.`;
  });

  value(ev: Event): string {
    return (ev.target as HTMLInputElement).value;
  }

  setModel(n: AgentNode, ev: Event): void {
    const v = this.value(ev);
    this.store.updateNode(n.id, { model: v || null });
  }

  toggleTool(n: AgentNode, tool: string): void {
    const tools = n.tools.includes(tool) ? n.tools.filter((t) => t !== tool) : [...n.tools, tool];
    this.store.updateNode(n.id, { tools });
  }

  output(n: AgentNode): string {
    return this.run.nodeRun(n.name).output ?? '';
  }

  duration(n: AgentNode): string {
    const ms = this.run.durationOf(n.name);
    return ms === null ? '—' : `${ms} ms`;
  }

  setIterations(layer: Layer, delta: number): void {
    this.store.updateLayer(layer.id, { maxIterations: Math.min(10, Math.max(1, layer.maxIterations + delta)) });
  }

  locate(nodeId?: string | null, layerId?: string | null): void {
    if (nodeId) this.store.select({ type: 'node', id: nodeId });
    else if (layerId) this.store.select({ type: 'layer', id: layerId });
  }
}
