import { ChangeDetectionStrategy, Component, ElementRef, effect, inject, viewChild } from '@angular/core';
import { ChatStore } from '../../core/chat-store';
import { GraphStore } from '../../core/graph-store';
import { ChatMessage, GraphSpec, Proposal } from '../../core/models';
import { Autocomplete, CHEATS, SAMPLE_SCRIPT, ScriptHighlightPipe } from '../../core/script-tools';
import { UiStore } from '../../core/ui-store';
import { GraphMiniComponent } from './graph-mini';

/** Describe a graph in English or Stratum Script; every reply is a previewable proposal. */
@Component({
  selector: 'app-chat-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [GraphMiniComponent, ScriptHighlightPipe],
  templateUrl: './chat-view.html',
  styleUrl: './chat-view.scss',
})
export class ChatViewComponent {
  readonly chat = inject(ChatStore);
  readonly graph = inject(GraphStore);
  readonly ui = inject(UiStore);
  readonly ac = new Autocomplete();
  readonly cheats = CHEATS;

  private readonly thread = viewChild<ElementRef<HTMLElement>>('thread');
  private readonly box = viewChild.required<ElementRef<HTMLTextAreaElement>>('box');

  readonly examples = [
    { title: 'Describe it', text: 'Research a topic with 4 analysts, combine their findings, draft and edit in a loop, then I approve' },
    { title: 'Type the script', text: SAMPLE_SCRIPT },
    { title: 'Branching', text: 'Triage tickets into billing, technical or general, then compose a reply and ask me before sending' },
    { title: 'Edit the current graph', text: 'add 2 more reviewers to stage 2' },
  ];

  constructor() {
    effect(() => {
      this.chat.messages();
      this.chat.pending();
      const el = this.thread()?.nativeElement;
      if (el) queueMicrotask(() => el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' }));
    });
  }

  isScript(text: string): boolean {
    return text.includes('@');
  }

  diff(p: Proposal): { added: number; removed: number } {
    const before = new Set(this.graph.spec().layers.flatMap((l) => l.nodes.map((n) => n.name)));
    const after = new Set(p.spec.layers.flatMap((l) => l.nodes.map((n) => n.name)));
    return { added: [...after].filter((n) => !before.has(n)).length, removed: [...before].filter((n) => !after.has(n)).length };
  }

  errors(p: Proposal): number {
    return p.diagnostics.filter((d) => d.level === 'error').length;
  }

  compareTo(): GraphSpec | null {
    return this.graph.spec().layers.length ? this.graph.spec() : null;
  }

  send(text = this.chat.draft()): void {
    this.ac.close();
    void this.chat.send(text);
  }

  use(text: string): void {
    this.chat.draft.set(text);
    queueMicrotask(() => this.box().nativeElement.focus());
  }

  insert(code: string): void {
    const el = this.box().nativeElement;
    const v = this.chat.draft();
    this.chat.draft.set(v ? `${v.replace(/\s*$/, '')}, ${code}` : code);
    queueMicrotask(() => el.focus());
  }

  apply(m: ChatMessage): void {
    this.chat.apply(m.id);
  }

  openInScript(p: Proposal): void {
    this.ui.scriptHandoff.set(p.script);
    this.ui.setView('script');
  }

  onInput(ev: Event): void {
    const el = ev.target as HTMLTextAreaElement;
    this.chat.draft.set(el.value);
    this.ac.update(el, this.graph.spec());
  }

  onKey(ev: KeyboardEvent): void {
    const el = ev.target as HTMLTextAreaElement;
    if (this.ac.key(ev, el, (v) => this.chat.draft.set(v))) return;
    if (ev.key === 'Enter' && !ev.shiftKey) {
      ev.preventDefault();
      ev.stopPropagation();
      this.send();
    }
  }
}
