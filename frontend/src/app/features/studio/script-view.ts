import { ChangeDetectionStrategy, Component, ElementRef, computed, effect, inject, signal, viewChild } from '@angular/core';
import { ApiService } from '../../core/api';
import { GraphStore } from '../../core/graph-store';
import { Diagnostic, GraphSpec, ScriptError } from '../../core/models';
import { RunStore } from '../../core/run-store';
import { Autocomplete, CHEATS, ScriptHighlightPipe } from '../../core/script-tools';
import { UiStore } from '../../core/ui-store';
import { GraphMiniComponent } from './graph-mini';

/**
 * Text view of the graph, synced both ways: canvas edits re-format the script;
 * typing parses live (merged with the canvas by name) and Apply commits it.
 */
@Component({
  selector: 'app-script-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphMiniComponent, ScriptHighlightPipe],
  templateUrl: './script-view.html',
  styleUrl: './script-view.scss',
})
export class ScriptViewComponent {
  readonly graph = inject(GraphStore);
  readonly ui = inject(UiStore);
  private readonly api = inject(ApiService);
  private readonly run = inject(RunStore);
  readonly ac = new Autocomplete();
  readonly cheats = CHEATS;

  readonly text = signal('');
  readonly dirty = signal(false);
  readonly compact = signal(false);
  readonly preview = signal<GraphSpec | null>(null);
  readonly error = signal<ScriptError | null>(null);
  readonly diagnostics = signal<Diagnostic[]>([]);
  readonly summary = signal('');
  readonly parsing = signal(false);

  private readonly ta = viewChild.required<ElementRef<HTMLTextAreaElement>>('ta');
  private readonly hl = viewChild.required<ElementRef<HTMLElement>>('hl');
  private readonly gutter = viewChild.required<ElementRef<HTMLElement>>('gutter');
  private parseTimer: ReturnType<typeof setTimeout> | undefined;
  private parseCtrl: AbortController | undefined;

  readonly lines = computed(() => Array.from({ length: this.text().split('\n').length }, (_, i) => i + 1));
  readonly errors = computed(() => this.diagnostics().filter((d) => d.level === 'error').length);
  readonly canApply = computed(() => this.dirty() && !!this.preview() && !this.error() && this.errors() === 0);

  constructor() {
    effect((onCleanup) => {
      if (this.dirty()) return;
      const spec = this.graph.spec();
      const compact = this.compact();
      const ctrl = new AbortController();
      const t = setTimeout(async () => {
        try {
          const r = await this.api.formatScript(spec, compact, ctrl.signal);
          this.text.set(r.text);
          this.summary.set('');
        } catch {
          /* offline banner already explains */
        }
      }, 120);
      onCleanup(() => {
        clearTimeout(t);
        ctrl.abort();
      });
    });

    const handoff = this.ui.scriptHandoff();
    if (handoff) {
      this.ui.scriptHandoff.set(null);
      this.text.set(handoff);
      this.dirty.set(true);
      this.scheduleParse();
    }
  }

  onInput(ev: Event): void {
    const el = ev.target as HTMLTextAreaElement;
    this.text.set(el.value);
    this.dirty.set(true);
    this.ac.update(el, this.graph.spec());
    this.scheduleParse();
  }

  onKey(ev: KeyboardEvent): void {
    const el = ev.target as HTMLTextAreaElement;
    if (this.ac.key(ev, el, (v) => this.setText(v))) return;
    const mod = ev.ctrlKey || ev.metaKey;
    if (mod && (ev.key === 's' || ev.key === 'Enter')) {
      ev.preventDefault();
      ev.stopPropagation();
      this.apply();
    } else if (ev.key === 'Tab') {
      ev.preventDefault();
      const s = el.selectionStart;
      this.setText(el.value.slice(0, s) + '  ' + el.value.slice(el.selectionEnd));
      queueMicrotask(() => el.setSelectionRange(s + 2, s + 2));
    }
  }

  syncScroll(): void {
    const el = this.ta().nativeElement;
    this.hl().nativeElement.scrollTop = el.scrollTop;
    this.hl().nativeElement.scrollLeft = el.scrollLeft;
    this.gutter().nativeElement.scrollTop = el.scrollTop;
  }

  setText(v: string): void {
    this.text.set(v);
    this.ta().nativeElement.value = v;
    this.dirty.set(true);
    this.scheduleParse();
  }

  insert(code: string): void {
    const v = this.text().replace(/\s*$/, '');
    this.setText(v ? `${v}\n${code}` : code);
  }

  apply(): void {
    const p = this.preview();
    if (!this.canApply() || !p) return;
    const cur = this.graph.spec();
    this.run.reset(); // structure changed — the last run no longer matches the canvas
    this.graph.load({ ...p, id: cur.id, name: cur.name, description: cur.description });
    this.dirty.set(false);
    this.preview.set(null);
  }

  revert(): void {
    this.dirty.set(false);
    this.preview.set(null);
    this.error.set(null);
    this.diagnostics.set([]);
  }

  gotoError(): void {
    const e = this.error();
    const el = this.ta().nativeElement;
    if (!e) return;
    const lines = this.text().split('\n');
    const pos = lines.slice(0, e.line - 1).reduce((n, l) => n + l.length + 1, 0) + e.col - 1;
    el.focus();
    el.setSelectionRange(pos, pos + 1);
  }

  private scheduleParse(): void {
    clearTimeout(this.parseTimer);
    this.parseTimer = setTimeout(async () => {
      this.parseCtrl?.abort();
      const ctrl = (this.parseCtrl = new AbortController());
      this.parsing.set(true);
      try {
        const r = await this.api.parseScript(this.text(), this.graph.spec(), ctrl.signal);
        this.error.set(r.error);
        this.preview.set(r.spec);
        this.diagnostics.set(r.diagnostics ?? []);
        this.summary.set(r.summary ?? '');
      } catch {
        /* aborted or offline */
      } finally {
        if (!ctrl.signal.aborted) this.parsing.set(false);
      }
    }, 180);
  }
}
