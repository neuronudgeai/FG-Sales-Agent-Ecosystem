# Vendor Agent — Scorecard Template

## Weekly Scorecard Structure

```json
{
  "vendor_name": "Yubi",
  "period": "Week of YYYY-MM-DD",
  "overall_score": 92,
  "sla_status": "PASS",
  "metrics": {
    "on_time_delivery_pct": {"actual": 98, "target": 95, "status": "PASS"},
    "quality_score": {"actual": 91, "target": 90, "status": "PASS"},
    "response_time_hours": {"actual": 18, "target": 24, "status": "PASS"},
    "budget_variance_pct": {"actual": 2, "target": 5, "status": "PASS"}
  },
  "escalation_required": false,
  "action_items": [],
  "trend": "STABLE"
}
```

## Trend Analysis Rules

| Trend | Condition |
|---|---|
| IMPROVING | Score increased ≥ 5 points vs prior week |
| STABLE | Score within ±4 points of prior week |
| DECLINING | Score decreased 5–9 points vs prior week |
| AT_RISK | Score decreased ≥ 10 points vs prior week |

## Action Item Templates (use when metrics miss)

- **Response time WARN:** "Schedule weekly check-in call; set 4-hour response SLA for blockers."
- **Quality WARN:** "Request root cause analysis; add QA checkpoint before next submission."
- **Budget WARN:** "Review spend breakdown; freeze discretionary items pending approval."
- **On-time FAIL:** "Invoke escalation to PMO; request revised delivery plan within 48 hours."
