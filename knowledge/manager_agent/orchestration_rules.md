# Manager Agent — Orchestration Rules

## Agent Sequencing (correct order matters)

```
BA Agent  →  PM Agent  →  QA Agent  →  Vendor Agent  →  Manager Agent
```

- BA Agent runs first (design session → requirements)
- PM Agent runs after BA (charter quality is 15% higher with BA-first pattern)
- QA Agent runs before any customer delivery
- Vendor Agent runs weekly (independent of project phase)
- Manager Agent runs last — consolidates all outputs into the dashboard

## Cross-Agent Dependencies

| Manager needs | From agent | Gate required before consolidation |
|---|---|---|
| Charter status | PM Agent | charter_approval (Trice) |
| Requirements status | BA Agent | requirements_approval (Kiera) |
| QA readiness | QA Agent | None — read QA output directly |
| Vendor health | Vendor Agent | None — read scorecard directly |

## Escalation Routing (Manager Agent owns this)

| Condition | Route to | Channel |
|---|---|---|
| Any CRITICAL blocker | Pascal Watty (CEO) | Email + dashboard alert |
| QA FAIL on any project | Elina Mathieu (PMO) | Email |
| Vendor SLA FAIL | Elina Mathieu (PMO) | Email |
| Budget overage > 10% | Pascal Watty (CEO) | Email |
| Pending approval > 48h | Gate owner (see frozen facts) | Email |
| Portfolio AT_RISK | Pascal Watty (CEO) | Email + weekly report |

## Resource Allocation Rules
- If two projects compete for the same owner, flag as resource conflict in dashboard
- Never allocate a team member to more than 2 active projects simultaneously
- Ron Watty (CTO) is a shared resource — his availability constrains WWT Enhancement

## Output JSON Schema (mandatory keys)

```json
{
  "recommendation": "One-sentence portfolio status",
  "confidence_score": 0.85,
  "reasoning": ["..."],
  "assumptions": ["..."],
  "portfolio_health": "STRONG | MODERATE | AT_RISK",
  "projects_summary": [
    {"name": "...", "status": "...", "owner": "...", "risk": "Green|Amber|Red"}
  ],
  "budget_summary": {
    "total_budget": 0,
    "total_spent": 0,
    "total_remaining": 0,
    "pct_used": 0
  },
  "top_risks": ["risk 1", "risk 2"],
  "pending_decisions": ["workflow_id: gate_name (Xh waiting)"],
  "key_metrics": {
    "success_rate": 0.94,
    "pending_approvals": 0,
    "active_agents": 5,
    "daily_token_cost_usd": 0.20
  }
}
```
