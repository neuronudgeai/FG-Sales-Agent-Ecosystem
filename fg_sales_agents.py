"""
fg_sales_agents.py
──────────────────
The four FG Sales agents, each built on the governance framework:

    1. LeadQualifierAgent   — scores and filters inbound leads
    2. AccountManagerAgent  — account health, upsell/cross-sell opportunities
    3. ForecastAgent        — pipeline analytics and revenue forecasting
    4. CompetitorIntelAgent — competitive positioning and battlecard recommendations

Every agent:
    • Calls Claude and returns an AgentDecision (not raw text)
    • Includes confidence score, reasoning, data sources, assumptions
    • Flags sensitivity on PII-containing or high-value decisions
    • Tracks tokens via TokenBudget
    • Logs decisions immediately to AuditLogger
    • Instructs Claude to respond in structured JSON

Agents do NOT know about ReviewGate or downstream routing — that is the
orchestrator's responsibility (see fg_gated_orchestrator.py).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import anthropic

from fg_audit_logger import AuditLogger
from fg_decision_models import AgentDecision
from fg_token_budget import TokenBudget


# ── Base class ────────────────────────────────────────────────────────────────

class SalesAgentBase:
    """
    Shared infrastructure for all FG sales agents.

    Subclasses set:
        AGENT_NAME    — machine-readable name matching AGENT_BUDGETS key
        SYSTEM_PROMPT — Claude system prompt (must instruct JSON output)

    Then call:
        self._run(user_message, workflow_id, input_data, sensitivity_flag, data_sources)
    """

    AGENT_NAME: str = "base_agent"
    SYSTEM_PROMPT: str = ""

    def __init__(
        self,
        audit_logger: AuditLogger,
        token_budget: TokenBudget,
        api_key: Optional[str] = None,
    ):
        self.audit_logger = audit_logger
        self.token_budget = token_budget
        self.client = anthropic.Anthropic(
            api_key=api_key or os.getenv("ANTHROPIC_API_KEY")
        )

    # ── Core execution ────────────────────────────────────────────────────────

    def _run(
        self,
        user_message: str,
        workflow_id: str,
        input_data: Dict[str, Any],
        sensitivity_flag: bool = False,
        data_sources: Optional[List[str]] = None,
    ) -> AgentDecision:
        """
        Full governance-aware Claude call:
            1. Budget check → may switch to cheaper model or reject
            2. Prompt optimisation if near limit
            3. Claude call
            4. Token logging
            5. Output parsing → AgentDecision
            6. Audit logging
        """
        data_sources = data_sources or []

        # 1 & 2. Budget + optimisation
        estimated = len(self.SYSTEM_PROMPT.split()) + len(user_message.split())
        budget_ok, budget_msg, model = self.token_budget.check_budget(
            self.AGENT_NAME, estimated
        )
        if not budget_ok:
            raise RuntimeError(
                f"[{self.AGENT_NAME}] Budget blocked: {budget_msg}"
            )

        user_message, model = self.token_budget.optimize_prompt(
            self.AGENT_NAME, user_message
        )

        # 3. Call Claude
        response = self.client.messages.create(
            model=model,
            max_tokens=1_500,
            system=self.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text
        in_tok = response.usage.input_tokens
        out_tok = response.usage.output_tokens

        # 4. Log tokens (cost returned for audit)
        cost = self.token_budget.log_usage(
            self.AGENT_NAME, in_tok, out_tok, model,
            workflow_id=workflow_id,
        )

        # 5. Parse structured output
        decision = self._parse(
            raw=raw,
            workflow_id=workflow_id,
            tokens_used=in_tok + out_tok,
            model_version=model,
            input_data=input_data,
            sensitivity_flag=sensitivity_flag,
            data_sources=data_sources,
        )

        # 6. Audit
        self.audit_logger.log_decision(decision)
        self.audit_logger.log_cost(
            decision.decision_id,
            self.AGENT_NAME,
            in_tok,
            out_tok,
            model,
            cost,
        )
        return decision

    def _parse(
        self,
        raw: str,
        workflow_id: str,
        tokens_used: int,
        model_version: str,
        input_data: Dict[str, Any],
        sensitivity_flag: bool,
        data_sources: List[str],
    ) -> AgentDecision:
        """
        Parse Claude's JSON response into an AgentDecision.
        Falls back gracefully for non-JSON output (lowers confidence score).
        """
        try:
            # Strip markdown code fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[:-1])

            parsed = json.loads(cleaned)
            recommendation = str(parsed.get("recommendation", raw[:200]))
            confidence = float(parsed.get("confidence_score", 0.75))
            confidence = max(0.0, min(1.0, confidence))   # clamp to [0, 1]
            reasoning = _to_list(parsed.get("reasoning", []))
            assumptions = _to_list(parsed.get("assumptions", []))

            # Merge structured fields back into input_data for audit (safe snapshot)
            safe_structured = {
                k: v for k, v in parsed.items()
                if k not in ("reasoning", "assumptions", "confidence_score", "recommendation")
            }
            merged_input = {**input_data, **safe_structured}

        except (json.JSONDecodeError, ValueError, AttributeError):
            recommendation = raw[:500]
            confidence = 0.65    # Lower confidence for unstructured output
            reasoning = ["Agent returned unstructured output — manual review recommended."]
            assumptions = ["Output was not valid JSON; fields may be missing."]
            merged_input = input_data

        return AgentDecision(
            agent_name=self.AGENT_NAME,
            workflow_id=workflow_id,
            recommendation=recommendation,
            confidence_score=confidence,
            reasoning=reasoning,
            data_sources=data_sources,
            assumptions=assumptions,
            requires_review=sensitivity_flag or confidence < 0.80,
            sensitivity_flag=sensitivity_flag,
            tokens_used=tokens_used,
            model_version=model_version,
            input_data=merged_input,
        )


# ── Agent 1: Lead Qualifier ───────────────────────────────────────────────────

class LeadQualifierAgent(SalesAgentBase):
    """
    Scores inbound leads against the First Genesis ICP and outputs a priority tier.

    Sensitivity flag = True (always — lead data contains prospect PII).
    """

    AGENT_NAME = "lead_qualifier"

    SYSTEM_PROMPT = """\
You are the Sales Lead Qualifier AI for First Genesis, a digital-transformation consultancy.

Evaluate each lead against our Ideal Customer Profile (ICP):
  • Enterprise (≥ 500 employees)
  • Technology, energy, or financial services sector
  • Active digital-transformation or AI initiative
  • Budget authority confirmed or strongly signalled
  • Q1 / Q2 urgency signals present

You MUST respond with valid JSON only — no markdown, no prose outside the JSON.

{
  "recommendation": "<one sentence: HIGH/MEDIUM/LOW priority lead and primary reason>",
  "confidence_score": <0.0-1.0>,
  "reasoning": ["<step 1>", "<step 2>", "<step 3>"],
  "assumptions": ["<assumption if data is missing>"],
  "lead_score": "HIGH|MEDIUM|LOW",
  "priority_tier": "URGENT|STANDARD|NURTURE|DISQUALIFY",
  "icp_fit_score": <0-100>,
  "icp_gaps": ["<gap 1>"],
  "next_action": "Schedule demo|Send case study|Add to nurture|Disqualify"
}

Lower confidence_score when critical fields are missing.
Never fabricate company data.
"""

    def qualify_lead(
        self, lead_data: Dict[str, Any], workflow_id: str
    ) -> AgentDecision:
        """
        Qualify a lead.

        Args:
            lead_data: Dict with keys: company, title, industry, employee_count,
                       budget_signals, engagement_history, source, pain_points
            workflow_id: Links this decision to a workflow run.
        """
        msg = f"""\
Qualify this lead against our ICP:

Company:           {lead_data.get("company", "Unknown")}
Contact Title:     {lead_data.get("title", "Unknown")}
Industry:          {lead_data.get("industry", "Unknown")}
Employee Count:    {lead_data.get("employee_count", "Unknown")}
Budget Signals:    {lead_data.get("budget_signals", "None provided")}
Engagement:        {lead_data.get("engagement_history", "None")}
Source:            {lead_data.get("source", "Unknown")}
Pain Points:       {lead_data.get("pain_points", "Not captured")}

Score this lead and provide a recommendation with full reasoning.
"""
        # Redact PII in the audit snapshot — store only non-identifying fields
        audit_snapshot = {
            "industry": lead_data.get("industry"),
            "employee_count": lead_data.get("employee_count"),
            "source": lead_data.get("source"),
            "company": lead_data.get("company", "[REDACTED]"),
            # Prospect names / personal contact info excluded
        }

        return self._run(
            user_message=msg,
            workflow_id=workflow_id,
            input_data=audit_snapshot,
            sensitivity_flag=True,   # Always — lead data contains prospect PII
            data_sources=["CRM inbound data", "Engagement history", "Company signals"],
        )


# ── Agent 2: Account Manager ──────────────────────────────────────────────────

class AccountManagerAgent(SalesAgentBase):
    """
    Analyses account health and surfaces upsell / cross-sell / churn-risk signals.

    Sensitivity flag = True if ARR > $100 K (high-value account).
    """

    AGENT_NAME = "account_manager"

    SYSTEM_PROMPT = """\
You are the Account Manager AI for First Genesis.

Analyse the account health data and recommend the most important next action.

You MUST respond with valid JSON only — no markdown, no prose outside the JSON.

{
  "recommendation": "<one sentence action recommendation>",
  "confidence_score": <0.0-1.0>,
  "reasoning": ["<step 1>", "<step 2>"],
  "assumptions": ["<assumption if data is missing>"],
  "account_health": "HEALTHY|AT_RISK|CHURNING",
  "churn_probability": <0.0-1.0>,
  "upsell_opportunity": "HIGH|MEDIUM|LOW|NONE",
  "upsell_products": ["<product 1>"],
  "recommended_actions": ["<action 1>", "<action 2>"],
  "risk_flags": ["<flag 1>"],
  "next_qbr_topics": ["<topic 1>"]
}

Flag churn probability above 0.5 prominently in risk_flags.
Never recommend specific pricing — that requires human approval.
"""

    def analyze_account(
        self, account_data: Dict[str, Any], workflow_id: str
    ) -> AgentDecision:
        """
        Analyse account health and identify growth or risk signals.

        Args:
            account_data: Dict with keys: account_name, arr, renewal_date,
                          support_tickets_30d, usage_metrics, last_contact, sentiment
            workflow_id: Links this decision to a workflow run.
        """
        arr = account_data.get("arr", 0)
        msg = f"""\
Analyse this account:

Account:                   {account_data.get("account_name", "Unknown")}
ARR:                       ${arr:,}
Contract Renewal:          {account_data.get("renewal_date", "Unknown")}
Support Tickets (30 days): {account_data.get("support_tickets_30d", 0)}
Product Usage:             {account_data.get("usage_metrics", "No data")}
Last Contact:              {account_data.get("last_contact", "Unknown")}
Customer Sentiment:        {account_data.get("sentiment", "Unknown")}
NPS Score:                 {account_data.get("nps", "Unknown")}

Assess account health, identify churn risk, and surface growth opportunities.
"""
        audit_snapshot = {
            "account_name": account_data.get("account_name", "[REDACTED]"),
            "arr_tier": "ENTERPRISE" if arr > 100_000 else "MID_MARKET" if arr > 25_000 else "SMB",
            "renewal_date": account_data.get("renewal_date"),
            "support_tickets_30d": account_data.get("support_tickets_30d"),
        }

        return self._run(
            user_message=msg,
            workflow_id=workflow_id,
            input_data=audit_snapshot,
            sensitivity_flag=arr > 100_000,   # High-value accounts are sensitive
            data_sources=["CRM account data", "Support ticket system", "Usage analytics", "NPS data"],
        )


# ── Agent 3: Forecast Agent ───────────────────────────────────────────────────

class ForecastAgent(SalesAgentBase):
    """
    Generates risk-adjusted pipeline forecasts from aggregate CRM data.

    Sensitivity flag = False — this agent receives aggregate / anonymised data
    (output of the lead qualifier after redaction), not raw PII.
    """

    AGENT_NAME = "forecast_agent"

    SYSTEM_PROMPT = """\
You are the Sales Forecast Agent for First Genesis.

Analyse the pipeline data and generate a risk-adjusted revenue forecast.

You MUST respond with valid JSON only — no markdown, no prose outside the JSON.

{
  "recommendation": "<one sentence forecast summary>",
  "confidence_score": <0.0-1.0>,
  "reasoning": ["<step 1>", "<step 2>"],
  "assumptions": ["<assumption 1>"],
  "forecast_period": "<e.g. Q1 2026>",
  "forecast_value_usd": <number>,
  "confidence_range": {"low": <number>, "mid": <number>, "high": <number>},
  "risk_adjusted_value": <number>,
  "pipeline_health": "STRONG|MODERATE|WEAK",
  "coverage_ratio": <number>,
  "key_risks": ["<risk 1>"],
  "required_actions": ["<action 1>"]
}

Coverage ratio = pipeline value ÷ quota.  Flag deals with no activity > 30 days.
Use weighted probability: STAGE_WEIGHTS = {prospect:0.10, qualified:0.25, proposal:0.50, negotiation:0.75, closed_won:1.0}
"""

    def generate_forecast(
        self, pipeline_data: Dict[str, Any], workflow_id: str
    ) -> AgentDecision:
        """
        Generate a pipeline forecast.

        Args:
            pipeline_data: Dict with keys: period, total_pipeline, open_opps,
                           weighted_pipeline, stage_breakdown, win_rate, avg_cycle_days,
                           quota (optional)
            workflow_id: Links this decision to a workflow run.
        """
        msg = f"""\
Generate a revenue forecast:

Period:            {pipeline_data.get("period", "Q1 2026")}
Total Pipeline:    ${pipeline_data.get("total_pipeline", 0):,}
Open Opportunities:{pipeline_data.get("open_opps", 0)}
Weighted Pipeline: ${pipeline_data.get("weighted_pipeline", 0):,}
Quota:             ${pipeline_data.get("quota", 0):,}
Historical Win %:  {pipeline_data.get("win_rate", 0)}%
Avg Deal Cycle:    {pipeline_data.get("avg_cycle_days", 0)} days

Stage Breakdown:
{json.dumps(pipeline_data.get("stage_breakdown", {}), indent=2)}

Produce a risk-adjusted forecast with high / mid / low confidence range.
"""
        audit_snapshot = {
            "period": pipeline_data.get("period"),
            "total_pipeline": pipeline_data.get("total_pipeline"),
            "open_opps": pipeline_data.get("open_opps"),
            "quota": pipeline_data.get("quota"),
        }

        return self._run(
            user_message=msg,
            workflow_id=workflow_id,
            input_data=audit_snapshot,
            sensitivity_flag=False,   # Aggregate data — no PII
            data_sources=["CRM pipeline export", "Historical win-rate data", "Stage distribution"],
        )


# ── Agent 4: Competitor Intel ─────────────────────────────────────────────────

class CompetitorIntelAgent(SalesAgentBase):
    """
    Competitive positioning analysis and objection-handling for active deals.

    Sensitivity flag = True if deal value > $50 K.
    """

    AGENT_NAME = "competitor_intel"

    SYSTEM_PROMPT = """\
You are the Competitive Intelligence Agent for First Genesis.

Analyse the competitive situation in an active deal and recommend positioning.

You MUST respond with valid JSON only — no markdown, no prose outside the JSON.

{
  "recommendation": "<one sentence positioning recommendation>",
  "confidence_score": <0.0-1.0>,
  "reasoning": ["<step 1>", "<step 2>"],
  "assumptions": ["<assumption if intel is incomplete>"],
  "primary_competitor": "<company name>",
  "threat_level": "HIGH|MEDIUM|LOW",
  "win_probability": <0-100>,
  "our_advantages": ["<advantage 1>", "<advantage 2>"],
  "competitor_strengths": ["<strength 1>"],
  "differentiation_points": ["<point 1>", "<point 2>"],
  "objection_responses": {"<objection>": "<response>"},
  "recommended_proof_points": ["<proof point 1>"],
  "intel_gaps": ["<missing info>"]
}

Flag any intel gaps as assumptions.
Win probability should reflect evidence quality — lower if intel is incomplete.
"""

    def analyze_competition(
        self, deal_data: Dict[str, Any], workflow_id: str
    ) -> AgentDecision:
        """
        Analyse competitive landscape for a deal.

        Args:
            deal_data: Dict with keys: stage, competitor, value, pain_points,
                       eval_criteria, our_position, timeline
            workflow_id: Links this decision to a workflow run.
        """
        deal_value = deal_data.get("value", 0)
        msg = f"""\
Analyse competitive situation:

Deal Stage:          {deal_data.get("stage", "Unknown")}
Competitor:          {deal_data.get("competitor", "Unknown")}
Deal Value:          ${deal_value:,}
Customer Pain Points:{deal_data.get("pain_points", "Unknown")}
Evaluation Criteria: {deal_data.get("eval_criteria", "Unknown")}
Our Current Position:{deal_data.get("our_position", "Unknown")}
Decision Timeline:   {deal_data.get("timeline", "Unknown")}

Provide competitive positioning and objection-handling strategy.
"""
        audit_snapshot = {
            "stage": deal_data.get("stage"),
            "competitor": deal_data.get("competitor"),
            "deal_value_tier": "ENTERPRISE" if deal_value > 100_000 else "MID" if deal_value > 25_000 else "SMB",
        }

        return self._run(
            user_message=msg,
            workflow_id=workflow_id,
            input_data=audit_snapshot,
            sensitivity_flag=deal_value > 50_000,   # High-value deals are sensitive
            data_sources=["Deal CRM data", "Competitive battlecards", "Win/loss analysis"],
        )


# ── Utility ───────────────────────────────────────────────────────────────────

def _to_list(val: Any) -> List[str]:
    """Ensure a value is a list of strings."""
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str):
        return [val]
    return [str(val)]
