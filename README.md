# FG Sales Agent Ecosystem

A production-grade, containerized, multi-agent AI system built for **First Genesis** to automate and govern the full project delivery lifecycle — from project charter generation through final customer delivery — with human-in-the-loop approval gates, budget enforcement, hallucination detection, and a real-time web dashboard.

---

## Overview

The FG Sales Agent Ecosystem orchestrates five specialized AI agents powered by **Claude (claude-opus-4-6)** through the Anthropic API. Each agent handles a distinct phase of a project engagement. Workflows are gated at five critical checkpoints, requiring human email approval before the system advances. All activity is persisted in SQLite, with costs tracked per agent call and a hallucination guard validating every AI output before it is accepted.

A companion Flask/WebSocket dashboard server (`dashboard_server.py`) provides a real-time command center for monitoring agent status, budget consumption, pending approvals, and collaboration activity.

---

## Repository Structure

```
FG-Sales-Agent-Ecosystem/
├── claude_code_agent_ecosystem.py   # Core agent system + CLI entrypoint
├── dashboard_server.py              # Flask REST + WebSocket dashboard server
├── dashboard.html                   # Static dashboard front-end (standalone)
└── README.md
```

---

## Agents

| Agent | Role | Daily Call Limit | Daily Budget |
|---|---|---|---|
| `pm_agent` | Project Manager — generates charter, WBS, kickoff checklist | 2 | $0.10 |
| `ba_agent` | Business Analyst — requirements spec, traceability matrix, design sessions | 3 | $0.15 |
| `qa_agent` | QA Lead — pre-delivery audit checklist, scope creep detection | 1 | $0.10 |
| `vendor_agent` | Vendor/Partner Manager — SLA templates, performance scorecards | 1 | $0.05 |
| `manager_agent` | Portfolio Manager — cross-project dashboard, risk and blocker reporting | 1 | $0.10 |

---

## Stage Gates

Every workflow pauses at one of five **stage gates** and sends an approval email via Outlook SMTP before proceeding. The system resumes automatically once a human replies with `APPROVED` or `REJECTED`.

| Gate | Description | Approver | Timeout |
|---|---|---|---|
| `charter_approval` | Project Charter Review | tjohnson@firstgenesis.com | 24 h |
| `requirements_approval` | Requirements Specification Review | k.phipps@firstgenesis.com | 12 h |
| `qa_audit_approval` | QA Audit & Pre-Delivery Approval | emaiteu@firstgenesis.com | 6 h |
| `delivery_approval` | Final Approval Before Customer Delivery | tjohnson@firstgenesis.com | 2 h |
| `budget_escalation` | Budget Alert — Approval to Continue | pwatty@firstgenesis.com | 1 h |

---

## Key Components

### Workflow Persistence (SQLite)

`WorkflowDatabase` maintains four tables:

- **`workflows`** — full state machine for every workflow (status, stage gate, content hash, approver)
- **`approvals`** — immutable audit log of every human decision
- **`agent_calls`** — token counts, cost, status, and output hash for every Claude API call
- **`hallucination_flags`** — flagged outputs with reason and snippet

### Budget Enforcer

`BudgetEnforcer` enforces a **$5.00 daily system-wide hard cap**, plus per-agent daily spend and call limits. Every agent call is pre-checked against these limits before the Anthropic API is invoked. An 80% threshold triggers a warning log.

### Hallucination Guard

`HallucinationGuard` validates every Claude response against:

- **Frozen facts** — e.g. the AURA project timeline must be "3 months"; contradictory timelines are rejected.
- **Impossible claims** — regex patterns catch fabricated events (Chevron approval, AURA completion, unauthorized hires, company failure).
- **Soft warnings** — project claims without a document reference are logged for review.

Rejected outputs are recorded in `hallucination_flags` and the agent call is marked `rejected_hallucination`.

### Email Gateway

`EmailGateway` sends approval requests via Outlook SMTP (`smtp.office365.com:587`). Emails include a full content summary and detail block. Approval responses are parsed by scanning the reply body for `APPROVED` or `REJECTED`.

### Knowledge Library

`KnowledgeLibrary` (a second SQLite database) stores agent skills, workflow execution patterns, and lessons learned, enabling the dashboard to display learning metrics and replay successful workflow templates.

### Dashboard Server

`dashboard_server.py` is a full Flask application with:

- **WebSocket** real-time push (every 3 seconds via `flask-socketio`)
- **REST API** endpoints: `/api/dashboard`, `/api/agents`, `/api/patterns`, `/api/lessons`, `/api/communicate`
- **Alert system** — monitors error rates, budget thresholds, and execution duration
- **Metrics export** — CSV and PDF (via ReportLab) reports
- **Audit trail** — access logs and change history
- **Collaboration** — comments, voting, and versioning per workflow

---

## Prerequisites

- Python 3.9+
- An Anthropic API key (claude-opus-4-6 access)
- Outlook / Microsoft 365 SMTP credentials
- pip packages:

```bash
pip install anthropic flask flask-socketio flask-cors flask-sqlalchemy python-socketio reportlab
```

---

## Configuration

Export the following environment variables before running:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OUTLOOK_SENDER="your-email@firstgenesis.com"
export OUTLOOK_PASSWORD="your-password"

# Optional (dashboard server)
export SECRET_KEY="your-secret"
export DATABASE_URL="sqlite:////path/to/fg_dashboard.db"
```

The system writes two SQLite databases by default:

- `/home/claude/fg_workflows.db` — workflow state and audit data
- `/home/claude/fg_knowledge.db` — agent learning and pattern library

And a log file at `/home/claude/fg_agents.log`.

To use different paths, edit the `db_path` arguments in `WorkflowDatabase.__init__` and `KnowledgeLibrary.__init__`.

---

## Usage

### Run the PM Agent (starts a workflow)

```bash
python claude_code_agent_ecosystem.py run_pm_agent
```

This generates a project charter for the AURA MVP engagement, pauses at the `charter_approval` gate, and sends an approval email to the configured recipient.

### Check Pending Approvals

```bash
python claude_code_agent_ecosystem.py check_approvals
```

### Resume Approved Workflows

```bash
python claude_code_agent_ecosystem.py resume_workflows
```

### Process an Approval Manually

```bash
python claude_code_agent_ecosystem.py process_approval \
  --workflow-id pm_AURA_MVP_20260406_140000 \
  --approver tjohnson@firstgenesis.com \
  --decision approved \
  --feedback "Charter looks good, proceed"
```

### Budget Status

```bash
python claude_code_agent_ecosystem.py budget_status
```

### Audit Hallucination Flags

```bash
python claude_code_agent_ecosystem.py audit_hallucinations
```

### Token Strategy Reports

```bash
python claude_code_agent_ecosystem.py show_budget_model
python claude_code_agent_ecosystem.py show_cost_breakdown
python claude_code_agent_ecosystem.py show_optimization_impact
python claude_code_agent_ecosystem.py project_monthly_cost
python claude_code_agent_ecosystem.py show_executive_summary
python claude_code_agent_ecosystem.py token_dashboard
```

### Dashboard Demo (no API key required)

```bash
python claude_code_agent_ecosystem.py demo_dashboard
```

### Start the Dashboard Server

```bash
python dashboard_server.py
# Open http://localhost:5000/dashboard
# Admin panel: http://localhost:5000/admin
# API base: http://localhost:5000/api/
```

---

## Workflow Lifecycle

```
run_pm_agent
     │
     ▼
PM Agent generates charter (Claude API)
     │
     ├─ Budget check ──► REJECTED if over limit
     ├─ Hallucination guard ──► REJECTED if invalid output
     │
     ▼
Pause at charter_approval gate
Email sent to approver
     │
     ▼
Human replies APPROVED / REJECTED
     │
     ├─ REJECTED → workflow.status = rejected (stops)
     │
     ▼
resume_workflows picks up approved workflow
Agent proceeds to next phase
```

---

## Cost Model

Token pricing is based on **claude-opus-4-6** rates:

| Token Type | Rate |
|---|---|
| Input | $5.00 / 1M tokens |
| Output | $25.00 / 1M tokens |
| Cache Write | $0.50 / 1M tokens |
| Cache Read | $0.50 / 1M tokens |

The system-wide daily hard cap is **$5.00**. Per-agent limits are enforced on top of the global cap.

---

## Frozen Facts (Hallucination Guard Reference)

The following facts are hardcoded and any agent output contradicting them will be rejected:

| Key | Value |
|---|---|
| AURA project timeline | 3 months |
| AURA client | Malcolm Goodwin |
| Daily AI budget | $5 USD |
| PM | Kiera Phipps |
| CTO | Ron Watty |
| CDO | Trice Johnson |
| PMO | Elina Mathieu |
| CEO | Pascal Watty |
| WBT vendor | Yubi |
| WBT deadline | April 30, 2026 |

---

## License

This repository is proprietary to **First Genesis**. All rights reserved.
