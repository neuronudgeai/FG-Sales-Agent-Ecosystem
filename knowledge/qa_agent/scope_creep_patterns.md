# QA Agent — Scope Creep Detection Patterns

## Common Scope Creep Signals

| Signal | Severity | Action |
|---|---|---|
| New UI screens not in charter | WARN | Document delta, flag for re-approval |
| Third-party integration added mid-project | WARN | Assess timeline + budget impact |
| Performance target raised post-charter | WARN | Check if NFR test plan updated |
| Data migration added after kickoff | FAIL | Full scope review required |
| New user role type added | WARN | Security and access control review |
| "Quick fix" requests from client | WARN | Each one is undocumented scope |
| Feature labeled "out of scope" now included | FAIL | Needs formal change request |

## How to Compare Against Original Scope

1. Extract the charter's scope statement and WBS
2. List all deliverables actually produced
3. For each extra item: classify as scope creep or legitimate evolution
4. Calculate delta: `(extra_items / original_items) * 100`
5. Apply thresholds: > 5% = WARN, > 20% = FAIL

## Budget Variance Checks

```
budget_variance_pct = ((actual_spend - charter_budget) / charter_budget) * 100

< 0%   → under budget (note in report, no flag)
0–5%   → within tolerance (PASS)
5–10%  → WARN — flag for review
> 10%  → FAIL — Budget Escalation gate required
```

## Timeline Variance Checks

```
timeline_variance_pct = ((actual_weeks - charter_weeks) / charter_weeks) * 100

< 10%  → within tolerance
10–20% → WARN
> 20%  → FAIL — re-baseline required
```
