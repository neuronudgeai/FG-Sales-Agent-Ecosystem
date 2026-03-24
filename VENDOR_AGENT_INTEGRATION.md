# Vendor Agent Integration Guide

**Version:** 1.1 | **Updated:** March 2026 | **File:** `vendor_agent_extension.py`

---

## Overview

The `VendorAgentExtension` monitors contractor capacity and project budget health for First Genesis.
Unlike the base Vendor Agent which sends alerts directly, this extension **never notifies stakeholders
without explicit human approval**. Every alert and hiring recommendation is packaged as an
`ApprovalRequest` and routed through `ManagerBot` before anyone is contacted.

---

## Step 1: Install Dependencies

```bash
pip install anthropic python-dotenv flask flask-socketio flask-cors
```

---

## Step 2: Set Environment Variables

```bash
export ANTHROPIC_API_KEY="sk-..."

# Optional — for email approval notifications
export OUTLOOK_SENDER="alerts@firstgenesis.com"
export OUTLOOK_PASSWORD="your-smtp-password"
export OUTLOOK_SMTP_SERVER="smtp.office365.com"

# Optional budget
export DAILY_BUDGET="5.00"
```

---

## Step 3: Test Basic Integration

```python
from vendor_agent_extension import VendorAgentExtension
from claude_code_agent_ecosystem import ApprovalDecision

vendor = VendorAgentExtension()

# Run capacity check — returns ApprovalRequest or None
cap_req = vendor.daily_capacity_check_with_approval()

if cap_req:
    print(f"Approval ID: {cap_req.approval_id}")
    print(f"Status: {cap_req.approval_status.value}")
    # → PENDING until a human decides

# Run hiring recommendation check
hire_req = vendor.recommend_hiring_with_approval()

# Generate Yubi SLA scorecard (no approval needed — read-only)
scorecard = vendor.generate_scorecard("Yubi", {
    "on_time_delivery_pct": 98.0,
    "quality_score":        92.0,
    "response_time_hours":  18.0,
    "budget_variance_pct":   3.0,
})
print(f"Yubi SLA: {scorecard['overall']}")
```

---

## Step 4: Configure Human Approvers

Before deployment, define who approves each type of alert.

### Approver Authority Levels

| Authority | Approver | Decision Scope |
|-----------|----------|---------------|
| PM / BA Lead | Kiera Phipps | Capacity reassignment, scope <$5K |
| CTO / PMO | Trice Johnson | Budget >$5K, timeline >3 days, hiring |
| HR Lead | Internal HR | Initiates hiring once CTO approves |
| Executive Sponsor | Patrick Watty | Critical blockers, contracts, >$50K |

### Configuration

1. Update `StageGateManager.STAGE_GATES` in `claude_code_agent_ecosystem.py` with each
   approver's email address for the relevant gate.

2. Map approver names to notification channels in `.env`:

```bash
# Per-approver routing (JSON)
APPROVER_CHANNELS='{"tjohnson@firstgenesis.com":["slack","email"],"k.phipps@firstgenesis.com":["email"]}'
```

3. Set SLA timers in `VendorAgentExtension.SLA_TARGETS`:

```python
SLA_TARGETS = {
    "on_time_delivery_pct": 95.0,   # Yubi target
    "quality_score":        90.0,
    "response_time_hours":  24.0,
    "budget_variance_pct":   5.0,
}
```

---

## Step 5: Set Up Approval Gates

### Decision paths

```
VendorAgentExtension.daily_capacity_check_with_approval()
        │
        └── AlertsForApproval.package_capacity_alerts()
                │
                ▼
        ApprovalRequest (PENDING)
                │
        ManagerBot.notify_approver_for_review()  ← escalates to human
                │
        human replies APPROVE / REVISE / REJECT
                │
  ┌─────────────┼─────────────┐
  ▼             ▼             ▼
APPROVE       REVISE        REJECT
  │             │             │
send to       return to     escalate to
PM Lead/CTO   Vendor Agent  ManagerBot for
              for revision  decision recon-
                            sideration
```

### 5-Point Checklist (auto-validated before approval email is sent)

The `ApprovalChecklist` validates every `ApprovalRequest` on these criteria:

1. **Context clear** — stakeholders understand WHY the alert was raised
2. **Action items clear** — PM Lead / CTO know their exact task
3. **Deadlines set** — deadlines are present and in the future
4. **Resources identified** — contacts and documents provided
5. **Confirmations defined** — confirmation method is specified per person

A failing checklist blocks the approval email until all 5 checks pass.

### Escalation timelines

| Urgency | Response SLA |
|---------|-------------|
| CRITICAL | 2 hours |
| HIGH | 24 hours |
| MEDIUM | 48 hours |
| LOW | 1 week |

Set in `ApprovalRequest.deadlines` when building the package.

---

## Step 6: Test Approval Workflow

Run all four scenarios before going to production.

### Scenario 1 — APPROVE path

```python
from vendor_agent_extension import VendorAgentExtension
from claude_code_agent_ecosystem import ApprovalDecision

vendor = VendorAgentExtension()
req = vendor.daily_capacity_check_with_approval()

if req:
    result = vendor.process_approval_decision(
        req, ApprovalDecision.APPROVE, "Trice Johnson", "Acknowledged"
    )
    assert result["status"] == "APPROVED"
    # Verify notifications were sent
    confs = vendor.notif.track_developer_confirmations(req.approval_id)
    print(f"Pending confirmations: {list(confs.keys())}")
```

Expected: PM Lead and CTO each receive a notification; confirmations tracked in DB.

---

### Scenario 2 — REVISE path

```python
req = vendor.daily_capacity_check_with_approval()
if req:
    result = vendor.process_approval_decision(
        req, ApprovalDecision.REVISE, "Trice Johnson",
        "Please include Chevron budget detail before sending"
    )
    assert result["status"] == "REVISION_REQUESTED"
    print(f"Revision notes: {result['notes']}")
    # → Vendor Agent regenerates alert with additional detail, resubmits
```

Expected: `ApprovalRequest.revision_count` increments; workflow resets to PENDING.

---

### Scenario 3 — REJECT path

```python
req = vendor.recommend_hiring_with_approval()
if req:
    result = vendor.process_approval_decision(
        req, ApprovalDecision.REJECT, "Trice Johnson",
        "Budget freeze in effect — no new hires this quarter"
    )
    assert result["status"] == "REJECTED"
    # ManagerBot escalates back for decision reconsideration
```

Expected: `ApprovalRequest.approval_status == ApprovalStatus.REJECTED`; DB flagged;
ManagerBot logged the escalation.

---

### Scenario 4 — Developer confirmations

```python
# After APPROVE (Scenario 1)
vendor.notif.receive_developer_confirmation("PM Lead", req.approval_id)
vendor.notif.receive_developer_confirmation("CTO",     req.approval_id)

confs = vendor.notif.track_developer_confirmations(req.approval_id)
for dev, status in confs.items():
    print(f"{dev}: acknowledged={status['acknowledged']}")
```

Expected: Both developers show `acknowledged=True`; timestamps saved to
`developer_confirmations` DB table.

---

## Database Tables

The approval flow writes to four SQLite tables in `fg_workflows.db`:

| Table | Purpose |
|-------|---------|
| `approval_requests` | One row per `ApprovalRequest`; checklist results + status |
| `notification_log` | One row per developer per sent notification |
| `developer_confirmations` | Tracks who has acknowledged |
| `workflows` | Stage gate state machine (used by `StageGateManager`) |

Query examples:

```sql
-- All pending vendor alerts
SELECT approval_id, decision_context, created_timestamp
FROM approval_requests
WHERE approval_status = 'pending'
  AND approval_id LIKE 'CAP_%' OR approval_id LIKE 'HIRE_%';

-- Unacknowledged notifications older than 24h
SELECT nl.approval_id, nl.developer, nl.sent_timestamp
FROM notification_log nl
LEFT JOIN developer_confirmations dc
  ON nl.approval_id = dc.approval_id AND nl.developer = dc.developer
WHERE dc.acknowledged IS NULL OR dc.acknowledged = 0
  AND datetime(nl.sent_timestamp) < datetime('now', '-24 hours');
```

---

## Verification Checklist

Before going live, confirm every item:

- [ ] `ApprovalRequest` created with all 5 elements for every alert
- [ ] `ApprovalChecklist` passes (5/5 checks) before email is sent
- [ ] No notification reaches PM Lead / CTO / HR Lead without `approval_status == APPROVED`
- [ ] REVISE path increments `revision_count` and resets to PENDING
- [ ] REJECT path calls `ManagerBot.escalate_if_rejected()` and flags DB
- [ ] Developer confirmations tracked in `developer_confirmations` table
- [ ] Audit trail visible in `approval_requests` table
- [ ] `daily_capacity_check_with_approval()` returns `None` when no alerts
- [ ] `recommend_hiring_with_approval()` returns `None` when no recommendations
- [ ] SLA scorecard still works independently (no approval gate needed)

---

## Files Reference

| File | Purpose |
|------|---------|
| `vendor_agent_extension.py` | This module — `VendorAgentExtension`, `AlertsForApproval`, `ContractorManager` |
| `claude_code_agent_ecosystem.py` | Canonical approval classes — `ApprovalRequest`, `ApprovalChecklist`, `PMNotificationEngine`, `ManagerBot` |
| `aura_pm_ba_agents.py` | AURA-scoped PM + BA agents using the same approval gate pattern |
| `dashboard_server.py` | Flask server — exposes `approval_requests` via REST + WebSocket |
| `dashboard.html` | Web UI — Chat view allows direct conversation with any agent |
