import { Injectable, signal } from '@angular/core';
import { ChatReply, CompileMode, CompileResult, Engine, GraphSpec, Health, ParseResult, RunEvent } from './models';

const PYODIDE = 'https://cdn.jsdelivr.net/pyodide/v0.28.3/full/';
const MODULES = ['__init__', 'spec', 'ir', 'script', 'nl', 'codegen', 'templates', 'chat', 'sim', 'web'];

interface PyWeb {
  templates(): string;
  compile_spec(spec: string, mode: string): string;
  parse_script(text: string, base: string): string;
  format_script(spec: string, compact: boolean): string;
  chat_turn(messages: string, spec: string): PromiseLike<string>;
  run(spec: string, mode: string, input: string, emit: (e: string) => void, ask: (i: string) => Promise<string>): PromiseLike<void>;
}

/**
 * Demo backend for the static GitHub Pages build: the same Python compiler, script and
 * chat parsers run in the browser under Pyodide (WebAssembly); runs use stratum/sim.py.
 */
@Injectable({ providedIn: 'root' })
export class DemoEngine {
  readonly ready = signal(false);
  readonly stage = signal('Starting…');
  readonly failed = signal<string | null>(null);

  private boot?: Promise<PyWeb>;
  private sink: ((e: RunEvent) => void) | null = null;
  private done: (() => void) | null = null;
  private answer: ((r: string) => void) | null = null;
  private lastT = 0;

  load(): Promise<PyWeb> {
    return (this.boot ??= this.start());
  }

  async health(): Promise<Health> {
    await this.load();
    return { ok: true, adk: 'in-browser demo', engines: { mock: true, gemini: false } };
  }

  async templates(): Promise<GraphSpec[]> {
    return JSON.parse((await this.load()).templates());
  }

  async compile(spec: GraphSpec, mode: CompileMode): Promise<CompileResult> {
    return JSON.parse((await this.load()).compile_spec(JSON.stringify(spec), mode));
  }

  async parseScript(text: string, base: GraphSpec | null): Promise<ParseResult> {
    return JSON.parse((await this.load()).parse_script(text, base ? JSON.stringify(base) : ''));
  }

  async formatScript(spec: GraphSpec, compact: boolean): Promise<{ text: string }> {
    return JSON.parse((await this.load()).format_script(JSON.stringify(spec), compact));
  }

  async chat(messages: { role: string; content: string; script?: string }[], spec: GraphSpec | null): Promise<ChatReply> {
    const web = await this.load();
    return JSON.parse(await web.chat_turn(JSON.stringify(messages), spec ? JSON.stringify(spec) : ''));
  }

  /** Resolves when the run completes OR pauses at a human gate (like the SSE stream closing). */
  async run(body: { spec: GraphSpec; mode: CompileMode; engine: Engine; input: string }, onEvent: (e: RunEvent) => void): Promise<void> {
    const web = await this.load();
    return new Promise<void>((resolve) => {
      this.sink = onEvent;
      this.done = resolve;
      const emit = (raw: string) => {
        const e = JSON.parse(raw) as RunEvent;
        this.lastT = e.t;
        this.sink?.(e);
        if (e.type === 'run_end') this.finish();
      };
      const ask = () =>
        new Promise<string>((answer) => {
          this.answer = answer;
          this.sink?.({ type: 'run_end', status: 'paused', output: '', tokens: 0, t: this.lastT });
          this.finish();
        });
      Promise.resolve(web.run(JSON.stringify(body.spec), body.mode, body.input, emit, ask)).catch((err: unknown) => {
        this.sink?.({ type: 'error', message: String(err), t: this.lastT });
        this.sink?.({ type: 'run_end', status: 'completed', output: '', tokens: 0, t: this.lastT });
        this.finish();
      });
    });
  }

  resume(runId: string, _interruptId: string, response: string, onEvent: (e: RunEvent) => void): Promise<void> {
    return new Promise<void>((resolve) => {
      this.sink = onEvent;
      this.done = resolve;
      onEvent({ type: 'run_resume', runId, t: this.lastT });
      const answer = this.answer;
      this.answer = null;
      answer?.(response);
    });
  }

  private finish(): void {
    const done = this.done;
    this.done = null;
    done?.();
  }

  private async start(): Promise<PyWeb> {
    try {
      this.stage.set('Downloading Python (WebAssembly)…');
      await loadScript(`${PYODIDE}pyodide.js`);
      const loadPyodide = (window as unknown as { loadPyodide: (o: object) => Promise<any> }).loadPyodide;
      const py = await loadPyodide({ indexURL: PYODIDE });
      this.stage.set('Installing pydantic…');
      await py.loadPackage(['pydantic']);
      this.stage.set('Loading the Stratum compiler…');
      py.FS.mkdirTree('/home/pyodide/stratum');
      await Promise.all(
        MODULES.map(async (m) => {
          const res = await fetch(new URL(`py/${m}.py`, document.baseURI));
          if (!res.ok) throw new Error(`missing py/${m}.py`);
          py.FS.writeFile(`/home/pyodide/stratum/${m}.py`, await res.text());
        }),
      );
      py.runPython('import sys; sys.path.insert(0, "/home/pyodide")');
      const web = py.pyimport('stratum.web') as PyWeb;
      this.ready.set(true);
      return web;
    } catch (e) {
      this.failed.set(e instanceof Error ? e.message : String(e));
      throw e;
    }
  }
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const s = Object.assign(document.createElement('script'), { src, async: true });
    s.onload = () => resolve();
    s.onerror = () => reject(new Error(`Could not load ${src}`));
    document.head.appendChild(s);
  });
}
