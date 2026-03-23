"""
fg_review_gate.py
─────────────────
The governance gate that every agent output must pass through before it can
reach a downstream agent or human.

Flow
────
    Agent Output (AgentDecision)
         │
         ▼
    ReviewGate.evaluate()
         ├─ can_auto_approve?  → AUTO_APPROVED  → log → prepare_downstream_input()
         ├─ needs_escalation?  → ESCALATED      → notify architect
         └─ needs_sme_review?  → PENDING_SME    → block here, wait for human
                                      │
                              process_sme_review()
                                      ├─ APPROVE  → log → prepare_downstream_input()
                                      ├─ REDACT   → apply_redactions() → log → prepare_downstream_input()
                                      └─ REJECT   → log → escalate

Key principles
──────────────
  • Nothing flows downstream without an explicit APPROVE or REDACT decision.
  • Downstream payloads NEVER carry: raw PII, confidence scores, reasoning
    steps, assumptions, or internal data-source details.
  • All gate actions are persisted immediately to the audit logger.
  • The SME routing table is agent-aware (different SMEs per agent type).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fg_decision_models import AgentDecision, DownstreamPayload, ReviewDecision

# ── PII detection patterns ────────────────────────────────────────────────────
_PII_PATTERNS: List[re.Pattern] = [
    re.compile(r"\b[A-Z][a-z]+ [A-Z][a-z]+\b"),               # Full names
    re.compile(r"\b[\w.+-]+@[\w-]+\.[a-z]{2,}\b", re.I),      # Email addresses
    re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),         # Phone numbers
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                      # SSN
    re.compile(r"\$\s?\d[\d,]+"),                               # Dollar amounts
]

# ── Safe fields for downstream (aggregated / non-PII) ────────────────────────
_ALWAYS_SAFE_FIELDS: List[str] = [
    "recommendation",
    "lead_score",
    "priority",
    "priority_tier",
    "account_health",
    "risk_level",
    "forecast_value_usd",
    "confidence_range",
    "pipeline_health",
    "deal_stage",
    "next_steps",
    "action_required",
    "threat_level",
    "win_probability",
    "upsell_opportunity",
    "icp_fit_score",
    "next_action",
]

# ── Fields that NEVER leave to downstream agents ─────────────────────────────
_NEVER_DOWNSTREAM_FIELDS: List[str] = [
    "prospect_name",
    "contact_email",
    "personal_context",
    "internal_scoring_logic",
    # Governance metadata — downstream agent should reason independently
    "confidence_score",
    "reasoning",
    "assumptions",
    "data_sources",
]


# ── SME routing table ─────────────────────────────────────────────────────────
# Maps agent_name → (sme_email, sme_role)
_SME_ROUTES: Dict[str, Tuple[str, str]] = {
    "lead_qualifier":  ("sales_ops@firstgenesis.com",  "Sales Operations Lead"),
    "account_manager": ("crm_lead@firstgenesis.com",   "CRM Manager"),
    "forecast_agent":  ("finance@firstgenesis.com",    "Finance Director"),
    "competitor_intel":("strategy@firstgenesis.com",   "Strategy Lead"),
    # Legacy delivery agents (fall back to CDO)
    "pm_agent":        ("tjohnson@firstgenesis.com",   "CDO"),
    "ba_agent":        ("k.phipps@firstgenesis.com",   "PMO"),
    "qa_agent":        ("emaiteu@firstgenesis.com",    "PMO"),
    "vendor_agent":    ("emaiteu@firstgenesis.com",    "PMO"),
    "manager_agent":   ("tjohnson@firstgenesis.com",   "CDO"),
    # Default catch-all
    "default":         ("tjohnson@firstgenesis.com",   "CDO"),
}

# ── Escalation contact ────────────────────────────────────────────────────────
_ESCALATION_CONTACT = ("pwatty@firstgenesis.com", "CTO / Senior Architect")


# ── ReviewGate ────────────────────────────────────────────────────────────────

class ReviewGate:
    """
    Intercepts every AgentDecision and enforces the three-path gate:

        AUTO_APPROVED  — high-confidence, non-sensitive decision; no human needed
        PENDING_SME    — queued for human SME review
        ESCALATED      — very low confidence; routed to senior architect
        AUTO_REJECTED  — confidence below minimum floor

    Usage
    ─────
        gate = ReviewGate(audit_logger)

        status, review = gate.evaluate(decision)

        if status == "PENDING_SME":
            # ... show gate.get_pending_reviews() to SME CLI / web UI ...
            review = gate.process_sme_review(decision_id, sme_email, "APPROVE")

        if review and review.is_approved:
            payload = gate.prepare_downstream_input(decision, review, "forecast_agent")
            # hand payload to next agent
    """

    # Confidence floor — anything below this is auto-rejected (not even queued for SME)
    MIN_CONFIDENCE_FLOOR: float = 0.20

    def __init__(self, audit_logger, auto_approve_threshold: float = 0.90):
        """
        Args:
            audit_logger:           An AuditLogger instance (injected).
            auto_approve_threshold: Minimum confidence for automatic approval.
                                    Default 0.90 (90 %).
        """
        self.audit_logger = audit_logger
        self.auto_approve_threshold = auto_approve_threshold
        self._pending: Dict[str, AgentDecision] = {}   # decision_id → AgentDecision

    # ── Primary entry point ───────────────────────────────────────────────────

    def evaluate(
        self, decision: AgentDecision
    ) -> Tuple[str, Optional[ReviewDecision]]:
        """
        Route an AgentDecision through the gate.

        Returns:
            (status, review_decision_or_None)

            status values:
                "AUTO_APPROVED"  — review is a ReviewDecision with action=APPROVE
                "AUTO_REJECTED"  — review is a ReviewDecision with action=REJECT
                "ESCALATED"      — review is a ReviewDecision with action=REJECT + note
                "PENDING_SME"    — review is None (waiting for human)
        """
        # 1. Hard floor — too uncertain to queue for SME
        if decision.confidence_score < self.MIN_CONFIDENCE_FLOOR:
            review = self._build_review(
                decision,
                reviewer="system:auto_reject",
                action="REJECT",
                notes=(
                    f"Confidence {decision.confidence_score:.0%} below minimum floor "
                    f"({self.MIN_CONFIDENCE_FLOOR:.0%}). Decision discarded."
                ),
            )
            self.audit_logger.log_review_action(decision, review)
            return "AUTO_REJECTED", review

        # 2. Low confidence → escalate to senior architect (not regular SME)
        if decision.needs_escalation:
            sme_email, sme_role = _ESCALATION_CONTACT
            review = self._build_review(
                decision,
                reviewer="system:escalation",
                action="REJECT",
                notes=(
                    f"Confidence {decision.confidence_score:.0%} requires escalation "
                    f"to {sme_role} ({sme_email}). Pending manual review."
                ),
            )
            self.audit_logger.log_review_action(decision, review)
            self._pending[decision.decision_id] = decision
            return "ESCALATED", review

        # 3. Eligible for automatic approval
        if decision.can_auto_approve:
            review = self._build_review(
                decision,
                reviewer="system:auto_approve",
                action="APPROVE",
                notes=(
                    f"Auto-approved: confidence={decision.confidence_score:.0%}, "
                    f"sensitivity=False, requires_review=False."
                ),
                approved_fields=list(_ALWAYS_SAFE_FIELDS),
                redacted_fields=list(_NEVER_DOWNSTREAM_FIELDS),
            )
            self.audit_logger.log_review_action(decision, review)
            return "AUTO_APPROVED", review

        # 4. Queue for human SME review
        self._pending[decision.decision_id] = decision
        return "PENDING_SME", None

    # ── Human SME review ──────────────────────────────────────────────────────

    def process_sme_review(
        self,
        decision_id: str,
        reviewer_email: str,
        action: str,                          # "APPROVE" | "REDACT" | "REJECT"
        redaction_descriptions: Optional[List[str]] = None,
        explicitly_redacted_fields: Optional[List[str]] = None,
        notes: str = "",
    ) -> ReviewDecision:
        """
        Record a human SME's decision on a pending review.

        Args:
            decision_id:               Links to the AgentDecision.
            reviewer_email:            Identity of the approving SME.
            action:                    "APPROVE" | "REDACT" | "REJECT"
            redaction_descriptions:    Human-readable list of what was removed
                                       (e.g. "removed prospect name from notes").
            explicitly_redacted_fields: Field keys to strip from approved_fields.
            notes:                     SME comments / instructions for next agent.

        Returns:
            ReviewDecision (logged to audit trail).

        Raises:
            KeyError: If decision_id is not in the pending queue.
        """
        if decision_id not in self._pending:
            raise KeyError(
                f"No pending review found for decision_id={decision_id}. "
                "It may have been already reviewed or the ID is wrong."
            )

        decision = self._pending.pop(decision_id)
        redaction_descriptions = redaction_descriptions or []
        explicitly_redacted_fields = explicitly_redacted_fields or []

        # Build approved / redacted field lists
        redacted = set(_NEVER_DOWNSTREAM_FIELDS) | set(explicitly_redacted_fields)
        approved = [f for f in _ALWAYS_SAFE_FIELDS if f not in redacted]

        review = self._build_review(
            decision,
            reviewer=reviewer_email,
            action=action,
            notes=notes,
            redaction_descriptions=redaction_descriptions,
            approved_fields=approved,
            redacted_fields=list(redacted),
        )
        self.audit_logger.log_review_action(decision, review)
        return review

    # ── Redaction helper ──────────────────────────────────────────────────────

    def apply_redactions(
        self, decision: AgentDecision, fields_to_redact: List[str]
    ) -> AgentDecision:
        """
        Return a copy of the decision with specified input_data fields replaced
        with "[REDACTED]".  Does not mutate the original.
        """
        import copy
        redacted_decision = copy.deepcopy(decision)
        for key in fields_to_redact:
            if key in (redacted_decision.input_data or {}):
                redacted_decision.input_data[key] = "[REDACTED]"
        return redacted_decision

    # ── Downstream payload builder ────────────────────────────────────────────

    def prepare_downstream_input(
        self,
        decision: AgentDecision,
        review: ReviewDecision,
        recipient_agent: str,
    ) -> DownstreamPayload:
        """
        Build the sanitised payload that a downstream agent receives.

        What goes in:
            • Approved summary fields (lead_score, priority, recommendation, etc.)
            • Sanitised recommendation text (PII stripped)
            • Routing metadata (source agent, review action, redaction count)

        What NEVER goes in:
            • Raw prospect names / PII
            • Confidence scores  (prevents anchoring bias in downstream model)
            • Full reasoning chain  (forces independent analysis)
            • Assumptions / data-source lists
            • Any field in _NEVER_DOWNSTREAM_FIELDS

        The downstream agent sees decisions, not raw data.
        """
        # Start with approved fields from the review
        safe: Dict[str, Any] = {}

        # Always include a clean recommendation
        safe["recommendation"] = self._strip_pii(decision.recommendation)
        safe["priority"] = self._extract_priority(decision.recommendation)
        safe["context"] = self._sanitise_context(decision.recommendation)

        # Approved structured fields from input_data
        for field in review.approved_fields:
            if field in _ALWAYS_SAFE_FIELDS and field in (decision.input_data or {}):
                val = decision.input_data[field]
                # String values get PII-stripped too
                safe[field] = self._strip_pii(str(val)) if isinstance(val, str) else val

        payload = DownstreamPayload(
            source_decision_id=decision.decision_id,
            source_agent=decision.agent_name,
            recipient_agent=recipient_agent,
            approved_fields=safe,
            metadata={
                "review_action": review.action,
                "reviewed_by": review.reviewed_by,
                "redactions_count": len(review.redactions_made),
                "sensitivity_was_flagged": decision.sensitivity_flag,
                "workflow_id": decision.workflow_id,
            },
        )

        # Log to audit trail
        self.audit_logger.log_downstream_pass(payload)
        return payload

    # ── Query helpers ─────────────────────────────────────────────────────────

    def get_pending_reviews(self) -> Dict[str, Dict]:
        """
        Return all decisions currently waiting for human SME review.
        Safe to display in a CLI or web dashboard.
        """
        return {
            did: {
                "decision_id": d.decision_id,
                "agent_name": d.agent_name,
                "workflow_id": d.workflow_id,
                "recommendation": d.recommendation[:150],
                "confidence_score": d.confidence_score,
                "sensitivity_flag": d.sensitivity_flag,
                "timestamp": d.timestamp.isoformat() + "Z",
                "sme_email": self.get_sme_for_agent(d.agent_name)[0],
                "sme_role": self.get_sme_for_agent(d.agent_name)[1],
            }
            for did, d in self._pending.items()
        }

    def get_sme_for_agent(self, agent_name: str) -> Tuple[str, str]:
        """Return (sme_email, sme_role) for the appropriate SME."""
        return _SME_ROUTES.get(agent_name, _SME_ROUTES["default"])

    def pending_count(self) -> int:
        return len(self._pending)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_review(
        self,
        decision: AgentDecision,
        reviewer: str,
        action: str,
        notes: str = "",
        redaction_descriptions: Optional[List[str]] = None,
        approved_fields: Optional[List[str]] = None,
        redacted_fields: Optional[List[str]] = None,
    ) -> ReviewDecision:
        return ReviewDecision(
            decision_id=decision.decision_id,
            reviewed_by=reviewer,
            action=action,
            redactions_made=redaction_descriptions or [],
            approved_fields=approved_fields or list(_ALWAYS_SAFE_FIELDS),
            redacted_fields=redacted_fields or list(_NEVER_DOWNSTREAM_FIELDS),
            notes=notes,
            reviewed_at=datetime.utcnow(),
        )

    def _strip_pii(self, text: str) -> str:
        """Replace PII patterns in text with [REDACTED]."""
        for pattern in _PII_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text

    def _extract_priority(self, recommendation: str) -> str:
        """Derive a simple priority label from the recommendation text."""
        lower = recommendation.lower()
        if any(w in lower for w in ("urgent", "critical", "immediate", "high priority")):
            return "URGENT"
        if any(w in lower for w in ("medium", "moderate", "consider", "evaluate")):
            return "MEDIUM"
        if any(w in lower for w in ("low", "deprioritize", "watch", "nurture")):
            return "LOW"
        return "STANDARD"

    def _sanitise_context(self, text: str, max_len: int = 400) -> str:
        """Strip PII and truncate for safe inclusion in downstream context."""
        return self._strip_pii(text)[:max_len]
