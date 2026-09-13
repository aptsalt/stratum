import {
  ChangeDetectionStrategy,
  Component,
  DestroyRef,
  ElementRef,
  afterNextRender,
  afterRenderEffect,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { CdkDrag, CdkDragDrop, CdkDragPlaceholder, CdkDropList, CdkDropListGroup } from '@angular/cdk/drag-drop';
import { EdgeKind, GraphStore } from '../../core/graph-store';
import { AgentNode, LAYER_KINDS, Layer, LayerKind } from '../../core/models';
import { NodeRunStatus, RunStore } from '../../core/run-store';

interface EdgePath {
  id: string;
  kind: EdgeKind;
  from: string;
  to: string;
  d: string;
  label?: string;
  lx: number;
  ly: number;
}

/**
 * Layered canvas: each layer is a band, nodes are cards, edges are measured
 * from the DOM after render and drawn as SVG béziers underneath.
 */
@Component({
  selector: 'app-canvas',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkDropListGroup, CdkDropList, CdkDrag, CdkDragPlaceholder],
  templateUrl: './canvas.html',
  styleUrl: './canvas.scss',
})
export class CanvasComponent {
  readonly store = inject(GraphStore);
  readonly run = inject(RunStore);
  readonly kinds = LAYER_KINDS;
  readonly kindOrder: LayerKind[] = ['stage', 'router', 'loop', 'gate'];

  private readonly content = viewChild.required<ElementRef<HTMLElement>>('content');
  readonly paths = signal<EdgePath[]>([]);
  readonly size = signal({ w: 0, h: 0 });
  readonly inserting = signal<number | null>(null);
  readonly kindMenu = signal<string | null>(null);
  private readonly tick = signal(0);
  private lastKey = '';

  readonly nameOf = computed(() => {
    const m = new Map<string, string>();
    for (const [id, e] of this.store.index()) m.set(id, e.node.name);
    return m;
  });

  readonly routedLabel = computed(() => {
    const m = new Map<string, string>();
    for (const l of this.store.spec().layers)
      if (l.kind === 'router') for (const r of l.nodes[0]?.routes ?? []) m.set(r.target, r.label);
    return m;
  });

  /** Upstream + downstream of the selected node — everything else dims. */
  readonly lineage = computed<Set<string> | null>(() => {
    const sel = this.store.selectedNode();
    if (!sel) return null;
    const edges = this.store.edges().filter((e) => e.kind !== 'loop');
    const seen = new Set<string>([sel.node.id]);
    for (const dir of ['up', 'down'] as const) {
      let frontier = [sel.node.id];
      while (frontier.length) {
        const next: string[] = [];
        for (const e of edges) {
          const [a, b] = dir === 'up' ? [e.to, e.from] : [e.from, e.to];
          if (frontier.includes(a) && !seen.has(b)) {
            seen.add(b);
            next.push(b);
          }
        }
        frontier = next;
      }
    }
    return seen;
  });

  constructor() {
    afterRenderEffect(() => {
      this.store.spec();
      this.store.edges();
      this.run.nodes();
      this.tick();
      this.measure();
    });
    const destroyRef = inject(DestroyRef);
    afterNextRender(() => {
      const ro = new ResizeObserver(() => this.tick.update((v) => v + 1));
      ro.observe(this.content().nativeElement);
      destroyRef.onDestroy(() => ro.disconnect());
    });
  }

  // ───────────── geometry ─────────────
  private measure(): void {
    const root = this.content().nativeElement;
    const base = root.getBoundingClientRect();
    const rect = (id: string) => {
      const el = root.querySelector<HTMLElement>(`[data-anchor="${id}"]`);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height };
    };
    const out: EdgePath[] = [];
    for (const e of this.store.edges()) {
      const a = rect(e.from);
      const b = rect(e.to);
      if (!a || !b) continue;
      let p: [number, number][];
      if (e.kind === 'inner') {
        const [x1, y1, x2, y2] = [a.x + a.w, a.y + a.h / 2, b.x, b.y + b.h / 2];
        p = [[x1, y1], [x1 + 34, y1], [x2 - 34, y2], [x2, y2]];
      } else if (e.kind === 'loop') {
        const [x1, y1, x2, y2] = [a.x + a.w / 2, a.y + a.h, b.x + b.w / 2, b.y + b.h];
        const yb = Math.max(y1, y2) + 48;
        p = [[x1, y1], [x1, yb], [x2, yb], [x2, y2]];
      } else {
        const [x1, y1, x2, y2] = [a.x + a.w / 2, a.y + a.h, b.x + b.w / 2, b.y];
        const dy = Math.max(26, (y2 - y1) * 0.5);
        const bend = e.kind === 'skip' ? -(40 + Math.min(120, (y2 - y1) * 0.18)) : 0;
        p = [[x1, y1], [x1 + bend, y1 + dy], [x2 + bend, y2 - dy], [x2, y2]];
      }
      const d = `M${p[0]} C${p[1]} ${p[2]} ${p[3]}`;
      const mid = (i: 0 | 1) => (p[0][i] + 3 * p[1][i] + 3 * p[2][i] + p[3][i]) / 8;
      out.push({ id: e.id, kind: e.kind, from: e.from, to: e.to, label: e.label, d, lx: mid(0), ly: e.kind === 'loop' ? mid(1) - 2 : mid(1) });
    }
    const size = { w: root.scrollWidth, h: root.scrollHeight };
    const key = JSON.stringify([out, size]);
    if (key === this.lastKey) return;
    this.lastKey = key;
    this.paths.set(out);
    this.size.set(size);
  }

  // ───────────── run state ─────────────
  statusOf(n: AgentNode): NodeRunStatus {
    return this.run.statusOf(n.name);
  }

  private anchorStatus(id: string): NodeRunStatus {
    if (id === 'START') return this.run.runId() ? 'done' : 'idle';
    if (id === 'END') return this.run.status() === 'completed' ? 'done' : 'idle';
    return this.run.statusOf(this.nameOf().get(id) ?? '');
  }

  edgeState(p: EdgePath): string {
    if (this.run.status() === 'idle') return '';
    const a = this.anchorStatus(p.from);
    const b = this.anchorStatus(p.to);
    if (p.kind === 'route') {
      const chosen = this.run.routes()[this.nameOf().get(p.from) ?? ''];
      if (chosen) return chosen !== p.label ? 'dim' : b === 'running' ? 'flow' : 'done';
    }
    if (p.kind === 'loop') return this.run.nodeRun(this.nameOf().get(p.to) ?? '').iterations > 1 ? 'done' : '';
    if (a === 'done' && (b === 'running' || b === 'waiting')) return 'flow';
    if (a === 'done' && b === 'done') return 'done';
    if (a === 'skipped' || b === 'skipped') return 'dim';
    return '';
  }

  edgeDimmed(p: EdgePath): boolean {
    const l = this.lineage();
    return !!l && !(l.has(p.from) && l.has(p.to));
  }

  // ───────────── presentation ─────────────
  pad(i: number): string {
    return String(i).padStart(2, '0');
  }

  meta(layer: Layer): string {
    switch (layer.kind) {
      case 'stage':
        return layer.nodes.length > 1 ? `${layer.nodes.length} agents · run in parallel` : 'single step';
      case 'router':
        return `${layer.nodes[0]?.routes.length ?? 0} routes · exactly one runs`;
      case 'loop':
        return `generator ⇄ critic · ≤ ${layer.maxIterations} rounds`;
      default:
        return 'pauses for a human decision';
    }
  }

  icon(n: AgentNode, layer: Layer): string {
    if (n.kind === 'code') return 'ƒ';
    if (n.kind === 'human') return '✋';
    if (layer.kind === 'router') return '⑂';
    if (layer.kind === 'loop') return layer.nodes[0]?.id === n.id ? '✎' : '◎';
    return '✦';
  }

  role(n: AgentNode, layer: Layer): string {
    if (layer.kind === 'router') return 'classifier';
    if (layer.kind === 'loop') return layer.nodes[0]?.id === n.id ? 'generator' : 'critic';
    if (n.kind === 'code') return n.op;
    if (n.kind === 'human') return 'approver';
    return '';
  }

  modelOf(n: AgentNode): string {
    return (n.model ?? this.store.spec().model).replace(/^gemini-/, '');
  }

  duration(n: AgentNode): string {
    const ms = this.run.durationOf(n.name);
    return ms === null ? '' : ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
  }

  // ───────────── interactions ─────────────
  selectNode(n: AgentNode, ev: Event): void {
    ev.stopPropagation();
    this.store.select({ type: 'node', id: n.id });
  }

  selectLayer(layer: Layer, ev: Event): void {
    ev.stopPropagation();
    this.store.select({ type: 'layer', id: layer.id });
  }

  clearSelection(): void {
    this.store.select(null);
    this.inserting.set(null);
    this.kindMenu.set(null);
  }

  toggleInsert(i: number, ev: Event): void {
    ev.stopPropagation();
    this.inserting.update((v) => (v === i ? null : i));
  }

  insert(i: number, kind: LayerKind, ev: Event): void {
    ev.stopPropagation();
    this.inserting.set(null);
    this.store.addLayer(i, kind);
  }

  toggleKindMenu(layer: Layer, ev: Event): void {
    ev.stopPropagation();
    this.kindMenu.update((v) => (v === layer.id ? null : layer.id));
  }

  setKind(layer: Layer, kind: LayerKind, ev: Event): void {
    ev.stopPropagation();
    this.kindMenu.set(null);
    this.store.setLayerKind(layer.id, kind);
  }

  rename(layer: Layer, ev: Event): void {
    this.store.updateLayer(layer.id, { title: (ev.target as HTMLInputElement).value });
  }

  drop(ev: CdkDragDrop<string>): void {
    this.store.moveNode(ev.item.data as string, ev.container.data, ev.currentIndex);
  }

  canEnter = (_drag: CdkDrag, drop: CdkDropList<string>): boolean =>
    this.store.spec().layers.find((l) => l.id === drop.data)?.kind === 'stage';

  stop(ev: Event): void {
    ev.stopPropagation();
  }
}
