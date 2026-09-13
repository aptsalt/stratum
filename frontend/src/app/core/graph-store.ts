import { Injectable, computed, effect, inject, signal } from '@angular/core';
import { ApiService } from './api';
import { AgentNode, CompileMode, CompileResult, GraphSpec, Layer, LayerKind, NodeKind } from './models';

export type Selection = { type: 'node' | 'layer'; id: string } | null;
export type EdgeKind = 'start' | 'data' | 'skip' | 'route' | 'inner' | 'loop' | 'end';
export interface VisualEdge {
  id: string;
  from: string;
  to: string;
  kind: EdgeKind;
  label?: string;
}

const STORAGE = 'stratum.graph.v1';
const uid = (prefix: string) => `${prefix}_${Math.random().toString(36).slice(2, 8)}`;

export const SAMPLE_INPUTS: Record<string, string> = {
  'research-brief': 'State of on-device LLMs for mobile finance apps in 2026',
  'support-triage': 'I was double charged on my last invoice and need a refund',
  'code-review-swarm': 'Review PR #42: adds silent token refresh to the auth interceptor',
  blank: 'Explain why graph-based agent workflows beat a single mega-prompt',
};

export function sanitizeName(raw: string): string {
  const s = raw.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^[^a-z_]+/, '');
  return s.slice(0, 63) || 'agent';
}

function uniqueName(spec: GraphSpec, base: string, except?: string): string {
  const taken = new Set(spec.layers.flatMap((l) => l.nodes.filter((n) => n.id !== except).map((n) => n.name)));
  const clean = sanitizeName(base);
  if (!taken.has(clean)) return clean;
  let i = 2;
  while (taken.has(`${clean}_${i}`)) i++;
  return `${clean}_${i}`;
}

function makeNode(spec: GraphSpec, base: string, instruction = '', kind: NodeKind = 'agent'): AgentNode {
  return {
    id: uid('n'),
    name: uniqueName(spec, base),
    kind,
    instruction,
    model: null,
    tools: [],
    op: 'merge',
    dependsOn: null,
    routes: [],
  };
}

function makeLayer(spec: GraphSpec, kind: LayerKind): Layer {
  const layer: Layer = { id: uid('l'), title: '', kind, nodes: [], maxIterations: 3 };
  const add = (base: string, instr: string, k: NodeKind = 'agent') => layer.nodes.push(makeNode(spec, base, instr, k));
  if (kind === 'stage') {
    layer.title = 'New stage';
    add('agent', 'Describe what this agent should do.');
  } else if (kind === 'router') {
    layer.title = 'Route';
    add('router', 'Classify the request.');
  } else if (kind === 'loop') {
    layer.title = 'Refine loop';
    add('writer', 'Write a first draft from the inputs.');
    add('critic', 'Review the draft against the brief.');
  } else {
    layer.title = 'Human approval';
    add('approval', 'Approve to continue?', 'human');
  }
  return layer;
}

/** Keeps a spec structurally valid after any edit (routes, deps, node kinds, names). */
function normalize(s: GraphSpec): void {
  const layerOf = new Map<string, number>();
  s.layers.forEach((l, i) => l.nodes.forEach((n) => layerOf.set(n.id, i)));
  const seen = new Set<string>();
  s.layers.forEach((l, i) => {
    for (const n of l.nodes) {
      if (seen.has(n.name)) n.name = uniqueName(s, n.name, n.id);
      seen.add(n.name);
      if (n.dependsOn) n.dependsOn = n.dependsOn.filter((d) => (layerOf.get(d) ?? Infinity) < i);
      if (l.kind !== 'gate' && n.kind === 'human') n.kind = 'agent';
      if (l.kind !== 'router') n.routes = [];
    }
    const router = l.kind === 'router' ? l.nodes[0] : undefined;
    const next = s.layers[i + 1];
    if (router) {
      const ids = new Set(next?.nodes.map((n) => n.id) ?? []);
      router.routes = router.routes.filter((r) => ids.has(r.target));
      for (const n of next?.nodes ?? []) {
        n.dependsOn = null;
        if (router.routes.some((r) => r.target === n.id)) continue;
        let label = n.name.replace(/_agent$/, '');
        while (router.routes.some((r) => r.label === label)) label += '_2';
        router.routes.push({ label, target: n.id, when: '' });
      }
    }
  });
}

/** Edges as the canvas draws them (the IR adds JoinNodes / helper nodes on top). */
export function visualEdges(s: GraphSpec): VisualEdge[] {
  const out = new Map<string, VisualEdge>();
  const push = (e: VisualEdge) => out.set(e.id, e);
  const layerOf = new Map<string, number>();
  s.layers.forEach((l, i) => l.nodes.forEach((n) => layerOf.set(n.id, i)));
  const exitOf = (id: string) => {
    const l = s.layers[layerOf.get(id) ?? 0];
    return l?.kind === 'loop' && l.nodes[1] ? l.nodes[1].id : id;
  };
  const exits = (l?: Layer): string[] =>
    !l || l.kind === 'router' ? [] : l.kind === 'loop' ? l.nodes.slice(1, 2).map((n) => n.id) : l.nodes.map((n) => n.id);

  s.layers.forEach((l, i) => {
    const prev = s.layers[i - 1];
    const router = prev?.kind === 'router' ? prev.nodes[0] : undefined;
    const entries = l.kind === 'stage' ? l.nodes : l.nodes.slice(0, 1);
    for (const n of entries) {
      const route = router?.routes.find((r) => r.target === n.id);
      if (router) {
        if (route) push({ id: `${router.id}>${n.id}`, from: router.id, to: n.id, kind: 'route', label: route.label });
        continue;
      }
      const deps = n.dependsOn ?? exits(prev);
      if (!deps.length) push({ id: `START>${n.id}`, from: 'START', to: n.id, kind: 'start' });
      for (const d of deps) {
        const src = exitOf(d);
        const kind: EdgeKind = (layerOf.get(d) ?? 0) < i - 1 ? 'skip' : 'data';
        push({ id: `${src}>${n.id}`, from: src, to: n.id, kind });
      }
    }
    if (l.kind === 'loop' && l.nodes.length === 2) {
      const [g, c] = l.nodes;
      push({ id: `${g.id}>${c.id}`, from: g.id, to: c.id, kind: 'inner' });
      push({ id: `${c.id}>>${g.id}`, from: c.id, to: g.id, kind: 'loop', label: 'revise' });
    }
  });

  const hasOut = new Set([...out.values()].filter((e) => e.kind !== 'loop').map((e) => e.from));
  s.layers.forEach((l) => {
    const candidates = l.kind === 'loop' ? l.nodes.slice(1) : l.kind === 'router' ? [] : l.nodes;
    for (const n of candidates) if (!hasOut.has(n.id)) push({ id: `${n.id}>END`, from: n.id, to: 'END', kind: 'end' });
  });
  return [...out.values()];
}

const EMPTY: GraphSpec = { id: 'blank', name: 'Untitled graph', description: '', model: 'gemini-2.5-flash', layers: [] };

function loadSaved(): GraphSpec | null {
  try {
    const raw = localStorage.getItem(STORAGE);
    return raw ? (JSON.parse(raw) as GraphSpec) : null;
  } catch {
    return null;
  }
}

/** Single source of truth for the graph being edited. Immutable snapshots → cheap undo/redo. */
@Injectable({ providedIn: 'root' })
export class GraphStore {
  private readonly api = inject(ApiService);
  private readonly saved = loadSaved();

  readonly spec = signal<GraphSpec>(this.saved ?? EMPTY);
  readonly selection = signal<Selection>(null);
  readonly mode = signal<CompileMode>('graph');
  readonly templates = signal<GraphSpec[]>([]);
  readonly compiled = signal<CompileResult | null>(null);
  readonly compiling = signal(false);
  readonly offline = signal(false);
  readonly canUndo = signal(false);
  readonly canRedo = signal(false);

  private past: GraphSpec[] = [];
  private future: GraphSpec[] = [];
  private lastKey = '';
  private lastAt = 0;

  readonly index = computed(() => {
    const map = new Map<string, { node: AgentNode; layer: Layer; layerIndex: number }>();
    this.spec().layers.forEach((layer, layerIndex) =>
      layer.nodes.forEach((node) => map.set(node.id, { node, layer, layerIndex })),
    );
    return map;
  });
  readonly byName = computed(() => new Map([...this.index().values()].map((e) => [e.node.name, e.node])));
  readonly edges = computed(() => visualEdges(this.spec()));
  readonly selectedNode = computed(() => {
    const s = this.selection();
    return s?.type === 'node' ? (this.index().get(s.id) ?? null) : null;
  });
  readonly selectedLayer = computed(() => {
    const s = this.selection();
    if (s?.type !== 'layer') return null;
    const i = this.spec().layers.findIndex((l) => l.id === s.id);
    return i < 0 ? null : { layer: this.spec().layers[i], index: i };
  });
  readonly diagnostics = computed(() => this.compiled()?.diagnostics ?? []);
  readonly errors = computed(() => this.diagnostics().filter((d) => d.level === 'error'));
  readonly joins = computed(
    () => new Set((this.compiled()?.ir?.nodes ?? []).filter((n) => n.role === 'join').map((n) => n.name.slice(5))),
  );
  readonly stats = computed(() => {
    const layers = this.spec().layers;
    const agents = layers.flatMap((l) => l.nodes).filter((n) => n.kind === 'agent').length;
    const width = Math.max(0, ...layers.filter((l) => l.kind === 'stage').map((l) => l.nodes.length));
    const calls = layers.reduce((sum, l) => {
      if (l.kind === 'loop') return sum + 2 * l.maxIterations;
      if (l.kind === 'stage' && layers[layers.indexOf(l) - 1]?.kind === 'router') return sum + 1;
      return sum + l.nodes.filter((n) => n.kind === 'agent').length;
    }, 0);
    return { layers: layers.length, agents, width, calls };
  });

  constructor() {
    effect((onCleanup) => {
      const spec = this.spec();
      const mode = this.mode();
      const ctrl = new AbortController();
      const timer = setTimeout(async () => {
        this.compiling.set(true);
        try {
          this.compiled.set(await this.api.compile(spec, mode, ctrl.signal));
          this.offline.set(false);
        } catch {
          if (!ctrl.signal.aborted) this.offline.set(true);
        } finally {
          if (!ctrl.signal.aborted) this.compiling.set(false);
        }
      }, 220);
      onCleanup(() => {
        clearTimeout(timer);
        ctrl.abort();
      });
    });
    effect(() => {
      const s = this.spec();
      try {
        localStorage.setItem(STORAGE, JSON.stringify(s));
      } catch {
        /* private mode — autosave is a convenience only */
      }
    });
  }

  async init(): Promise<void> {
    try {
      const templates = await this.api.templates();
      this.templates.set(templates);
      if (!this.saved && templates[0]) this.load(templates[0], false);
      this.offline.set(false);
    } catch {
      this.offline.set(true);
    }
  }

  // ───────────── history ─────────────
  private commit(fn: (draft: GraphSpec) => void, key = ''): void {
    const current = this.spec();
    const draft = structuredClone(current);
    fn(draft);
    normalize(draft);
    const now = Date.now();
    if (!(key && key === this.lastKey && now - this.lastAt < 1200)) {
      this.past.push(current);
      if (this.past.length > 120) this.past.shift();
    }
    this.future = [];
    this.lastKey = key;
    this.lastAt = now;
    this.spec.set(draft);
    this.syncHistory();
  }

  undo(): void {
    const prev = this.past.pop();
    if (!prev) return;
    this.future.push(this.spec());
    this.spec.set(prev);
    this.lastKey = '';
    this.syncHistory();
  }

  redo(): void {
    const next = this.future.pop();
    if (!next) return;
    this.past.push(this.spec());
    this.spec.set(next);
    this.lastKey = '';
    this.syncHistory();
  }

  private syncHistory(): void {
    this.canUndo.set(this.past.length > 0);
    this.canRedo.set(this.future.length > 0);
  }

  // ───────────── graph ─────────────
  load(spec: GraphSpec, undoable = true): void {
    if (undoable) this.commit((d) => Object.assign(d, structuredClone(spec)));
    else this.spec.set(structuredClone(spec));
    this.selection.set(null);
  }

  updateGraph(patch: Partial<Pick<GraphSpec, 'name' | 'description' | 'model'>>): void {
    this.commit((d) => Object.assign(d, patch), `graph:${Object.keys(patch).join()}`);
  }

  select(sel: Selection): void {
    this.selection.set(sel);
  }

  // ───────────── layers ─────────────
  addLayer(index: number, kind: LayerKind): void {
    let created = '';
    this.commit((d) => {
      const layer = makeLayer(d, kind);
      created = layer.id;
      d.layers.splice(index, 0, layer);
      if (kind === 'router' && d.layers[index + 1]?.kind !== 'stage') {
        const branches: Layer = { id: uid('l'), title: 'Branches', kind: 'stage', maxIterations: 3, nodes: [] };
        d.layers.splice(index + 1, 0, branches);
        branches.nodes.push(makeNode(d, 'branch_a', 'Handle the first kind of request.'));
        branches.nodes.push(makeNode(d, 'branch_b', 'Handle the second kind of request.'));
      }
    });
    this.selection.set({ type: 'layer', id: created });
  }

  removeLayer(id: string): void {
    this.commit((d) => (d.layers = d.layers.filter((l) => l.id !== id)));
    if (this.selection()?.id === id) this.selection.set(null);
  }

  moveLayer(id: string, delta: -1 | 1): void {
    this.commit((d) => {
      const i = d.layers.findIndex((l) => l.id === id);
      const j = i + delta;
      if (i < 0 || j < 0 || j >= d.layers.length) return;
      [d.layers[i], d.layers[j]] = [d.layers[j], d.layers[i]];
    });
  }

  updateLayer(id: string, patch: Partial<Pick<Layer, 'title' | 'maxIterations'>>): void {
    this.commit((d) => Object.assign(d.layers.find((l) => l.id === id)!, patch), `layer:${id}:${Object.keys(patch)}`);
  }

  setLayerKind(id: string, kind: LayerKind): void {
    this.commit((d) => {
      const i = d.layers.findIndex((l) => l.id === id);
      const l = d.layers[i];
      if (!l || l.kind === kind) return;
      l.kind = kind;
      const agents = l.nodes.filter((n) => n.kind !== 'human');
      if (kind === 'stage') {
        l.nodes.forEach((n) => n.kind === 'human' && (n.kind = 'agent'));
        if (!l.nodes.length) l.nodes.push(makeNode(d, 'agent'));
      } else if (kind === 'router') {
        const first = agents[0] ?? makeNode(d, 'router', 'Classify the request.');
        first.kind = 'agent';
        l.nodes = [first];
        if (d.layers[i + 1]?.kind !== 'stage') {
          const branches: Layer = { id: uid('l'), title: 'Branches', kind: 'stage', maxIterations: 3, nodes: [] };
          d.layers.splice(i + 1, 0, branches);
          branches.nodes.push(makeNode(d, 'branch_a', 'Handle the first kind of request.'));
          branches.nodes.push(makeNode(d, 'branch_b', 'Handle the second kind of request.'));
        }
      } else if (kind === 'loop') {
        const pair = agents.slice(0, 2);
        l.nodes = pair;
        if (!pair[0]) pair.push(makeNode(d, 'writer', 'Write a first draft from the inputs.'));
        if (!pair[1]) pair.push(makeNode(d, 'critic', 'Review the draft against the brief.'));
      } else {
        l.nodes = [makeNode(d, 'approval', 'Approve to continue?', 'human')];
      }
    });
  }

  // ───────────── nodes ─────────────
  addNode(layerId: string, kind: NodeKind = 'agent'): void {
    let created = '';
    this.commit((d) => {
      const l = d.layers.find((x) => x.id === layerId);
      if (!l || l.kind !== 'stage') return;
      const n = makeNode(d, kind === 'code' ? 'merge' : 'agent', kind === 'code' ? 'Deterministic merge — no model call.' : '', kind);
      created = n.id;
      l.nodes.push(n);
    });
    if (created) this.selection.set({ type: 'node', id: created });
  }

  duplicateNode(id: string): void {
    let created = '';
    this.commit((d) => {
      const l = d.layers.find((x) => x.nodes.some((n) => n.id === id));
      if (!l || l.kind !== 'stage') return;
      const src = l.nodes.find((n) => n.id === id)!;
      const copy: AgentNode = { ...structuredClone(src), id: uid('n'), name: uniqueName(d, src.name) };
      created = copy.id;
      l.nodes.splice(l.nodes.indexOf(src) + 1, 0, copy);
    });
    if (created) this.selection.set({ type: 'node', id: created });
  }

  removeNode(id: string): void {
    this.commit((d) => {
      for (const l of d.layers) {
        if (l.kind !== 'stage') continue;
        l.nodes = l.nodes.filter((n) => n.id !== id);
      }
      d.layers = d.layers.filter((l) => l.nodes.length > 0);
    });
    if (this.selection()?.id === id) this.selection.set(null);
  }

  updateNode(id: string, patch: Partial<AgentNode>): void {
    this.commit((d) => {
      for (const l of d.layers) {
        const n = l.nodes.find((x) => x.id === id);
        if (!n) continue;
        Object.assign(n, patch);
        if (patch.name !== undefined) n.name = uniqueName(d, patch.name, id);
      }
    }, `node:${id}:${Object.keys(patch).join()}`);
  }

  moveNode(id: string, toLayerId: string, index: number): void {
    this.commit((d) => {
      const from = d.layers.find((l) => l.nodes.some((n) => n.id === id));
      const to = d.layers.find((l) => l.id === toLayerId);
      if (!from || !to || from.kind !== 'stage' || to.kind !== 'stage') return;
      const node = from.nodes.find((n) => n.id === id)!;
      from.nodes = from.nodes.filter((n) => n.id !== id);
      to.nodes.splice(index, 0, node);
      if (from !== to) node.dependsOn = null;
      d.layers = d.layers.filter((l) => l.nodes.length > 0);
    });
  }

  /** Default deps for a node = every exit of the previous layer. */
  defaultDeps(id: string): string[] {
    const hit = this.index().get(id);
    const prev = hit ? this.spec().layers[hit.layerIndex - 1] : undefined;
    if (!prev || prev.kind === 'router') return [];
    return prev.kind === 'loop' ? prev.nodes.slice(0, 1).map((n) => n.id) : prev.nodes.map((n) => n.id);
  }

  toggleDependency(id: string, dep: string): void {
    const node = this.index().get(id)?.node;
    if (!node) return;
    const current = node.dependsOn ?? this.defaultDeps(id);
    const next = current.includes(dep) ? current.filter((d) => d !== dep) : [...current, dep];
    this.commit((d) => {
      const n = d.layers.flatMap((l) => l.nodes).find((x) => x.id === id);
      if (n) n.dependsOn = next;
    });
  }

  setAutoDependencies(id: string, auto: boolean): void {
    const deps = auto ? null : this.defaultDeps(id);
    this.commit((d) => {
      const n = d.layers.flatMap((l) => l.nodes).find((x) => x.id === id);
      if (n) n.dependsOn = deps;
    });
  }

  updateRoute(routerId: string, target: string, patch: { label?: string; when?: string }): void {
    this.commit((d) => {
      const r = d.layers.flatMap((l) => l.nodes).find((n) => n.id === routerId)?.routes.find((x) => x.target === target);
      if (!r) return;
      if (patch.label !== undefined) r.label = sanitizeName(patch.label);
      if (patch.when !== undefined) r.when = patch.when;
    }, `route:${routerId}:${target}:${Object.keys(patch)}`);
  }
}
