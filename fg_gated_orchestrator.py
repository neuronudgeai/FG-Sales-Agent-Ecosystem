"""
fg_gated_orchestrator.py
────────────────────────
End-to-end orchestration of the FG Sales Agent pipeline with full governance.

This module is the single entry point for running agent workflows. It wires
together:
    • AuditLogger        — immutable decision trail
    • TokenBudget        — per-agent cost tracking and model optimisation
    • ReviewGate         — SME routing, auto-approve, redaction
    • SalesAgentBase     — the four specialised agents

Gated pipeline (Lead Qualifier example)
────────────────────────────────────────
    User Input
        ↓
    LeadQualifierAgent.qualify_lead()   →  AgentDecision
        ↓
    ReviewGate.evaluate()
        ├─ AUTO_APPROVED  →  prepare_downstream_input()  →  ForecastAgent
        ├─ PENDING_SME    →  [blocked — wait for human]
        │       ↓  process_sme_review()
        │   APPROVE / REDACT / REJECT
        │       ↓ (if approved)
        └─  prepare_downstream_input()  →  ForecastAgent
        ↓
    ForecastAgent.generate_forecast()   →  AgentDecision
        ↓
    ReviewGate.evaluate()               →  ReviewDecision
        ↓
    Final output to caller (DownstreamPayload or rejection notice)

CLI commands (run this file directly)
──────────────────────────────────────
    python fg_gated_orchestrator.py qualify_lead
    python fg_gated_orchestrator.py full_pipeline
    python fg_gated_orchestrator.py pending_reviews
    python fg_gated_orchestrator.py approve <decision_id> <sme_email>
    python fg_gated_orchestrator.py reject  <decision_id> <sme_email> "<reason>"
    python fg_gated_orchestrator.py audit   <decision_id>
    python fg_gated_orchestrator.py budget_status
    python fg_gated_orchestrator.py approval_stats
    python fg_gated_orchestrator.py daily_cost
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fg_audit_logger import AuditLogger
from fg_decision_models import AgentDecision, DownstreamPayload, ReviewDecision
from fg_review_gate import ReviewGate
from fg_sales_agents import (
    AccountManagerAgent,
    CompetitorIntelAgent,
    ForecastAgent,
    LeadQualifierAgent,
)
from fg_token_budget import TokenBudget

# ── Shared singletons ─────────────────────────────────────────────────────────

_audit = AuditLogger()
_budget = TokenBudget()
_gate = ReviewGate(audit_logger=_audit)

_lead_agent = LeadQualifierAgent(audit_logger=_audit, token_budget=_budget)
_account_agent = AccountManagerAgent(audit_logger=_audit, token_budget=_budget)
_forecast_agent = ForecastAgent(audit_logger=_audit, token_budget=_budget)
_competitor_agent = CompetitorIntelAgent(audit_logger=_audit, token_budget=_budget)


# ── GatedOrchestrator ─────────────────────────────────────────────────────────

class GatedOrchestrator:
    """
    Orchestrates multi-agent workflows with human-in-the-loop governance.

    Every agent output passes through the ReviewGate before the next agent
    receives it.  Nothing flows downstream without an APPROVE or REDACT decision.
    """

    def __init__(
        self,
        audit_logger: Optional[AuditLogger] = None,
        token_budget: Optional[TokenBudget] = None,
        review_gate: Optional[ReviewGate] = None,
    ):
        self.audit = audit_logger or _audit
        self.budget = token_budget or _budget
        self.gate = review_gate or _gate

        self.lead_agent = LeadQualifierAgent(self.audit, self.budget)
        self.account_agent = AccountManagerAgent(self.audit, self.budget)
        self.forecast_agent = ForecastAgent(self.audit, self.budget)
        self.competitor_agent = CompetitorIntelAgent(self.audit, self.budget)

    # ── Single-agent runs ─────────────────────────────────────────────────────

    def run_lead_qualifier(
        self, lead_data: Dict[str, Any], workflow_id: Optional[str] = None
    ) -> Tuple[str, AgentDecision, Optional[ReviewDecision]]:
        """
        Qualify a single lead through the gate.

        Returns:
            (gate_status, decision, review_or_None)

            gate_status: "AUTO_APPROVED" | "PENDING_SME" | "AUTO_REJECTED" | "ESCALATED"
        """
        workflow_id = workflow_id or _wid("lead")
        print(f"\n[Lead Qualifier] Starting workflow: {workflow_id}")

        decision = self.lead_agent.qualify_lead(lead_data, workflow_id)
        self._print_decision(decision)

        status, review = self.gate.evaluate(decision)
        self._print_gate_outcome(status, review)

        return status, decision, review

    def run_account_analysis(
        self, account_data: Dict[str, Any], workflow_id: Optional[str] = None
    ) -> Tuple[str, AgentDecision, Optional[ReviewDecision]]:
        """Analyse an account through the gate."""
        workflow_id = workflow_id or _wid("acct")
        print(f"\n[Account Manager] Starting workflow: {workflow_id}")

        decision = self.account_agent.analyze_account(account_data, workflow_id)
        self._print_decision(decision)

        status, review = self.gate.evaluate(decision)
        self._print_gate_outcome(status, review)

        return status, decision, review

    def run_forecast(
        self, pipeline_data: Dict[str, Any], workflow_id: Optional[str] = None
    ) -> Tuple[str, AgentDecision, Optional[ReviewDecision]]:
        """Generate a forecast through the gate."""
        workflow_id = workflow_id or _wid("fcast")
        print(f"\n[Forecast Agent] Starting workflow: {workflow_id}")

        decision = self.forecast_agent.generate_forecast(pipeline_data, workflow_id)
        self._print_decision(decision)

        status, review = self.gate.evaluate(decision)
        self._print_gate_outcome(status, review)

        return status, decision, review

    def run_competitor_analysis(
        self, deal_data: Dict[str, Any], workflow_id: Optional[str] = None
    ) -> Tuple[str, AgentDecision, Optional[ReviewDecision]]:
        """Analyse competitive position through the gate."""
        workflow_id = workflow_id or _wid("comp")
        print(f"\n[Competitor Intel] Starting workflow: {workflow_id}")

        decision = self.competitor_agent.analyze_competition(deal_data, workflow_id)
        self._print_decision(decision)

        status, review = self.gate.evaluate(decision)
        self._print_gate_outcome(status, review)

        return status, decision, review

    # ── Multi-agent pipeline ──────────────────────────────────────────────────

    def run_full_sales_pipeline(
        self,
        lead_data: Dict[str, Any],
        pipeline_data: Dict[str, Any],
        auto_approve_for_demo: bool = False,
    ) -> Dict[str, Any]:
        """
        Run the complete sales intelligence pipeline:

            Lead Qualifier  →  [Gate]  →  Forecast Agent  →  [Gate]

        If auto_approve_for_demo=True, SME-flagged decisions are auto-approved
        (for testing only — never use in production).

        Returns a summary dict with all decisions and gate outcomes.
        """
        workflow_id = _wid("pipeline")
        results: Dict[str, Any] = {
            "workflow_id": workflow_id,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "steps": [],
        }

        print(f"\n{'═'*60}")
        print(f"  FG SALES PIPELINE — {workflow_id}")
        print(f"{'═'*60}")

        # ── Step 1: Lead Qualifier ────────────────────────────────────────────
        print("\n📋 STEP 1: Lead Qualification")
        lead_decision = self.lead_agent.qualify_lead(lead_data, workflow_id)
        self._print_decision(lead_decision)

        lead_status, lead_review = self.gate.evaluate(lead_decision)
        if lead_status == "PENDING_SME" and auto_approve_for_demo:
            lead_review = self._demo_approve(lead_decision, "demo@firstgenesis.com")
            lead_status = "AUTO_APPROVED"
        self._print_gate_outcome(lead_status, lead_review)

        results["steps"].append({
            "step": "lead_qualification",
            "decision_id": lead_decision.decision_id,
            "gate_status": lead_status,
            "review_action": lead_review.action if lead_review else None,
        })

        if not lead_review or not lead_review.is_approved:
            results["outcome"] = "BLOCKED_AT_LEAD_QUALIFICATION"
            results["completed_at"] = datetime.utcnow().isoformat() + "Z"
            print("\n⛔ Pipeline blocked at Lead Qualification gate.")
            return results

        # Pass lead output to forecast (redacted, no PII)
        lead_payload = self.gate.prepare_downstream_input(
            lead_decision, lead_review, "forecast_agent"
        )
        print(f"\n✅ Lead payload for Forecast Agent:")
        print(f"   Fields: {list(lead_payload.approved_fields.keys())}")

        # ── Step 2: Forecast ──────────────────────────────────────────────────
        print("\n📊 STEP 2: Revenue Forecast")

        # Enrich pipeline_data with approved lead context (no PII)
        enriched_pipeline = {
            **pipeline_data,
            "lead_priority": lead_payload.get("priority", "STANDARD"),
            "lead_context": lead_payload.get("context", ""),
        }
        forecast_decision = self.forecast_agent.generate_forecast(
            enriched_pipeline, workflow_id
        )
        self._print_decision(forecast_decision)

        forecast_status, forecast_review = self.gate.evaluate(forecast_decision)
        if forecast_status == "PENDING_SME" and auto_approve_for_demo:
            forecast_review = self._demo_approve(forecast_decision, "demo@firstgenesis.com")
            forecast_status = "AUTO_APPROVED"
        self._print_gate_outcome(forecast_status, forecast_review)

        results["steps"].append({
            "step": "forecast",
            "decision_id": forecast_decision.decision_id,
            "gate_status": forecast_status,
            "review_action": forecast_review.action if forecast_review else None,
        })

        if forecast_review and forecast_review.is_approved:
            forecast_payload = self.gate.prepare_downstream_input(
                forecast_decision, forecast_review, "human_sales_leader"
            )
            results["final_forecast_payload"] = forecast_payload.to_dict()
            results["outcome"] = "COMPLETED"
        else:
            results["outcome"] = "BLOCKED_AT_FORECAST_GATE"

        results["completed_at"] = datetime.utcnow().isoformat() + "Z"
        self._print_pipeline_summary(results)
        return results

    # ── SME review interface ──────────────────────────────────────────────────

    def approve_decision(
        self,
        decision_id: str,
        sme_email: str,
        notes: str = "",
        redactions: Optional[List[str]] = None,
    ) -> ReviewDecision:
        """
        SME approves (or approves-with-redaction) a pending decision.
        Triggers downstream pass preparation.
        """
        action = "REDACT" if redactions else "APPROVE"
        review = self.gate.process_sme_review(
            decision_id=decision_id,
            reviewer_email=sme_email,
            action=action,
            redaction_descriptions=redactions or [],
            notes=notes,
        )
        print(f"\n✅ Decision {decision_id[:8]}… {action}D by {sme_email}")
        if notes:
            print(f"   Notes: {notes}")
        return review

    def reject_decision(
        self, decision_id: str, sme_email: str, reason: str
    ) -> ReviewDecision:
        """SME rejects a pending decision."""
        review = self.gate.process_sme_review(
            decision_id=decision_id,
            reviewer_email=sme_email,
            action="REJECT",
            notes=reason,
        )
        print(f"\n🚫 Decision {decision_id[:8]}… REJECTED by {sme_email}: {reason}")
        return review

    def list_pending_reviews(self) -> None:
        """Print all decisions waiting for SME review."""
        pending = self.gate.get_pending_reviews()
        if not pending:
            print("\n✅ No decisions pending SME review.")
            return
        print(f"\n{'─'*60}")
        print(f"  PENDING SME REVIEWS ({len(pending)})")
        print(f"{'─'*60}")
        for did, info in pending.items():
            flag = "🔴" if info["sensitivity_flag"] else "🟡"
            print(f"\n  {flag} {info['agent_name'].upper()} | {info['workflow_id']}")
            print(f"  Decision ID: {did}")
            print(f"  Confidence:  {info['confidence_score']:.0%}")
            print(f"  SME:         {info['sme_role']} ({info['sme_email']})")
            print(f"  Recommendation: {info['recommendation']}")
        print(f"\n{'─'*60}")

    # ── Reporting ─────────────────────────────────────────────────────────────

    def print_audit_record(self, decision_id: str) -> None:
        """Pretty-print the full audit record for a decision."""
        record = self.audit.get_full_audit_record(decision_id)
        print(f"\n{'─'*60}")
        print(f"  AUDIT RECORD — {decision_id[:8]}…")
        print(f"{'─'*60}")
        print(json.dumps(record, indent=2, default=str))

    def print_approval_stats(self) -> None:
        """Print per-agent approval / rejection rates."""
        stats = self.audit.get_approval_stats()
        print(f"\n{'─'*60}")
        print(f"  APPROVAL STATS (all time)")
        print(f"{'─'*60}")
        print(f"  {'Agent':<22} {'Total':>6} {'Approved':>9} {'Rejected':>9} "
              f"{'Redacted':>9} {'Approve%':>9} {'Avg Conf':>9}")
        print(f"  {'─'*75}")
        for agent, s in stats.items():
            print(
                f"  {agent:<22} {s['total']:>6} {s['approved']:>9} {s['rejected']:>9} "
                f"{s['redacted']:>9} {s['approval_rate']:>8.1f}% {s['avg_confidence']:>9.2f}"
            )

    def print_budget_status(self) -> None:
        """Print token budget status."""
        self.budget.print_status_report()

    def print_daily_cost(self) -> None:
        """Print today's cost breakdown."""
        report = self.audit.get_daily_cost_report()
        print(f"\n{'─'*60}")
        print(f"  DAILY COST — {report['date']}")
        print(f"{'─'*60}")
        print(f"  Total: ${report['total_cost_usd']:.4f} / $5.00  "
              f"({report['budget_pct_used']}% of budget)")
        print(f"\n  {'Agent':<22} {'Calls':>6} {'In Toks':>8} {'Out Toks':>9} {'Cost':>8}")
        print(f"  {'─'*58}")
        for agent, d in report.get("by_agent", {}).items():
            print(f"  {agent:<22} {d['calls']:>6} {d['input_tokens']:>8,} "
                  f"{d['output_tokens']:>9,} ${d['cost_usd']:>7.4f}")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _demo_approve(self, decision: AgentDecision, reviewer: str) -> ReviewDecision:
        """Auto-approve for demo/test purposes only."""
        return self.gate.process_sme_review(
            decision_id=decision.decision_id,
            reviewer_email=reviewer,
            action="APPROVE",
            notes="[DEMO] Auto-approved for demonstration purposes.",
        )

    def _print_decision(self, d: AgentDecision) -> None:
        flag = "🔴 SENSITIVE" if d.sensitivity_flag else "🟢 STANDARD"
        print(f"\n  Agent:       {d.agent_name}")
        print(f"  Decision ID: {d.decision_id[:8]}…")
        print(f"  Confidence:  {d.confidence_score:.0%}  {flag}")
        print(f"  Output:      {d.recommendation[:100]}")
        print(f"  Tokens:      {d.tokens_used:,}  |  Auto-approve: {d.can_auto_approve}")

    def _print_gate_outcome(self, status: str, review: Optional[ReviewDecision]) -> None:
        icons = {
            "AUTO_APPROVED": "✅ AUTO-APPROVED",
            "PENDING_SME":   "⏳ PENDING SME REVIEW",
            "ESCALATED":     "🔺 ESCALATED",
            "AUTO_REJECTED": "⛔ AUTO-REJECTED",
        }
        print(f"\n  Gate: {icons.get(status, status)}")
        if review:
            print(f"  By:   {review.reviewed_by}")
            if review.notes:
                print(f"  Note: {review.notes[:120]}")

    def _print_pipeline_summary(self, results: Dict[str, Any]) -> None:
        print(f"\n{'═'*60}")
        print(f"  PIPELINE COMPLETE — {results['outcome']}")
        print(f"{'═'*60}")
        for step in results["steps"]:
            icon = "✅" if step.get("review_action") in ("APPROVE", "REDACT") else "⛔"
            print(f"  {icon} {step['step']:<30} {step['gate_status']}")
        print(f"  Workflow ID: {results['workflow_id']}")
        print(f"  Duration: {results['started_at']} → {results.get('completed_at','')}")

    # ── Legacy agent wrappers ─────────────────────────────────────────────────
    # These wrap AutonomousAgentWithEmailGates so all 5 legacy agents are
    # accessible from the orchestrator with governance metadata printed.

    def _legacy_agent(self):
        """Lazy-init the legacy agent system."""
        if not hasattr(self, "_legacy"):
            from claude_code_agent_ecosystem import AutonomousAgentWithEmailGates
            self._legacy = AutonomousAgentWithEmailGates()
        return self._legacy

    def run_pm_agent(self, project_metadata: Optional[Dict] = None) -> None:
        """Run PM Agent with full governance (charter → email gate + ReviewGate)."""
        metadata = project_metadata or {
            "project": "AURA MVP", "client": "Malcolm Goodwin",
            "budget": 150000, "timeline": "3 months",
            "scope": "Silhouette technology + 3D mesh design",
        }
        print(f"\n{'═'*60}")
        print("  PM AGENT — Charter Generation")
        print(f"{'═'*60}")
        output, wf = self._legacy_agent().run_pm_agent_with_gates(metadata)
        print(f"\n  Workflow: {wf.workflow_id}")
        print(f"  Gate:     {wf.current_stage_gate.value}")
        print(f"  Status:   {wf.status.value}")
        print(f"  Preview:  {output[:150]}…")

    def run_ba_agent(self, project_name: str = "AURA MVP", transcript: str = "") -> None:
        """Run BA Agent with full governance (requirements → email gate + ReviewGate)."""
        transcript = transcript or (
            "We need to support 1000 concurrent users. The system must work on mobile "
            "and desktop. Security is critical — 2FA and AES-256 encryption required. "
            "The UI should allow any core task in 3 clicks or fewer. "
            "Integration with existing Salesforce CRM is mandatory."
        )
        print(f"\n{'═'*60}")
        print("  BA AGENT — Requirements Extraction")
        print(f"{'═'*60}")
        output, wf = self._legacy_agent().run_ba_agent_with_gates(project_name, transcript)
        print(f"\n  Workflow: {wf.workflow_id}")
        print(f"  Gate:     {wf.current_stage_gate.value}")
        print(f"  Status:   {wf.status.value}")
        print(f"  Preview:  {output[:150]}…")

    def run_qa_agent(self, workflow_id_to_audit: str = "", project_name: str = "AURA MVP") -> None:
        """Run QA Agent with full governance (pre-delivery audit → email gate + ReviewGate)."""
        print(f"\n{'═'*60}")
        print("  QA AGENT — Pre-Delivery Audit")
        print(f"{'═'*60}")
        output, wf = self._legacy_agent().run_qa_agent_with_gates(
            workflow_id_to_audit or "no_prior_workflow", project_name
        )
        print(f"\n  Workflow: {wf.workflow_id}")
        print(f"  Gate:     {wf.current_stage_gate.value}")
        print(f"  Status:   {wf.status.value}")
        print(f"  Preview:  {output[:150]}…")

    def run_vendor_agent(self, vendor_name: str = "Yubi", metrics: Optional[Dict] = None) -> None:
        """Run Vendor Agent with full governance (SLA scorecard → email gate + ReviewGate)."""
        metrics = metrics or {
            "on_time_delivery_pct": 98, "quality_score": 92,
            "response_time_hours": 18, "budget_variance_pct": 3,
            "period": "March 2026",
        }
        print(f"\n{'═'*60}")
        print(f"  VENDOR AGENT — {vendor_name} Scorecard")
        print(f"{'═'*60}")
        output, wf = self._legacy_agent().run_vendor_agent_with_gates(vendor_name, metrics)
        print(f"\n  Workflow: {wf.workflow_id}")
        print(f"  Gate:     {wf.current_stage_gate.value}")
        print(f"  Status:   {wf.status.value}")
        print(f"  Preview:  {output[:150]}…")

    def run_manager_agent(self, portfolio_data: Optional[Dict] = None) -> None:
        """Run Manager Agent with full governance (portfolio dashboard → email gate + ReviewGate)."""
        print(f"\n{'═'*60}")
        print("  MANAGER AGENT — Portfolio Dashboard")
        print(f"{'═'*60}")
        output, wf = self._legacy_agent().run_manager_agent_with_gates(portfolio_data)
        print(f"\n  Workflow: {wf.workflow_id}")
        print(f"  Gate:     {wf.current_stage_gate.value}")
        print(f"  Status:   {wf.status.value}")
        print(f"  Preview:  {output[:150]}…")


# ── CLI entry point ───────────────────────────────────────────────────────────

def _wid(prefix: str = "wf") -> str:
    return f"{prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{str(uuid.uuid4())[:6]}"


# ── Demo / sample data ────────────────────────────────────────────────────────

DEMO_LEAD = {
    "company": "Meridian Energy Corp",
    "title": "VP of Digital Transformation",
    "industry": "Energy",
    "employee_count": 4200,
    "budget_signals": "Approved $2M digital transformation budget, Q1 initiative",
    "engagement_history": "Downloaded whitepaper, attended webinar, SDR call last week",
    "source": "Inbound — LinkedIn ad",
    "pain_points": "Manual processes, legacy ERP, no real-time analytics",
}

DEMO_PIPELINE = {
    "period": "Q1 2026",
    "total_pipeline": 3_400_000,
    "open_opps": 22,
    "weighted_pipeline": 1_250_000,
    "quota": 1_500_000,
    "win_rate": 28,
    "avg_cycle_days": 47,
    "stage_breakdown": {
        "prospect": {"count": 8, "value": 800_000},
        "qualified": {"count": 6, "value": 1_200_000},
        "proposal": {"count": 5, "value": 900_000},
        "negotiation": {"count": 3, "value": 500_000},
    },
}

DEMO_ACCOUNT = {
    "account_name": "Chevron Sand Management",
    "arr": 180_000,
    "renewal_date": "2026-06-30",
    "support_tickets_30d": 7,
    "usage_metrics": "Login rate dropped 40% MoM, feature adoption stalled",
    "last_contact": "2026-02-14",
    "sentiment": "Neutral — some frustration with onboarding speed",
    "nps": 32,
}

DEMO_DEAL = {
    "stage": "Proposal",
    "competitor": "Accenture",
    "value": 320_000,
    "pain_points": "Too slow, expensive, lacks AI capability",
    "eval_criteria": "Price, implementation speed, AI/ML native",
    "our_position": "Premium but faster time-to-value, AI-native platform",
    "timeline": "Decision by March 31, 2026",
}


def main() -> None:
    """CLI dispatcher."""
    orchestrator = GatedOrchestrator()
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        print(__doc__)
        return

    cmd = args[0]

    if cmd == "qualify_lead":
        orchestrator.run_lead_qualifier(DEMO_LEAD)

    elif cmd == "full_pipeline":
        auto = "--auto-approve" in args
        orchestrator.run_full_sales_pipeline(DEMO_LEAD, DEMO_PIPELINE, auto_approve_for_demo=auto)

    elif cmd == "account_analysis":
        orchestrator.run_account_analysis(DEMO_ACCOUNT)

    elif cmd == "competitor_analysis":
        orchestrator.run_competitor_analysis(DEMO_DEAL)

    elif cmd == "pending_reviews":
        orchestrator.list_pending_reviews()

    elif cmd == "approve":
        if len(args) < 3:
            print("Usage: approve <decision_id> <sme_email> [notes]")
            sys.exit(1)
        did, email = args[1], args[2]
        notes = args[3] if len(args) > 3 else ""
        orchestrator.approve_decision(did, email, notes=notes)

    elif cmd == "reject":
        if len(args) < 4:
            print("Usage: reject <decision_id> <sme_email> <reason>")
            sys.exit(1)
        did, email, reason = args[1], args[2], args[3]
        orchestrator.reject_decision(did, email, reason)

    elif cmd == "audit":
        if len(args) < 2:
            print("Usage: audit <decision_id>")
            sys.exit(1)
        orchestrator.print_audit_record(args[1])

    elif cmd == "budget_status":
        orchestrator.print_budget_status()

    elif cmd == "approval_stats":
        orchestrator.print_approval_stats()

    elif cmd == "daily_cost":
        orchestrator.print_daily_cost()

    # ── Legacy agent commands ──────────────────────────────────────────────────
    elif cmd == "pm_agent":
        orchestrator.run_pm_agent()

    elif cmd == "ba_agent":
        project = args[1] if len(args) > 1 else "AURA MVP"
        orchestrator.run_ba_agent(project_name=project)

    elif cmd == "qa_agent":
        wf_id = args[1] if len(args) > 1 else ""
        project = args[2] if len(args) > 2 else "AURA MVP"
        orchestrator.run_qa_agent(workflow_id_to_audit=wf_id, project_name=project)

    elif cmd == "vendor_agent":
        vendor = args[1] if len(args) > 1 else "Yubi"
        orchestrator.run_vendor_agent(vendor_name=vendor)

    elif cmd == "manager_agent":
        orchestrator.run_manager_agent()

    else:
        print(f"Unknown command: {cmd}")
        print("Run with --help to see available commands.")
        sys.exit(1)


if __name__ == "__main__":
    main()
