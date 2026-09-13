import { Injectable, computed, inject, signal } from '@angular/core';
import { ApiError, ApiService } from './api';
import { CompileMode, Diagnostic, Engine, GraphSpec, RunEvent } from './models';

export type RunStatus = 'idle' | 'running' | 'paused' | 'completed' | 'error';
export type NodeRunStatus = 'idle' | 'running' | 'done' | 'waiting' | 'skipped';
export type InterruptEvent = Extract<RunEvent, { type: 'interrupt' }>;

export interface NodeRun {
  status: NodeRunStatus;
  iterations: number;
  spans: { start: number; end?: number }[];
  output?: string;
  tokens?: number | null;
}

const EMPTY_NODE: NodeRun = { status: 'idle', iterations: 0, spans: [] };

/** Folds the SSE event stream into per-node run state for canvas, timeline and log. */
@Injectable({ providedIn: 'root' })
export class RunStore {
  private readonly api = inject(ApiService);

  readonly status = signal<RunStatus>('idle');
  readonly engine = signal<Engine>('mock');
  readonly input = signal('');
  readonly runId = signal<string | null>(null);
  readonly log = signal<RunEvent[]>([]);
  readonly nodes = signal<Record<string, NodeRun>>({});
  readonly routes = signal<Record<string, string>>({});
  readonly interrupt = signal<InterruptEvent | null>(null);
  readonly output = signal('');
  readonly error = signal('');
  readonly errorDiagnostics = signal<Diagnostic[]>([]);
  readonly tokens = signal(0);
  readonly clock = signal(0);
  readonly mode = signal<CompileMode>('graph');

  readonly busy = computed(() => this.status() === 'running');
  readonly finished = computed(() => this.status() === 'completed' || this.status() === 'error');
  readonly span = computed(() => {
    let max = this.clock();
    for (const n of Object.values(this.nodes())) for (const s of n.spans) max = Math.max(max, s.end ?? 0);
    return Math.max(max, 1);
  });

  private t0 = 0;
  private timer: ReturnType<typeof setInterval> | undefined;

  statusOf(name: string): NodeRunStatus {
    const n = this.nodes()[name];
    if (n) return n.status;
    return this.finished() && this.runId() ? 'skipped' : 'idle';
  }

  nodeRun(name: string): NodeRun {
    return this.nodes()[name] ?? EMPTY_NODE;
  }

  durationOf(name: string): number | null {
    const spans = this.nodes()[name]?.spans ?? [];
    const closed = spans.filter((s) => s.end !== undefined);
    return closed.length ? closed.reduce((sum, s) => sum + (s.end! - s.start), 0) : null;
  }

  async start(spec: GraphSpec, mode: CompileMode): Promise<void> {
    this.reset();
    this.mode.set(mode);
    this.status.set('running');
    this.ticking(true);
    try {
      await this.api.run({ spec, mode, engine: this.engine(), input: this.input().trim() }, (e) => this.apply(e));
    } catch (e) {
      this.fail(e);
    } finally {
      this.ticking(false);
      if (this.status() === 'running') this.status.set('completed');
    }
  }

  async resume(approve: boolean, reason = ''): Promise<void> {
    const gate = this.interrupt();
    const id = this.runId();
    if (!gate || !id) return;
    const response = approve ? 'approve' : `reject${reason ? ': ' + reason : ''}`;
    this.interrupt.set(null);
    this.patch(gate.node, (n) => ({ ...n, status: 'done', output: response, spans: closeLast(n.spans, this.clock()) }));
    this.status.set('running');
    this.ticking(true);
    try {
      await this.api.resume(id, gate.interruptId, response, (e) => this.apply(e));
    } catch (e) {
      this.fail(e);
    } finally {
      this.ticking(false);
      if (this.status() === 'running') this.status.set('completed');
    }
  }

  reset(): void {
    this.status.set('idle');
    this.runId.set(null);
    this.log.set([]);
    this.nodes.set({});
    this.routes.set({});
    this.interrupt.set(null);
    this.output.set('');
    this.error.set('');
    this.errorDiagnostics.set([]);
    this.tokens.set(0);
    this.clock.set(0);
  }

  private apply(e: RunEvent): void {
    this.log.update((l) => [...l, e]);
    switch (e.type) {
      case 'run_start':
        this.runId.set(e.runId);
        this.t0 = performance.now() - e.t;
        break;
      case 'run_resume':
        this.t0 = performance.now() - e.t;
        break;
      case 'node_start':
        this.patch(e.node, (n) => ({
          ...n,
          status: 'running',
          iterations: e.iteration,
          spans: [...n.spans, { start: e.t }],
        }));
        break;
      case 'node_end':
        if (e.internal) {
          if (e.route && e.node.startsWith('route_')) this.routes.update((r) => ({ ...r, [e.node.slice(6)]: e.route! }));
          break;
        }
        this.patch(e.node, (n) => ({
          ...n,
          status: 'done',
          output: e.output,
          tokens: e.tokens ?? n.tokens,
          spans: n.spans.length ? closeLast(n.spans, e.t) : [{ start: e.t, end: e.t }],
        }));
        break;
      case 'interrupt':
        this.interrupt.set(e);
        this.patch(e.node, (n) => ({ ...n, status: 'waiting' }));
        break;
      case 'error':
        this.error.set(e.message);
        break;
      case 'run_end':
        this.status.set(e.status === 'paused' ? 'paused' : this.error() ? 'error' : 'completed');
        this.output.set(e.output);
        this.tokens.set(e.tokens);
        this.clock.set(e.t);
        break;
    }
  }

  private patch(name: string, fn: (n: NodeRun) => NodeRun): void {
    this.nodes.update((all) => ({ ...all, [name]: fn(all[name] ?? EMPTY_NODE) }));
  }

  private fail(e: unknown): void {
    this.status.set('error');
    if (e instanceof ApiError) {
      this.error.set(e.message);
      this.errorDiagnostics.set(e.diagnostics);
    } else {
      this.error.set(e instanceof TypeError ? 'Backend unreachable — start it with `npm run api`.' : String(e));
    }
  }

  private ticking(on: boolean): void {
    clearInterval(this.timer);
    if (on) this.timer = setInterval(() => this.clock.set(Math.round(performance.now() - this.t0)), 80);
  }
}

function closeLast(spans: NodeRun['spans'], t: number): NodeRun['spans'] {
  return spans.map((s, i) => (i === spans.length - 1 && s.end === undefined ? { ...s, end: t } : s));
}
