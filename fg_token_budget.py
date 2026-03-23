"""
fg_token_budget.py
──────────────────
Per-agent token budget tracking with automatic model optimisation.

Features
─────────
• Per-agent daily and monthly token limits
• Global daily USD hard-cap ($5.00 by default)
• Automatic fallback to a cheaper model when budget is tight (≥ 80 %)
• Prompt truncation as a last resort before hard-blocking a call
• SQLite persistence for cross-session tracking
• Status report consumed by the monitoring dashboard
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

# ── Database path ─────────────────────────────────────────────────────────────
BUDGET_DB_PATH: str = os.getenv("FG_BUDGET_DB", "/home/claude/fg_token_budget.db")

# ── Model pricing (USD per 1 M tokens) ───────────────────────────────────────
MODEL_PRICING: Dict[str, Dict[str, float]] = {
    "claude-opus-4-6":    {"input": 5.00,  "output": 25.00},
    "claude-sonnet-4-6":  {"input": 3.00,  "output": 15.00},
    "claude-haiku-4-5":   {"input": 0.25,  "output":  1.25},
}
DEFAULT_MODEL = "claude-opus-4-6"

# ── Global caps ───────────────────────────────────────────────────────────────
GLOBAL_DAILY_BUDGET_USD: float = float(os.getenv("DAILY_BUDGET", "5.00"))
GLOBAL_MONTHLY_BUDGET_USD: float = 100.00
BUDGET_WARNING_PCT: float = 0.80   # Start recommending cheaper model at 80 %
BUDGET_CRITICAL_PCT: float = 0.95  # Truncate prompts at 95 %


# ── Per-agent configuration ───────────────────────────────────────────────────

@dataclass
class AgentBudgetConfig:
    monthly_token_limit: int
    daily_token_limit: int
    preferred_model: str
    fallback_model: Optional[str] = None   # cheaper model when budget is tight


# All agents — sales + legacy project-management agents
AGENT_BUDGETS: Dict[str, AgentBudgetConfig] = {
    # ── Sales agents ──────────────────────────────────────────────────────────
    "lead_qualifier": AgentBudgetConfig(
        monthly_token_limit=150_000,
        daily_token_limit=6_000,
        preferred_model="claude-opus-4-6",
        fallback_model="claude-sonnet-4-6",
    ),
    "account_manager": AgentBudgetConfig(
        monthly_token_limit=200_000,
        daily_token_limit=8_000,
        preferred_model="claude-opus-4-6",
        fallback_model="claude-sonnet-4-6",
    ),
    "forecast_agent": AgentBudgetConfig(
        monthly_token_limit=120_000,
        daily_token_limit=5_000,
        preferred_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
    ),
    "competitor_intel": AgentBudgetConfig(
        monthly_token_limit=100_000,
        daily_token_limit=4_000,
        preferred_model="claude-sonnet-4-6",
        fallback_model="claude-haiku-4-5",
    ),
    # ── Legacy PM / delivery agents ──────────────────────────────────────────
    "pm_agent": AgentBudgetConfig(
        monthly_token_limit=100_000,
        daily_token_limit=5_000,
        preferred_model="claude-opus-4-6",
        fallback_model="claude-sonnet-4-6",
    ),
    "ba_agent": AgentBudgetConfig(
        monthly_token_limit=120_000,
        daily_token_limit=5_000,
        preferred_model="claude-opus-4-6",
        fallback_model="claude-sonnet-4-6",
    ),
    "qa_agent": AgentBudgetConfig(
        monthly_token_limit=80_000,
        daily_token_limit=4_000,
        preferred_model="claude-sonnet-4-6",
    ),
    "vendor_agent": AgentBudgetConfig(
        monthly_token_limit=60_000,
        daily_token_limit=3_000,
        preferred_model="claude-sonnet-4-6",
    ),
    "manager_agent": AgentBudgetConfig(
        monthly_token_limit=100_000,
        daily_token_limit=5_000,
        preferred_model="claude-opus-4-6",
        fallback_model="claude-sonnet-4-6",
    ),
}


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = [
    """
    CREATE TABLE IF NOT EXISTS token_usage (
        record_id    TEXT PRIMARY KEY,
        agent_name   TEXT NOT NULL,
        decision_id  TEXT,
        workflow_id  TEXT,
        model        TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        cost_usd     REAL NOT NULL,
        usage_date   TEXT NOT NULL,
        timestamp    TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_usage_agent_date ON token_usage (agent_name, usage_date)",
    "CREATE INDEX IF NOT EXISTS idx_usage_date        ON token_usage (usage_date)",
]


# ── TokenBudget ───────────────────────────────────────────────────────────────

class TokenBudget:
    """
    Track per-agent token spending and recommend the cheapest model that fits.

    Typical call sequence:
        1. ok, msg, model = budget.check_budget("lead_qualifier", ~2000)
        2. If ok: call Claude with `model`
        3. budget.log_usage("lead_qualifier", in_tok, out_tok, model, decision_id)

    Prompt optimisation:
        prompt, model = budget.optimize_prompt("lead_qualifier", original_prompt)
    """

    def __init__(self, db_path: str = BUDGET_DB_PATH):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._conn() as conn:
            for stmt in _DDL:
                conn.execute(stmt)

    # ── Pre-call check ────────────────────────────────────────────────────────

    def check_budget(
        self, agent_name: str, estimated_tokens: int
    ) -> Tuple[bool, str, str]:
        """
        Check whether an agent has budget for `estimated_tokens`.

        Returns:
            (allowed: bool, message: str, recommended_model: str)

        Decision logic (in priority order):
            1. Global daily USD cap hit → BLOCK
            2. Agent monthly token limit hit (no fallback) → BLOCK
            3. Agent daily token limit hit (no fallback) → BLOCK
            4. Agent daily tokens ≥ 80 % AND fallback available → ALLOW with fallback model
            5. Agent monthly tokens ≥ 80 % AND fallback available → ALLOW with fallback model
            6. All clear → ALLOW with preferred model
        """
        config = AGENT_BUDGETS.get(agent_name)
        if config is None:
            return True, f"No budget config for '{agent_name}', proceeding.", DEFAULT_MODEL

        global_daily = self._global_daily_cost()
        daily_toks, daily_cost = self._agent_daily(agent_name)
        month_toks, month_cost = self._agent_monthly(agent_name)

        # 1. Global hard cap
        if global_daily >= GLOBAL_DAILY_BUDGET_USD:
            return (
                False,
                f"⛔ Global daily budget ${GLOBAL_DAILY_BUDGET_USD:.2f} reached "
                f"(${global_daily:.3f} spent). All agents paused.",
                config.preferred_model,
            )

        # 2. Agent monthly hard block (no fallback)
        if not config.fallback_model and month_toks + estimated_tokens > config.monthly_token_limit:
            return (
                False,
                f"⛔ {agent_name} monthly token limit "
                f"({config.monthly_token_limit:,}) reached ({month_toks:,} used).",
                config.preferred_model,
            )

        # 3. Agent daily hard block (no fallback)
        if not config.fallback_model and daily_toks + estimated_tokens > config.daily_token_limit:
            return (
                False,
                f"⛔ {agent_name} daily token limit "
                f"({config.daily_token_limit:,}) reached ({daily_toks:,} used).",
                config.preferred_model,
            )

        # 4. Daily ≥ 80 % → use fallback model
        daily_pct = daily_toks / config.daily_token_limit if config.daily_token_limit else 0
        if daily_pct >= BUDGET_WARNING_PCT and config.fallback_model:
            return (
                True,
                f"⚠️  {agent_name} daily tokens at {daily_pct:.0%} "
                f"— switching to {config.fallback_model} for efficiency.",
                config.fallback_model,
            )

        # 5. Monthly ≥ 80 % → use fallback model
        month_pct = month_toks / config.monthly_token_limit if config.monthly_token_limit else 0
        if month_pct >= BUDGET_WARNING_PCT and config.fallback_model:
            return (
                True,
                f"⚠️  {agent_name} monthly tokens at {month_pct:.0%} "
                f"— switching to {config.fallback_model}.",
                config.fallback_model,
            )

        return True, "✅ Budget OK", config.preferred_model

    # ── Post-call logging ─────────────────────────────────────────────────────

    def log_usage(
        self,
        agent_name: str,
        input_tokens: int,
        output_tokens: int,
        model: str,
        decision_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
    ) -> float:
        """
        Record actual token usage after a successful Claude call.
        Returns the USD cost of this call.
        """
        cost = self.calculate_cost(input_tokens, output_tokens, model)
        today = date.today().isoformat()
        now = datetime.utcnow().isoformat() + "Z"
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO token_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    str(uuid.uuid4()),
                    agent_name,
                    decision_id,
                    workflow_id,
                    model,
                    input_tokens,
                    output_tokens,
                    cost,
                    today,
                    now,
                ),
            )
        return cost

    # ── Prompt optimisation ───────────────────────────────────────────────────

    def optimize_prompt(
        self, agent_name: str, base_prompt: str
    ) -> Tuple[str, str]:
        """
        If the agent is near its daily limit, shorten the prompt and/or switch model.

        Returns:
            (optimised_prompt: str, model_to_use: str)

        Truncation thresholds (of daily token limit):
            ≥ 95 % → truncate to 1 000 chars
            ≥ 80 % → truncate to 3 000 chars
            < 80 % → no truncation
        """
        config = AGENT_BUDGETS.get(agent_name)
        if config is None:
            return base_prompt, DEFAULT_MODEL

        daily_toks, _ = self._agent_daily(agent_name)
        pct = daily_toks / config.daily_token_limit if config.daily_token_limit else 0

        if pct >= BUDGET_CRITICAL_PCT:
            truncated = base_prompt[:1_000]
            if len(base_prompt) > 1_000:
                truncated += "\n\n[Context truncated — critical token budget]"
            model = config.fallback_model or config.preferred_model
            return truncated, model

        if pct >= BUDGET_WARNING_PCT:
            truncated = base_prompt[:3_000]
            if len(base_prompt) > 3_000:
                truncated += "\n\n[Context truncated — token budget optimisation]"
            model = config.fallback_model or config.preferred_model
            return truncated, model

        return base_prompt, config.preferred_model

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_status_report(self) -> Dict:
        """
        Full budget status snapshot for all agents.
        Consumed by the monitoring dashboard and /budget_status CLI command.
        """
        global_daily = self._global_daily_cost()
        global_monthly = self._global_monthly_cost()

        report = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "global": {
                "daily_budget_usd": GLOBAL_DAILY_BUDGET_USD,
                "daily_spent_usd": round(global_daily, 4),
                "daily_pct": round(global_daily / GLOBAL_DAILY_BUDGET_USD * 100, 1),
                "monthly_budget_usd": GLOBAL_MONTHLY_BUDGET_USD,
                "monthly_spent_usd": round(global_monthly, 4),
                "monthly_pct": round(global_monthly / GLOBAL_MONTHLY_BUDGET_USD * 100, 1),
                "status": self._global_status(global_daily),
            },
            "agents": {},
        }

        for name, cfg in AGENT_BUDGETS.items():
            daily_toks, daily_cost = self._agent_daily(name)
            month_toks, month_cost = self._agent_monthly(name)
            daily_pct = daily_toks / cfg.daily_token_limit * 100 if cfg.daily_token_limit else 0
            month_pct = month_toks / cfg.monthly_token_limit * 100 if cfg.monthly_token_limit else 0
            report["agents"][name] = {
                "daily_tokens_used": daily_toks,
                "daily_token_limit": cfg.daily_token_limit,
                "daily_pct": round(daily_pct, 1),
                "daily_cost_usd": round(daily_cost, 4),
                "monthly_tokens_used": month_toks,
                "monthly_token_limit": cfg.monthly_token_limit,
                "monthly_pct": round(month_pct, 1),
                "monthly_cost_usd": round(month_cost, 4),
                "preferred_model": cfg.preferred_model,
                "fallback_model": cfg.fallback_model,
                "status": self._agent_status(daily_pct),
            }

        return report

    def print_status_report(self) -> None:
        """Pretty-print the budget status to stdout."""
        r = self.get_status_report()
        g = r["global"]
        print(f"\n{'─'*60}")
        print(f"  TOKEN BUDGET STATUS  —  {r['generated_at'][:10]}")
        print(f"{'─'*60}")
        print(f"  Global daily:   ${g['daily_spent_usd']:.4f} / ${g['daily_budget_usd']:.2f}  "
              f"({g['daily_pct']}%)  {g['status']}")
        print(f"  Global monthly: ${g['monthly_spent_usd']:.2f} / ${g['monthly_budget_usd']:.2f}  "
              f"({g['monthly_pct']}%)")
        print(f"\n  {'Agent':<20} {'Daily%':>7} {'Month%':>7} {'Model':<22} Status")
        print(f"  {'─'*68}")
        for name, a in r["agents"].items():
            model_label = a["preferred_model"].replace("claude-", "")
            print(f"  {name:<20} {a['daily_pct']:>6}% {a['monthly_pct']:>6}%  "
                  f"{model_label:<22} {a['status']}")
        print(f"{'─'*60}\n")

    # ── Utility ───────────────────────────────────────────────────────────────

    def calculate_cost(self, input_tokens: int, output_tokens: int, model: str) -> float:
        pricing = MODEL_PRICING.get(model, MODEL_PRICING[DEFAULT_MODEL])
        return (input_tokens * pricing["input"] + output_tokens * pricing["output"]) / 1_000_000

    def _agent_daily(self, agent_name: str) -> Tuple[int, float]:
        today = date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(input_tokens + output_tokens), 0),
                          COALESCE(SUM(cost_usd), 0)
                   FROM token_usage WHERE agent_name=? AND usage_date=?""",
                (agent_name, today),
            ).fetchone()
        return int(row[0]), float(row[1])

    def _agent_monthly(self, agent_name: str) -> Tuple[int, float]:
        prefix = date.today().strftime("%Y-%m")
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(input_tokens + output_tokens), 0),
                          COALESCE(SUM(cost_usd), 0)
                   FROM token_usage WHERE agent_name=? AND usage_date LIKE ?""",
                (agent_name, f"{prefix}%"),
            ).fetchone()
        return int(row[0]), float(row[1])

    def _global_daily_cost(self) -> float:
        today = date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM token_usage WHERE usage_date=?",
                (today,),
            ).fetchone()
        return float(row[0])

    def _global_monthly_cost(self) -> float:
        prefix = date.today().strftime("%Y-%m")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM token_usage WHERE usage_date LIKE ?",
                (f"{prefix}%",),
            ).fetchone()
        return float(row[0])

    def _global_status(self, daily_cost: float) -> str:
        pct = daily_cost / GLOBAL_DAILY_BUDGET_USD
        if pct >= 1.0:
            return "🔴 EXHAUSTED"
        if pct >= BUDGET_CRITICAL_PCT:
            return "🟠 CRITICAL"
        if pct >= BUDGET_WARNING_PCT:
            return "🟡 WARNING"
        return "🟢 OK"

    def _agent_status(self, daily_pct: float) -> str:
        if daily_pct >= 100:
            return "🔴 EXHAUSTED"
        if daily_pct >= 95:
            return "🟠 CRITICAL"
        if daily_pct >= 80:
            return "🟡 WARNING"
        return "🟢 OK"

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
