import { Pipe, PipeTransform, signal } from '@angular/core';
import { GraphSpec } from './models';

/** Syntax cheatsheet shared by the Chat and Script views. */
export const CHEATS: { code: string; text: string }[] = [
  { code: '@research(a, b, c)', text: 'parallel agents' },
  { code: '@team(agt 1,2,3,4)', text: 'numbered agents agt_1…agt_4' },
  { code: '@team(4 analysts)', text: 'analyst_1…analyst_4' },
  { code: '@merge_step(combine)', text: 'code merge · 0 tokens' },
  { code: '@triage route(billing: billing_agent "refunds", tech: tech_agent)', text: 'router · one branch runs' },
  { code: '@draft loop(writer, editor) x3', text: 'generator ⇄ critic loop' },
  { code: 'judge <- (research, planner)', text: 'explicit inputs (skip-level)' },
  { code: 'writer[pro, search] "Write the brief"', text: 'model · tools · instruction' },
  { code: '@signoff(human)', text: 'human approval gate' },
];

export const SAMPLE_SCRIPT = 'start @stage1, @stage2(agt 1,2,3,4), @stage3(combine), @stage4(abc, abc) @stage5(human)';

const KEYWORDS = /^(route|loop|human|approve|approval|combine|merge|aggregate|start|end|then|gate|if|x\d+)$/i;
const TOKEN = /("(?:[^"\\]|\\.)*"?)|(@[A-Za-z_][\w-]*)|(\[[^\]\n]*\]?)|(<-|←|->|→)|(\b\d+\b)|([A-Za-z_][\w-]*)/g;
const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

/** Highlights Stratum Script — escapes first, emits only <span class>. */
@Pipe({ name: 'scriptHighlight' })
export class ScriptHighlightPipe implements PipeTransform {
  transform(src: string | null | undefined): string {
    if (!src) return '';
    let out = '';
    let last = 0;
    for (const m of src.matchAll(TOKEN)) {
      out += esc(src.slice(last, m.index));
      last = m.index! + m[0].length;
      const [whole, str, layer, tags, arrow, num, word] = m;
      if (str) out += `<span class="s-str">${esc(str)}</span>`;
      else if (layer) out += `<span class="s-layer">${esc(layer)}</span>`;
      else if (tags) out += `<span class="s-tag">${esc(tags)}</span>`;
      else if (arrow) out += `<span class="s-arrow">${esc(arrow)}</span>`;
      else if (num) out += `<span class="s-num">${num}</span>`;
      else if (word && KEYWORDS.test(word)) out += `<span class="s-kw">${esc(word)}</span>`;
      else out += esc(whole);
    }
    return out + esc(src.slice(last));
  }
}

export interface Suggestion {
  label: string;
  insert: string;
  detail: string;
}

const SNIPPETS: Suggestion[] = [
  { label: '@name(a, b)', insert: '@stage(a, b)', detail: 'parallel stage' },
  { label: '@name route(…)', insert: '@triage route(yes: yes_agent, no: no_agent)', detail: 'router' },
  { label: '@name loop(…) x3', insert: '@draft loop(writer, editor) x3', detail: 'refine loop' },
  { label: '@name(combine)', insert: '@merge_step(combine)', detail: 'code merge' },
  { label: '@signoff(human)', insert: '@signoff(human)', detail: 'human gate' },
];
const WORDS: Suggestion[] = [
  { label: 'combine', insert: 'combine', detail: 'code merge node' },
  { label: 'human', insert: 'human', detail: 'approval node' },
  { label: '[pro]', insert: '[pro]', detail: 'gemini-2.5-pro' },
  { label: '[search]', insert: '[search]', detail: 'Google Search tool' },
  { label: '<- (…)', insert: '<- ()', detail: 'explicit inputs' },
];

const ident = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');

export function suggest(text: string, caret: number, spec: GraphSpec): { items: Suggestion[]; from: number } {
  const before = text.slice(0, caret);
  const word = /[@\w-]*$/.exec(before)?.[0] ?? '';
  const from = caret - word.length;
  if (word.startsWith('@')) {
    const q = word.slice(1).toLowerCase();
    const layers = spec.layers.map((l) => ({ label: `@${ident(l.title)}`, insert: `@${ident(l.title)}`, detail: `${l.kind} · ${l.nodes.length}` }));
    const items = [...layers, ...SNIPPETS].filter((s) => s.label.slice(1).toLowerCase().includes(q));
    return { items: items.slice(0, 7), from };
  }
  const lastAt = before.lastIndexOf('@');
  const open = (before.slice(lastAt).match(/\(/g) ?? []).length > (before.slice(lastAt).match(/\)/g) ?? []).length;
  if (lastAt >= 0 && open && word.length >= 1) {
    const q = word.toLowerCase();
    const nodes = spec.layers.flatMap((l) => l.nodes.map((n) => ({ label: n.name, insert: n.name, detail: l.title })));
    const items = [...nodes, ...WORDS].filter((s) => s.label.toLowerCase().startsWith(q) && s.label.toLowerCase() !== q);
    return { items: items.slice(0, 7), from };
  }
  return { items: [], from };
}

/** Keyboard-driven suggestion list for a textarea (arrows, Tab/Enter to accept, Esc to close). */
export class Autocomplete {
  readonly items = signal<Suggestion[]>([]);
  readonly index = signal(0);
  private from = 0;

  update(el: HTMLTextAreaElement, spec: GraphSpec): void {
    const r = suggest(el.value, el.selectionStart ?? el.value.length, spec);
    this.items.set(r.items);
    this.from = r.from;
    this.index.set(0);
  }

  close(): void {
    this.items.set([]);
  }

  key(ev: KeyboardEvent, el: HTMLTextAreaElement, set: (v: string) => void): boolean {
    const n = this.items().length;
    if (!n) return false;
    if (ev.key === 'ArrowDown') this.index.update((i) => (i + 1) % n);
    else if (ev.key === 'ArrowUp') this.index.update((i) => (i - 1 + n) % n);
    else if (ev.key === 'Tab' || ev.key === 'Enter') this.accept(el, set);
    else if (ev.key === 'Escape') this.close();
    else return false;
    ev.preventDefault();
    ev.stopPropagation();
    return true;
  }

  accept(el: HTMLTextAreaElement, set: (v: string) => void, i = this.index()): void {
    const s = this.items()[i];
    if (!s) return;
    const caret = el.selectionStart ?? el.value.length;
    const value = el.value.slice(0, this.from) + s.insert + el.value.slice(caret);
    const at = this.from + s.insert.length - (s.insert.endsWith('()') ? 1 : 0);
    set(value);
    this.close();
    queueMicrotask(() => {
      el.value = value;
      el.focus();
      el.setSelectionRange(at, at);
    });
  }
}
