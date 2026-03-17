#!/usr/bin/env python3
"""
claude_code_agent_ecosystem.py
Complete multi-agent system for First Genesis.
Ready to run in Claude Code.
Templates injected via TEMPLATES dict or external file.
Usage:
    python claude_code_agent_ecosystem.py run_pm_agent --project "AURA MVP"
    python claude_code_agent_ecosystem.py check_approvals
    python claude_code_agent_ecosystem.py process_approval --workflow-id <id> --decision approved
"""
import anthropic
import sqlite3
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass
from enum import Enum
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import hashlib
# ============================================================================
# CONFIGURATION: TEMPLATES
# ============================================================================
# YOUR TEMPLATES HERE
# Replace these with Trice's actual templates after setup
TEMPLATES = {
    "pm_agent": {
        "project_charter": """
PROJECT CHARTER
Title: {project_name}
Client: {client_name}
Budget: ${budget}
Timeline: {timeline} weeks
Scope: {scope}
Success Criteria:
  - {criteria_1}
  - {criteria_2}
Risks:
  - {risk_1}: Mitigation: {mitigation_1}
Created: {created_date}
""",

        "wbs": """
WORK BREAKDOWN STRUCTURE
Project: {project_name}
Phase 1: Project Initiation
  - Task 1.1: Kickoff meeting
  - Task 1.2: Baseline establishment
Phase 2: Design & Planning
  - Task 2.1: Design documentation
  - Task 2.2: Planning sessions
Phase 3: Execution
  - Task 3.1: Implementation
  - Task 3.2: Testing
Phase 4: Closure
  - Task 4.1: Handoff
  - Task 4.2: Lessons learned
""",

        "kickoff_checklist": """
PROJECT KICKOFF CHECKLIST
Project: {project_name}
[ ] Charter approved by stakeholders
[ ] Team roles and responsibilities assigned
[ ] Project schedule communicated
[ ] Risks documented and owned
[ ] Communication plan established
[ ] Kickoff meeting scheduled
[ ] Success criteria understood
[ ] Budget approved
[ ] Resources allocated
""",
    },

    "ba_agent": {
        "requirements": """
REQUIREMENTS SPECIFICATION
Project: {project_name}
FUNCTIONAL REQUIREMENTS:
FR1: {requirement_1}
  Acceptance Criteria: {criteria_1}
  Priority: {priority_1}
FR2: {requirement_2}
  Acceptance Criteria: {criteria_2}
  Priority: {priority_2}
NON-FUNCTIONAL REQUIREMENTS:
NFR1: Performance - {performance_req}
NFR2: Security - {security_req}
NFR3: Usability - {usability_req}
ASSUMPTIONS:
  - {assumption_1}
  - {assumption_2}
CONSTRAINTS:
  - {constraint_1}
  - {constraint_2}
""",

        "traceability_matrix": """
REQUIREMENTS TRACEABILITY MATRIX
Project: {project_name}
ID  | Requirement | Design Doc | Test Case | Status
----|-------------|-----------|-----------|--------
FR1 | {req_1}     | DESIGN-1  | TEST-1   | {status_1}
FR2 | {req_2}     | DESIGN-2  | TEST-2   | {status_2}
NFR1| {req_3}     | DESIGN-3  | TEST-3   | {status_3}
""",

        "design_session_template": """
DESIGN SESSION NOTES
Project: {project_name}
Date: {session_date}
Attendees: {attendees}
DISCUSSION POINTS:
1. {topic_1}
   Decision: {decision_1}
   Owner: {owner_1}
2. {topic_2}
   Decision: {decision_2}
   Owner: {owner_2}
NEXT STEPS:
[ ] {action_1} - Owner: {owner_1} - Due: {due_date_1}
[ ] {action_2} - Owner: {owner_2} - Due: {due_date_2}
OPEN ITEMS:
- {open_item_1}
- {open_item_2}
""",
    },

    "qa_agent": {
        "qa_checklist": """
PRE-DELIVERY QA CHECKLIST
Project: {project_name}
Date: {review_date}
COMPLETENESS:
[ ] All requirements satisfied
[ ] All acceptance criteria met
[ ] Documentation complete
[ ] Test coverage adequate
[ ] Known issues documented
QUALITY:
[ ] No major defects found
[ ] Code quality acceptable
[ ] Performance acceptable
[ ] Security review passed
[ ] Accessibility standards met
SCOPE CREEP:
[ ] No scope changes outside approved RFP
[ ] Budget variance < 5%
[ ] Timeline variance < 10%
[ ] Team additions pre-approved
READINESS:
[ ] Customer expectations aligned
[ ] Support team prepared
[ ] Deployment plan ready
[ ] Rollback plan ready
OVERALL ASSESSMENT: {assessment}
Recommendation: READY FOR DELIVERY / NEEDS WORK
Reviewed By: {reviewer}
Approved By: {approver}
""",

        "scope_creep_detection": """
SCOPE CREEP DETECTION RULES
Project: {project_name}
Rule 1: Unplanned Features
  Trigger: New feature request not in original RFP
  Action: Alert PM, get approval before proceeding

Rule 2: Timeline Variance
  Trigger: Schedule change > 10%
  Action: Alert PM and customer

Rule 3: Budget Variance
  Trigger: Cost variance > 5%
  Action: Escalate to CEO if critical

Rule 4: Team Changes
  Trigger: Resource additions not pre-approved
  Action: Alert PM, verify budget impact

Rule 5: Requirement Changes
  Trigger: Requirements modified post-approval
  Action: Document change, get sign-off
CURRENT PROJECT STATUS:
Scope variance: {scope_variance}%
Timeline variance: {timeline_variance}%
Budget variance: {budget_variance}%
Status: {creep_status}
""",
    },

    "vendor_agent": {
        "sla_template": """
VENDOR/PARTNER SLA
Vendor: {vendor_name}
Engagement: {engagement_name}
SCOPE:
{scope}
DELIVERABLES:
  1. {deliverable_1} - Due: {due_date_1}
  2. {deliverable_2} - Due: {due_date_2}
  3. {deliverable_3} - Due: {due_date_3}
TIMELINE: {start_date} to {end_date} ({duration} weeks)
BUDGET: ${budget}
PERFORMANCE METRICS:
  - On-time delivery: 95%+ required
  - Quality score: 90%+ required
  - Communication: <24h response time
  - Escalation handling: <4 business hours
PAYMENT TERMS:
  {payment_terms}
TERMINATION CLAUSE:
  {termination_clause}
""",

        "scorecard_template": """
VENDOR PERFORMANCE SCORECARD
Vendor: {vendor_name}
Period: {period}
Engagement: {engagement_name}
PERFORMANCE METRICS:
Metric                 | Target | Actual | Status   | Notes
-----------------------|--------|--------|----------|----------
On-time delivery       | 95%    | {pct1} | {st1}    | {notes1}
Quality score          | 90%    | {pct2} | {st2}    | {notes2}
Avg response time      | <24h   | {time} | {st3}    | {notes3}
Budget variance        | <5%    | {var}  | {st4}    | {notes4}
Issue resolution time  | <4h    | {hours}| {st5}    | {notes5}
OVERALL RATING: {rating}/5.0
Status: ON_TRACK / NEEDS_IMPROVEMENT / AT_RISK
RECOMMENDATIONS:
  1. {recommendation_1}
  2. {recommendation_2}
Reviewed By: {reviewer}
Date: {review_date}
""",
    },

    "manager_agent": {
        "portfolio_dashboard": """
PORTFOLIO MANAGEMENT DASHBOARD
Generated: {timestamp}
PROJECT STATUS OVERVIEW:
Project              | Status        | Owner         | Risk Level | Days Active
---------------------|---------------|---------------|------------|----------
{project_1}          | {status_1}    | {owner_1}     | {risk_1}   | {days_1}
{project_2}          | {status_2}    | {owner_2}     | {risk_2}   | {days_2}
{project_3}          | {status_3}    | {owner_3}     | {risk_3}   | {days_3}
BUDGET SUMMARY:
Project              | Budget    | Spent     | Remaining | % Used
---------------------|-----------|-----------|-----------|--------
{project_1}          | ${b1}     | ${s1}     | ${r1}     | {pct1}%
{project_2}          | ${b2}     | ${s2}     | ${r2}     | {pct2}%
{project_3}          | ${b3}     | ${s3}     | ${r3}     | {pct3}%
TOTAL                | ${total_b}| ${total_s}| ${total_r}| {total_pct}%
PENDING APPROVALS ({approval_count}):
  - {workflow_1} (waiting {hours_1}h)
  - {workflow_2} (waiting {hours_2}h)
RISKS & BLOCKERS ({blocker_count}):
  🔴 CRITICAL: {critical_blocker}
  🟡 HIGH: {high_blocker}

NEXT ACTIONS:
  [ ] {action_1} - Owner: {owner} - Due: {due_date}
  [ ] {action_2} - Owner: {owner} - Due: {due_date}
METRICS:
  Total Projects: {total_projects}
  On Track: {on_track}
  At Risk: {at_risk}
  Avg Project Health: {health_score}%

Report Generated: {timestamp}
""",
    }
}
# ============================================================================
# DATA STRUCTURES
# ============================================================================
class StageGateName(Enum):
    CHARTER_APPROVAL = "charter_approval"
    REQUIREMENTS_APPROVAL = "requirements_approval"
    QA_AUDIT_APPROVAL = "qa_audit_approval"
    DELIVERY_APPROVAL = "delivery_approval"
    BUDGET_ESCALATION = "budget_escalation"
class WorkflowStatus(Enum):
    PENDING = "pending"
    APPROVAL_SENT = "approval_sent"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESUMED = "resumed"
    COMPLETED = "completed"
@dataclass
class Agent:
    """Base agent class"""
    name: str
    templates: Dict = None
    client: anthropic.Anthropic = None
    db: sqlite3.Connection = None

    def __post_init__(self):
        if self.templates is None:
            self.templates = {}
        if self.client is None:
            self.client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
# ============================================================================
# DATABASE
# ============================================================================
class WorkflowDB:
    """SQLite workflow database"""

    def __init__(self, db_path: str = "/tmp/fg_workflows.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.init_tables()

    def init_tables(self):
        """Initialize database schema"""
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                agent_name TEXT,
                project_name TEXT,
                stage_gate TEXT,
                status TEXT,
                content TEXT,
                created_at TEXT,
                updated_at TEXT
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT,
                stage_gate TEXT,
                approver TEXT,
                decision TEXT,
                feedback TEXT,
                timestamp TEXT
            )
        """)

        self.conn.commit()

    def save_workflow(self, workflow_id: str, agent_name: str, project_name: str,
                     stage_gate: str, status: str, content: str):
        """Save workflow state"""
        self.conn.execute("""
            INSERT OR REPLACE INTO workflows
            (workflow_id, agent_name, project_name, stage_gate, status, content, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (workflow_id, agent_name, project_name, stage_gate, status, content,
              datetime.now().isoformat(), datetime.now().isoformat()))
        self.conn.commit()

    def get_workflow(self, workflow_id: str) -> Optional[Dict]:
        """Get workflow by ID"""
        cursor = self.conn.execute(
            "SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)
        )
        row = cursor.fetchone()
        if row:
            return {
                "workflow_id": row[0],
                "agent_name": row[1],
                "project_name": row[2],
                "stage_gate": row[3],
                "status": row[4],
                "content": row[5],
                "created_at": row[6],
                "updated_at": row[7]
            }
        return None

    def get_pending_approvals(self) -> List[Dict]:
        """Get all workflows awaiting approval"""
        cursor = self.conn.execute(
            "SELECT * FROM workflows WHERE status = 'approval_sent' ORDER BY updated_at"
        )
        return [
            {
                "workflow_id": r[0],
                "agent_name": r[1],
                "project_name": r[2],
                "stage_gate": r[3],
                "status": r[4],
                "created_at": r[6],
            }
            for r in cursor.fetchall()
        ]
# ============================================================================
# EMAIL GATEWAY
# ============================================================================
class EmailGateway:
    """Email notification system"""

    def __init__(self):
        self.sender = os.environ.get("GMAIL_SENDER")
        self.password = os.environ.get("GMAIL_PASSWORD")
        self.enabled = bool(self.sender and self.password)

    def send_approval_request(self, workflow_id: str, stage_gate: str,
                             approver_email: str, content_summary: str) -> bool:
        """Send approval request email"""
        if not self.enabled:
            print(f"⚠️  Email disabled. Would send to {approver_email}")
            return True  # Pretend sent for testing

        try:
            subject = f"[Approval Required] {stage_gate} ({workflow_id})"
            body = f"""
APPROVAL REQUEST
Workflow ID: {workflow_id}
Stage Gate: {stage_gate}
Request Time: {datetime.now().isoformat()}
CONTENT:
{content_summary[:500]}...
ACTION REQUIRED:
Reply with: APPROVED or REJECTED
Timeout: 24 hours
"""

            msg = MIMEMultipart()
            msg["From"] = self.sender
            msg["To"] = approver_email
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP("smtp.gmail.com", 587) as server:
                server.starttls()
                server.login(self.sender, self.password)
                server.sendmail(self.sender, [approver_email], msg.as_string())

            print(f"📧 Email sent to {approver_email}")
            return True

        except Exception as e:
            print(f"❌ Email failed: {str(e)}")
            return False
# ============================================================================
# AGENTS
# ============================================================================
class PMAgent(Agent):
    """Project Manager Agent"""

    def __init__(self, templates: Dict = None):
        super().__init__(name="pm_agent", templates=templates or TEMPLATES.get("pm_agent", {}))
        self.db = WorkflowDB()

    def generate_charter(self, project_metadata: Dict) -> Tuple[str, str]:
        """Generate project charter with approval gate"""

        workflow_id = f"pm_{project_metadata.get('project', 'unknown')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Generate charter using Claude
        system_prompt = "You are a project manager. Generate a professional project charter in JSON format."
        user_message = f"""Create a project charter for:
{json.dumps(project_metadata, indent=2)}
Use this template structure:
{self.templates.get('project_charter', 'DEFAULT CHARTER')}
Output as JSON only."""

        try:
            response = self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            charter = response.content[0].text

            # Save workflow
            self.db.save_workflow(
                workflow_id=workflow_id,
                agent_name="pm_agent",
                project_name=project_metadata.get('project', 'unknown'),
                stage_gate=StageGateName.CHARTER_APPROVAL.value,
                status=WorkflowStatus.PENDING.value,
                content=charter
            )

            # Send approval email
            email_gateway = EmailGateway()
            email_gateway.send_approval_request(
                workflow_id=workflow_id,
                stage_gate=StageGateName.CHARTER_APPROVAL.value,
                approver_email="trice@firstgenesis.com",
                content_summary=charter[:200]
            )

            # Update status
            self.db.save_workflow(
                workflow_id=workflow_id,
                agent_name="pm_agent",
                project_name=project_metadata.get('project', 'unknown'),
                stage_gate=StageGateName.CHARTER_APPROVAL.value,
                status=WorkflowStatus.APPROVAL_SENT.value,
                content=charter
            )

            return charter, workflow_id

        except Exception as e:
            print(f"❌ Charter generation failed: {str(e)}")
            return "", ""
class BAAgent(Agent):
    """Business Analyst Agent"""

    def __init__(self, templates: Dict = None):
        super().__init__(name="ba_agent", templates=templates or TEMPLATES.get("ba_agent", {}))
        self.db = WorkflowDB()

    def process_design_session(self, transcript: str, project_name: str) -> Tuple[str, str]:
        """Process design session transcript"""

        workflow_id = f"ba_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        system_prompt = "You are a business analyst. Extract requirements from the design session."
        user_message = f"""Analyze this design session transcript and extract requirements:
{transcript}
Use this template:
{self.templates.get('requirements', 'DEFAULT REQUIREMENTS')}
Output as JSON."""

        try:
            response = self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            requirements = response.content[0].text

            # Save workflow
            self.db.save_workflow(
                workflow_id=workflow_id,
                agent_name="ba_agent",
                project_name=project_name,
                stage_gate=StageGateName.REQUIREMENTS_APPROVAL.value,
                status=WorkflowStatus.APPROVAL_SENT.value,
                content=requirements
            )

            # Send approval email
            email_gateway = EmailGateway()
            email_gateway.send_approval_request(
                workflow_id=workflow_id,
                stage_gate=StageGateName.REQUIREMENTS_APPROVAL.value,
                approver_email="kiera@firstgenesis.com",
                content_summary=requirements[:200]
            )

            return requirements, workflow_id

        except Exception as e:
            print(f"❌ Requirements extraction failed: {str(e)}")
            return "", ""
class QAAgent(Agent):
    """Quality Assurance Agent"""

    def __init__(self, templates: Dict = None):
        super().__init__(name="qa_agent", templates=templates or TEMPLATES.get("qa_agent", {}))
        self.db = WorkflowDB()

    def audit_deliverable(self, workflow_id: str) -> Tuple[str, str]:
        """Audit deliverable before delivery"""

        qa_workflow_id = f"qa_{workflow_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Get original workflow
        original = self.db.get_workflow(workflow_id)
        if not original:
            return "", ""

        system_prompt = "You are a QA auditor. Review deliverables for quality and completeness."
        user_message = f"""Audit this deliverable for quality:
{original['content'][:500]}...
Use this audit template:
{self.templates.get('qa_checklist', 'DEFAULT QA CHECKLIST')}
Output as JSON."""

        try:
            response = self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=1500,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            audit_report = response.content[0].text

            # Save workflow
            self.db.save_workflow(
                workflow_id=qa_workflow_id,
                agent_name="qa_agent",
                project_name=original['project_name'],
                stage_gate=StageGateName.QA_AUDIT_APPROVAL.value,
                status=WorkflowStatus.APPROVAL_SENT.value,
                content=audit_report
            )

            # Send approval email
            email_gateway = EmailGateway()
            email_gateway.send_approval_request(
                workflow_id=qa_workflow_id,
                stage_gate=StageGateName.QA_AUDIT_APPROVAL.value,
                approver_email="trice@firstgenesis.com",
                content_summary=audit_report[:200]
            )

            return audit_report, qa_workflow_id

        except Exception as e:
            print(f"❌ Audit failed: {str(e)}")
            return "", ""
class ManagerAgent(Agent):
    """Portfolio Manager Agent"""

    def __init__(self, templates: Dict = None):
        super().__init__(name="manager_agent", templates=templates or TEMPLATES.get("manager_agent", {}))
        self.db = WorkflowDB()

    def generate_dashboard(self) -> str:
        """Generate portfolio dashboard"""

        pending = self.db.get_pending_approvals()

        dashboard = self.templates.get('portfolio_dashboard', 'DEFAULT DASHBOARD').format(
            timestamp=datetime.now().isoformat(),
            approval_count=len(pending),
            blocker_count=0,
            total_projects=len(pending),
            on_track=len(pending) // 2,
            at_risk=len(pending) // 4,
            health_score=85
        )

        return dashboard
# ============================================================================
# MAIN INTERFACE
# ============================================================================
def main():
    """CLI interface"""

    if len(sys.argv) < 2:
        print("Usage: python script.py [command]")
        print("\nCommands:")
        print("  run_pm_agent --project <name> --client <name>")
        print("  check_approvals")
        print("  process_approval --workflow-id <id> --decision <approved|rejected>")
        print("  run_dashboard")
        return

    command = sys.argv[1]

    if command == "run_pm_agent":
        # Parse arguments
        project = None
        client = None
        for i, arg in enumerate(sys.argv[2:]):
            if arg == "--project" and i + 2 < len(sys.argv):
                project = sys.argv[i + 3]
            elif arg == "--client" and i + 2 < len(sys.argv):
                client = sys.argv[i + 3]

        if not project or not client:
            print("ERROR: --project and --client required")
            return

        pm_agent = PMAgent(templates=TEMPLATES["pm_agent"])
        charter, workflow_id = pm_agent.generate_charter({
            "project": project,
            "client": client,
            "budget": 150000,
            "timeline": 12,
            "scope": "Project scope here"
        })

        print(f"\n✅ Charter generated")
        print(f"Workflow ID: {workflow_id}")
        print(f"Status: Awaiting approval")
        print(f"\nCharter preview:\n{charter[:300]}...")

    elif command == "check_approvals":
        db = WorkflowDB()
        pending = db.get_pending_approvals()

        print(f"\n📋 Pending Approvals ({len(pending)})")
        for wf in pending:
            print(f"  - {wf['workflow_id']}: {wf['stage_gate']}")

    elif command == "process_approval":
        # Parse arguments
        workflow_id = None
        decision = None
        for i, arg in enumerate(sys.argv[2:]):
            if arg == "--workflow-id" and i + 2 < len(sys.argv):
                workflow_id = sys.argv[i + 3]
            elif arg == "--decision" and i + 2 < len(sys.argv):
                decision = sys.argv[i + 3]

        if not workflow_id or not decision:
            print("ERROR: --workflow-id and --decision required")
            return

        db = WorkflowDB()
        db.conn.execute("""
            INSERT INTO approvals (workflow_id, stage_gate, approver, decision, timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, (workflow_id, "charter_approval", "trice@firstgenesis.com", decision,
              datetime.now().isoformat()))
        db.conn.commit()

        print(f"✅ Approval recorded for {workflow_id}: {decision}")

    elif command == "run_dashboard":
        manager = ManagerAgent(templates=TEMPLATES["manager_agent"])
        dashboard = manager.generate_dashboard()
        print(f"\n{dashboard}")

    else:
        print(f"Unknown command: {command}")
if __name__ == "__main__":
    main()
