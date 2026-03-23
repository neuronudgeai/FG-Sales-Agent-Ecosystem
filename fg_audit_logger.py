"""
fg_audit_logger.py
──────────────────
Immutable audit trail for all FG Sales Agent decisions, reviews, and downstream passes.

Design principles:
  • Append-only. Rows are never updated or deleted.
  • Every decision has a UUID. Every review links to a decision UUID.
  • Downstream passes are logged separately so we can prove exactly what each
    downstream agent received (vs. what was originally generated).
  • Cost is logged alongside each decision for per-agent spend reporting.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fg_decision_models import AgentDecision, DownstreamPayload, ReviewDecision

# ── Database path (override via env var FG_AUDIT_DB) ─────────────────────────
import os
AUDIT_DB_PATH: str = os.getenv("FG_AUDIT_DB", "/home/claude/fg_audit.db")


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = [
    # Every agent decision
    """
    CREATE TABLE IF NOT EXISTS audit_decisions (
        record_id          TEXT PRIMARY KEY,
        decision_id        TEXT NOT NULL,
        agent_name         TEXT NOT NULL,
        workflow_id        TEXT,
        timestamp          TEXT NOT NULL,
        recommendation     TEXT,
        confidence_score   REAL,
        reasoning_json     TEXT,
        data_sources_json  TEXT,
        assumptions_json   TEXT,
        requires_review    INTEGER,
        sensitivity_flag   INTEGER,
        tokens_used        INTEGER,
        model_version      TEXT,
        input_data_json    TEXT,
        created_at         TEXT NOT NULL
    )
    """,

    # Every review gate outcome (human or auto)
    """
    CREATE TABLE IF NOT EXISTS audit_reviews (
        record_id              TEXT PRIMARY KEY,
        decision_id            TEXT NOT NULL,
        reviewed_by            TEXT NOT NULL,
        reviewed_at            TEXT NOT NULL,
        action                 TEXT NOT NULL,
        redactions_made_json   TEXT,
        approved_fields_json   TEXT,
        redacted_fields_json   TEXT,
        notes                  TEXT,
        created_at             TEXT NOT NULL
    )
    """,

    # What each downstream agent actually received
    """
    CREATE TABLE IF NOT EXISTS audit_downstream (
        record_id              TEXT PRIMARY KEY,
        source_decision_id     TEXT NOT NULL,
        source_agent           TEXT NOT NULL,
        recipient_agent        TEXT NOT NULL,
        approved_fields_json   TEXT,
        metadata_json          TEXT,
        created_at             TEXT NOT NULL
    )
    """,

    # Token cost per decision
    """
    CREATE TABLE IF NOT EXISTS audit_costs (
        record_id          TEXT PRIMARY KEY,
        decision_id        TEXT NOT NULL,
        agent_name         TEXT NOT NULL,
        input_tokens       INTEGER,
        output_tokens      INTEGER,
        model              TEXT,
        estimated_cost_usd REAL,
        usage_date         TEXT NOT NULL,
        timestamp          TEXT NOT NULL
    )
    """,

    # Indexes for common queries
    "CREATE INDEX IF NOT EXISTS idx_decisions_agent    ON audit_decisions (agent_name)",
    "CREATE INDEX IF NOT EXISTS idx_decisions_wf       ON audit_decisions (workflow_id)",
    "CREATE INDEX IF NOT EXISTS idx_reviews_decision   ON audit_reviews   (decision_id)",
    "CREATE INDEX IF NOT EXISTS idx_downstream_src     ON audit_downstream(source_decision_id)",
    "CREATE INDEX IF NOT EXISTS idx_costs_decision     ON audit_costs     (decision_id)",
    "CREATE INDEX IF NOT EXISTS idx_costs_agent_date   ON audit_costs     (agent_name, usage_date)",
]


# ── AuditLogger ───────────────────────────────────────────────────────────────

class AuditLogger:
    """
    Append-only audit trail.  Call in this order per decision lifecycle:

        1. log_decision(decision)          ← when agent produces output
        2. log_review_action(d, review)    ← when gate approves / rejects
        3. log_downstream_pass(payload)    ← when clean payload goes to next agent
        4. log_cost(...)                   ← alongside log_decision (token costs)

    Then query with:
        get_full_audit_record(decision_id) → complete lifecycle dict
        query_audit_trail(...)             → filtered list for dashboards
        get_approval_stats()               → per-agent approval rates
    """

    def __init__(self, db_path: str = AUDIT_DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            for stmt in _DDL:
                conn.execute(stmt)

    # ── Write operations (append-only) ────────────────────────────────────────

    def log_decision(self, decision: AgentDecision) -> str:
        """
        Persist an agent decision immediately after Claude returns.
        Returns the generated record_id.
        """
        record_id = _uid()
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO audit_decisions VALUES
                   (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    record_id,
                    decision.decision_id,
                    decision.agent_name,
                    decision.workflow_id,
                    decision.timestamp.isoformat() + "Z",
                    decision.recommendation,
                    decision.confidence_score,
                    _j(decision.reasoning),
                    _j(decision.data_sources),
                    _j(decision.assumptions),
                    int(decision.requires_review),
                    int(decision.sensitivity_flag),
                    decision.tokens_used,
                    decision.model_version,
                    _j(decision.input_data),
                    now,
                ),
            )
        return record_id

    def log_review_action(
        self, decision: AgentDecision, review: ReviewDecision
    ) -> str:
        """
        Persist a gate review outcome — human or automatic.
        Returns the generated record_id.
        """
        record_id = _uid()
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO audit_reviews VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    record_id,
                    review.decision_id,
                    review.reviewed_by,
                    review.reviewed_at.isoformat() + "Z",
                    review.action,
                    _j(review.redactions_made),
                    _j(review.approved_fields),
                    _j(review.redacted_fields),
                    review.notes,
                    now,
                ),
            )
        return record_id

    def log_downstream_pass(self, payload: DownstreamPayload) -> str:
        """
        Record exactly what a downstream agent received (post-redaction).
        Returns the generated record_id.
        """
        record_id = _uid()
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO audit_downstream VALUES (?,?,?,?,?,?,?)""",
                (
                    record_id,
                    payload.source_decision_id,
                    payload.source_agent,
                    payload.recipient_agent,
                    _j(payload.approved_fields),
                    _j(payload.metadata),
                    now,
                ),
            )
        return record_id

    def log_cost(
        self,
        decision_id: str,
        agent_name: str,
        input_tokens: int,
        output_tokens: int,
        model: str,
        cost_usd: float,
    ) -> str:
        """
        Record token cost for one agent call.
        Returns the generated record_id.
        """
        record_id = _uid()
        today = date.today().isoformat()
        now = _now()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO audit_costs VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    record_id,
                    decision_id,
                    agent_name,
                    input_tokens,
                    output_tokens,
                    model,
                    cost_usd,
                    today,
                    now,
                ),
            )
        return record_id

    # ── Read operations ───────────────────────────────────────────────────────

    def get_full_audit_record(self, decision_id: str) -> Dict[str, Any]:
        """
        Return the complete lifecycle for one decision_id:
            agent_decision → review_gate → downstream_output → cost_tracking

        This is the canonical "audit record" structure from the spec.
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row

            d = conn.execute(
                "SELECT * FROM audit_decisions WHERE decision_id=? LIMIT 1",
                (decision_id,),
            ).fetchone()

            r = conn.execute(
                "SELECT * FROM audit_reviews WHERE decision_id=? LIMIT 1",
                (decision_id,),
            ).fetchone()

            ds = conn.execute(
                "SELECT * FROM audit_downstream WHERE source_decision_id=? LIMIT 1",
                (decision_id,),
            ).fetchone()

            c = conn.execute(
                "SELECT * FROM audit_costs WHERE decision_id=? LIMIT 1",
                (decision_id,),
            ).fetchone()

        record: Dict[str, Any] = {"decision_id": decision_id}

        if d:
            ad = dict(d)
            ad["reasoning"] = _uj(ad.pop("reasoning_json", "[]"))
            ad["data_sources"] = _uj(ad.pop("data_sources_json", "[]"))
            ad["assumptions"] = _uj(ad.pop("assumptions_json", "[]"))
            ad["input_data"] = _uj(ad.pop("input_data_json", "{}"))
            record["agent_decision"] = ad

        if r:
            rv = dict(r)
            rv["redactions_made"] = _uj(rv.pop("redactions_made_json", "[]"))
            rv["approved_fields"] = _uj(rv.pop("approved_fields_json", "[]"))
            rv["redacted_fields"] = _uj(rv.pop("redacted_fields_json", "[]"))
            record["review_gate"] = rv

        if ds:
            dsd = dict(ds)
            dsd["approved_fields"] = _uj(dsd.pop("approved_fields_json", "{}"))
            dsd["metadata"] = _uj(dsd.pop("metadata_json", "{}"))
            record["downstream_output"] = dsd

        if c:
            cd = dict(c)
            cd["estimated_cost_formatted"] = f"${cd.get('estimated_cost_usd', 0):.4f}"
            record["cost_tracking"] = cd

        return record

    def query_audit_trail(
        self,
        agent_name: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Filtered query for dashboards and compliance reports.

        Args:
            agent_name: Filter by agent (e.g. "lead_qualifier")
            action:     Filter by gate outcome ("APPROVE" | "REDACT" | "REJECT")
            since:      ISO timestamp — only records after this time
            limit:      Max rows returned (default 100)
        """
        conditions: List[str] = []
        params: List[Any] = []

        base = """
            SELECT
                d.decision_id,
                d.agent_name,
                d.timestamp,
                d.recommendation,
                d.confidence_score,
                d.sensitivity_flag,
                d.tokens_used,
                r.action,
                r.reviewed_by,
                r.reviewed_at,
                r.notes,
                c.estimated_cost_usd
            FROM audit_decisions d
            LEFT JOIN audit_reviews  r ON d.decision_id = r.decision_id
            LEFT JOIN audit_costs    c ON d.decision_id = c.decision_id
        """

        if agent_name:
            conditions.append("d.agent_name = ?")
            params.append(agent_name)
        if action:
            conditions.append("r.action = ?")
            params.append(action)
        if since:
            conditions.append("d.timestamp >= ?")
            params.append(since)

        if conditions:
            base += " WHERE " + " AND ".join(conditions)
        base += " ORDER BY d.timestamp DESC LIMIT ?"
        params.append(limit)

        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(base, params).fetchall()

        return [dict(r) for r in rows]

    def get_approval_stats(self) -> Dict[str, Any]:
        """
        Per-agent approval / rejection / redaction rates plus average confidence.
        Used by the monitoring dashboard.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT
                    d.agent_name,
                    COUNT(*)                                              AS total,
                    SUM(CASE WHEN r.action = 'APPROVE' THEN 1 ELSE 0 END) AS approved,
                    SUM(CASE WHEN r.action = 'REJECT'  THEN 1 ELSE 0 END) AS rejected,
                    SUM(CASE WHEN r.action = 'REDACT'  THEN 1 ELSE 0 END) AS redacted,
                    AVG(d.confidence_score)                               AS avg_confidence,
                    SUM(c.estimated_cost_usd)                             AS total_cost_usd
                FROM audit_decisions d
                LEFT JOIN audit_reviews r ON d.decision_id = r.decision_id
                LEFT JOIN audit_costs   c ON d.decision_id = c.decision_id
                GROUP BY d.agent_name
            """).fetchall()

        return {
            r[0]: {
                "total": r[1],
                "approved": r[2] or 0,
                "rejected": r[3] or 0,
                "redacted": r[4] or 0,
                "approval_rate": round((r[2] or 0) / r[1] * 100, 1) if r[1] else 0,
                "avg_confidence": round(r[5] or 0, 3),
                "total_cost_usd": round(r[6] or 0, 4),
            }
            for r in rows
        }

    def get_pending_reviews_summary(self) -> List[Dict[str, Any]]:
        """
        Decisions that were logged but have no review yet — i.e. still waiting for SME.
        """
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT d.decision_id, d.agent_name, d.timestamp,
                       d.confidence_score, d.sensitivity_flag, d.recommendation
                FROM audit_decisions d
                LEFT JOIN audit_reviews r ON d.decision_id = r.decision_id
                WHERE r.decision_id IS NULL
                ORDER BY d.timestamp ASC
            """).fetchall()
        return [dict(r) for r in rows]

    def get_daily_cost_report(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Per-agent cost breakdown for a given date (defaults to today).
        """
        target = target_date or date.today().isoformat()
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT agent_name,
                       SUM(input_tokens)       AS input_tokens,
                       SUM(output_tokens)      AS output_tokens,
                       SUM(estimated_cost_usd) AS cost_usd,
                       COUNT(*)                AS calls
                FROM audit_costs
                WHERE usage_date = ?
                GROUP BY agent_name
            """, (target,)).fetchall()

            total = conn.execute("""
                SELECT COALESCE(SUM(estimated_cost_usd), 0)
                FROM audit_costs WHERE usage_date = ?
            """, (target,)).fetchone()[0]

        return {
            "date": target,
            "total_cost_usd": round(float(total), 4),
            "daily_budget_usd": 5.00,
            "budget_pct_used": round(float(total) / 5.00 * 100, 1),
            "by_agent": {
                r[0]: {
                    "input_tokens": r[1],
                    "output_tokens": r[2],
                    "cost_usd": round(r[3], 4),
                    "calls": r[4],
                }
                for r in rows
            },
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")  # concurrent reads + writes
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


# ── Module-level helpers ──────────────────────────────────────────────────────

def _uid() -> str:
    return str(uuid.uuid4())


def _now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _j(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _uj(s: str) -> Any:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError):
        return s
