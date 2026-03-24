#!/usr/bin/env python3
"""
vendor_agent_extension.py
Vendor Agent extension with Human Approval Gate hooks.

Extends the base Vendor Agent with two approval-aware methods:
  - daily_capacity_check_with_approval()  → packages capacity/budget alerts
                                            as ApprovalRequest for human review
  - recommend_hiring_with_approval()      → packages hiring recommendations
                                            as ApprovalRequest for human review

Neither method sends alerts directly to stakeholders; both return an
ApprovalRequest that must pass through ManagerBot before any notification
reaches a developer, PM Lead, CTO, or HR Lead.

Usage:
    from vendor_agent_extension import VendorAgentExtension

    vendor = VendorAgentExtension()
    approval_req = vendor.daily_capacity_check_with_approval()
    if approval_req:
        vendor.manager_bot.notify_approver_for_review(approval_req)
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Tuple

import anthropic

from claude_code_agent_ecosystem import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalStatus,
    ApprovalChecklist,
    ManagerBot,
    PMNotificationEngine,
    StageGateManager,
    WorkflowDatabase,
)

_MODEL = "claude-opus-4-6"


# ============================================================================
# CONTRACTOR / CAPACITY MODELS
# ============================================================================

class ContractorStatus(Enum):
    ACTIVE      = "active"
    CONSTRAINED = "constrained"   # 70–95% capacity
    OVERLOADED  = "overloaded"    # > 95% capacity
    AVAILABLE   = "available"     # < 70% capacity


class BudgetStatus(Enum):
    HEALTHY  = "healthy"    # < 70% of budget used
    WARNING  = "warning"    # 70–90% used
    CRITICAL = "critical"   # > 90% used


@dataclass
class Contractor:
    contractor_id:       str
    name:                str
    role:                str
    capacity_percentage: float  # 0–100
    hourly_rate:         float
    weekly_hours:        float = 40.0
    status: ContractorStatus = ContractorStatus.AVAILABLE

    def __post_init__(self):
        if self.capacity_percentage > 95:
            self.status = ContractorStatus.OVERLOADED
        elif self.capacity_percentage > 70:
            self.status = ContractorStatus.CONSTRAINED
        else:
            self.status = ContractorStatus.AVAILABLE


@dataclass
class BudgetAlert:
    project_id: str
    status:     BudgetStatus
    message:    str
    pct_used:   float


@dataclass
class HiringRecommendation:
    role:             str
    reason:           str
    urgency:          str   # CRITICAL / HIGH / MEDIUM / LOW
    timeline_to_hire: str


# ============================================================================
# CONTRACTOR MANAGER (lightweight in-memory store)
# ============================================================================

class ContractorManager:
    """Manages contractor capacity and project budget health."""

    def __init__(self):
        # Pre-loaded with known FG / Yubi contractors
        self.contractors: Dict[str, Contractor] = {
            "yubi_dev_1": Contractor("yubi_dev_1", "Yubi Dev A", "3D Mesh Engineer",   78.0, 125.0),
            "yubi_dev_2": Contractor("yubi_dev_2", "Yubi Dev B", "Silhouette Engineer", 65.0, 125.0),
            "fg_qa_1":    Contractor("fg_qa_1",    "FG QA",      "QA Engineer",         55.0,  95.0),
        }
        # {project_id: {budget_total, budget_spent}}
        self.project_budgets: Dict[str, Dict] = {
            "AURA_MVP":       {"total": 150_000, "spent":  42_000},
            "CHEVRON_SAND":   {"total": 200_000, "spent": 162_000},
            "WWT_ENHANCEMENT":{"total":  80_000, "spent":  31_000},
        }

    def check_budget_health(self) -> List[BudgetAlert]:
        alerts = []
        for pid, b in self.project_budgets.items():
            pct = (b["spent"] / b["total"]) * 100 if b["total"] else 0
            if pct > 90:
                alerts.append(BudgetAlert(pid, BudgetStatus.CRITICAL,
                    f"{pid}: {pct:.1f}% of budget used (${b['spent']:,} / ${b['total']:,})", pct))
            elif pct > 70:
                alerts.append(BudgetAlert(pid, BudgetStatus.WARNING,
                    f"{pid}: {pct:.1f}% of budget used (${b['spent']:,} / ${b['total']:,})", pct))
        return alerts

    def recommend_hiring(self) -> List[HiringRecommendation]:
        recs = []
        overloaded = [c for c in self.contractors.values() if c.status == ContractorStatus.OVERLOADED]
        constrained = [c for c in self.contractors.values() if c.status == ContractorStatus.CONSTRAINED]

        if overloaded:
            recs.append(HiringRecommendation(
                role=f"{overloaded[0].role} (backup)",
                reason=f"{overloaded[0].name} is at {overloaded[0].capacity_percentage:.0f}% capacity",
                urgency="HIGH",
                timeline_to_hire="2–3 weeks",
            ))
        if len(constrained) >= 2:
            recs.append(HiringRecommendation(
                role="General contractor",
                reason=f"{len(constrained)} contractors constrained simultaneously",
                urgency="MEDIUM",
                timeline_to_hire="3–4 weeks",
            ))
        return recs


# ============================================================================
# ALERTS FOR APPROVAL
# ============================================================================

class AlertsForApproval:
    """
    Collects Vendor Agent alerts and packages them as ApprovalRequest objects
    ready for ManagerBot → human approver review.
    """

    def __init__(self, notif_engine: PMNotificationEngine):
        self.notif = notif_engine

    def package_capacity_alerts(
        self, alerts: List[dict], budget_alerts: List[BudgetAlert]
    ) -> ApprovalRequest:
        all_msgs = [a["message"] for a in alerts] + [b.message for b in budget_alerts]
        context  = (
            f"Vendor Agent detected {len(alerts)} capacity alert(s) and "
            f"{len(budget_alerts)} budget alert(s): {'; '.join(all_msgs[:3])}"
        )
        return self.notif.prepare_notification_package(
            decision={"id": f"CAP_{datetime.now().strftime('%Y%m%d_%H%M%S')}", "context": context},
            affected_developers=["PM Lead", "CTO"],
            action_items={
                "PM Lead": "Review capacity constraints. Consider task reassignment or timeline adjustment.",
                "CTO":     "Review budget status. Initiate cost mitigation plan if CRITICAL.",
            },
            deadlines={
                "PM Lead": datetime.now() + timedelta(hours=24),
                "CTO":     datetime.now() + timedelta(hours=24),
            },
            resources={
                "PM Lead": "Capacity report in dashboard. Contact Vendor Agent for details.",
                "CTO":     "Budget report in dashboard. Contact Finance for CRITICAL items.",
            },
            confirmation_requirements={
                "PM Lead": 'Email: "Capacity reviewed, action taken / no action needed"',
                "CTO":     'Email: "Budget status reviewed, next steps: [describe]"',
            },
        )

    def package_hiring_recommendations(
        self, recommendations: List[HiringRecommendation]
    ) -> ApprovalRequest:
        action_items_str = " | ".join(
            f"Hire {r.role} — {r.reason} (urgency: {r.urgency}, timeline: {r.timeline_to_hire})"
            for r in recommendations
        )
        return self.notif.prepare_notification_package(
            decision={
                "id":      f"HIRE_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "context": f"Vendor Agent recommends hiring {len(recommendations)} contractor(s): "
                           + "; ".join(r.role for r in recommendations),
            },
            affected_developers=["CTO", "HR Lead"],
            action_items={
                "CTO":     action_items_str,
                "HR Lead": "Initiate hiring process for approved roles once CTO confirms.",
            },
            deadlines={
                "CTO":     datetime.now() + timedelta(hours=48),
                "HR Lead": datetime.now() + timedelta(days=3),
            },
            resources={
                "CTO":     "Hiring recommendation report in Vendor Agent dashboard.",
                "HR Lead": "Contact CTO and Vendor Agent for role requirements.",
            },
            confirmation_requirements={
                "CTO":     'Email: "Hiring APPROVED" or "Hiring DEFERRED — reason: [describe]"',
                "HR Lead": 'Email: "Hiring process initiated" once CTO approves.',
            },
        )


# ============================================================================
# VENDOR AGENT EXTENSION
# ============================================================================

class VendorAgentExtension:
    """
    Vendor Agent with human approval gate hooks.

    Core responsibilities (inherited behaviour):
    - SLA tracking for Yubi and other contractors
    - Capacity monitoring and alerts
    - Hiring recommendations

    New approval-aware methods:
    - daily_capacity_check_with_approval()   → returns ApprovalRequest or None
    - recommend_hiring_with_approval()       → returns ApprovalRequest or None
    """

    SLA_TARGETS = {
        "on_time_delivery_pct": 95.0,
        "quality_score":        90.0,
        "response_time_hours":  24.0,
        "budget_variance_pct":   5.0,
    }

    def __init__(self, api_key: Optional[str] = None):
        self.client             = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.db                 = WorkflowDatabase()
        self.contractor_manager = ContractorManager()
        self.notif              = PMNotificationEngine(self.db)

        class _Router:
            def send_approval_request(self, **_): return False

        self.manager_bot = ManagerBot(
            self.notif,
            StageGateManager(self.db, _Router()),
            self.db,
        )
        self.alerts_packager = AlertsForApproval(self.notif)

    # ── SLA scorecard ─────────────────────────────────────────────────────────

    def generate_scorecard(self, vendor_name: str, metrics: dict) -> dict:
        """
        Generate an SLA scorecard for a vendor.
        Returns a dict with overall_score, sla_status, metrics_breakdown,
        escalation_required, action_items.
        """
        scorecard = {"vendor": vendor_name, "metrics": {}, "overall": "PASS", "escalations": []}
        for key, target in self.SLA_TARGETS.items():
            actual = metrics.get(key, 0)
            passed = actual >= target if "pct" in key or key == "quality_score" else actual <= target
            scorecard["metrics"][key] = {
                "actual": actual, "target": target, "status": "PASS" if passed else "FAIL"
            }
            if not passed:
                scorecard["overall"] = "FAIL"
                scorecard["escalations"].append(
                    f"{key}: {actual} vs target {target}"
                )
        return scorecard

    # ── Approval-aware capacity check ─────────────────────────────────────────

    def daily_capacity_check_with_approval(self) -> Optional[ApprovalRequest]:
        """
        Run daily capacity + budget check.

        If any alerts are found, packages them as an ApprovalRequest and
        calls ManagerBot.notify_approver_for_review().  The package is
        PENDING — no stakeholder is notified until APPROVE is received.

        Returns:
            ApprovalRequest if alerts exist, else None
        """
        capacity_alerts = []
        for contractor in self.contractor_manager.contractors.values():
            if contractor.capacity_percentage > 95:
                capacity_alerts.append({
                    "type":        "CAPACITY_CRITICAL",
                    "contractor":  contractor.name,
                    "capacity":    contractor.capacity_percentage,
                    "message":     f"{contractor.name} is fully booked ({contractor.capacity_percentage:.0f}%+)",
                })
            elif contractor.capacity_percentage > 70:
                capacity_alerts.append({
                    "type":       "CAPACITY_CONSTRAINED",
                    "contractor": contractor.name,
                    "capacity":   contractor.capacity_percentage,
                    "message":    f"{contractor.name} is constrained ({contractor.capacity_percentage:.0f}%)",
                })

        budget_alerts = self.contractor_manager.check_budget_health()

        if not capacity_alerts and not budget_alerts:
            print("✓ Daily capacity check: no alerts")
            return None

        print(f"⚠ Daily capacity check: {len(capacity_alerts)} capacity, "
              f"{len(budget_alerts)} budget alerts → packaging for approval")

        approval_req = self.alerts_packager.package_capacity_alerts(capacity_alerts, budget_alerts)
        self.manager_bot.notify_approver_for_review(approval_req)
        return approval_req

    # ── Approval-aware hiring recommendation ─────────────────────────────────

    def recommend_hiring_with_approval(self) -> Optional[ApprovalRequest]:
        """
        Generate hiring recommendations and package for human approval.

        Returns:
            ApprovalRequest if recommendations exist, else None
        """
        recommendations = self.contractor_manager.recommend_hiring()

        if not recommendations:
            print("✓ Hiring check: no recommendations")
            return None

        print(f"📋 Hiring check: {len(recommendations)} recommendation(s) → packaging for approval")

        approval_req = self.alerts_packager.package_hiring_recommendations(recommendations)
        self.manager_bot.notify_approver_for_review(approval_req)
        return approval_req

    # ── Approval decision processing ──────────────────────────────────────────

    def process_approval_decision(
        self,
        approval_request: ApprovalRequest,
        decision: ApprovalDecision,
        approver_name: str,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Route APPROVE / REVISE / REJECT decision.
        On APPROVE: sends notifications to PM Lead / CTO / HR Lead.
        On REJECT:  escalates back to ManagerBot for reconsideration.
        """
        result = self.manager_bot.receive_approval_decision(
            approval_id=approval_request.approval_id,
            decision=decision,
            approver_name=approver_name,
            notes=notes,
        )
        if decision == ApprovalDecision.APPROVE:
            self.notif.send_approved_notifications(approval_request)
        elif decision == ApprovalDecision.REJECT:
            self.manager_bot.escalate_if_rejected(
                approval_id=approval_request.approval_id,
                approver_notes=notes or "No reason provided",
            )
        return result


# ============================================================================
# DEMO / CLI
# ============================================================================

def _demo():
    print("=" * 60)
    print("Vendor Agent Extension — Approval Flow Demo")
    print("=" * 60)

    vendor = VendorAgentExtension()

    # ── Capacity check ────────────────────────────────────────────────────────
    print("\n--- Daily capacity check ---")
    cap_req = vendor.daily_capacity_check_with_approval()

    if cap_req:
        checklist = ApprovalChecklist(cap_req)
        print(f"Checklist: {'PASS' if checklist.all_checks_passed() else 'FAIL'}")

        result = vendor.process_approval_decision(
            cap_req, ApprovalDecision.APPROVE, "Trice Johnson", "Capacity acknowledged"
        )
        print(f"Decision result: {result['status']}")

    # ── Hiring recommendation ─────────────────────────────────────────────────
    print("\n--- Hiring recommendation check ---")
    hire_req = vendor.recommend_hiring_with_approval()

    if hire_req:
        result = vendor.process_approval_decision(
            hire_req, ApprovalDecision.REVISE, "Trice Johnson",
            "Please confirm urgency level before proceeding"
        )
        print(f"Decision result: {result['status']}")

    # ── SLA scorecard ─────────────────────────────────────────────────────────
    print("\n--- Yubi SLA scorecard ---")
    scorecard = vendor.generate_scorecard("Yubi", {
        "on_time_delivery_pct": 98.0,
        "quality_score":        92.0,
        "response_time_hours":  18.0,
        "budget_variance_pct":   3.0,
    })
    print(f"Overall: {scorecard['overall']}")
    if scorecard["escalations"]:
        print(f"Escalations: {scorecard['escalations']}")

    print("\n✅ Demo complete")


if __name__ == "__main__":
    _demo()
