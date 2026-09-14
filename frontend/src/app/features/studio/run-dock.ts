import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, input, signal, viewChild } from '@angular/core';
import { DEMO } from '../../core/demo';
import { GraphStore } from '../../core/graph-store';
import { PyHighlightPipe } from '../../core/highlight';
import { Health, RunEvent } from '../../core/models';
import { RunStore } from '../../core/run-store';
import { DockTab, UiStore } from '../../core/ui-store';
import { download } from './download';

interface TimelineRow {
  name: string;
  kind: string;
  layer: number;
  bars: { left: number; width: number; open: boolean }[];
  status: string;
}

@Component({
  selector: 'app-run-dock',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [PyHighlightPipe],
  templateUrl: './run-dock.html',
  styleUrl: './run-dock.scss',
})
export class RunDockComponent {
  readonly store = inject(GraphStore);
  readonly run = inject(RunStore);
  readonly ui = inject(UiStore);
  readonly demo = DEMO;
  readonly geminiReady = input(false);
  readonly health = input<Health | null>(null);

  private readonly logEl = viewChild<ElementRef<HTMLElement>>('logEl');
  readonly copied = signal(false);

  readonly tabs: { id: DockTab; label: string }[] = [
    { id: 'run', label: 'Run' },
    { id: 'code', label: 'Python export' },
    { id: 'ir', label: 'Compiled graph' },
    { id: 'problems', label: 'Problems' },
  ];

  readonly rows = computed<TimelineRow[]>(() => {
    const total = this.run.span() * 1.03;
    const clock = this.run.clock();
    const nodes = this.run.nodes();
    return this.store.spec().layers.flatMap((l, li) =>
      l.nodes.map((n) => {
        const r = nodes[n.name];
        return {
          name: n.name,
          kind: n.kind === 'agent' ? l.kind : n.kind,
          layer: li + 1,
          status: this.run.statusOf(n.name),
          bars: (r?.spans ?? []).map((s) => {
            const end = s.end ?? clock;
            return { left: (s.start / total) * 100, width: Math.max(0.6, ((end - s.start) / total) * 100), open: s.end === undefined };
          }),
        };
      }),
    );
  });

  readonly ticks = computed(() => {
    const total = this.run.span() * 1.03;
    return [0, 0.25, 0.5, 0.75, 1].map((f) => ({ left: f * 100, label: `${((total * f) / 1000).toFixed(1)}s` }));
  });

  readonly parallelPeak = computed(() => {
    const spans = Object.values(this.run.nodes()).flatMap((n) => n.spans);
    let peak = 0;
    for (const s of spans) {
      const at = s.start + 1;
      peak = Math.max(peak, spans.filter((o) => o.start <= at && (o.end ?? Infinity) >= at).length);
    }
    return peak;
  });

  readonly visibleLog = computed(() => this.run.log().filter((e) => e.type !== 'node_end' || !e.internal || e.route));

  readonly irGroups = computed(() => {
    const ir = this.store.compiled()?.ir;
    if (!ir) return null;
    return { nodes: ir.nodes, edges: ir.edges, internal: ir.nodes.filter((n) => n.internal).length };
  });

  constructor() {
    effect(() => {
      this.visibleLog();
      const el = this.logEl()?.nativeElement;
      if (el) queueMicrotask(() => (el.scrollTop = el.scrollHeight));
    });
  }

  describe(e: RunEvent): string {
    switch (e.type) {
      case 'run_start':
        return `run ${e.runId} · ${e.mode} workflow · ${e.engine} engine`;
      case 'run_resume':
        return 'resumed with your decision';
      case 'node_start':
        return `▸ ${e.node}${e.iteration > 1 ? ` (round ${e.iteration})` : ''}`;
      case 'node_end':
        return e.internal ? `⑂ ${e.node} → route "${e.route}"` : `✓ ${e.node}${e.route ? ` → ${e.route}` : ''}`;
      case 'interrupt':
        return `⏸ ${e.node} is waiting: ${e.message}`;
      case 'error':
        return `✕ ${e.message}`;
      case 'run_end':
        return e.status === 'paused' ? '⏸ paused at human gate' : `■ completed${e.tokens ? ` · ${e.tokens} tokens` : ''}`;
    }
  }

  onInput(ev: Event): void {
    this.run.input.set((ev.target as HTMLTextAreaElement).value);
  }

  async copyCode(): Promise<void> {
    await navigator.clipboard.writeText(this.store.compiled()?.code ?? '');
    this.copied.set(true);
    setTimeout(() => this.copied.set(false), 1500);
  }

  downloadCode(): void {
    download('agent.py', this.store.compiled()?.code ?? '', 'text/x-python');
  }

  locate(nodeId?: string | null, layerId?: string | null): void {
    if (nodeId) this.store.select({ type: 'node', id: nodeId });
    else if (layerId) this.store.select({ type: 'layer', id: layerId });
  }

  selectByName(name: string): void {
    const n = this.store.byName().get(name);
    if (n) this.store.select({ type: 'node', id: n.id });
  }
}
