import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { ApiService } from '../../core/api';
import { GraphStore, SAMPLE_INPUTS } from '../../core/graph-store';
import { GraphSpec, Health, LAYER_KINDS, LayerKind } from '../../core/models';
import { RunStore } from '../../core/run-store';
import { UiStore } from '../../core/ui-store';
import { StudioView } from '../../core/ui-store';
import { CanvasComponent } from './canvas';
import { ChatViewComponent } from './chat-view';
import { CommandPaletteComponent, PaletteAction } from './command-palette';
import { ScriptViewComponent } from './script-view';
import { download } from './download';
import { InspectorComponent } from './inspector';
import { RunDockComponent } from './run-dock';

@Component({
  selector: 'app-studio',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CanvasComponent, ChatViewComponent, ScriptViewComponent, InspectorComponent, RunDockComponent, CommandPaletteComponent],
  templateUrl: './studio.html',
  styleUrl: './studio.scss',
  host: { '(document:keydown)': 'onKey($event)' },
})
export class StudioComponent {
  readonly store = inject(GraphStore);
  readonly run = inject(RunStore);
  readonly ui = inject(UiStore);
  private readonly api = inject(ApiService);
  private readonly router = inject(Router);

  readonly kinds = LAYER_KINDS;
  readonly kindOrder: LayerKind[] = ['stage', 'router', 'loop', 'gate'];
  readonly health = signal<Health | null>(null);
  readonly dockHeight = signal(318);
  readonly rejecting = signal(false);
  readonly reason = signal('');
  readonly coachDismissed = signal(readFlag('stratum.coach'));

  readonly views: { id: StudioView; label: string; glyph: string; hint: string }[] = [
    { id: 'graph', label: 'Graph', glyph: '◇', hint: 'Visual layered canvas' },
    { id: 'chat', label: 'Chat', glyph: '✦', hint: 'Describe the graph in English or script' },
    { id: 'script', label: 'Script', glyph: '⌨', hint: 'Edit the graph as Stratum Script' },
  ];
  readonly viewHint = computed(() => {
    switch (this.ui.view()) {
      case 'chat':
        return 'Describe it or type @stage(…) — proposals preview before they touch the canvas';
      case 'script':
        return '@layer(agents…) per layer · synced both ways with the canvas';
      default:
        return 'Drag agents between stages · hover between layers to insert';
    }
  });

  readonly canRun = computed(
    () => !this.run.busy() && !this.store.offline() && this.store.errors().length === 0 && this.store.spec().layers.length > 0,
  );
  readonly healthState = computed(() => {
    if (this.store.offline()) return { tone: 'bad', text: 'offline' };
    if (this.store.compiling()) return { tone: '', text: 'compiling…' };
    const e = this.store.errors().length;
    const w = this.store.diagnostics().length - e;
    if (e) return { tone: 'bad', text: `${e} error${e > 1 ? 's' : ''}` };
    if (w) return { tone: 'warn', text: `${w} warning${w > 1 ? 's' : ''}` };
    return { tone: 'ok', text: 'valid' };
  });

  readonly actions = computed<PaletteAction[]>(() => {
    const sel = this.store.selectedNode();
    const layerSel = this.store.selectedLayer();
    const stageId = sel?.layer.kind === 'stage' ? sel.layer.id : layerSel?.layer.kind === 'stage' ? layerSel.layer.id : null;
    const end = this.store.spec().layers.length;
    return [
      { id: 'run', group: 'Run', label: 'Run graph', hint: 'Ctrl ↵', disabled: !this.canRun(), run: () => this.runGraph() },
      { id: 'mode-graph', group: 'Run', label: 'Compile as graph workflow', run: () => this.store.mode.set('graph') },
      { id: 'mode-dyn', group: 'Run', label: 'Compile as dynamic workflow', run: () => this.store.mode.set('dynamic') },
      ...this.kindOrder.map((k) => ({
        id: `add-${k}`,
        group: 'Build',
        label: `Add ${this.kinds[k].label.toLowerCase()} layer`,
        hint: this.kinds[k].glyph,
        run: () => this.store.addLayer(end, k),
      })),
      { id: 'add-agent', group: 'Build', label: 'Add agent to selected stage', disabled: !stageId, run: () => stageId && this.store.addNode(stageId) },
      { id: 'add-code', group: 'Build', label: 'Add code node to selected stage', disabled: !stageId, run: () => stageId && this.store.addNode(stageId, 'code') },
      { id: 'dup', group: 'Build', label: 'Duplicate selected agent', disabled: sel?.layer.kind !== 'stage', run: () => sel && this.store.duplicateNode(sel.node.id) },
      { id: 'del', group: 'Build', label: 'Delete selected agent', hint: 'Del', disabled: sel?.layer.kind !== 'stage', run: () => sel && this.store.removeNode(sel.node.id) },
      { id: 'undo', group: 'Edit', label: 'Undo', hint: 'Ctrl Z', disabled: !this.store.canUndo(), run: () => this.store.undo() },
      { id: 'redo', group: 'Edit', label: 'Redo', hint: 'Ctrl ⇧ Z', disabled: !this.store.canRedo(), run: () => this.store.redo() },
      ...this.store.templates().map((t) => ({ id: `tpl-${t.id}`, group: 'Templates', label: `Load “${t.name}”`, run: () => this.loadTemplate(t) })),
      ...this.views.map((v, i) => ({ id: `view-${v.id}`, group: 'View', label: `${v.label} view — ${v.hint.toLowerCase()}`, hint: `Alt ${i + 1}`, run: () => this.ui.setView(v.id) })),
      { id: 'code', group: 'View', label: 'Show Python export', run: () => this.ui.openDock('code') },
      { id: 'ir', group: 'View', label: 'Show compiled ADK graph', run: () => this.ui.openDock('ir') },
      { id: 'problems', group: 'View', label: 'Show problems', run: () => this.ui.openDock('problems') },
      { id: 'theme', group: 'View', label: 'Toggle light / dark theme', run: () => this.ui.toggleTheme() },
      { id: 'learn', group: 'View', label: 'How ADK graph workflows work', run: () => void this.router.navigate(['/learn']) },
      { id: 'export-py', group: 'Export', label: 'Download agent.py', disabled: !this.store.compiled()?.code, run: () => this.exportPython() },
      { id: 'export-json', group: 'Export', label: 'Download graph JSON', run: () => this.exportJson() },
    ];
  });

  constructor() {
    this.api
      .health()
      .then((h) => this.health.set(h))
      .catch(() => this.health.set(null));

    // seed the run input with a sample that matches the loaded template
    effect(() => {
      const id = this.store.spec().id;
      const current = this.run.input();
      if (!current || Object.values(SAMPLE_INPUTS).includes(current)) this.run.input.set(SAMPLE_INPUTS[id] ?? SAMPLE_INPUTS['blank']);
    });

    const destroyRef = inject(DestroyRef);
    destroyRef.onDestroy(() => this.ui.paletteOpen.set(false));
  }

  runGraph(): void {
    if (!this.canRun()) return;
    if (!this.run.input().trim()) this.run.input.set(SAMPLE_INPUTS['blank']);
    this.dismissCoach();
    this.ui.openDock('run');
    void this.run.start(this.store.spec(), this.store.mode());
  }

  loadTemplate(t: GraphSpec): void {
    this.run.reset();
    this.store.load(t);
    this.run.input.set(SAMPLE_INPUTS[t.id] ?? SAMPLE_INPUTS['blank']);
  }

  approve(): void {
    this.rejecting.set(false);
    void this.run.resume(true);
  }

  confirmReject(): void {
    this.rejecting.set(false);
    void this.run.resume(false, this.reason().trim());
    this.reason.set('');
  }

  exportJson(): void {
    const spec = this.store.spec();
    download(`${spec.id || 'graph'}.stratum.json`, JSON.stringify(spec, null, 2), 'application/json');
  }

  exportPython(): void {
    download('agent.py', this.store.compiled()?.code ?? '', 'text/x-python');
  }

  async importJson(ev: Event): Promise<void> {
    const file = (ev.target as HTMLInputElement).files?.[0];
    if (!file) return;
    try {
      const spec = JSON.parse(await file.text()) as GraphSpec;
      if (!Array.isArray(spec.layers)) throw new Error('not a Stratum graph');
      this.run.reset();
      this.store.load(spec);
    } catch (e) {
      alert(`Could not import: ${e instanceof Error ? e.message : e}`);
    } finally {
      (ev.target as HTMLInputElement).value = '';
    }
  }

  dismissCoach(): void {
    this.coachDismissed.set(true);
    try {
      localStorage.setItem('stratum.coach', '1');
    } catch {
      /* ignore */
    }
  }

  value(ev: Event): string {
    return (ev.target as HTMLInputElement).value;
  }

  startResize(ev: PointerEvent): void {
    ev.preventDefault();
    const startY = ev.clientY;
    const startH = this.dockHeight();
    const move = (e: PointerEvent) => this.dockHeight.set(Math.min(window.innerHeight * 0.7, Math.max(180, startH + startY - e.clientY)));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    this.ui.dockOpen.set(true);
  }

  onKey(ev: KeyboardEvent): void {
    const mod = ev.ctrlKey || ev.metaKey;
    const typing = ev.target instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(ev.target.tagName);
    const key = ev.key.toLowerCase();
    if (ev.altKey && ['1', '2', '3'].includes(ev.key)) {
      ev.preventDefault();
      this.ui.setView(this.views[Number(ev.key) - 1].id);
    } else if (mod && key === 'k') {
      ev.preventDefault();
      this.ui.paletteOpen.update((v) => !v);
    } else if (mod && key === 'enter') {
      ev.preventDefault();
      this.runGraph();
    } else if (typing || this.ui.paletteOpen()) {
      return;
    } else if (mod && (key === 'y' || (key === 'z' && ev.shiftKey))) {
      ev.preventDefault();
      this.store.redo();
    } else if (mod && key === 'z') {
      ev.preventDefault();
      this.store.undo();
    } else if ((key === 'delete' || key === 'backspace') && this.store.selectedNode()?.layer.kind === 'stage') {
      ev.preventDefault();
      this.store.removeNode(this.store.selectedNode()!.node.id);
    } else if (key === 'escape') {
      this.store.select(null);
    }
  }
}

function readFlag(key: string): boolean {
  try {
    return localStorage.getItem(key) === '1';
  } catch {
    return false;
  }
}
