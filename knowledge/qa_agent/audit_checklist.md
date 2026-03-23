# QA Agent — Pre-Delivery Audit Checklist

## Mandatory Deliverables (all must be PRESENT before QA can PASS)

| # | Deliverable | Check |
|---|---|---|
| 1 | Project charter (Trice-approved) | Present / Missing |
| 2 | Requirements specification (Kiera-approved) | Present / Missing |
| 3 | Technical architecture document | Present / Missing |
| 4 | UI/UX designs or wireframes | Present / Missing |
| 5 | QA test plan | Present / Missing |
| 6 | Test results (pass/fail per acceptance criterion) | Present / Missing |
| 7 | UAT sign-off from client | Present / Missing |
| 8 | Deployment runbook | Present / Missing |

## Scope Creep Detection Rules

Flag as WARN when:
- Any deliverable was not in the original charter scope
- Feature count increased by > 5% from charter
- Timeline slipped > 10% from charter
- Budget consumed > 105% of charter amount

Flag as FAIL when:
- New deliverable added without re-approval
- Scope change > 20% without documented change request
- A mandatory deliverable is absent

## Quality Score Calculation

```
quality_score = (passed_checks / total_checks) * 100

READY:      quality_score >= 85 AND no FAIL items
NEEDS WORK: quality_score < 85 OR any FAIL item present
```

## Readiness Decision Matrix

| Condition | Decision |
|---|---|
| All mandatory deliverables present + quality ≥ 85 + no FAIL | READY |
| 1–2 WARN items, no FAIL, quality ≥ 75 | READY WITH NOTES |
| Any FAIL item OR quality < 75 | NEEDS WORK |
| Missing > 2 mandatory deliverables | BLOCKED |

## Lessons Learned — QA
- **LESSON-QA-001:** UAT sign-off is the most commonly missing item. Chase this
  first — it blocks delivery even when everything else passes.
- **LESSON-QA-002:** Technical architecture docs are often drafts. Verify they
  reflect the actual build, not the original design. Mismatches = scope creep.
- **LESSON-QA-003:** Check acceptance criteria explicitly. "Tests pass" is not
  enough — each FR and NFR must have a documented test result linked to it.
- **LESSON-QA-004:** If budget variance > 5%, always flag for Budget Escalation
  gate before recommending READY — even if quality is high.
