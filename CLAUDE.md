# 🚀 First Genesis Multi-Agent Ecosystem: Complete Guide

**Version:** 1.0 | **Status:** Production-Ready | **Last Updated:** March 17, 2026

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Agent Architecture](#agent-architecture)
3. [File Structure](#file-structure)
4. [Dependencies & Setup](#dependencies--setup)
5. [Commands & Testing](#commands--testing)
6. [API Reference](#api-reference)
7. [Example Prompts](#example-prompts)
8. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Minimum Setup (5 minutes)

```bash
# 1. Clone/download all files
cd first_genesis
ls -la  # Should see all files in /mnt/user-data/outputs

# 2. Set environment variables
export ANTHROPIC_API_KEY="sk-..."
export GMAIL_SENDER="your@gmail.com"
export GMAIL_PASSWORD="app-password"
export DAILY_BUDGET="5.00"

# 3. Install dependencies
pip install anthropic python-dotenv flask flask-socketio flask-cors reportlab

# 4. Start dashboard server
python dashboard_server.py
# Server runs on http://localhost:5000

# 5. In another terminal, test agents
python claude_code_agent_ecosystem.py run_pm_agent \
  --project "AURA MVP" \
  --client "Malcolm Goodwin"

# 6. Open dashboard
open http://localhost:5000/admin
```

> **You're up and running in 5 minutes!**

---

## Agent Architecture

### Overview

5 Autonomous Agents working together in a portfolio management ecosystem:

```
┌─────────────────────────────────────────────────────────┐
│         First Genesis Multi-Agent System               │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  PM Agent ──→ BA Agent ──→ QA Agent ──→ Manager Agent │
│  ▲                                          │          │
│  │─────────── Vendor Agent ──────────────←─┘          │
│                                                         │
│  All agents communicate via Message Bus                │
│  All activity logged to Knowledge Library              │
│  All decisions tracked in Audit Trail                  │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

### 1. PM Agent (Project Manager)

**Role:** Project setup, charter creation, work breakdown structure

**Responsibilities:**
- Generate project charter
- Create work breakdown structure (WBS)
- Establish project timeline
- Identify risks and mitigation strategies
- Track project status
- Report to stakeholders

**Input Templates:**
- Project metadata (client, budget, timeline, scope)
- Design session transcripts
- Stakeholder requirements

**Output:**
- Project charter (approved by Trice)
- WBS (work breakdown structure)
- Project kickoff checklist
- Risk register

**Skill Progression:**
- Novice: First charter (1 success)
- Intermediate: 5 successful charters
- Advanced: 20 successful charters
- Expert: 50+ successful charters (88.5s avg execution)

**Example Usage:**

```python
pm_agent = PMAgent(templates=TEMPLATES["pm_agent"])
charter, workflow_id = pm_agent.generate_charter({
    "project": "AURA MVP",
    "client": "Malcolm Goodwin",
    "budget": 150000,
    "timeline": 12,
    "scope": "Silhouette technology + 3D mesh design"
})
```

---

### 2. BA Agent (Business Analyst)

**Role:** Requirements analysis, design session support, traceability

**Responsibilities:**
- Process design session transcripts
- Extract functional requirements (FR)
- Identify non-functional requirements (NFR)
- Create requirements traceability matrix
- Support design sessions
- Validate requirements completeness

**Input:**
- Design session transcripts (Zoom, meeting notes)
- Project charter (for context)
- Stakeholder feedback

**Output:**
- Requirements specification
- Traceability matrix (FR → Design → Test)
- Design session notes
- Change log

**Skill Progression:**
- Novice: First requirements extraction
- Intermediate: 5 successful extractions
- Advanced: 20+ extractions with 90%+ accuracy
- Expert: 50+ extractions (avg 65s execution)

**Example Usage:**

```python
ba_agent = BAAgent(templates=TEMPLATES["ba_agent"])
requirements, workflow_id = ba_agent.process_design_session(
    transcript="[meeting transcript here]",
    project_name="AURA MVP"
)
```

---

### 3. QA Agent (Quality Assurance)

**Role:** Pre-delivery audit, scope creep detection, quality validation

**Responsibilities:**
- Audit deliverables before customer handoff
- Detect scope creep (comparing to original RFP)
- Verify all acceptance criteria met
- Assess readiness for delivery
- Generate audit report
- Track quality metrics

**Input:**
- Deliverables (charter, requirements, design, code)
- Original project scope
- Acceptance criteria

**Output:**
- QA audit checklist (pass/fail/warn)
- Scope creep detection report
- Readiness assessment (READY / NEEDS WORK)
- Quality metrics

**Alert Triggers:**
- Missing deliverable → FAIL
- Scope change > 5% → WARN
- Budget change > 5% → WARN
- Timeline change > 10% → WARN

**Example Usage:**

```python
qa_agent = QAAgent(templates=TEMPLATES["qa_agent"])
audit_report, workflow_id = qa_agent.audit_deliverable(
    workflow_id="pm_AURA_MVP_..."
)
```

---

### 4. Vendor Agent (Partner Monitor)

**Role:** SLA tracking, vendor performance monitoring, escalation

**Responsibilities:**
- Track vendor deliverables against SLA
- Monitor performance metrics
- Generate vendor scorecards
- Escalate performance issues
- Manage vendor relationships
- Report to leadership

**Input:**
- Vendor SLA documents
- Weekly status updates
- Cost actuals
- Delivery schedules

**Output:**
- Weekly vendor scorecard
- Performance metrics
- Escalation alerts
- Vendor health dashboard

**Metrics Tracked:**
- On-time delivery rate (target: 95%+)
- Quality score (target: 90%+)
- Response time (target: <24h)
- Budget variance (target: <5%)

**Example Usage:**

```python
vendor_agent = VendorAgent(templates=TEMPLATES["vendor_agent"])
scorecard = vendor_agent.generate_scorecard(
    vendor_name="Yubi",
    metrics=weekly_metrics
)
```

---

### 5. Manager Agent (Portfolio Orchestrator)

**Role:** Portfolio dashboard, orchestration, status reporting

**Responsibilities:**
- Consolidate all agent outputs
- Generate portfolio dashboard
- Track cross-project dependencies
- Manage resource allocation
- Generate executive reports
- Identify blockers/risks

**Input:**
- Status from all agents
- Portfolio metadata
- Resource constraints
- Risk register

**Output:**
- Portfolio dashboard
- Executive summary
- Blocker/risk alerts
- Resource allocation plan

**Dashboard Shows:**
- All projects at a glance (status, owner, risk level)
- Budget summary (spent, remaining, % used)
- Pending approvals (count, aging)
- Top blockers (priority)
- Key metrics (success rate, cost, timeline)

**Example Usage:**

```python
manager_agent = ManagerAgent(templates=TEMPLATES["manager_agent"])
dashboard = manager_agent.generate_dashboard()
print(dashboard)  # Full portfolio view
```

---

## File Structure

### Complete Directory Layout

```
first_genesis/
├── 📄 CLAUDE.md                              # This file
│
├── 🚀 CORE SYSTEMS
│   ├── claude_code_agent_ecosystem.py        # 5 agents + templates (600 lines)
│   ├── agent_dashboard_and_command_center.py # Knowledge library (550 lines)
│   ├── dashboard_server.py                   # Flask server + APIs (1000 lines)
│   └── dashboard.html                        # Web UI (800 lines)
│
├── 🔐 COST & SAFETY
│   ├── FG_Agent_Guardrails.py               # Cost control (1200 lines)
│   ├── FG_Agent_Guardrails_With_Email_Gates.py  # Email gates (1500 lines)
│   └── token_strategy_executive.py          # Token dashboard (400 lines)
│
├── 📊 DASHBOARDS & METRICS
│   ├── token_strategy_executive.py          # Token cost calculator
│   ├── dashboard.html                       # Agent monitoring UI
│   ├── DASHBOARD_AND_COMMAND_CENTER_GUIDE.md # Integration guide
│   └── DASHBOARD_SERVER_COMPLETE_GUIDE.md   # Flask server guide
│
├── 📚 DOCUMENTATION
│   ├── CLAUDE_CODE_QUICK_START.md            # 10-min setup
│   ├── CLAUDE_CODE_SETUP_PROMPT.md           # System architecture
│   ├── EMAIL_GATES_QUICK_START.md            # Email approval setup
│   ├── EMAIL_GATES_Documentation.md          # Full email guide
│   ├── FG_Token_Optimization_Strategy.md     # Token budget (10K words)
│   ├── FG_Swimlanes_Agent_Architecture.md    # Project analysis
│   ├── FG_Agent_Build_Checklist.md           # Implementation steps
│   ├── EXECUTIVE_SUMMARY_Token_Strategy.md   # For decision-makers
│   ├── QUICK_REFERENCE_Cheat_Sheet.md        # Daily reference
│   ├── README.md                             # Master index
│   ├── COMPLETE_PACKAGE_SUMMARY.md           # Overview
│   ├── FINAL_DELIVERY_SUMMARY.md             # What you have
│   ├── WHAT_YOU_WERE_MISSING.md              # Feature checklist
│   └── ALL_6_FEATURES_COMPLETE.md            # All features summary
│
├── 🗂️ PROJECT FILES (from First Genesis analysis)
│   ├── meeting_transcript.txt                # Team meeting notes
│   ├── fg_workflows.db                       # SQLite workflow database
│   ├── fg_knowledge.db                       # SQLite knowledge library
│   └── dashboard_server.db                   # SQLite dashboard database
│
└── ⚙️ CONFIG (create these)
    ├── .env                                  # Environment variables
    ├── templates.json                        # Agent templates (optional)
    └── requirements.txt                      # Python dependencies
```

### Key Files Explained

#### `claude_code_agent_ecosystem.py` (600 lines)
- **Purpose:** The 5 agents + template system
- **What it does:** Agents that run workflows with email approval gates
- **Key classes:** PMAgent, BAAgent, QAAgent, VendorAgent, ManagerAgent
- **Use it for:** Running agents, generating deliverables
- **Command:** `python claude_code_agent_ecosystem.py run_pm_agent`

#### `agent_dashboard_and_command_center.py` (550 lines)
- **Purpose:** Knowledge library + skill learning system
- **What it does:** Tracks agent skill progression, captures workflow patterns, stores lessons
- **Key classes:** KnowledgeLibrary, SkillCompoundingEngine, DashboardStateManager
- **Use it for:** Understanding how agents learn and improve
- **Command:** `python agent_dashboard_and_command_center.py`

#### `dashboard_server.py` (1000 lines)
- **Purpose:** Flask server for real-time monitoring
- **What it does:** WebSocket updates, API endpoints, alerts, exports, audit trail, collaboration
- **Key features:** 15+ API endpoints, real-time WebSocket, admin panel
- **Use it for:** Monitoring agents, managing workflows, exporting metrics
- **Command:** `python dashboard_server.py`

#### `dashboard.html` (800 lines)
- **Purpose:** Web UI for agent monitoring
- **What it does:** Real-time agent cards, communication viewer, metrics
- **Key features:** 8 views (overview, communications, workflows, skills, patterns, lessons, performance, costs)
- **Use it for:** Watching agents work
- **Command:** Open `http://localhost:5000/dashboard`

#### `FG_Agent_Guardrails.py` (1200 lines)
- **Purpose:** Cost control + hallucination prevention
- **What it does:** Hard budget stops, token tracking, frozen facts validation
- **Use it for:** Ensuring agents stay within budget and don't hallucinate
- **Key constants:** `DAILY_BUDGET = $5.00`, `FROZEN_FACTS = {...}`

#### `FG_Agent_Guardrails_With_Email_Gates.py` (1500 lines)
- **Purpose:** Email approval gates + workflow state machine
- **What it does:** Pauses agents at stage gates, emails humans for approval, resumes on approval
- **Key classes:** StageGateManager, EmailGateway, WorkflowDB
- **Use it for:** Human-in-the-loop approval before customer delivery

#### `token_strategy_executive.py` (400 lines)
- **Purpose:** Token cost calculation + reporting
- **What it does:** Shows budget allocation, cost breakdown, optimization impact
- **Key commands:** `show_budget_model`, `show_cost_breakdown`, `dashboard`, `show_executive_summary`
- **Use it for:** Understanding token costs and optimization

---

## Dependencies & Setup

### Python Version
- Python 3.9+ required
- Tested on Python 3.11

### Core Dependencies

```
anthropic>=0.25.0          # Claude API
flask>=3.0.0               # Web server
flask-socketio>=5.3.0      # WebSocket support
flask-cors>=4.0.0          # CORS handling
python-socketio>=5.9.0     # SocketIO client
flask-sqlalchemy>=3.1.1    # Database ORM
reportlab>=4.0.0           # PDF generation
python-dotenv>=1.0.0       # Environment variables
```

### Installation

#### Option 1: From Requirements File

```bash
# Create requirements.txt
cat > requirements.txt << EOF
anthropic>=0.25.0
flask>=3.0.0
flask-socketio>=5.3.0
flask-cors>=4.0.0
python-socketio>=5.9.0
flask-sqlalchemy>=3.1.1
reportlab>=4.0.0
python-dotenv>=1.0.0
EOF

# Install
pip install -r requirements.txt
```

#### Option 2: Install Individual Packages

```bash
pip install anthropic flask flask-socketio flask-cors python-socketio flask-sqlalchemy reportlab python-dotenv
```

#### Option 3: For Claude Code (No Installation Needed)

Claude Code comes with all dependencies pre-installed.

### Environment Setup

Create `.env` file:

```bash
cat > .env << EOF
# Anthropic API
ANTHROPIC_API_KEY=sk-your-key-here

# Gmail (for email approval gates)
GMAIL_SENDER=your-email@gmail.com
GMAIL_PASSWORD=your-16-char-app-password

# Budget
DAILY_BUDGET=5.00

# Paths
WORKFLOW_DB=/tmp/fg_workflows.db
KNOWLEDGE_DB=/tmp/fg_knowledge.db

# Server
FLASK_ENV=production
FLASK_HOST=0.0.0.0
FLASK_PORT=5000
EOF

# Load it
export $(cat .env | xargs)
```

### Database Setup

Databases are automatically created on first run:
- `/tmp/fg_workflows.db` — Workflow state + approvals
- `/tmp/fg_knowledge.db` — Skills, patterns, lessons
- `/tmp/dashboard_server.db` — Alerts, audit logs, comments

To reset databases:

```bash
rm /tmp/fg*.db /tmp/dashboard*.db
# Databases will be recreated on next run
```

### Email Setup (Optional but Recommended)

For email approval gates:

**1. Enable 2-Step Verification on Gmail**

```
myaccount.google.com/security → 2-Step Verification → Enable
```

**2. Create App Password**

```
myaccount.google.com/apppasswords → Select Mail + Device → Get 16-char password
```

**3. Add to `.env`**

```
GMAIL_SENDER=your-email@gmail.com
GMAIL_PASSWORD=xxxx xxxx xxxx xxxx  # 16-character app password
```

---

## Commands & Testing

### Agent Execution Commands

#### 1. Run PM Agent (Create Charter)

```bash
python claude_code_agent_ecosystem.py run_pm_agent \
  --project "AURA MVP" \
  --client "Malcolm Goodwin"
```

**What happens:**
1. PM Agent generates project charter
2. Charter pauses at approval gate
3. Email sent to trice@firstgenesis.com
4. Human replies "APPROVED"
5. Agent resumes and completes

**Expected Output:**

```
✅ Charter generated
Workflow ID: pm_AURA_MVP_20260317_120000
Status: Awaiting approval
Charter preview:
  PROJECT CHARTER
  Title: AURA MVP
  Client: Malcolm Goodwin
  ...
```

#### 2. Check Pending Approvals

```bash
python claude_code_agent_ecosystem.py check_approvals
```

**Output:**

```
📋 Pending Approvals (1)
  - pm_AURA_MVP_20260317_120000: charter_approval
```

#### 3. Process Approval (Simulate Human Response)

```bash
python claude_code_agent_ecosystem.py process_approval \
  --workflow-id pm_AURA_MVP_20260317_120000 \
  --approver trice@firstgenesis.com \
  --decision approved \
  --feedback "Looks good, proceed to kickoff"
```

**Output:**

```
✅ Approval recorded for pm_AURA_MVP_20260317_120000: approved
Workflow status updated to: approved
```

#### 4. Resume Approved Workflows

```bash
python claude_code_agent_ecosystem.py resume_workflows
```

**Output:**

```
✅ Resumed pm_AURA_MVP_20260317_120000
   Next step: Charter approved. Ready for customer kickoff.
```

#### 5. View Portfolio Dashboard

```bash
python claude_code_agent_ecosystem.py run_dashboard
```

**Output:**

```
======================================================================
                        PORTFOLIO DASHBOARD
======================================================================

PROJECT STATUS:
Project            | Status      | Owner   | Risk Level
-------------------|-------------|---------|----------
AURA MVP           | On Track    | Kiera   | Green
Chevron Sand Mgmt  | In Progress | Elina   | Amber
WWT Enhancement    | At Risk     | Ron     | Red

PENDING APPROVALS: 2
- pm_AURA_MVP: Charter approval (2h waiting)
- ba_CHEVRON: Requirements approval (1h waiting)
```

---

### Dashboard Server Commands

#### Start Dashboard Server

```bash
python dashboard_server.py
```

**Output:**

```
╔════════════════════════════════════════════════════════════════╗
║     First Genesis Dashboard Server - Ready                     ║
╚════════════════════════════════════════════════════════════════╝

Endpoints:
📊 Dashboard:     http://localhost:5000/dashboard
🔧 Admin Panel:   http://localhost:5000/admin
📡 WebSocket:     ws://localhost:5000/socket.io

Starting server...
```

#### Open Admin Panel

```
# In browser
http://localhost:5000/admin
```

**Features Available:**
- Dashboard statistics
- Active alerts with acknowledge buttons
- Export metrics (CSV, PDF, email)
- Audit log (searchable)

---

### Token Strategy Commands

#### Show Budget Model

```bash
python token_strategy_executive.py show_budget_model
```

**Output:**

```
Daily Budget........................ $5.00
Agent Execution Cost............... $0.20
Contingency (20%).................. $1.00
Safety Buffer...................... $3.80
Headroom Multiplier................ 25.0×

Status:........................... ✅ WITHIN BUDGET
```

#### Show Cost Breakdown

```bash
python token_strategy_executive.py show_cost_breakdown
```

**Output:**

```
Agent                     Per Call        Daily Cost
───────────────────────────────────────────────────
PM Agent                  $0.0255         $0.0510
BA Agent                  $0.0405         $0.0405
QA Agent                  $0.0540         $0.0540
Vendor Agent              $0.0180         $0.0180
Manager Agent             $0.0365         $0.0365
───────────────────────────────────────────────────
TOTAL DAILY                                $0.2000
```

#### Show Executive Summary

```bash
python token_strategy_executive.py show_executive_summary
```

Output: 3-page comprehensive report for executives

#### View Real-Time Dashboard

```bash
python token_strategy_executive.py dashboard
```

Output: Real-time monitoring dashboard with recommendations

---

### Testing Workflow (End-to-End)

```bash
#!/bin/bash
# test_ecosystem.sh

echo "🚀 Testing First Genesis Agent Ecosystem"
echo "========================================"

# 1. Run PM Agent
echo "1. Running PM Agent..."
python claude_code_agent_ecosystem.py run_pm_agent \
  --project "Test Project" \
  --client "Test Client"

# 2. Check approvals
echo -e "\n2. Checking pending approvals..."
python claude_code_agent_ecosystem.py check_approvals

# 3. Process approval
echo -e "\n3. Processing approval..."
python claude_code_agent_ecosystem.py process_approval \
  --workflow-id pm_Test_Project_20260317_120000 \
  --approver trice@firstgenesis.com \
  --decision approved

# 4. Resume workflows
echo -e "\n4. Resuming approved workflows..."
python claude_code_agent_ecosystem.py resume_workflows

# 5. View dashboard
echo -e "\n5. Viewing portfolio dashboard..."
python claude_code_agent_ecosystem.py run_dashboard

# 6. Check costs
echo -e "\n6. Checking token costs..."
python token_strategy_executive.py show_cost_breakdown

echo -e "\n✅ Test complete!"
```

Run it:

```bash
chmod +x test_ecosystem.sh
./test_ecosystem.sh
```

---

## API Reference

### REST Endpoints (Dashboard Server)

#### Dashboard State

```
GET /api/dashboard?user=kiera
```

Returns: Full dashboard state (agents, metrics, workflows)

#### Agent Details

```
GET /api/agents/<name>
```

Example: `GET /api/agents/PM%20Agent`
Returns: Agent details, skills, recent messages

#### Workflow Patterns

```
GET /api/patterns
```

Returns: All successful workflow patterns (reusable templates)

#### Lessons Learned

```
GET /api/lessons
```

Returns: All captured best practices

#### Send Message Between Agents

```
POST /api/communicate
Body: {
  "from_agent": "PM Agent",
  "to_agent": "BA Agent",
  "type": "DELEGATE",
  "content": "Extract requirements from design session"
}
```

#### Get Alerts

```
GET /api/alerts
```

Returns: All active alerts (error rate, budget, duration)

#### Acknowledge Alert

```
POST /api/alerts/<id>/acknowledge
Body: {"user": "kiera"}
```

#### Export Metrics

```
POST /api/export/csv
POST /api/export/pdf
POST /api/export/email
```

#### Collaboration

```
POST /api/lessons/<id>/comment      — Add comment
GET  /api/lessons/<id>/comments     — Get comments
POST /api/lessons/<id>/vote         — Vote on lesson
GET  /api/lessons/<id>/versions     — Get versions
POST /api/lessons/<id>/version      — Create version
```

#### Audit Log

```
GET /api/audit-log?action=create&resource=pattern&user=kiera
```

---

### WebSocket Events

#### Listen for Updates

```javascript
socket.on('dashboard_update', (data) => {
    // Receives: {timestamp, agents, metrics, workflows}
});
```

#### Request Manual Update

```javascript
socket.emit('request_update', {});
```

---

### Python SDK

#### Import Agents

```python
from claude_code_agent_ecosystem import PMAgent, BAAgent, QAAgent

# Create agents
pm_agent = PMAgent(templates=TEMPLATES["pm_agent"])
ba_agent = BAAgent(templates=TEMPLATES["ba_agent"])
qa_agent = QAAgent(templates=TEMPLATES["qa_agent"])
```

#### Track Skills

```python
from agent_dashboard_and_command_center import SkillCompoundingEngine

skill_engine = SkillCompoundingEngine(knowledge_library)

# Record success
skill_engine.record_success(
    "PM Agent",
    "create_charter",
    execution_time=120.5,
    output_quality=0.95
)

# Get improvement metrics
metrics = skill_engine.get_agent_improvement("PM Agent")
print(metrics["avg_skill_level"])  # 3.2/4.0
```

#### Control Cost

```python
from FG_Agent_Guardrails import BudgetModel

# Check if within budget
is_ok, msg = BudgetModel.is_within_budget(estimated_cost=0.05)
if not is_ok:
    print(f"Budget exceeded: {msg}")
```

---

## Example Prompts

### 1. Generate Project Charter

**Prompt:**

```
Generate a professional project charter for:
Project: AURA MVP
Client: Malcolm Goodwin
Budget: $150,000
Timeline: 12 weeks
Scope: Silhouette technology + 3D mesh design

Include:
- Project objectives
- Success criteria
- Key risks with mitigations
- Team structure
- Communication plan

Output as JSON only.
```

**What Happens:**
1. Claude generates charter
2. Agent pauses at approval gate
3. Email sent to Trice
4. Trice approves
5. Charter delivered to customer

---

### 2. Extract Requirements from Meeting

**Prompt:**

```
Extract requirements from this design session transcript:

[Meeting Transcript]
"We need to support 1000 users per minute. The system must
work on mobile and desktop. Security is critical - we need
2FA and encryption. The UI should be intuitive, no more than
3 clicks to complete any task."

Create a structured requirements document with:
- Functional Requirements (FR1, FR2, ...)
- Non-Functional Requirements (NFR1, NFR2, ...)
- Acceptance criteria for each
- Dependencies
Output as JSON.
```

**What Happens:**
1. BA Agent extracts requirements
2. Creates traceability matrix
3. Pauses for approval
4. Kiera reviews and approves
5. Requirements documented

---

### 3. Audit Deliverables

**Prompt:**

```
Audit these deliverables against the original project scope:

Original Scope:
- Project charter
- Requirements specification
- Design mockups
- Technical architecture
- Implementation plan

Deliverables Provided:
- charter.pdf (✓)
- requirements.docx (✓)
- ui_mockups.figma (✓)
- arch_diagram.png (✓)
- implementation_plan.xlsx (✓)

Check:
1. All deliverables present?
2. Scope creep? (any extra items beyond scope?)
3. Quality assessment
4. Readiness for delivery? (READY / NEEDS WORK)

Output as JSON checklist.
```

**What Happens:**
1. QA Agent audits deliverables
2. Flags any issues
3. Generates readiness report
4. Pauses for approval
5. Delivery approved/blocked

---

### 4. Monitor Vendor Performance

**Prompt:**

```
Generate vendor performance scorecard for Yubi:

SLA Metrics:
- On-time delivery: 95%+ required
- Quality score: 90%+ required
- Response time: <24h required
- Budget variance: <5% required

Actual Performance:
- On-time delivery: 98% ✓
- Quality score: 92% ✓
- Response time: 18h ✓
- Budget variance: 3% ✓

Generate:
1. Overall scorecard (PASS/FAIL)
2. Performance trends
3. Any escalations needed?
4. Recommendations

Output as JSON.
```

**What Happens:**
1. Vendor Agent processes metrics
2. Generates scorecard
3. Flags any issues
4. Reports to portfolio manager
5. Updates vendor health dashboard

---

### 5. Generate Portfolio Dashboard

**Prompt:**

```
Create a portfolio status dashboard for First Genesis:

Projects:
1. AURA MVP - Status: In Progress, Owner: Kiera, Risk: Green
2. Chevron Sand Mgmt - Status: On Track, Owner: Elina, Risk: Amber
3. WWT Enhancement - Status: At Risk, Owner: Ron, Risk: Red
4. Middle East - Status: Active, Owner: Partner, Risk: Green

Metrics Needed:
- Total budget: $500,000
- Spent: $125,000
- Remaining: $375,000
- Success rate: 94%
- Pending approvals: 2
- Key blockers: 1 (Chevron GPU availability)

Generate dashboard showing:
1. All projects at a glance
2. Budget summary
3. Top risks
4. Pending items
5. Key metrics
Output as formatted dashboard.
```

**What Happens:**
1. Manager Agent consolidates all outputs
2. Generates executive dashboard
3. Flags risks/blockers
4. Provides recommendations
5. Sends to leadership

---

### 6. Capture Lesson from Success

**Prompt:**

```
Document the lesson learned from the Aura MVP charter success:

Observation: When BA Agent reviewed the design session
first, the PM Agent produced 15% higher quality charters.

Create a lesson document:
- Title: "Charter quality improves with design review"
- Content: Detailed explanation of why/how
- Applicable agents: PM Agent, BA Agent
- Implementation: How to apply this pattern
- Evidence: Success metrics

Output as lesson documentation.
```

**What Happens:**
1. Lesson captured in knowledge library
2. Available for future projects
3. Team can vote on usefulness
4. Version tracked
5. Reusable pattern created

---

### 7. Example Integration Prompt

For Claude Code (Running Agent Ecosystem):

```python
"""
First Genesis Multi-Agent System Integration

This script demonstrates how to use the agent ecosystem
in your own Claude Code project.
"""

from claude_code_agent_ecosystem import (
    PMAgent, BAAgent, QAAgent, ManagerAgent, VendorAgent,
    DashboardStateManager, CommandCenter, TEMPLATES
)

# Initialize
state = DashboardStateManager()
command_center = CommandCenter(state)

# Create agents
pm_agent = PMAgent(templates=TEMPLATES["pm_agent"])
ba_agent = BAAgent(templates=TEMPLATES["ba_agent"])
qa_agent = QAAgent(templates=TEMPLATES["qa_agent"])

# Run workflow
print("Starting Aura MVP workflow...")

# PM generates charter
charter, workflow_id = pm_agent.generate_charter({
    "project": "AURA MVP",
    "client": "Malcolm Goodwin",
    "budget": 150000,
    "timeline": 12,
    "scope": "Silhouette technology + 3D mesh"
})

print(f"✅ Charter generated: {workflow_id}")

# Check dashboard
dashboard = command_center.get_dashboard()
print(f"Agents online: {len(dashboard['agents'])}")
print(f"Success rate: {dashboard['metrics']['success_rate']*100:.1f}%")

# Record approval (simulated)
state.stage_gate_manager.record_approval_response(
    workflow_id,
    "trice@firstgenesis.com",
    "approved",
    "Looks good, proceed to kickoff"
)

# Get updated status
workflow = state.db.get_workflow_state(workflow_id)
print(f"Workflow status: {workflow.status.value}")
```

---

## Troubleshooting

### Agent Won't Generate Output

**Problem:** Agent runs but returns empty output

**Solutions:**
1. Check API key is set: `echo $ANTHROPIC_API_KEY`
2. Verify token budget: `python token_strategy_executive.py show_budget_model`
3. Check Claude API status: https://status.anthropic.com
4. Increase `max_tokens` in agent code

---

### Email Not Sending

**Problem:** Approval email not received

**Solutions:**
1. Check Gmail credentials: `echo $GMAIL_SENDER`
2. Verify app password (not regular password): `myaccount.google.com/apppasswords`
3. Check 2-Step Verification is enabled
4. Try test send:
```bash
python -c "import smtplib; server = smtplib.SMTP('smtp.gmail.com', 587); server.starttls(); server.login('email', 'password'); print('✅ Email works')"
```

---

### Dashboard Won't Load

**Problem:** `http://localhost:5000` shows connection refused

**Solutions:**
1. Check server is running: `ps aux | grep dashboard_server`
2. Kill and restart: `pkill -f dashboard_server.py && python dashboard_server.py`
3. Check port 5000 is available: `lsof -i :5000`
4. Try different port: Modify `port=5001` in `dashboard_server.py`

---

### Database Errors

**Problem:** "database is locked" or "disk I/O error"

**Solutions:**
1. Close other processes using database
2. Check disk space: `df -h`
3. Reset databases: `rm /tmp/fg*.db /tmp/dashboard*.db`
4. Use PostgreSQL instead: Change `SQLALCHEMY_DATABASE_URI` in `dashboard_server.py`

---

### Approval Workflow Stuck

**Problem:** Workflow stays in `APPROVAL_SENT` for hours

**Solutions:**
1. Check email was actually sent (check spam folder)
2. Manually process approval:
```bash
python claude_code_agent_ecosystem.py process_approval \
  --workflow-id <id> \
  --approver <email> \
  --decision approved
```
3. Check timeout settings: Default is 24 hours, modify in `StageGateManager.STAGE_GATES`

---

### Cost Tracking Issues

**Problem:** Daily cost exceeds $5 budget

**Solutions:**
1. Check what's using tokens: `python token_strategy_executive.py show_cost_breakdown`
2. Reduce agent execution frequency
3. Use `prompt_caching` (cache templates)
4. Enable token batching
5. Upgrade to Claude Enterprise if > 10 projects

---

### WebSocket Connection Issues

**Problem:** "WebSocket connection failed" in browser console

**Solutions:**
1. Check server is running on port 5000
2. Check firewall allows port 5000
3. Try `localhost` vs `0.0.0.0` (change in code)
4. Clear browser cache: `Ctrl+Shift+Delete`
5. Check browser console for CORS errors

---

## Best Practices

### Agent Execution
- ✅ Run one agent at a time for development
- ✅ Use email gates for production (human approval)
- ✅ Monitor costs daily
- ✅ Archive completed workflows

### Knowledge Library
- ✅ Capture lessons after every project
- ✅ Version control best practices
- ✅ Vote on most useful patterns
- ✅ Comment to refine approaches

### Dashboard Monitoring
- ✅ Check admin panel daily
- ✅ Acknowledge alerts within 1 hour
- ✅ Export weekly metrics
- ✅ Review audit trail monthly

### Scaling
- ✅ Start with 1 project (Aura)
- ✅ Add project every week
- ✅ Monitor token costs daily
- ✅ Move to PostgreSQL at 1M records
- ✅ Upgrade to Enterprise if > 10 concurrent projects

---

## Support & Contact

### Documentation
- Overview: `README.md`
- Setup: `CLAUDE_CODE_QUICK_START.md`
- Email: `EMAIL_GATES_QUICK_START.md`
- Tokens: `FG_Token_Optimization_Strategy.md`
- Dashboard: `DASHBOARD_SERVER_COMPLETE_GUIDE.md`

### Issues
1. Check troubleshooting section above
2. Review logs: `tail -f /home/claude/fg_agents.log`
3. Try fresh setup: Back up databases, delete, restart
4. Contact: See `SUPPORT_CONTACTS.md`

---

## Version History

### v1.0 - March 17, 2026
- ✅ 5 autonomous agents
- ✅ Email approval gates
- ✅ Real-time dashboard
- ✅ Knowledge library
- ✅ Token optimization
- ✅ Cost control
- ✅ Skill learning
- ✅ Complete documentation

---

## License & Attribution

**First Genesis Multi-Agent Ecosystem**
- Built with Claude (Anthropic)
- Production-ready code
- Open for team use

---

> **You're ready to deploy!**
>
> Start with: `python dashboard_server.py` then run an agent.
>
> Questions? Check the docs or troubleshooting section above.
