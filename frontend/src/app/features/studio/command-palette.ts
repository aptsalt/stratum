import { ChangeDetectionStrategy, Component, ElementRef, afterNextRender, computed, input, output, signal, viewChild } from '@angular/core';

export interface PaletteAction {
  id: string;
  label: string;
  group: string;
  hint?: string;
  disabled?: boolean;
  run: () => void;
}

/** Ctrl+K palette — fuzzy subsequence match, arrow keys + enter. */
@Component({
  selector: 'app-command-palette',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scrim" (click)="closed.emit()"></div>
    <div class="palette" role="dialog" aria-label="Command palette" (keydown)="onKey($event)">
      <div class="search">
        <span class="icon">⌕</span>
        <input #q [value]="query()" (input)="setQuery($event)" placeholder="Type a command — add layer, run, export…" aria-label="Search commands" />
        <span class="kbd">Esc</span>
      </div>
      <ul class="list" role="listbox">
        @for (a of results(); track a.id; let i = $index) {
          @if (i === 0 || results()[i - 1].group !== a.group) {
            <li class="group">{{ a.group }}</li>
          }
          <li
            role="option"
            class="item"
            [class.active]="i === cursor()"
            [class.disabled]="a.disabled"
            [attr.aria-selected]="i === cursor()"
            (mouseenter)="cursor.set(i)"
            (click)="exec(a)"
          >
            <span>{{ a.label }}</span>
            @if (a.hint) { <span class="hint">{{ a.hint }}</span> }
          </li>
        } @empty {
          <li class="none">No matching command.</li>
        }
      </ul>
    </div>
  `,
  styles: `
    :host { position: fixed; inset: 0; z-index: 100; display: grid; justify-items: center; align-items: start; padding-top: 12vh; }
    .scrim { position: absolute; inset: 0; background: rgba(4, 5, 10, 0.55); backdrop-filter: blur(3px); animation: fadeIn 0.12s ease; }
    .palette { position: relative; width: min(620px, calc(100vw - 32px)); border-radius: 16px; background: var(--panel); border: 1px solid var(--line-2); box-shadow: var(--shadow); overflow: hidden; animation: popIn 0.14s ease both; }
    .search { display: flex; align-items: center; gap: 0.6rem; padding: 0.8rem 1rem; border-bottom: 1px solid var(--line); }
    .icon { color: var(--brand-2); font-size: 1.1rem; }
    input { flex: 1; border: 0; background: transparent; font-size: 0.98rem; color: var(--text); }
    input:focus { outline: none; }
    .list { list-style: none; margin: 0; padding: 0.4rem; max-height: 52vh; overflow-y: auto; }
    .group { font-size: 0.64rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; color: var(--faint); padding: 0.6rem 0.65rem 0.3rem; }
    .item { display: flex; justify-content: space-between; align-items: center; gap: 1rem; padding: 0.5rem 0.7rem; border-radius: 9px; cursor: pointer; font-size: 0.86rem; }
    .item.active { background: color-mix(in srgb, var(--brand) 16%, var(--panel-2)); box-shadow: inset 2px 0 0 var(--brand); }
    .item.disabled { opacity: 0.4; cursor: not-allowed; }
    .hint { font: 0.7rem var(--mono); color: var(--faint); }
    .none { padding: 1rem; color: var(--faint); text-align: center; font-size: 0.85rem; }
  `,
})
export class CommandPaletteComponent {
  readonly actions = input.required<PaletteAction[]>();
  readonly closed = output<void>();
  readonly query = signal('');
  readonly cursor = signal(0);
  private readonly q = viewChild.required<ElementRef<HTMLInputElement>>('q');

  readonly results = computed(() => {
    const q = this.query().toLowerCase().trim();
    const all = this.actions();
    if (!q) return all;
    return all
      .map((a) => ({ a, s: score(`${a.group} ${a.label}`.toLowerCase(), q) }))
      .filter((x) => x.s > 0)
      .sort((x, y) => y.s - x.s)
      .map((x) => x.a);
  });

  constructor() {
    afterNextRender(() => this.q().nativeElement.focus());
  }

  setQuery(ev: Event): void {
    this.query.set((ev.target as HTMLInputElement).value);
    this.cursor.set(0);
  }

  onKey(ev: KeyboardEvent): void {
    const n = this.results().length;
    if (ev.key === 'ArrowDown') this.cursor.update((c) => (c + 1) % Math.max(1, n));
    else if (ev.key === 'ArrowUp') this.cursor.update((c) => (c - 1 + n) % Math.max(1, n));
    else if (ev.key === 'Enter') {
      const a = this.results()[this.cursor()];
      if (a) this.exec(a);
    } else if (ev.key === 'Escape') this.closed.emit();
    else return;
    ev.preventDefault();
    ev.stopPropagation();
  }

  exec(a: PaletteAction): void {
    if (a.disabled) return;
    this.closed.emit();
    a.run();
  }
}

/** Subsequence match; contiguous + word-start hits score higher. */
function score(text: string, q: string): number {
  if (text.includes(q)) return 100 - text.indexOf(q);
  let ti = 0;
  let s = 0;
  for (const ch of q) {
    const at = text.indexOf(ch, ti);
    if (at < 0) return 0;
    s += at === ti ? 3 : 1;
    ti = at + 1;
  }
  return s;
}
