import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { GraphSpec, LAYER_KINDS } from '../../core/models';

interface Chip {
  text: string;
  added: boolean;
  kind: string;
}

/** Compact vertical stack of a graph's layers; optional diff against another spec. */
@Component({
  selector: 'app-graph-mini',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <ol class="mini">
      @for (r of rows(); track r.key) {
        <li class="row" [attr.data-kind]="r.kind">
          <span class="num mono">{{ r.n }}</span>
          <div class="body">
            <div class="head">
              <b>{{ r.title }}</b><span class="k">{{ r.label }}</span>
            </div>
            <div class="chips">
              @for (c of r.chips; track $index) {
                <span class="c mono" [attr.data-kind]="c.kind" [class.added]="c.added">{{ c.text }}</span>
              }
            </div>
          </div>
        </li>
      } @empty {
        <li class="empty">No layers yet.</li>
      }
    </ol>
    @if (removed().length) {
      <p class="removed">− {{ removed().join(', ') }}</p>
    }
  `,
  styles: `
    :host { display: block; }
    .mini { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
    .row {
      --kc: var(--k-stage);
      position: relative; display: grid; grid-template-columns: 22px 1fr; gap: 0.55rem; padding: 0.45rem 0.55rem;
      border-radius: 10px; background: var(--panel-2); border: 1px solid var(--line); border-left: 3px solid var(--kc);
      animation: fadeUp 0.25s ease both;
    }
    .row + .row::before {
      content: ''; position: absolute; left: 20px; top: -7px; width: 2px; height: 6px; background: var(--line-2);
    }
    .row[data-kind='router'] { --kc: var(--k-router); }
    .row[data-kind='loop'] { --kc: var(--k-loop); }
    .row[data-kind='gate'] { --kc: var(--k-gate); }
    .num { font-size: 0.66rem; color: var(--kc); font-weight: 700; padding-top: 2px; }
    .head { display: flex; align-items: baseline; gap: 0.45rem; margin-bottom: 0.3rem; }
    .head b { font-size: 0.78rem; }
    .k { font-size: 0.64rem; color: var(--faint); }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .c {
      font-size: 0.66rem; padding: 0.08rem 0.4rem; border-radius: 6px; background: var(--bg-2);
      border: 1px solid var(--line-2); color: var(--muted);
    }
    .c[data-kind='code'] { color: var(--k-code); }
    .c[data-kind='human'] { color: var(--k-gate); }
    .c.added { color: var(--ok); border-color: color-mix(in srgb, var(--ok) 55%, var(--line-2)); background: color-mix(in srgb, var(--ok) 9%, var(--bg-2)); }
    .c.added::before { content: '+ '; }
    .removed { margin: 0.45rem 0 0; font: 0.68rem var(--mono); color: var(--bad); text-decoration: line-through; opacity: 0.85; }
    .empty { font-size: 0.76rem; color: var(--faint); padding: 0.4rem; }
  `,
})
export class GraphMiniComponent {
  readonly spec = input.required<GraphSpec>();
  readonly compare = input<GraphSpec | null>(null);

  private readonly before = computed(() => {
    const c = this.compare();
    return c ? new Set(c.layers.flatMap((l) => l.nodes.map((n) => n.name))) : null;
  });

  readonly removed = computed(() => {
    const c = this.compare();
    if (!c) return [];
    const now = new Set(this.spec().layers.flatMap((l) => l.nodes.map((n) => n.name)));
    return c.layers.flatMap((l) => l.nodes.map((n) => n.name)).filter((n) => !now.has(n));
  });

  readonly rows = computed(() => {
    const layers = this.spec().layers;
    const before = this.before();
    const isNew = (name: string) => !!before && !before.has(name);
    const byId = new Map(layers.flatMap((l) => l.nodes.map((n) => [n.id, n] as const)));
    const rows: { key: string; n: number; kind: string; title: string; label: string; chips: Chip[] }[] = [];
    layers.forEach((l, i) => {
      if (i > 0 && layers[i - 1].kind === 'router') return;
      const base = { key: l.id, n: rows.length + 1, kind: l.kind, title: l.title };
      if (l.kind === 'router') {
        const r = l.nodes[0];
        rows.push({
          ...base,
          label: `router · ${r?.routes.length ?? 0} branches`,
          chips: (r?.routes ?? []).map((rt) => {
            const t = byId.get(rt.target)?.name ?? '?';
            return { text: `${rt.label} → ${t}`, added: isNew(t), kind: 'agent' };
          }),
        });
      } else if (l.kind === 'loop') {
        rows.push({
          ...base,
          label: `loop ≤${l.maxIterations}`,
          chips: [{ text: l.nodes.map((n) => n.name).join(' ⇄ '), added: l.nodes.some((n) => isNew(n.name)), kind: 'agent' }],
        });
      } else {
        rows.push({
          ...base,
          label: l.kind === 'gate' ? 'human gate' : l.nodes.length > 1 ? `${l.nodes.length}× parallel` : LAYER_KINDS.stage.label.toLowerCase(),
          chips: l.nodes.map((n) => ({ text: n.name, added: isNew(n.name), kind: n.kind })),
        });
      }
    });
    return rows;
  });
}
