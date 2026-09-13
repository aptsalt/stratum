import { Injectable, effect, inject, signal } from '@angular/core';
import { ApiError, ApiService } from './api';
import { GraphStore } from './graph-store';
import { ChatMessage } from './models';

const STORAGE = 'stratum.chat.v1';
const uid = () => Math.random().toString(36).slice(2, 10);

/** Conversation that proposes graphs; nothing touches the canvas until the user applies a proposal. */
@Injectable({ providedIn: 'root' })
export class ChatStore {
  private readonly api = inject(ApiService);
  private readonly graph = inject(GraphStore);

  readonly messages = signal<ChatMessage[]>(load());
  readonly pending = signal(false);
  readonly draft = signal('');

  constructor() {
    effect(() => {
      const m = this.messages();
      try {
        localStorage.setItem(STORAGE, JSON.stringify(m.slice(-40)));
      } catch {
        /* ignore */
      }
    });
  }

  async send(text: string): Promise<void> {
    const content = text.trim();
    if (!content || this.pending()) return;
    this.messages.update((m) => [...m, { id: uid(), role: 'user', content }]);
    this.draft.set('');
    this.pending.set(true);
    try {
      const history = this.messages().map((m) => ({ role: m.role, content: m.content, script: m.proposal?.script }));
      const spec = this.graph.spec();
      const r = await this.api.chat(history, spec.layers.length ? spec : null);
      const proposal = r.spec && r.script ? { spec: r.spec, script: r.script, summary: r.summary ?? '', diagnostics: r.diagnostics ?? [] } : undefined;
      this.messages.update((m) => [...m, { id: uid(), role: 'assistant', content: r.reply, engine: r.engine, proposal, error: !!r.error }]);
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : 'Backend unreachable — start it with `npm run api`.';
      this.messages.update((m) => [...m, { id: uid(), role: 'assistant', content: msg, error: true }]);
    } finally {
      this.pending.set(false);
    }
  }

  apply(id: string): void {
    const msg = this.messages().find((m) => m.id === id);
    if (!msg?.proposal) return;
    this.graph.load(msg.proposal.spec);
    this.messages.update((all) => all.map((m) => (m.id === id ? { ...m, applied: true } : m)));
  }

  clear(): void {
    this.messages.set([]);
  }
}

function load(): ChatMessage[] {
  try {
    return JSON.parse(localStorage.getItem(STORAGE) ?? '[]') as ChatMessage[];
  } catch {
    return [];
  }
}
