import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

const STAGE_CODE = `edges = [
    ("START", planner, (research_a, research_b, research_c), JoinNode(name="merge"), writer),
]`;

const ROUTER_CODE = `def route_fn(ctx, node_input) -> Event:
    topic = node_input.get("topic")
    return Event(route="billing" if topic == "billing" else "technical")

edges = [
    ("START", classifier, (route_fn, {
        "billing": billing_agent,
        "technical": tech_agent,
    }), responder),
]`;

const LOOP_CODE = `def loop_gate(ctx, node_input) -> Event:
    revisions = ctx.session.state.get("temp:revision_count", 0)
    if node_input.get("approved") or revisions >= 3:
        return Event(route="done")
    ctx.session.state["temp:revision_count"] = revisions + 1
    return Event(route="revise")

edges = [
    ("START", drafter, critic, (loop_gate, {
        "revise": drafter,
        "done": publisher,
    })),
]`;

const HUMAN_GATE_CODE = `class ApprovalNode(FunctionNode):
    async def run(self, ctx, node_input):
        yield RequestInput(
            message="Approve this refund?",
            payload={"amount": node_input["amount"]},
        )`;

const DEPENDS_ON_CODE = `edges = [
    ("START", fetch_user, fetch_orders, summarizer),
]
# summarizer.dependsOn = ["fetch_user", "fetch_orders"]
# two upstreams -> Stratum auto-inserts a JoinNode here`;

const DATA_FLOW_CODE = `class Summary(BaseModel):
    headline: str
    bullets: list[str]

writer = Agent(
    name="writer",
    model="gemini-2.5-flash",
    instruction="Write about {topic} using {Planner.bullets}.",
    output_schema=Summary,
    output_key="draft_summary",
    mode="single_turn",
)`;

const GRAPH_MODE_CODE = `workflow = Workflow(
    edges=[
        ("START", intake, (kyc_check, risk_check), JoinNode(name="merge"), decision),
    ],
    max_concurrency=4,
)`;

const DYNAMIC_MODE_CODE = `@node(rerun_on_resume=True)
async def orchestrator(ctx: Context, node_input):
    plan = await ctx.run_node(planner, node_input)

    if plan.route == "parallel":
        a, b = await asyncio.gather(
            ctx.run_node(agent_a, plan.output),
            ctx.run_node(agent_b, plan.output),
        )
        return Event(output={"a": a, "b": b})

    for step in plan.output["steps"]:
        node_input = await ctx.run_node(worker, step)

    return Event(output=node_input)

workflow = Workflow(edges=[("START", orchestrator)])`;

const RESUME_CODE = `{
  "name": "adk_request_input",
  "response": { "result": "approve" },
  "id": "call_7f3a",
  "invocation_id": "inv_9c21"
}`;

const MIGRATION_CODE = `# 1.x - overridden run() is now ignored by the graph engine
class LegacyAgent(BaseAgent):
    async def run(self, ctx):
        ...

# 2.x - use before/after agent callbacks instead
class LegacyAgent(BaseAgent):
    async def before_agent_callback(self, ctx):
        ...
    async def after_agent_callback(self, ctx):
        ...`;

const PROD_CODE = `workflow = Workflow(
    edges=[...],
    max_concurrency=8,
    retry_config=RetryConfig(max_attempts=3, backoff="exponential"),
    timeout=30,
)`;

interface LayerCard {
  readonly kind: string;
  readonly meaning: string;
  readonly code: string;
  readonly codeId: string;
  readonly filename: string;
  readonly note: string;
}

interface TocSection {
  readonly id: string;
  readonly label: string;
}

interface ChecklistCard {
  readonly title: string;
  readonly body: string;
}

@Component({
  selector: 'app-learn',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './learn.html',
  styleUrl: './learn.scss',
  imports: [RouterLink],
})
export class LearnComponent {
  protected readonly copiedId = signal<string | null>(null);

  protected readonly sections: readonly TocSection[] = [
    { id: 'mental-model', label: '1. The mental model' },
    { id: 'layers', label: '2. Layers to ADK constructs' },
    { id: 'pipeline', label: '3. Spec to running graph' },
    { id: 'data-flow', label: '4. Data flow and state' },
    { id: 'graph-vs-dynamic', label: '5. Graph vs. dynamic' },
    { id: 'dynamic-meanings', label: '6. Three kinds of "dynamic"' },
    { id: 'human-loop', label: '7. Human-in-the-loop' },
    { id: 'production', label: '8. Production checklist' },
  ];

  protected readonly layers: readonly LayerCard[] = [
    {
      kind: 'Stage',
      meaning:
        'All nodes run once their inputs are ready. One node in a step is a single call; a tuple of N nodes is a parallel fan-out.',
      code: STAGE_CODE,
      codeId: 'stage',
      filename: 'stage.py',
      note: 'A JoinNode waits for every predecessor and emits a dict keyed by node name.',
    },
    {
      kind: 'Router',
      meaning:
        'A function inspects the input and returns an Event naming exactly one branch. Only that branch runs.',
      code: ROUTER_CODE,
      codeId: 'router',
      filename: 'router.py',
      note: 'Exclusive branches converge with a plain edge, never a JoinNode - a join would wait forever for the branch that never ran.',
    },
    {
      kind: 'Loop',
      meaning:
        'A back-edge re-enters an earlier node until a gate function routes to "done". The iteration count lives in session state.',
      code: LOOP_CODE,
      codeId: 'loop',
      filename: 'loop.py',
      note: '',
    },
    {
      kind: 'Human gate',
      meaning: 'A node yields RequestInput and the run pauses until a person responds.',
      code: HUMAN_GATE_CODE,
      codeId: 'human-gate',
      filename: 'human_gate.py',
      note: '',
    },
  ];

  protected readonly dependsOnCode = DEPENDS_ON_CODE;
  protected readonly dataFlowCode = DATA_FLOW_CODE;
  protected readonly graphModeCode = GRAPH_MODE_CODE;
  protected readonly dynamicModeCode = DYNAMIC_MODE_CODE;
  protected readonly resumeCode = RESUME_CODE;
  protected readonly migrationCode = MIGRATION_CODE;
  protected readonly prodCode = PROD_CODE;

  protected readonly checklist: readonly ChecklistCard[] = [
    {
      title: 'Persistent session service',
      body: 'Swap InMemoryRunner’s session service for a persistent one before real users touch it - in-memory state does not survive a restart.',
    },
    {
      title: 'Deploy target',
      body: 'Run on Cloud Run or Vertex AI Agent Engine rather than a laptop process.',
    },
    {
      title: 'Concurrency, retries, timeouts',
      body: 'Set max_concurrency on the Workflow, retry_config per flaky node, and timeout per node so one slow call cannot stall the whole graph.',
    },
    {
      title: 'Push logic into function nodes',
      body: 'Anything deterministic - parsing, formatting, math, gating - belongs in a plain function node. It costs no tokens and runs in milliseconds.',
    },
    {
      title: 'Evaluate before promoting',
      body: 'Run the ADK evaluation suite against the graph before it replaces the previous version in production.',
    },
    {
      title: 'Migrating from 1.x',
      body: 'BaseAgent now subclasses BaseNode. An overridden run() from 1.x is silently ignored by the graph engine - move that logic into before_agent_callback / after_agent_callback.',
    },
  ];

  protected copy(id: string, text: string): void {
    navigator.clipboard.writeText(text).then(() => {
      this.copiedId.set(id);
      setTimeout(() => {
        if (this.copiedId() === id) {
          this.copiedId.set(null);
        }
      }, 1500);
    });
  }
}
