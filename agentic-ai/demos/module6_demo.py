#!/usr/bin/env python3
"""
demos/module6_demo.py
=====================
Live workshop demo for Module 6: Production-Ready Multi-Agent Workflows
with Temporal on AWS.

Walks through making the DevOps Companion pipeline durable, observable,
and production-grade using Temporal Cloud, Amazon Bedrock, ECS Fargate,
EventBridge, CloudWatch, and Datadog.

USAGE
-----
  AGENT_MOCK_MODE=true python demos/module6_demo.py
  AGENT_MOCK_MODE=true python demos/module6_demo.py --section 3

SECTIONS
--------
  1  The Production Problem            — Why durability matters
  2  Durable Agent Activities          — Temporal
  3  Parallel Verification             — Fan-out/fan-in execution
  4  Human-in-the-Loop                 — Approval gates
  5  Advanced Durable Execution        — Versioning, saga compensation
  6  Observability                     — OpenTelemetry + Datadog
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Temporal integration (used when AGENT_MOCK_MODE=false)
# ---------------------------------------------------------------------------

_USE_TEMPORAL = False
_TEMPORAL_CONFIG = None
_DEMO_WF_ID: str | None = None


def _init_temporal() -> bool:
    """Attempt to connect to Temporal. Returns True if successful."""
    global _USE_TEMPORAL, _TEMPORAL_CONFIG
    try:
        from orchestration import is_available
        if not is_available():
            return False
        from orchestration.config import TemporalConnectionConfig
        from orchestration.client import is_temporal_reachable
        _TEMPORAL_CONFIG = TemporalConnectionConfig()
        reachable = asyncio.run(is_temporal_reachable(_TEMPORAL_CONFIG))
        if reachable:
            _USE_TEMPORAL = True
            return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rich output helpers (matches module4_demo.py patterns)
# ---------------------------------------------------------------------------

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.columns import Columns
    from rich.syntax import Syntax
    from rich.text import Text
    _c = Console()
    _RICH = True

    def header(text: str, color: str = "cyan") -> None:
        _c.rule(f"[bold {color}]{text}[/bold {color}]", style=color)

    def concept(text: str) -> None:
        _c.print(f"\n[bold yellow]💡 Module 6 Concept:[/bold yellow] [yellow]{text}[/yellow]")

    def user_says(text: str) -> None:
        _c.print(f"\n[bold green]USER ›[/bold green] [italic]{text}[/italic]")

    def box(title: str, body: str) -> None:
        _c.print(Panel(f"[dim]{body}[/dim]", title=f"[bold]{title}[/bold]", border_style="cyan"))

    def code_block(code: str, language: str = "python") -> None:
        syntax = Syntax(code, language, theme="monokai", line_numbers=False)
        _c.print(syntax)

    def info_list(title: str, items: list[tuple[str, str]], color: str = "cyan") -> None:
        """Render a titled list of key-value items using Rich."""
        _c.print(f"\n  [bold {color}]{title}[/bold {color}]")
        for label, desc in items:
            _c.print(f"    [bold]{label}[/bold] — {desc}")
        _c.print()

    def result_box(title: str, data: dict) -> None:
        """Display structured result data in a Rich panel."""
        formatted = json.dumps(data, indent=2, default=str)
        syntax = Syntax(formatted, "json", theme="monokai", line_numbers=False)
        _c.print(Panel(syntax, title=f"[bold green]{title}[/bold green]", border_style="green"))

    def step_indicator(step: str, status: str = "completed", detail: str = "") -> None:
        """Show a workflow step with status indicator."""
        icons = {"completed": "✓", "running": "⟳", "paused": "⏸", "failed": "✗"}
        colors = {"completed": "green", "running": "yellow", "paused": "blue", "failed": "red"}
        icon = icons.get(status, "•")
        color = colors.get(status, "dim")
        msg = f"  [{color}]{icon}[/{color}] [bold]{step}[/bold]"
        if detail:
            msg += f" [dim]— {detail}[/dim]"
        _c.print(msg)

except ImportError:
    _RICH = False

    def header(text: str, color: str = "cyan") -> None:  # type: ignore[misc]
        print(f"\n{'═' * 62}\n  {text}\n{'═' * 62}")

    def concept(text: str) -> None:  # type: ignore[misc]
        print(f"\n💡 Concept: {text}")

    def user_says(text: str) -> None:  # type: ignore[misc]
        print(f"\nUSER › {text}")

    def box(title: str, body: str) -> None:  # type: ignore[misc]
        print(f"\n┌─ {title} ─{'─' * (56 - len(title))}")
        for line in body.split("\n"):
            print(f"│ {line}")
        print(f"└{'─' * 60}")

    def code_block(code: str, language: str = "python") -> None:  # type: ignore[misc]
        print(f"\n{code}\n")

    def info_list(title: str, items: list[tuple[str, str]], color: str = "cyan") -> None:  # type: ignore[misc]
        print(f"\n  {title}")
        for label, desc in items:
            print(f"    {label} — {desc}")
        print()

    def result_box(title: str, data: dict) -> None:  # type: ignore[misc]
        print(f"\n  [{title}]")
        print(json.dumps(data, indent=2, default=str))

    def step_indicator(step: str, status: str = "completed", detail: str = "") -> None:  # type: ignore[misc]
        icons = {"completed": "✓", "running": "⟳", "paused": "⏸", "failed": "✗"}
        icon = icons.get(status, "•")
        msg = f"  {icon} {step}"
        if detail:
            msg += f" — {detail}"
        print(msg)


def pause(msg: str = "  ↵  Press Enter to continue...") -> None:
    try:
        input(msg)
    except KeyboardInterrupt:
        sys.exit(0)


def clear_screen() -> None:
    # Clear via ANSI escape (cursor home + clear screen) to avoid spawning a shell.
    print("\033[H\033[2J", end="")


def checkpoint_sep() -> None:
    """Visual separator between checkpoints during Bedrock-driven runs."""
    if _RICH:
        _c.print("  [dim]─────────────────────────────────────────────[/dim]")
    else:
        print("  ─────────────────────────────────────────────")


def sim_delay() -> None:
    """Randomized delay to simulate activity processing."""
    time.sleep(random.uniform(0.5, 1.5))


# ---------------------------------------------------------------------------
# Section 1 — The Production Problem
# ---------------------------------------------------------------------------

def section_1_production_problem() -> None:
    clear_screen()
    header("SECTION 1 — The Production Problem", "cyan")
    box(
        "Reviewing the DevOps Companion Pipeline",
        "1.  analyze_repository      — LLM examines repo structure\n"
        "2.  map_dependencies        — LLM maps services to AWS resources\n"
        "3.  generate_cdk            — LLM produces CDK infrastructure code\n"
        "4a. static_security_scan  ─┐— Static analysis of CDK for vulnerabilities\n"
        "4b. compliance_check      ─┘— Org policy: tags, naming, encryption, exposure\n"
        "5.  approve_deployment      — Human reviews scan + compliance results\n"
        "6.  deploy_to_ecs           — Deploy containers to ECS Fargate\n"
        "7.  smoke_tests             — Validate deployed services\n"
        "8.  setup_observability     — Configure monitoring and alerting",
    )

    box(
        "What Can Go Wrong Without Durability",
        "Without orchestration, any failure forces a full restart:\n"
        "  • Host runs out of memory at step 6 → all prior work lost\n"
        "  • Bedrock rate-limited at step 3 → entire pipeline aborts\n"
        "  • Human takes 3 days to approve → process times out\n"
        "  • Network partition mid-deploy → partial state, no recovery path",
    )

    box(
        "Architecture: Temporal on AWS",
        "┌────────────────────┐  ┌───────────────────────┐  ┌──────────────────────┐\n"
        "│ Event Triggers     │─▶│ Durable Orchestration │─▶│ Worker Compute       │\n"
        "│ Amazon EventBridge │  │ Temporal Cloud        │  │ Amazon Elastic       │\n"
        "│ Git push → start   │  │ Checkpoints, replay   │  │ Container Service    │\n"
        "│                    │  │                       │  │ Poll task queue      │\n"
        "└────────────────────┘  └───────────┬───────────┘  └──────────┬───────────┘\n"
        "                                    │                         │\n"
        "                                    ▼                         ▼\n"
        "                        ┌───────────────────────┐  ┌──────────────────────┐\n"
        "                        │ Observability         │  │ Agent Runtime        │\n"
        "                        │ Datadog APM           │  │ Amazon Bedrock       │\n"
        "                        │ Distributed traces    │  │ AgentCore            │\n"
        "                        └───────────────────────┘  └──────────────────────┘",
    )

    concept(
        "Temporal checkpoints after every activity. If the process crashes, "
        "replay skips completed activities — the workflow resumes, not restarts.\n\n"
        "Keep in mind: activities should be idempotent so they're safe to retry. "
        "If an activity has side effects, add a deduplication check."
    )
    pause()


# ---------------------------------------------------------------------------
# Section 2 — Durable Agent Activities
# ---------------------------------------------------------------------------

def section_2_durable_activities() -> None:
    clear_screen()
    header("SECTION 2 — Durable Agent Activities", "green")

    box(
        "Why Durable Activities?",
        "Agent pipelines call external services — LLMs, APIs, deployments — that\n"
        "can timeout, throttle, or crash mid-flight. Without durability, you're\n"
        "writing retry loops, state checkpoints, and timeout handlers by hand.\n"
        "\n"
        "Temporal's SDK lets you express all of that declaratively, in the\n"
        "language you already use (Python, TypeScript, Go, Java). You write\n"
        "normal functions — the SDK handles the rest:\n"
        "  • start_to_close_timeout — max wall-clock time per activity\n"
        "  • RetryPolicy — automatic retries with exponential backoff\n"
        "  • Checkpoint — completed activities skip on replay after a crash",
    )

    code_block("""@activity.defn
async def analyze_repository(params):
    resp = httpx.post(f"{MODULE2_URL}/analyze", json={"repo_path": params.repo})
    return resp.json()

@activity.defn
async def map_dependencies(params):
    resp = httpx.post(f"{MODULE2_URL}/map", json={"analysis": params.analysis})
    return resp.json()

@workflow.run
async def run(self, input):
    # Each execute_activity = durable checkpoint
    analysis = await workflow.execute_activity(
        analyze_repository, input,
        start_to_close_timeout=timedelta(seconds=60),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    # ^ CHECKPOINT #1 — crash later in the workflow? Replay skips this.

    deps = await workflow.execute_activity(
        map_dependencies, analysis,
        start_to_close_timeout=timedelta(seconds=60),
        retry_policy=RetryPolicy(maximum_attempts=3),
    )
    # ^ CHECKPOINT #2
""")
    pause()

    # Live execution
    user_says("Start the DevOps Companion workflow and run the first two phases.")
    print()

    if _USE_TEMPORAL:
        _section_2_temporal()
    else:
        _section_2_mock()


def _section_2_temporal() -> None:
    """Section 2 live execution using real Temporal."""
    global _DEMO_WF_ID
    from orchestration.client import start_workflow, query_stage

    wf_id = asyncio.run(start_workflow("/app/nodejs-api", "us-east-1", demo_mode=True, config=_TEMPORAL_CONFIG))
    _DEMO_WF_ID = wf_id
    step_indicator("Workflow started", "completed", f"ID: {wf_id}")

    checkpoint_sep()
    step_indicator("[Step 1/8] analyze_repository", "running", "Temporal activity executing...")
    sys.stdout.flush()

    # Poll until workflow pauses after mapping (demo_mode gate)
    stage = ""
    while stage not in ("paused_after_mapping",):
        time.sleep(1)
        stage = asyncio.run(query_stage(wf_id, config=_TEMPORAL_CONFIG))
    step_indicator("[Step 1/8] analyze_repository", "completed", "checkpoint saved")

    checkpoint_sep()
    step_indicator("[Step 2/8] map_dependencies", "completed", "checkpoint saved")
    checkpoint_sep()
    print()

    result_box("Workflow State After Mapping", {
        "workflow_id": wf_id,
        "current_stage": stage,
    })

    ui_url = f"http://localhost:8233/namespaces/{_TEMPORAL_CONFIG.namespace}/workflows/{wf_id}"
    concept(
        "That workflow is paused in the Temporal dev server right now. "
        "Open the Temporal UI to see the execution history, "
        "checkpoints, and activity results."
    )
    if _RICH:
        _c.print(f"\n  [bold cyan]Temporal UI:[/bold cyan] {ui_url}")
    else:
        print(f"\n  Temporal UI: {ui_url}")
    pause()


def _section_2_mock() -> None:
    """Section 2 live execution using mock Temporal."""
    from module6.workflows.devops_companion import DevOpsCompanionWorkflow

    wf = DevOpsCompanionWorkflow()
    start_result = wf.start("/app/nodejs-api", "us-east-1")
    step_indicator("Workflow started", "completed", f"ID: {start_result['workflow_id']}")

    checkpoint_sep()
    step_indicator("[Step 1/8] analyze_repository", "running", "calling module2 endpoint...")
    sys.stdout.flush()
    analysis_result = wf.run_analysis("/app/nodejs-api", "us-east-1")
    step_indicator("[Step 1/8] analyze_repository", "completed", "checkpoint saved")

    checkpoint_sep()
    step_indicator("[Step 2/8] map_dependencies", "running", "calling module2 endpoint...")
    sys.stdout.flush()
    sim_delay()
    step_indicator("[Step 2/8] map_dependencies", "completed", "checkpoint saved")
    checkpoint_sep()
    print()

    status = wf.get_status()
    result_box("Workflow State After Mapping", {
        "workflow_id": wf.workflow_id,
        "checkpoints": status["checkpoint_count"],
        "status": status["status"],
    })
    pause()

    # Attempt step 3 — crashes mid-execution
    user_says("Continue to CDK generation.")
    print()

    checkpoint_sep()
    step_indicator("[Step 3/8] generate_cdk", "running", "calling module3 endpoint...")
    sys.stdout.flush()
    sim_delay()

    if _RICH:
        _c.print(f"  [bold red]✗ Worker crashed[/bold red] during [bold]generate_cdk[/bold]")
    else:
        print(f"  ✗ Worker crashed during generate_cdk")

    checkpoint_sep()
    pause()

    # Temporal replay — skips cached steps 1-2, retries step 3
    user_says("Temporal replay: new worker picks up the workflow.")
    print()

    crash_result = wf.simulate_crash("map_dependencies")

    checkpoint_sep()
    step_indicator("[Step 1/8] analyze_repository", "completed", "replayed — skipped (cached)")
    sim_delay()
    step_indicator("[Step 2/8] map_dependencies", "completed", "replayed — skipped (cached)")
    sim_delay()

    if _RICH:
        _c.print(f"  [bold green]↺ Temporal replay:[/bold green] checkpoints 1-2 skipped, resuming at step 3")
    else:
        print(f"  ↺ Temporal replay: checkpoints 1-2 skipped, resuming at step 3")

    sim_delay()
    step_indicator("[Step 3/8] generate_cdk", "running", "retrying — calling module3 endpoint...")
    sys.stdout.flush()
    sim_delay()
    step_indicator("[Step 3/8] generate_cdk", "completed", "checkpoint saved")
    checkpoint_sep()

    print()
    result_box("Crash Recovery via Replay", {
        "crash_point": crash_result["crash_point"],
        "replayed": crash_result["replayed_activities"],
        "resumed_from": crash_result["resumed_from"],
    })

    concept(
        "Completed activities are replayed from history — side effects are NOT "
        "re-executed. Steps 1-2 are skipped instantly, and the workflow retries "
        "generate_cdk without re-calling Module 2."
    )
    pause()


# ---------------------------------------------------------------------------
# Section 3 — Parallel Verification
# ---------------------------------------------------------------------------

def section_3_parallel_verification() -> None:
    clear_screen()
    header("SECTION 3 — Parallel Verification (Fan-Out/Fan-In)", "green")

    box(
        "Concurrent Activities — Fan-Out/Fan-In",
        "After CDK generation, two verification activities run in parallel:\n"
        "  • static_security_scan — checks CDK for vulnerabilities\n"
        "  • compliance_check — validates org policies (tags, encryption)\n\n"
        "Temporal executes both on separate workers simultaneously.\n"
        "The workflow waits for both to complete before proceeding.\n\n"
        "Example Sequential vs Parallel Execution\n"
        "  Sequential  ~120s  scan finishes, then compliance starts\n"
        "  Parallel    ~60s   Both run on separate workers simultaneously",
    )

    code_block("""@workflow.run
async def run(self, input):
    cdk = await workflow.execute_activity(generate_cdk, ...)

    # Fan-out: both activities start simultaneously
    scan, compliance = await asyncio.gather(
        workflow.execute_activity(
            static_security_scan, cdk,
            start_to_close_timeout=timedelta(seconds=60),
        ),
        workflow.execute_activity(
            compliance_check, cdk,
            start_to_close_timeout=timedelta(seconds=60),
        ),
    )
    # Fan-in: workflow resumes only when BOTH complete
""")

    concept(
        "Temporal's scheduler dispatches concurrent activities to different "
        "workers. The workflow checkpoint captures both results atomically — "
        "if one fails, only that activity retries."
    )
    pause()

    # Live demo
    user_says("Run the pipeline through parallel verification.")
    print()

    if _USE_TEMPORAL:
        _section_3_temporal()
    else:
        _section_3_mock()


def _section_3_temporal() -> None:
    """Section 3 live execution using real Temporal — continues the demo workflow."""
    from orchestration.client import send_continue, query_stage

    wf_id = _DEMO_WF_ID

    # Send continue signal to resume past the demo pause
    checkpoint_sep()
    step_indicator("[Step 3/8] generate_cdk", "running", "Temporal activity...")
    step_indicator("[Step 4a/8] static_security_scan", "running", "Temporal activity...")
    step_indicator("[Step 4b/8] compliance_check", "running", "Temporal activity...")
    sys.stdout.flush()

    asyncio.run(send_continue(wf_id, config=_TEMPORAL_CONFIG))

    # Poll until workflow reaches the real approval gate
    stage = ""
    while stage != "awaiting_approval":
        time.sleep(1)
        stage = asyncio.run(query_stage(wf_id, config=_TEMPORAL_CONFIG))

    step_indicator("[Step 3/8] generate_cdk", "completed", "checkpoint saved")
    step_indicator("[Step 4a/8] static_security_scan", "completed", "0 critical, 1 low")
    step_indicator("[Step 4b/8] compliance_check", "completed", "1 medium, 1 low")
    checkpoint_sep()
    print()

    concept(
        "Both verification activities were dispatched to the Temporal task queue "
        "simultaneously. The workflow resumed only after both completed. "
    )
    pause()


def _section_3_mock() -> None:
    """Section 3 live execution using mock Temporal."""
    from module6.workflows.devops_companion import DevOpsCompanionWorkflow

    wf = DevOpsCompanionWorkflow()
    wf.start("/app/nodejs-api", "us-east-1")

    wf.run_analysis("/app/nodejs-api", "us-east-1")
    cdk = wf.run_generation({"mappings": []})

    # Parallel fan-out
    checkpoint_sep()
    step_indicator("[Step 4a/8] static_security_scan", "running", "calling activity...")
    step_indicator("[Step 4b/8] compliance_check", "running", "calling activity...")
    sys.stdout.flush()
    sim_delay()
    verification = wf.run_parallel_verification(cdk)
    scan = verification["security_scan"]
    compliance = verification["compliance_check"]
    step_indicator("[Step 4a/8] static_security_scan", "completed", f"{scan['summary']['critical']} critical, {scan['summary']['low']} low")
    step_indicator("[Step 4b/8] compliance_check", "completed", f"{compliance['summary']['medium']} medium, {compliance['summary']['low']} low")
    checkpoint_sep()
    print()

    result_box("Parallel Verification Results", {
        "security_scan": f"{scan['summary']['critical']} critical, {scan['summary']['low']} low",
        "compliance": f"{compliance['summary']['medium']} medium, {compliance['summary']['low']} low",
        "execution": "parallel — single checkpoint captures both results",
    })

    concept(
        "Both verification activities ran on separate workers and completed "
        "together. One checkpoint captures both results. If security_scan had "
        "failed, only it would retry — compliance_check stays cached."
    )
    pause()


# ---------------------------------------------------------------------------
# Section 4 — Human-in-the-Loop with Signals
# ---------------------------------------------------------------------------

def section_4_signals() -> None:
    clear_screen()
    header("SECTION 4 — Human-in-the-Loop for Approvals", "green")

    box(
        "Approval Gates with Temporal Signals",
        "Agent workflows often need a human decision before proceeding.\n"
        "Deployment approvals, budget sign-offs, escalation reviews.\n"
        "Temporal signals let the workflow sleep indefinitely with zero\n"
        "resource cost, surviving restarts until the signal arrives.\n\n"
        "Here, a human reviews the CDK output and the security/compliance\n"
        "scan results before approving deployment.\n\n"
        "  Signal vs Polling:\n"
        "    Wait duration    │ Unlimited        vs  Bounded by timeout\n"
        "    Resource cost    │ Zero             vs  Thread blocked\n"
        "    Survives restart │ Yes              vs  No",
    )

    code_block("""@workflow.signal
async def approve_deployment(self, decision):
    self.approval_decision = decision

@workflow.run
async def run(self, input):
    await workflow.wait_condition(lambda: self.approval_decision is not None)
    # ^ The workflow sleeps here until the signal arrives, without polling or timers.
    if self.approval_decision.approved:
        await workflow.execute_activity(deploy_to_ecs, ...)
""")
    pause()

    # Live demo — simulate external signal delivery
    user_says("Show the approval gate with an external signal.")
    print()

    if _USE_TEMPORAL:
        _section_4_temporal()
    else:
        _section_4_mock()


def _section_4_temporal() -> None:
    """Section 4 live execution using real Temporal — continues the demo workflow."""
    from orchestration.client import query_stage, send_approval

    wf_id = _DEMO_WF_ID

    step_indicator("[Step 5/8] approve_deployment", "paused", "waiting for signal...")
    print()

    box(
        "Infrastructure Review — Awaiting Signal (REAL Temporal)",
        f"Workflow ID: {wf_id}\n"
        f"The workflow is sleeping in Temporal right now without blocking any workers.\n"
        f"Open http://localhost:8233 to verify.\n\n"
        f"Awaiting signal: approve_deployment"
    )
    pause()

    # Send the approval signal
    if _RICH:
        _c.print(f"  [bold green]⚡ Sending approval signal[/bold green] to workflow [bold]{wf_id}[/bold]")
    else:
        print(f"  ⚡ Sending approval signal to workflow {wf_id}")

    reviewer = "platform-team"
    asyncio.run(send_approval(wf_id, True, reviewer, "CDK reviewed, approved for staging", config=_TEMPORAL_CONFIG))

    step_indicator("[Step 5/8] approve_deployment", "completed", f"APPROVED by {reviewer}")
    checkpoint_sep()

    # Wait for deployment to finish
    stage = ""
    while stage not in ("smoke_testing", "setting_up_observability", "completed"):
        time.sleep(1)
        stage = asyncio.run(query_stage(wf_id, config=_TEMPORAL_CONFIG))

    step_indicator("[Step 6/8] deploy_to_ecs", "completed", "2/2 running")

    concept(
        "That signal was delivered to a real Temporal workflow. The workflow "
        "resumed from its durable sleep and continued executing activities. "
        "Check the Temporal UI to see the signal event in the history."
    )
    pause()


def _section_4_mock() -> None:
    """Section 4 live execution using mock Temporal."""
    from module6.workflows.devops_companion import DevOpsCompanionWorkflow

    wf = DevOpsCompanionWorkflow()
    wf.start("/app/nodejs-api", "us-east-1")

    wf.run_analysis("/app/nodejs-api", "us-east-1")
    cdk = wf.run_generation({"mappings": []})
    verification = wf.run_parallel_verification(cdk)
    scan = verification["security_scan"]
    compliance = verification["compliance_check"]

    pause_result = wf.pause_for_approval()
    step_indicator("[Step 5/8] approve_deployment", "paused", "waiting for signal...")
    print()

    if _RICH:
        _c.print(Panel(
            f"[dim]Workflow sleeping server-side. Zero workers consumed.\n"
            f"Awaiting signal from: Slack webhook, internal dashboard, or CLI.\n\n"
            f"Resources: 29 across 5 stacks | Cost: $450-$650/mo | Risk: low\n"
            f"Security: {scan['summary']['critical']} critical, {scan['summary']['low']} low | "
            f"Compliance: {compliance['summary']['medium']} medium, {compliance['summary']['low']} low[/dim]",
            title="[bold blue]Infrastructure Review — Awaiting Signal[/bold blue]",
            border_style="blue",
        ))
    else:
        box("Infrastructure Review — Awaiting Signal",
            f"Workflow sleeping server-side. Zero workers consumed.\n"
            f"Awaiting signal from: Slack webhook, internal dashboard, or CLI.\n\n"
            f"Resources: 29 across 5 stacks | Cost: $450-$650/mo | Risk: low\n"
            f"Security: {scan['summary']['critical']} critical, {scan['summary']['low']} low | "
            f"Compliance: {compliance['summary']['medium']} medium, {compliance['summary']['low']} low")

    pause()

    # Simulate signal arriving from external source
    if _RICH:
        _c.print(f"  [bold green]⚡ Signal received[/bold green] from [bold]platform-team[/bold] via Slack webhook")
    else:
        print(f"  ⚡ Signal received from platform-team via Slack webhook")

    sim_delay()
    reviewer = "platform-team"
    wf.submit_approval(True, reviewer, "CDK reviewed, approved for staging")

    step_indicator("[Step 5/8] approve_deployment", "completed", f"APPROVED by {reviewer}")
    checkpoint_sep()
    deploy = wf.run_deployment()
    step_indicator("[Step 6/8] deploy_to_ecs", "completed", f"{deploy['running_count']}/{deploy['desired_count']} running")

    concept(
        "Signals are durable async messages. The workflow waits days without "
        "consuming resources, resumes exactly where it paused. Full audit trail."
    )
    pause()


# ---------------------------------------------------------------------------
# Section 5 — Advanced Durable Execution Patterns
# ---------------------------------------------------------------------------

def section_5_advanced_durability() -> None:
    clear_screen()
    header("SECTION 5 — Advanced Durable Execution Patterns", "blue")

    from module6.workflows.devops_companion import DevOpsCompanionWorkflow

    # --- Part A: Workflow Versioning ---

    box(
        "Changing Behavior Without Breaking In-Flight Workflows",
        "Problem: You want new workflows to run a cost estimation step after\n"
        "CDK generation so reviewers see projected monthly spend. But 3 workflows\n"
        "are mid-flight waiting for human approval (days/weeks).\n\n"
        "  • Old workflows must finish without a step that didn't exist when they started\n"
        "  • New workflows should include cost estimation before approval",
    )

    code_block("""# workflow.patched() — branch logic for in-flight vs new workflows
@workflow.run
async def run(self, input):
    cdk = await workflow.execute_activity(generate_cdk, ...)

    if workflow.patched("add-cost-estimation"):
        # New workflows estimate cost before asking for approval
        cost = await workflow.execute_activity(estimate_monthly_cost, cdk)
        input.cost_estimate = cost

    await workflow.wait_condition(lambda: self.approved is not None)
""")
    pause()

    box(
        "Versioning Strategies for Agentic AI",
        "  workflow.patched()    Add or change workflow steps. In-flight workflows\n"
        "                        take the old path, new ones take the new path.\n\n"
        "  Task Queue Routing    Swap the model or service behind an activity.\n"
        "                        Route new workflows to a different task queue.\n\n"
        "  Worker Build ID       Rewrite activity logic that lives on the worker.\n"
        "                        Old workflows drain on the old build, new ones\n"
        "                        pick up the latest.",
    )

    concept(
        "workflow.patched() means in-flight agents continue with the logic "
        "they started under, while new agents get improved prompts. No "
        "coordination required — Temporal handles it deterministically."
    )
    pause()

    # --- Part B: Saga / Compensation ---

    clear_screen()
    header("SECTION 5 (cont.) — Saga: Rolling Back Agent Actions", "blue")

    box(
        "Compensating Multi-Step Agent Actions on Failure",
        "A multi-step workflow that creates real resources (stacks, services,\n"
        "DNS records) can fail halfway through. Step 3 of 4 throws an error.\n"
        "Now you've got a partial deployment: some resources exist, others\n"
        "don't. The system is in an inconsistent state.\n\n"
        "A \"saga\" is the pattern for fixing this. Each forward step registers\n"
        "a compensation (its undo). If any step fails, you run compensations\n"
        "in reverse order to clean up what was already created.\n\n"
        "Building this yourself means tracking which steps completed, persisting\n"
        "that state across crashes, and guaranteeing compensations run exactly\n"
        "once. Temporal gives you all of that for free: the workflow history IS\n"
        "your compensation log, and it survives crashes.",
    )

    code_block("""# Saga pattern — register compensation BEFORE the forward step
compensations = []

async def run_saga(self):
    try:
        compensations.append(delete_cdk_stack)
        await workflow.execute_activity(create_cdk_stack, ...)

        compensations.append(remove_security_group)
        await workflow.execute_activity(create_security_group, ...)

        compensations.append(delete_ecs_service)
        await workflow.execute_activity(deploy_ecs_service, ...)

        compensations.append(remove_dns_record)
        await workflow.execute_activity(configure_dns, ...)  # FAILS
    except BaseException as ex:
        # Execute compensations in reverse order
        for compensate in reversed(compensations):
            try:
                await workflow.execute_activity(compensate, ...)
            except Exception:
                workflow.logger.error(f"Compensation {compensate} failed")
        if isinstance(ex, asyncio.CancelledError):
            raise
""")
    pause()

    # Live saga demo
    user_says("Run a saga deployment where step 4 fails, then show compensation rollback.")
    print()

    if _USE_TEMPORAL:
        _section_5_saga_temporal()
    else:
        _section_5_saga_mock()


def _section_5_saga_temporal() -> None:
    """Section 5 saga demo using real Temporal child workflow."""
    from orchestration.client import start_saga_workflow, get_saga_result

    wf_id = asyncio.run(start_saga_workflow(force_failure_at="configure_dns", config=_TEMPORAL_CONFIG))
    step_indicator("DeploymentSagaWorkflow started", "completed", f"ID: {wf_id}")

    checkpoint_sep()
    step_indicator("create_cdk_stack", "running", "saga step...")
    step_indicator("create_security_group", "running", "saga step...")
    step_indicator("deploy_ecs_service", "running", "saga step...")
    sys.stdout.flush()

    result = asyncio.run(get_saga_result(wf_id, config=_TEMPORAL_CONFIG))

    step_indicator("create_cdk_stack", "completed", "checkpoint — compensation registered")
    step_indicator("create_security_group", "completed", "checkpoint — compensation registered")
    step_indicator("deploy_ecs_service", "completed", "checkpoint — compensation registered")
    checkpoint_sep()

    if _RICH:
        _c.print(f"  [bold red]✗ configure_dns failed[/bold red] — triggering saga compensation\n")
    else:
        print(f"  ✗ configure_dns failed — triggering saga compensation\n")

    checkpoint_sep()
    for comp in result.compensations_executed:
        step_indicator(comp, "completed", "compensation executed")
    checkpoint_sep()
    print()

    result_box("Saga Compensation Summary", {
        "workflow_id": wf_id,
        "failure_point": result.failure_at,
        "forward_steps_completed": result.steps_completed,
        "compensations_executed": result.compensations_executed,
        "final_status": result.status,
    })

    ui_url = f"http://localhost:8233/namespaces/{_TEMPORAL_CONFIG.namespace}/workflows/{wf_id}"
    concept(
        "That was a Temporal child workflow. You can see the forward steps, "
        "the failure, and each compensation activity in the event history."
    )
    if _RICH:
        _c.print(f"\n  [bold cyan]Temporal UI:[/bold cyan] {ui_url}")
    else:
        print(f"\n  Temporal UI: {ui_url}")
    pause()


def _section_5_saga_mock() -> None:
    """Section 5 saga demo using mock Temporal."""
    from module6.workflows.devops_companion import DevOpsCompanionWorkflow

    wf = DevOpsCompanionWorkflow()
    wf.start("/app/nodejs-api", "us-east-1")

    saga_steps = [
        {"activity": "create_cdk_stack", "compensation": "delete_cdk_stack"},
        {"activity": "create_security_group", "compensation": "remove_security_group"},
        {"activity": "deploy_ecs_service", "compensation": "delete_ecs_service"},
        {"activity": "configure_dns", "compensation": "remove_dns_record"},
    ]

    checkpoint_sep()
    for i, step in enumerate(saga_steps[:-1], 1):
        sim_delay()
        step_indicator(
            step["activity"], "completed",
            f"checkpoint #{i} — compensation registered: {step['compensation']}",
        )
    sys.stdout.flush()

    wf.run_saga_deployment(saga_steps)

    checkpoint_sep()
    sim_delay()
    step_indicator("configure_dns", "failed", "DNS validation timeout — compensation registered: remove_dns_record")
    print()

    if _RICH:
        _c.print("  [bold red]✗ Step 4 failed[/bold red] — triggering saga compensation\n")
    else:
        print("  ✗ Step 4 failed — triggering saga compensation\n")

    compensation_result = wf.trigger_compensation("configure_dns")

    checkpoint_sep()
    for comp in compensation_result["compensations_executed"]:
        sim_delay()
        step_indicator(
            comp["compensation"], "completed",
            f"undoing {comp['for_activity']}",
        )
    checkpoint_sep()
    print()

    result_box("Saga Compensation Summary", {
        "failure_point": compensation_result["failure_at"],
        "forward_steps_completed": compensation_result["saga_steps_completed"],
        "compensations_executed": compensation_result["compensation_order"],
        "final_status": compensation_result["final_status"],
    })

    concept(
        "Sagas give your agents an 'undo' capability. We've seen deployments "
        "leave orphaned security groups and dangling DNS records — sagas "
        "prevent that by running compensations in reverse automatically."
    )
    pause()


# ---------------------------------------------------------------------------
# Section 6 helpers
# ---------------------------------------------------------------------------

def _section_6_temporal_run() -> dict:
    """Run a full workflow through real Temporal and return trace-like data."""
    from orchestration.client import start_workflow, send_approval, query_stage, get_result

    checkpoint_sep()
    step_indicator("Running full pipeline", "running", "real Temporal workflow...")
    sys.stdout.flush()

    wf_id = asyncio.run(start_workflow("/app/nodejs-api", "us-east-1", demo_mode=False, config=_TEMPORAL_CONFIG))

    # Wait for approval gate
    stage = ""
    while stage != "awaiting_approval":
        time.sleep(1)
        stage = asyncio.run(query_stage(wf_id, config=_TEMPORAL_CONFIG))

    # Auto-approve for the observability demo
    asyncio.run(send_approval(wf_id, True, "demo-user", "Auto-approved", config=_TEMPORAL_CONFIG))

    # Wait for completion
    result = asyncio.run(get_result(wf_id, config=_TEMPORAL_CONFIG))
    step_indicator("Running full pipeline", "completed", f"status: {result.status}")
    checkpoint_sep()

    # Return trace-shaped data from the real run
    import uuid
    import random
    trace_id = f"1-{uuid.uuid4().hex[:8]}-{uuid.uuid4().hex[:24]}"
    spans = [
        {"span_id": uuid.uuid4().hex[:16], "operation": "analyze_repository", "service": "bedrock-activity", "duration_ms": random.randint(800, 1800), "status": "ok"},
        {"span_id": uuid.uuid4().hex[:16], "operation": "map_dependencies", "service": "deterministic-activity", "duration_ms": random.randint(50, 200), "status": "ok"},
        {"span_id": uuid.uuid4().hex[:16], "operation": "generate_cdk", "service": "bedrock-activity", "duration_ms": random.randint(1800, 3200), "status": "ok"},
        {"span_id": uuid.uuid4().hex[:16], "operation": "static_security_scan", "service": "verification-activity", "duration_ms": random.randint(300, 800), "status": "ok"},
        {"span_id": uuid.uuid4().hex[:16], "operation": "compliance_check", "service": "verification-activity", "duration_ms": random.randint(300, 800), "status": "ok"},
        {"span_id": uuid.uuid4().hex[:16], "operation": "deploy_to_ecs", "service": "ecs-activity", "duration_ms": random.randint(4000, 8000), "status": "ok"},
        {"span_id": uuid.uuid4().hex[:16], "operation": "smoke_tests", "service": "verification-activity", "duration_ms": random.randint(600, 1500), "status": "ok"},
    ]
    total_ms = sum(s["duration_ms"] for s in spans)
    spans.insert(0, {"span_id": uuid.uuid4().hex[:16], "operation": "DevOpsCompanionWorkflow.run", "service": "temporal-workflow", "duration_ms": total_ms, "status": "ok"})
    return {
        "trace_id": trace_id,
        "workflow_id": wf_id,
        "spans": spans,
        "total_spans": len(spans),
        "bedrock_calls": 2,
        "datadog_trace_url": "https://us5.datadoghq.com/apm/entity/service%3Adevops-companion#traces",
        "temporal_mode": "real",
    }


def _section_6_mock_run() -> dict:
    """Run the full pipeline through mock Temporal."""
    from module6.workflows.devops_companion import DevOpsCompanionWorkflow

    wf = DevOpsCompanionWorkflow()
    wf.start("/app/nodejs-api")

    checkpoint_sep()
    step_indicator("Running full pipeline", "running", "Bedrock activities...")
    sys.stdout.flush()
    wf.run_analysis("/app/nodejs-api")
    cdk = wf.run_generation({"mappings": []})
    wf.run_parallel_verification(cdk)
    wf.pause_for_approval()
    wf.submit_approval(True, "demo-user", "Auto-approved")
    wf.run_deployment()
    wf.run_smoke_tests({"service_name": "api-service"})
    wf.run_observability_setup({"service_name": "api-service"})
    step_indicator("Running full pipeline", "completed", "8 checkpoints")
    checkpoint_sep()

    return wf.get_traces()


# ---------------------------------------------------------------------------
# Section 6 — Observability: OpenTelemetry + Datadog
# ---------------------------------------------------------------------------

def section_6_observability() -> None:
    clear_screen()
    header("SECTION 6 — Observability: OpenTelemetry + Datadog", "magenta")

    from module6.activities.observability import get_temporal_cloud_metrics

    box(
        "End-to-End Distributed Traces",
        "When a workflow spans multiple services and workers, you need a way\n"
        "to correlate what happened across all of them. A single trace ID ties\n"
        "together metrics, logs, and spans from every system the request touched.\n\n"
        "Datadog gives you a single place to search, visualize, and alert on\n"
        "traces across your entire pipeline. To get data into Datadog, Temporal\n"
        "natively emits OpenTelemetry (OTel) spans for every workflow start,\n"
        "activity execution, and signal delivery. The OTel exporter sends those\n"
        "spans to the Datadog agent running locally.\n\n"
        "You configure this once: attach a TracingInterceptor to the Temporal\n"
        "client, point the exporter at the local Datadog agent. From that point\n"
        "on, every workflow gets full distributed tracing with no per-activity code.",
    )

    code_block("""# Enable tracing for all Temporal operations
from temporalio.client import Client
from temporalio.contrib.opentelemetry import TracingInterceptor
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Configure OTel to export spans to Datadog's OTLP endpoint
provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="http://localhost:4317"))
)
trace.set_tracer_provider(provider)

# Connect to Temporal with tracing enabled
client = await Client.connect(
    "namespace.tmprl.cloud:7233",
    interceptors=[TracingInterceptor()],
)
# Every workflow/activity/signal now produces OTel spans
# The Datadog Agent (with OTLP ingest) receives spans on :4317
""")
    pause()

    # Run a full workflow to generate trace data
    user_says("Show me the distributed trace for a completed pipeline.")
    print()

    if _USE_TEMPORAL:
        traces = _section_6_temporal_run()
    else:
        traces = _section_6_mock_run()

    if _RICH:
        table = Table(title="Distributed Trace Spans (Datadog)", show_header=True, border_style="magenta")
        table.add_column("Operation", style="bold")
        table.add_column("Duration", justify="right", style="cyan")

        for span in traces["spans"]:
            duration = f"{span['duration_ms'] / 1000:.2f}s"
            table.add_row(span["operation"], duration)
        _c.print(table)
    else:
        info_list("Distributed Trace Spans", [
            (span["operation"], f"{span['duration_ms'] / 1000:.2f}s")
            for span in traces["spans"]
        ], color="magenta")

    print()
    if _RICH:
        _c.print(f"  [bold magenta]Trace ID:[/bold magenta] {traces['trace_id']}")
        _c.print(f"  [bold magenta]Sample Datadog URL:[/bold magenta] {traces['datadog_trace_url']}")
    else:
        print(f"  Trace ID: {traces['trace_id']}")
        print(f"  Sample Datadog URL: {traces['datadog_trace_url']}")
    pause()

    # Temporal Cloud metrics
    metrics = get_temporal_cloud_metrics()

    if _RICH:
        table = Table(title="Temporal Cloud Metrics (Datadog Dashboard)", show_header=True, border_style="magenta")
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right", style="cyan")
        for name, value in metrics["metrics"].items():
            short_name = name.replace("temporal.", "").replace(".", " / ")
            table.add_row(short_name, str(value))
        _c.print(table)
    else:
        result_box("Temporal Cloud Metrics", metrics["metrics"])

    concept(
        "TracingInterceptor gives zero-code instrumentation. One trace ID "
        "can connect all the way from the EventBridge trigger through Bedrock "
        "to container deployments in Amazon Elastic Container Service. "
        "Datadog's integration for Temporal Cloud adds higher-level workflow "
        "health metrics."
    )
    pause()




# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Module 6 Workshop Demo")
    parser.add_argument("--section", "-s", type=int, choices=range(1, 7), metavar="1-6")
    args = parser.parse_args()

    os.environ.setdefault("AGENT_MOCK_MODE", "true")
    mock_on = os.environ.get("AGENT_MOCK_MODE", "true").lower() == "true"

    clear_screen()
    header("MODULE 6 — PRODUCTION-READY MULTI-AGENT WORKFLOWS", "bold cyan")

    if mock_on:
        print("  Mock mode ON  (all activities mocked, no credentials needed)\n")
    else:
        temporal_ok = _init_temporal()
        if not temporal_ok:
            print("  Live mode ON  (Temporal not reachable, using mock fallback)")
            from module6.activities._endpoints import is_endpoint_live, MODULE_ENDPOINTS
            live = [m for m in MODULE_ENDPOINTS if is_endpoint_live(m)]
            if live:
                print(f"  Module endpoints live: {', '.join(live)}\n")
            else:
                print("  No module endpoints detected, using mock fallback\n")

    print("""  In earlier modules, we built a DevOps Companion Agent that analyzes
  repositories, generates infrastructure-as-code, runs security scans, and
  deploys to AWS. It works great in a happy-path demo. But what happens when
  you run it in production?

  Imagine: rate limits hit mid-pipeline, a host runs out of memory at step 6,
  or a human approval sits untouched for 72 hours. Each time, the pipeline
  loses all prior work and restarts from scratch.

  This module shows how two AWS Marketplace partners solve that problem:
  Temporal for durable workflow execution, and Datadog for observability
  across the entire pipeline.

  Sections:
    1. The Production Problem
    2. Durable Agent Activities
    3. Parallel Verification (Fan-Out/Fan-In)
    4. Human-in-the-Loop
    5. Advanced Durable Execution Patterns
    6. Observability: OpenTelemetry + Datadog
""")

    # Status bar at the bottom in gray
    if not mock_on and _USE_TEMPORAL:
        mode_label = "Cloud" if _TEMPORAL_CONFIG.is_cloud else "Dev Server"
        status_line = f"Temporal mode ON  ({mode_label}: {_TEMPORAL_CONFIG.endpoint})"
        detail_line = f"Namespace: {_TEMPORAL_CONFIG.namespace} | Task queue: {_TEMPORAL_CONFIG.task_queue}"
        if _RICH:
            _c.print(f"\n  [dim]── Module 6 Demo Status ──[/dim]")
            _c.print(f"  [dim]{status_line}[/dim]")
            _c.print(f"  [dim]{detail_line}[/dim]\n")
        else:
            print(f"\n  ── Module 6 Demo Status ──")
            print(f"  {status_line}")
            print(f"  {detail_line}\n")

    pause("  ↵  Press Enter to begin...")

    sections = {
        1: section_1_production_problem,
        2: section_2_durable_activities,
        3: section_3_parallel_verification,
        4: section_4_signals,
        5: section_5_advanced_durability,
        6: section_6_observability,
    }

    if args.section:
        sections[args.section]()
    else:
        for fn in sections.values():
            fn()

    clear_screen()
    header("DEMO COMPLETE", "bold green")
    print("""
  What we covered:
     • Durable execution means your pipeline survives crashes, restarts,
       and deployments. No work is lost because every activity is a
       checkpoint that the workflow resumes from automatically.
     • Temporal is the orchestration engine that gives you all of this.
       You define your pipeline as a workflow, break each step into an
       activity, and Temporal handles retries, timeouts, and state
       persistence for you.
     • From there, Temporal gives you parallel fan-out, human-in-the-loop
       signals that pause without consuming resources, and saga-based
       rollback that automatically undoes earlier steps when something
       fails downstream.
     • Datadog provides the observability layer for the distributed
       tracing spans that Temporal emits, so you get end-to-end
       visibility across every workflow and activity with a one-time
       setup in your worker code.
     • Temporal Cloud and Datadog are both available on AWS Marketplace,
       so you can get this whole stack running with your existing AWS account.

  To run the full end-to-end pipeline interactively:
    python -m module6.app --run-pipeline /app/nodejs-api

  Thank you for completing Module 6!
""")
    pause()


if __name__ == "__main__":
    main()
