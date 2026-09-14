import { Injectable, InjectionToken, inject } from '@angular/core';
import { DEMO } from './demo';
import { DemoEngine } from './demo-engine';
import { ChatReply, CompileMode, CompileResult, Diagnostic, Engine, GraphSpec, Health, ParseResult, RunEvent } from './models';

export const API_BASE = new InjectionToken<string>('API_BASE', {
  factory: () => 'http://localhost:8000',
});

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly diagnostics: Diagnostic[] = [],
  ) {
    super(message);
  }
}

/**
 * Thin fetch client — runs stream Server-Sent Events over a POST body.
 * In the static demo build every call goes to the in-browser DemoEngine instead.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly base = inject(API_BASE);
  private readonly demo = DEMO ? inject(DemoEngine) : null;

  health(): Promise<Health> {
    return this.demo ? this.demo.health() : this.json('/api/health');
  }

  templates(): Promise<GraphSpec[]> {
    return this.demo ? this.demo.templates() : this.json('/api/templates');
  }

  compile(spec: GraphSpec, mode: CompileMode, signal?: AbortSignal): Promise<CompileResult> {
    return this.demo ? this.demo.compile(spec, mode) : this.json('/api/compile', { spec, mode }, signal);
  }

  parseScript(text: string, base: GraphSpec | null, signal?: AbortSignal): Promise<ParseResult> {
    return this.demo ? this.demo.parseScript(text, base) : this.json('/api/script/parse', { text, base }, signal);
  }

  formatScript(spec: GraphSpec, compact: boolean, signal?: AbortSignal): Promise<{ text: string }> {
    return this.demo ? this.demo.formatScript(spec, compact) : this.json('/api/script/format', { spec, compact }, signal);
  }

  chat(messages: { role: string; content: string; script?: string }[], spec: GraphSpec | null): Promise<ChatReply> {
    return this.demo ? this.demo.chat(messages, spec) : this.json('/api/chat', { messages, spec });
  }

  run(
    body: { spec: GraphSpec; mode: CompileMode; engine: Engine; input: string },
    onEvent: (e: RunEvent) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    return this.demo ? this.demo.run(body, onEvent) : this.stream('/api/runs', body, onEvent, signal);
  }

  resume(runId: string, interruptId: string, response: string, onEvent: (e: RunEvent) => void): Promise<void> {
    return this.demo
      ? this.demo.resume(runId, interruptId, response, onEvent)
      : this.stream(`/api/runs/${runId}/resume`, { interruptId, response }, onEvent);
  }

  private async json<T>(path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const res = await fetch(this.base + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    if (!res.ok) throw await this.error(res);
    return res.json() as Promise<T>;
  }

  private async stream(path: string, body: unknown, onEvent: (e: RunEvent) => void, signal?: AbortSignal) {
    const res = await fetch(this.base + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) throw await this.error(res);
    const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = '';
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += value;
      let cut: number;
      while ((cut = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, cut);
        buffer = buffer.slice(cut + 2);
        if (frame.startsWith('data: ')) onEvent(JSON.parse(frame.slice(6)) as RunEvent);
      }
    }
  }

  private async error(res: Response): Promise<ApiError> {
    const body = await res.json().catch(() => null);
    const detail = body?.detail;
    if (detail?.diagnostics) return new ApiError(res.status, 'The graph has errors.', detail.diagnostics);
    return new ApiError(res.status, typeof detail === 'string' ? detail : `Request failed (${res.status})`);
  }
}
