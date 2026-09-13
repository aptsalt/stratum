/** Mirrors backend/stratum/spec.py — the layered contract the Studio edits. */

export type LayerKind = 'stage' | 'router' | 'loop' | 'gate';
export type NodeKind = 'agent' | 'code' | 'human';
export type CodeOp = 'merge' | 'passthrough';
export type CompileMode = 'graph' | 'dynamic';
export type Engine = 'mock' | 'gemini';

export interface Route {
  label: string;
  target: string;
  when: string;
}

export interface AgentNode {
  id: string;
  name: string;
  kind: NodeKind;
  instruction: string;
  model: string | null;
  tools: string[];
  op: CodeOp;
  /** null = auto: every node in the previous layer. */
  dependsOn: string[] | null;
  routes: Route[];
}

export interface Layer {
  id: string;
  title: string;
  kind: LayerKind;
  nodes: AgentNode[];
  maxIterations: number;
}

export interface GraphSpec {
  id: string;
  name: string;
  description: string;
  model: string;
  layers: Layer[];
}

export interface Diagnostic {
  level: 'error' | 'warning';
  message: string;
  layerId?: string | null;
  nodeId?: string | null;
}

export interface IRNodeJson {
  name: string;
  role: 'agent' | 'code' | 'human_ask' | 'human_decide' | 'router_fn' | 'loop_gate' | 'join';
  internal: boolean;
  sourceId: string | null;
}

export interface IRJson {
  nodes: IRNodeJson[];
  edges: { src: string; dst: string; route: string | null }[];
}

export interface CompileResult {
  diagnostics: Diagnostic[];
  ir: IRJson | null;
  code: string | null;
}

export interface Health {
  ok: boolean;
  adk: string;
  engines: { mock: boolean; gemini: boolean };
}

export type RunEvent =
  | { type: 'run_start'; runId: string; mode: CompileMode; engine: Engine; t: number }
  | { type: 'run_resume'; runId: string; t: number }
  | { type: 'node_start'; node: string; iteration: number; t: number }
  | {
      type: 'node_end';
      node: string;
      iteration: number;
      internal: boolean;
      route: string | null;
      output: string;
      tokens: number | null;
      t: number;
    }
  | { type: 'interrupt'; interruptId: string; node: string; message: string; payload: { preview?: string } | null; t: number }
  | { type: 'error'; message: string; t: number }
  | { type: 'run_end'; status: 'completed' | 'paused'; output: string; tokens: number; t: number };

export interface ScriptError {
  message: string;
  line: number;
  col: number;
  pos?: number;
}

export interface ParseResult {
  spec: GraphSpec | null;
  error: ScriptError | null;
  diagnostics?: Diagnostic[];
  summary?: string;
}

export interface Proposal {
  spec: GraphSpec;
  script: string;
  summary: string;
  diagnostics: Diagnostic[];
}

export interface ChatReply extends Partial<Proposal> {
  reply: string;
  engine: 'script' | 'parser' | 'gemini';
  error?: ScriptError;
}

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  engine?: ChatReply['engine'];
  proposal?: Proposal;
  applied?: boolean;
  error?: boolean;
}

export const LAYER_KINDS: Record<LayerKind, { label: string; glyph: string; blurb: string; adk: string }> = {
  stage: {
    label: 'Stage',
    glyph: '▤',
    blurb: 'Every agent runs as soon as its inputs are ready. One agent = a step, several = parallel fan-out.',
    adk: 'Fan-out edge (src, (a, b, c)) — ADK runs the targets concurrently; a JoinNode is inserted wherever a node consumes more than one upstream.',
  },
  router: {
    label: 'Router',
    glyph: '⑂',
    blurb: 'One classifier picks exactly one route. Each route targets an agent in the next layer.',
    adk: 'Routed edge (route_fn, {"label": target}) — the function returns Event(route=label); only that branch runs.',
  },
  loop: {
    label: 'Loop',
    glyph: '↻',
    blurb: 'Generator → critic, repeated until the critic answers PASS or the iteration cap is hit.',
    adk: 'Back-edge (loop_gate, {"revise": generator, "done": next}) — iteration count lives in session state.',
  },
  gate: {
    label: 'Human gate',
    glyph: '✋',
    blurb: 'Pauses the run for a person to approve or reject. Reject ends the workflow.',
    adk: 'A node that yields RequestInput — the runner stops; resuming sends a function_response for adk_request_input.',
  },
};

export const MODELS = ['gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.5-flash-lite', 'gemini-flash-latest'];
export const TOOLS = [{ id: 'google_search', label: 'Google Search' }];
