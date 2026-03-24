#!/usr/bin/env python3
"""
aura_pm_ba_agents.py
AURA MVP — PM Agent, BA Agent, and ManagerBot with Human Approval Gate.

This module focuses on the AURA MVP project (client: Malcolm Goodwin,
$150K budget, 12-week timeline, silhouette technology + 3D mesh scope).

Approval workflow:
  1. PM/BA Agent generates output
  2. PMNotificationEngine.prepare_notification_package() → ApprovalRequest
  3. ManagerBot.notify_approver_for_review()             → awaits human decision
  4. APPROVED  → PMNotificationEngine.send_approved_notifications()
  5. REVISED   → PM Agent regenerates with feedback
  6. REJECTED  → ManagerBot.escalate_if_rejected() → decision reconsidered

Usage:
    from aura_pm_ba_agents import AuraPMAgent, AuraBAAgent, ManagerBot

    pm = AuraPMAgent()
    charter, approval_req = pm.generate_charter_with_approval(project_metadata)

Dependencies (re-exported from claude_code_agent_ecosystem):
    ApprovalStatus, ApprovalDecision, ApprovalRequest, ApprovalChecklist,
    PMNotificationEngine, ManagerBot
"""

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import anthropic

# ---------------------------------------------------------------------------
# Re-use the canonical approval components from the main ecosystem module.
# This avoids duplicating class definitions and keeps a single source of truth.
# ---------------------------------------------------------------------------
from claude_code_agent_ecosystem import (
    # Enums / dataclasses
    ApprovalStatus,
    ApprovalDecision,
    ApprovalRequest,
    ApprovalChecklist,
    # Notification & orchestration
    PMNotificationEngine,
    ManagerBot,
    # Stage gate infrastructure
    StageGateName,
    WorkflowDatabase,
    StageGateManager,
    WorkflowState,
    WorkflowStatus,
)

# ---------------------------------------------------------------------------
# AURA MVP constants
# ---------------------------------------------------------------------------
AURA_PROJECT = {
    "name":     "AURA MVP",
    "client":   "Malcolm Goodwin",
    "budget":   150_000,
    "timeline": "12 weeks",
    "scope":    "Silhouette technology + 3D mesh design",
    "team":     {"Kiera": "PM Lead", "Elina": "BA Lead", "Ron": "QA Lead"},
}

AURA_APPROVERS = {
    StageGateName.CHARTER_APPROVAL:      "tjohnson@firstgenesis.com",
    StageGateName.REQUIREMENTS_APPROVAL: "k.phipps@firstgenesis.com",
}

_MODEL = "claude-opus-4-6"


# ============================================================================
# AURA PM AGENT
# ============================================================================
class AuraPMAgent:
    """
    Project Manager Agent scoped to the AURA MVP project.

    Responsibilities:
    - Generate project charter
    - Create WBS
    - Identify risks + mitigation strategies
    - Prepare developer notification packages for human approval
    """

    def __init__(self, api_key: Optional[str] = None):
        self.client   = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.db       = WorkflowDatabase()
        self.notif    = PMNotificationEngine(self.db)
        self.bot      = ManagerBot(self.notif, StageGateManager(self.db, self._dummy_router()), self.db)
        self.project  = AURA_PROJECT.copy()

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _dummy_router():
        """Minimal router shim used when no SMTP credentials are set."""
        class _Router:
            def send_approval_request(self, **_):
                return False
        return _Router()

    def _call_claude(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model=_MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    # ── Charter generation ────────────────────────────────────────────────────

    def generate_charter(self, extra_metadata: Optional[dict] = None) -> dict:
        """
        Generate the AURA MVP project charter.
        Returns parsed JSON dict (project_charter, wbs, risks, …).
        """
        metadata = {**self.project, **(extra_metadata or {})}
        raw = self._call_claude(
            system=(
                "You are the PM Agent for First Genesis, working on AURA MVP. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), project_charter (object), "
                "wbs (object), risks (list)."
            ),
            user=f"Generate project charter for:\n{json.dumps(metadata, indent=2)}",
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw, "project_charter": {}, "wbs": {}, "risks": []}

    def generate_charter_with_approval(
        self, extra_metadata: Optional[dict] = None
    ) -> Tuple[dict, ApprovalRequest]:
        """
        Generate charter AND prepare developer notification package.

        Returns:
            (charter_dict, ApprovalRequest) — ApprovalRequest is PENDING until
            ManagerBot.receive_approval_decision() is called with APPROVE.
        """
        charter = self.generate_charter(extra_metadata)
        charter_data = charter.get("project_charter", {})
        wbs_data     = charter.get("wbs", {})

        developers = list(self.project["team"].keys())
        wbs_values = list(wbs_data.values()) if isinstance(wbs_data, dict) else []
        timeline   = charter_data.get("timeline", self.project["timeline"])
        digits     = "".join(c for c in str(timeline) if c.isdigit())
        deadline   = datetime.now() + timedelta(weeks=int(digits) if digits else 12)

        approval_req = self.notif.prepare_notification_package(
            decision={
                "id":      f"aura_charter_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "context": (
                    f"Project: {charter_data.get('title', 'AURA MVP')} | "
                    f"Client: {charter_data.get('client', self.project['client'])} | "
                    f"Timeline: {timeline}"
                ),
            },
            affected_developers=developers,
            action_items={
                dev: (str(wbs_values[0]) if wbs_values else f"Review AURA MVP charter and confirm scope")
                for dev in developers
            },
            deadlines={dev: deadline for dev in developers},
            resources={
                dev: "AURA MVP charter, WBS, First Genesis templates — PMO for questions"
                for dev in developers
            },
            confirmation_requirements={
                dev: "Reply CONFIRMED within 48 hours"
                for dev in developers
            },
        )

        self.bot.notify_approver_for_review(approval_req)
        return charter, approval_req

    # ── Approval processing ───────────────────────────────────────────────────

    def process_approval_decision(
        self,
        approval_request: ApprovalRequest,
        decision: ApprovalDecision,
        approver_name: str,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Route an APPROVE / REVISE / REJECT decision through ManagerBot.
        On APPROVE: immediately sends notifications to developers.
        """
        result = self.bot.receive_approval_decision(
            approval_id=approval_request.approval_id,
            decision=decision,
            approver_name=approver_name,
            notes=notes,
        )

        if decision == ApprovalDecision.APPROVE:
            self.notif.send_approved_notifications(approval_request)

        elif decision == ApprovalDecision.REJECT:
            self.bot.escalate_if_rejected(
                approval_id=approval_request.approval_id,
                approver_notes=notes or "No reason provided",
            )

        return result


# ============================================================================
# AURA BA AGENT
# ============================================================================
class AuraBAAgent:
    """
    Business Analyst Agent scoped to the AURA MVP project.

    Responsibilities:
    - Extract functional / non-functional requirements from design sessions
    - Create requirements traceability matrix (FR → Design → Test)
    - Prepare requirements approval package for human review
    """

    def __init__(self, api_key: Optional[str] = None):
        self.client  = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.db      = WorkflowDatabase()
        self.notif   = PMNotificationEngine(self.db)
        self.bot     = ManagerBot(self.notif, StageGateManager(self.db, AuraPMAgent._dummy_router()), self.db)
        self.project = AURA_PROJECT.copy()

    def _call_claude(self, system: str, user: str) -> str:
        response = self.client.messages.create(
            model=_MODEL,
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return response.content[0].text

    # ── Requirements extraction ───────────────────────────────────────────────

    def extract_requirements(self, transcript: str) -> dict:
        """
        Extract structured requirements from a design session transcript.
        Returns parsed JSON with functional_requirements, non_functional_requirements,
        traceability_matrix.
        """
        raw = self._call_claude(
            system=(
                "You are the BA Agent for First Genesis, working on AURA MVP. "
                "Extract requirements from design session transcripts. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), functional_requirements (list), "
                "non_functional_requirements (list), traceability_matrix (object)."
            ),
            user=f"Extract requirements from this design session:\n\n{transcript}",
        )
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {
                "raw": raw,
                "functional_requirements": [],
                "non_functional_requirements": [],
                "traceability_matrix": {},
            }

    def extract_requirements_with_approval(
        self, transcript: str
    ) -> Tuple[dict, ApprovalRequest]:
        """
        Extract requirements AND prepare developer notification package for approval.

        Returns:
            (requirements_dict, ApprovalRequest)
        """
        requirements = self.extract_requirements(transcript)
        fr_count     = len(requirements.get("functional_requirements", []))
        nfr_count    = len(requirements.get("non_functional_requirements", []))

        developers = list(self.project["team"].keys())
        deadline   = datetime.now() + timedelta(weeks=2)

        approval_req = self.notif.prepare_notification_package(
            decision={
                "id":      f"aura_requirements_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "context": (
                    f"AURA MVP requirements extracted: {fr_count} functional, "
                    f"{nfr_count} non-functional requirements identified from design session."
                ),
            },
            affected_developers=developers,
            action_items={
                dev: f"Review AURA MVP requirements specification ({fr_count} FRs, {nfr_count} NFRs) "
                     f"and confirm scope coverage"
                for dev in developers
            },
            deadlines={dev: deadline for dev in developers},
            resources={
                dev: "Requirements specification, traceability matrix, design session notes"
                for dev in developers
            },
            confirmation_requirements={
                dev: "Reply CONFIRMED within 48 hours; flag any missing requirements"
                for dev in developers
            },
        )

        self.bot.notify_approver_for_review(approval_req)
        return requirements, approval_req

    def process_approval_decision(
        self,
        approval_request: ApprovalRequest,
        decision: ApprovalDecision,
        approver_name: str,
        notes: Optional[str] = None,
    ) -> dict:
        """Route APPROVE / REVISE / REJECT; sends notifications on APPROVE."""
        result = self.bot.receive_approval_decision(
            approval_id=approval_request.approval_id,
            decision=decision,
            approver_name=approver_name,
            notes=notes,
        )
        if decision == ApprovalDecision.APPROVE:
            self.notif.send_approved_notifications(approval_request)
        elif decision == ApprovalDecision.REJECT:
            self.bot.escalate_if_rejected(
                approval_id=approval_request.approval_id,
                approver_notes=notes or "No reason provided",
            )
        return result


# ============================================================================
# DEMO / CLI
# ============================================================================
def _demo():
    print("=" * 60)
    print("AURA MVP — PM + BA Agent Approval Flow Demo")
    print("=" * 60)

    pm = AuraPMAgent()

    # Simulate charter generation (no real API key needed for structure demo)
    approval_req = ApprovalRequest(
        approval_id="APR_DEMO_001",
        decision_id="aura_charter_demo",
        decision_context="Project: AURA MVP | Client: Malcolm Goodwin | Timeline: 12 weeks",
        affected_developers=["Kiera", "Elina", "Ron"],
        action_items={
            "Kiera": "Review AURA MVP charter and confirm PM scope",
            "Elina": "Review requirements section and flag gaps",
            "Ron":   "Review QA acceptance criteria",
        },
        deadlines={
            "Kiera": datetime.now() + timedelta(weeks=12),
            "Elina": datetime.now() + timedelta(weeks=12),
            "Ron":   datetime.now() + timedelta(weeks=12),
        },
        resources={k: "AURA MVP charter, WBS, FG templates" for k in ["Kiera", "Elina", "Ron"]},
        confirmation_requirements={k: "Reply CONFIRMED within 48 hours" for k in ["Kiera", "Elina", "Ron"]},
    )

    checklist = ApprovalChecklist(approval_req)
    print(f"\n5-point checklist: {'PASS' if checklist.all_checks_passed() else 'FAIL'}")
    failed = checklist.get_failed_checks()
    if failed:
        print(f"  Failed: {failed}")

    result = pm.bot.receive_approval_decision(
        approval_id=approval_req.approval_id,
        decision=ApprovalDecision.APPROVE,
        approver_name="Trice Johnson",
        notes="Charter looks good — proceed to kickoff",
    )
    print(f"\nApproval result: {result}")

    sent = pm.notif.send_approved_notifications(approval_req)
    print(f"\nNotifications sent to: {list(sent.keys())}")

    print("\n✅ Demo complete")


if __name__ == "__main__":
    _demo()
