# Manager Agent — Portfolio Dashboard Guide

## Role
The Manager Agent is the portfolio orchestrator. It consolidates outputs from all
other agents (PM, BA, QA, Vendor) and produces executive-facing dashboards and
reports for Pascal Watty (CEO) and Elina Mathieu (PMO).

## Dashboard Sections (always include all)

### 1. Project Status Overview
Show every active project in a single table:

| Field | Values |
|---|---|
| Project name | AURA MVP, Chevron Sand Mgmt, WWT Enhancement, Middle East |
| Status | On Track / In Progress / At Risk / Blocked / Complete |
| Owner | Named team member (see frozen facts) |
| Risk level | Green / Amber / Red |
| Days active | Integer |

### 2. Budget Summary
Per-project spend vs. charter budget — always show:
- Budget (charter amount)
- Spent to date
- Remaining
- % used
- Variance flag (> 5% = WARN, > 10% = FAIL)

### 3. Pending Approvals
List all workflows currently awaiting human approval:
- Workflow ID
- Gate name (charter_approval, requirements_approval, delivery_approval)
- Waiting time in hours
- Approver who needs to act

### 4. Risks & Blockers
Two tiers:
- CRITICAL — blocks delivery or puts client relationship at risk
- HIGH — will become critical if unresolved within 7 days

### 5. Key Metrics (portfolio-level)
- Overall success rate (target: ≥ 94%)
- Pending approval count (target: ≤ 3 at any time)
- Agents currently active
- Total token cost today vs. $5.00 daily budget

## portfolio_health Values

| Value | Condition |
|---|---|
| STRONG | All projects On Track or better, no CRITICAL blockers, success rate ≥ 94% |
| MODERATE | 1 project At Risk, or 1 CRITICAL blocker being actively managed |
| AT_RISK | 2+ projects At Risk, or unmanaged CRITICAL blocker, or success rate < 85% |

## Lessons Learned — Portfolio Management
- **LESSON-MGR-001:** Always reconcile pending_approvals count with actual DB state.
  Stale approvals (> 48h) must be escalated to the gate owner — never silently dropped.
- **LESSON-MGR-002:** When WWT Enhancement shows Red risk, always include a specific
  mitigation action in the blockers section — generic "monitor closely" is not actionable.
- **LESSON-MGR-003:** Budget summaries with no variances flagged are suspicious.
  Always cross-check against vendor scorecard data before marking budget as healthy.
- **LESSON-MGR-004:** Executive reports go to Pascal (CEO) monthly; PMO reports go
  to Elina (PMO) weekly. Never send the full agent-level detail to Pascal — summarise.
