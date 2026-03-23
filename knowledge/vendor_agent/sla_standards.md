# Vendor Agent — SLA Standards & Scoring

## First Genesis Vendor SLA Targets

| Metric | Target | WARN threshold | FAIL threshold |
|---|---|---|---|
| On-time delivery | ≥ 95% | 90–94% | < 90% |
| Quality score | ≥ 90% | 85–89% | < 85% |
| Response time | < 24 hours | 24–48 hours | > 48 hours |
| Budget variance | < 5% | 5–10% | > 10% |

## Overall Score Calculation

```
sla_score = (
  (on_time / 100) * 30 +     # 30% weight
  (quality / 100) * 35 +     # 35% weight
  (1 - min(response_hours/48, 1)) * 20 +  # 20% weight (0 = 48h, 1 = 0h)
  (1 - min(budget_variance/10, 1)) * 15   # 15% weight (0 = 10%, 1 = 0%)
) * 100

PASS:  sla_score >= 85 AND no FAIL metrics
WARN:  sla_score 70–84 OR any WARN metric
FAIL:  sla_score < 70 OR any FAIL metric
```

## Escalation Rules

Escalate to **PMO (Elina)** when:
- Any single metric hits FAIL threshold
- Two or more metrics hit WARN in the same week
- Vendor misses a hard deadline (like WBT April 30, 2026)

Escalate to **CEO (Pascal)** when:
- Overall SLA FAIL for 2 consecutive weeks
- Budget variance > 15%
- Vendor requests contract renegotiation

## Vendor: Yubi

- **Contract type:** Software delivery (WBT module)
- **Hard deadline:** April 30, 2026
- **Primary contact:** Yubi project lead (confirm via Elina)
- **Known risk:** April 30 deadline is tight — flag any delivery delays immediately
- **Historical performance:** Target baseline — first scorecard in progress

## Lessons Learned — Vendor
- **LESSON-VND-001:** Response time SLA breaches often precede quality issues.
  If response time starts slipping, increase check-in frequency proactively.
- **LESSON-VND-002:** Budget variance compounds — a 3% overage in week 4 often
  becomes 12% by week 10. Flag early and reset expectations.
- **LESSON-VND-003:** "On-time" means merged and deployed, not just submitted.
  Clarify definition with each vendor at contract start.
- **LESSON-VND-004:** Always confirm the April 30 WBT deadline is reflected in
  Yubi's internal sprint plan. External deadlines are often not cascaded internally.
