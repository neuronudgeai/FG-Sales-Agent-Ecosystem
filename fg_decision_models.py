"""
fg_decision_models.py
─────────────────────
Standardized output objects for the FG Sales Agent Ecosystem governance layer.

Every agent outputs an AgentDecision. Every review produces a ReviewDecision.
Every downstream pass produces a DownstreamPayload. Nothing flows between agents
without passing through this structure.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ── Core Decision Object ──────────────────────────────────────────────────────

@dataclass
class AgentDecision:
    """
    Standardized output from all FG Sales agents.

    Every agent call must produce one of these instead of raw text.
    The ReviewGate inspects this before anything flows downstream.
    """

    # Identity
    agent_name: str
    workflow_id: str

    # The actual output
    recommendation: str              # One-sentence decision/output

    # Explainability
    confidence_score: float          # 0.0 (uncertain) → 1.0 (certain)
    reasoning: List[str]             # Step-by-step logic
    data_sources: List[str]          # Inputs that informed this decision
    assumptions: List[str]           # What the agent assumed (gaps in data)

    # Governance flags
    requires_review: bool            # Must an SME see this before it proceeds?
    sensitivity_flag: bool           # Does this contain PII / high-impact data?

    # Cost tracking
    tokens_used: int                 # Total tokens (input + output)
    model_version: str               # e.g. "claude-opus-4-6"

    # Raw input snapshot (will be redacted before downstream)
    input_data: Dict[str, Any] = field(default_factory=dict)

    # Auto-generated
    decision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.utcnow)

    # ── Derived properties ────────────────────────────────────────────────────

    @property
    def can_auto_approve(self) -> bool:
        """
        High confidence + no sensitivity flag + no explicit review request
        → eligible for automatic approval without human review.
        Threshold: 90% confidence.
        """
        return (
            self.confidence_score >= 0.90
            and not self.sensitivity_flag
            and not self.requires_review
        )

    @property
    def needs_sme_review(self) -> bool:
        """Routes to a subject-matter expert for manual review."""
        return (
            self.sensitivity_flag
            or self.confidence_score < 0.75
            or self.requires_review
        )

    @property
    def needs_escalation(self) -> bool:
        """Very low confidence → escalate to senior architect."""
        return self.confidence_score < 0.40

    # ── Serialisation ─────────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "agent_name": self.agent_name,
            "workflow_id": self.workflow_id,
            "timestamp": self.timestamp.isoformat() + "Z",
            "recommendation": self.recommendation,
            "confidence_score": self.confidence_score,
            "reasoning": self.reasoning,
            "data_sources": self.data_sources,
            "assumptions": self.assumptions,
            "requires_review": self.requires_review,
            "sensitivity_flag": self.sensitivity_flag,
            "can_auto_approve": self.can_auto_approve,
            "needs_sme_review": self.needs_sme_review,
            "tokens_used": self.tokens_used,
            "model_version": self.model_version,
            "input_data": self.input_data,
        }

    def summary(self) -> str:
        """Human-readable one-liner for CLI / email output."""
        flag = "🔴 SENSITIVE" if self.sensitivity_flag else "🟢 STANDARD"
        return (
            f"[{self.agent_name}] {flag} | conf={self.confidence_score:.0%} | "
            f"{self.recommendation[:80]}…"
        )


# ── Review Decision ───────────────────────────────────────────────────────────

@dataclass
class ReviewDecision:
    """
    The outcome of a ReviewGate evaluation — either automatic or human SME.

    Possible actions:
        APPROVE  — output passes downstream as-is (after safe-field filtering)
        REDACT   — specific fields are removed, remainder passes downstream
        REJECT   — output is blocked; reason logged; escalation may follow
    """

    decision_id: str                 # Links back to AgentDecision
    reviewed_by: str                 # Email or "system:auto_approve"
    action: str                      # "APPROVE" | "REDACT" | "REJECT"
    redactions_made: List[str]       # Human-readable descriptions of what was removed
    approved_fields: List[str]       # Keys safe to pass downstream
    redacted_fields: List[str]       # Keys removed from downstream payload
    notes: str = ""                  # Reviewer comments / escalation reason

    reviewed_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": self.reviewed_at.isoformat() + "Z",
            "action": self.action,
            "redactions_made": self.redactions_made,
            "approved_fields": self.approved_fields,
            "redacted_fields": self.redacted_fields,
            "notes": self.notes,
        }

    @property
    def is_approved(self) -> bool:
        return self.action in ("APPROVE", "REDACT")

    @property
    def is_rejected(self) -> bool:
        return self.action == "REJECT"


# ── Downstream Payload ────────────────────────────────────────────────────────

@dataclass
class DownstreamPayload:
    """
    The clean, redacted object that flows into the next agent in the pipeline.

    Rules enforced here:
        • No raw prospect names or PII
        • No internal confidence scores (prevents anchoring bias)
        • No raw reasoning steps (forces independent analysis)
        • No assumptions or data-source details
        • Only approved summary fields

    A downstream agent sees decisions, not raw data.
    """

    source_decision_id: str
    source_agent: str
    recipient_agent: str
    approved_fields: Dict[str, Any]   # The sanitised data
    metadata: Dict[str, Any]          # Non-sensitive routing context

    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_decision_id": self.source_decision_id,
            "source_agent": self.source_agent,
            "recipient_agent": self.recipient_agent,
            "approved_fields": self.approved_fields,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() + "Z",
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self.approved_fields.get(key, default)


# ── Audit Log Entry (composite view) ─────────────────────────────────────────

@dataclass
class AuditRecord:
    """
    Full composite record of one decision lifecycle, used for reporting.
    Assembled by AuditLogger.get_full_audit_record().
    """

    decision_id: str
    agent_decision: Optional[Dict[str, Any]] = None
    review_gate: Optional[Dict[str, Any]] = None
    downstream_output: Optional[Dict[str, Any]] = None
    cost_tracking: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "agent_decision": self.agent_decision,
            "review_gate": self.review_gate,
            "downstream_output": self.downstream_output,
            "cost_tracking": self.cost_tracking,
        }
