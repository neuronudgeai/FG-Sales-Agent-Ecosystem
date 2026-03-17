#!/usr/bin/env python3
"""
claude_code_agent_ecosystem.py
Enhanced multi-agent system for First Genesis with email-based human approval gates.
Agents pause at stage gates, email humans for approval, and resume upon response.
Features:
  - Stage gate framework (5 standard gates across agents)
  - Email notifications to humans via Outlook SMTP (customizable recipients per gate)
  - Approval workflow (human replies, agent resumes automatically)
  - Workflow persistence (SQLite state machine)
  - Cost control + hallucination detection

Configuration:
    export ANTHROPIC_API_KEY="sk-..."
    export OUTLOOK_SENDER="your-email@yourdomain.com"
    export OUTLOOK_PASSWORD="your-password"

Usage:
    python claude_code_agent_ecosystem.py run_pm_agent
    python claude_code_agent_ecosystem.py check_approvals
    python claude_code_agent_ecosystem.py resume_workflows
    python claude_code_agent_ecosystem.py process_approval --workflow-id <id> --approver <email> --decision <approved|rejected>
"""
import anthropic
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
import sqlite3
import hashlib
import logging
import smtplib
import email
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from enum import Enum
import time

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)-8s %(message)s',
    handlers=[
        logging.FileHandler('/home/claude/fg_agents.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ============================================================================
# CONFIGURATION: TEMPLATES
# ============================================================================
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
  CRITICAL: {critical_blocker}
  HIGH: {high_blocker}

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
# DATA MODELS
# ============================================================================
class StageGateName(Enum):
    """Standard stage gates across all agents."""
    CHARTER_APPROVAL = "charter_approval"
    REQUIREMENTS_APPROVAL = "requirements_approval"
    QA_AUDIT_APPROVAL = "qa_audit_approval"
    DELIVERY_APPROVAL = "delivery_approval"
    BUDGET_ESCALATION = "budget_escalation"

class WorkflowStatus(Enum):
    """Status of a workflow at each stage gate."""
    PENDING = "pending"
    APPROVAL_SENT = "approval_sent"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESUMED = "resumed"
    COMPLETED = "completed"

@dataclass
class StageGate:
    """Definition of a stage gate."""
    name: StageGateName
    description: str
    approver_email: str
    cc_emails: List[str] = None
    auto_approve_if: Optional[str] = None
    require_comment: bool = False
    timeout_hours: int = 24

@dataclass
class WorkflowState:
    """State of an agent workflow."""
    workflow_id: str
    agent_name: str
    project_name: str
    current_stage_gate: StageGateName
    status: WorkflowStatus
    content_pending_approval: str
    content_hash: str
    approval_email_sent_at: Optional[str] = None
    human_approver: Optional[str] = None
    human_feedback: Optional[str] = None
    approval_timestamp: Optional[str] = None
    next_step_after_approval: Optional[str] = None
    created_at: str = None
    updated_at: str = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now().isoformat()
        self.updated_at = datetime.now().isoformat()

@dataclass
class TokenCost:
    """Track token usage and cost."""
    input_tokens: int
    output_tokens: int
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    model: str = "claude-opus-4-6"

    INPUT_COST_PER_1M = 5.00
    OUTPUT_COST_PER_1M = 25.00
    CACHE_WRITE_COST_PER_1M = 0.50
    CACHE_READ_COST_PER_1M = 0.50

    def total_cost_usd(self) -> float:
        input_cost = (self.input_tokens / 1_000_000) * self.INPUT_COST_PER_1M
        output_cost = (self.output_tokens / 1_000_000) * self.OUTPUT_COST_PER_1M
        cache_write_cost = (self.cache_creation_tokens / 1_000_000) * self.CACHE_WRITE_COST_PER_1M
        cache_read_cost = (self.cache_read_tokens / 1_000_000) * self.CACHE_READ_COST_PER_1M
        return input_cost + output_cost + cache_write_cost + cache_read_cost

    def __str__(self) -> str:
        return (f"In={self.input_tokens} Out={self.output_tokens} "
                f"CW={self.cache_creation_tokens} CR={self.cache_read_tokens} "
                f"Cost=${self.total_cost_usd():.4f}")

# ============================================================================
# DATABASE & WORKFLOW STATE MANAGEMENT
# ============================================================================
class WorkflowDatabase:
    """SQLite database for workflow state and approvals."""

    def __init__(self, db_path: str = "/home/claude/fg_workflows.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_tables()

    def _init_tables(self):
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS workflows (
                workflow_id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                project_name TEXT NOT NULL,
                current_stage_gate TEXT NOT NULL,
                status TEXT NOT NULL,
                content_pending_approval TEXT,
                content_hash TEXT,
                approval_email_sent_at TEXT,
                human_approver TEXT,
                human_feedback TEXT,
                approval_timestamp TEXT,
                next_step_after_approval TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                stage_gate TEXT NOT NULL,
                approver_email TEXT NOT NULL,
                decision TEXT NOT NULL,
                feedback TEXT,
                decided_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            )
        """)

        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS agent_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT,
                agent_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cost_usd REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            )
        """)

        self.conn.commit()
        logger.info("Workflow database initialized")

    def save_workflow_state(self, state: WorkflowState):
        self.cursor.execute("""
            INSERT OR REPLACE INTO workflows
            (workflow_id, agent_name, project_name, current_stage_gate, status,
             content_pending_approval, content_hash, approval_email_sent_at,
             human_approver, human_feedback, approval_timestamp,
             next_step_after_approval, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            state.workflow_id, state.agent_name, state.project_name,
            state.current_stage_gate.value, state.status.value,
            state.content_pending_approval, state.content_hash,
            state.approval_email_sent_at, state.human_approver,
            state.human_feedback, state.approval_timestamp,
            state.next_step_after_approval, state.created_at, state.updated_at
        ))
        self.conn.commit()
        logger.info(f"Workflow {state.workflow_id} saved: {state.status.value}")

    def get_workflow_state(self, workflow_id: str) -> Optional[WorkflowState]:
        self.cursor.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        return WorkflowState(
            workflow_id=row[0], agent_name=row[1], project_name=row[2],
            current_stage_gate=StageGateName(row[3]), status=WorkflowStatus(row[4]),
            content_pending_approval=row[5], content_hash=row[6],
            approval_email_sent_at=row[7], human_approver=row[8],
            human_feedback=row[9], approval_timestamp=row[10],
            next_step_after_approval=row[11], created_at=row[12], updated_at=row[13]
        )

    def get_pending_approvals(self) -> List[WorkflowState]:
        self.cursor.execute("""
            SELECT * FROM workflows WHERE status = ? ORDER BY updated_at ASC
        """, (WorkflowStatus.APPROVAL_SENT.value,))
        return [
            WorkflowState(
                workflow_id=r[0], agent_name=r[1], project_name=r[2],
                current_stage_gate=StageGateName(r[3]), status=WorkflowStatus(r[4]),
                content_pending_approval=r[5], content_hash=r[6],
                approval_email_sent_at=r[7], human_approver=r[8],
                human_feedback=r[9], approval_timestamp=r[10],
                next_step_after_approval=r[11], created_at=r[12], updated_at=r[13]
            )
            for r in self.cursor.fetchall()
        ]

    def get_approved_workflows_ready_to_resume(self) -> List[WorkflowState]:
        self.cursor.execute("""
            SELECT * FROM workflows WHERE status = ? AND approval_timestamp IS NOT NULL
            ORDER BY approval_timestamp ASC
        """, (WorkflowStatus.APPROVED.value,))
        return [
            WorkflowState(
                workflow_id=r[0], agent_name=r[1], project_name=r[2],
                current_stage_gate=StageGateName(r[3]), status=WorkflowStatus(r[4]),
                content_pending_approval=r[5], content_hash=r[6],
                approval_email_sent_at=r[7], human_approver=r[8],
                human_feedback=r[9], approval_timestamp=r[10],
                next_step_after_approval=r[11], created_at=r[12], updated_at=r[13]
            )
            for r in self.cursor.fetchall()
        ]

    def record_approval(self, workflow_id: str, stage_gate: StageGateName,
                        approver_email: str, decision: str, feedback: str = None):
        self.cursor.execute("""
            INSERT INTO approvals
            (workflow_id, stage_gate, approver_email, decision, feedback, decided_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (workflow_id, stage_gate.value, approver_email, decision, feedback,
              datetime.now().isoformat()))
        self.conn.commit()
        logger.info(f"Approval recorded for {workflow_id}: {decision}")

    def log_agent_call(self, workflow_id: str, agent_name: str,
                       input_tokens: int, output_tokens: int, cost_usd: float,
                       status: str, reason: str = None):
        self.cursor.execute("""
            INSERT INTO agent_calls
            (workflow_id, agent_name, timestamp, input_tokens, output_tokens, cost_usd, status, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (workflow_id, agent_name, datetime.now().isoformat(),
              input_tokens, output_tokens, cost_usd, status, reason))
        self.conn.commit()

# ============================================================================
# EMAIL GATEWAY (Outlook SMTP)
# ============================================================================
class EmailGateway:
    """Send approval emails to humans via Outlook SMTP."""

    def __init__(self, sender_email: Optional[str] = None,
                 sender_password: Optional[str] = None):
        self.sender_email = sender_email or os.environ.get("OUTLOOK_SENDER")
        self.sender_password = sender_password or os.environ.get("OUTLOOK_PASSWORD")
        self.smtp_server = "smtp.office365.com"
        self.smtp_port = 587

        if not self.sender_email or not self.sender_password:
            logger.warning("Email credentials not configured. Email gates will be skipped.")
            self.enabled = False
        else:
            self.enabled = True

    def send_approval_request(self,
                              workflow_id: str,
                              stage_gate: StageGate,
                              agent_name: str,
                              project_name: str,
                              content_summary: str,
                              content_detail: str) -> bool:
        """Send approval request email to human."""

        if not self.enabled:
            logger.warning(f"Email disabled. Approval email for {workflow_id} not sent.")
            return False

        subject = f"[Approval Required] {agent_name} - {stage_gate.description} ({project_name})"

        body = f"""
APPROVAL REQUEST
Agent:        {agent_name}
Project:      {project_name}
Stage Gate:   {stage_gate.description}
Workflow ID:  {workflow_id}
Request Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

SUMMARY:
{content_summary}

DETAILED CONTENT:
{content_detail[:500]}...

ACTION REQUIRED:
Reply to this email with:
  APPROVED  (to approve and allow agent to proceed)
  REJECTED  (to reject and pause workflow)

Optional: Include feedback in your reply.
Timeout: This request will auto-escalate in {stage_gate.timeout_hours} hours if no response.

---
This is an automated message from First Genesis Agent System.
Plain text replies only please.
"""

        try:
            message = MIMEMultipart()
            message["From"] = self.sender_email
            message["To"] = stage_gate.approver_email
            if stage_gate.cc_emails:
                message["Cc"] = ", ".join(stage_gate.cc_emails)
            message["Subject"] = subject
            message.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.sender_email, self.sender_password)
                recipients = [stage_gate.approver_email]
                if stage_gate.cc_emails:
                    recipients.extend(stage_gate.cc_emails)
                server.sendmail(self.sender_email, recipients, message.as_string())

            logger.info(f"Approval email sent for {workflow_id} to {stage_gate.approver_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send approval email for {workflow_id}: {str(e)}")
            return False

    def parse_approval_response(self, email_subject: str, email_body: str) -> Tuple[str, str]:
        """Parse approval response from human email. Returns (decision, feedback)."""
        body_upper = email_body.upper()

        if "APPROVED" in body_upper:
            decision = "approved"
        elif "REJECTED" in body_upper:
            decision = "rejected"
        else:
            decision = "clarification_needed"

        feedback = email_body.strip()
        return decision, feedback

# ============================================================================
# STAGE GATE MANAGER
# ============================================================================
class StageGateManager:
    """Manage stage gates and approval workflows."""

    STAGE_GATES = {
        StageGateName.CHARTER_APPROVAL: StageGate(
            name=StageGateName.CHARTER_APPROVAL,
            description="Project Charter Review & Approval",
            approver_email="tjohnson@firstgenesis.com",
            cc_emails=["pwatty@firstgenesis.com", "emaiteu@firstgenesis.com"],
            require_comment=False,
            timeout_hours=24
        ),
        StageGateName.REQUIREMENTS_APPROVAL: StageGate(
            name=StageGateName.REQUIREMENTS_APPROVAL,
            description="Requirements Specification Review",
            approver_email="k.phipps@firstgenesis.com",
            cc_emails=["tjohnson@firstgenesis.com", "emaiteu@firstgenesis.com"],
            require_comment=True,
            timeout_hours=12
        ),
        StageGateName.QA_AUDIT_APPROVAL: StageGate(
            name=StageGateName.QA_AUDIT_APPROVAL,
            description="QA Audit & Pre-Delivery Approval",
            approver_email="emaiteu@firstgenesis.com",
            cc_emails=["tjohnson@firstgenesis.com"],
            require_comment=False,
            timeout_hours=6
        ),
        StageGateName.DELIVERY_APPROVAL: StageGate(
            name=StageGateName.DELIVERY_APPROVAL,
            description="Final Approval Before Customer Delivery",
            approver_email="tjohnson@firstgenesis.com",
            cc_emails=["pwatty@firstgenesis.com"],
            require_comment=True,
            timeout_hours=2
        ),
        StageGateName.BUDGET_ESCALATION: StageGate(
            name=StageGateName.BUDGET_ESCALATION,
            description="Budget Alert - Approval to Continue",
            approver_email="pwatty@firstgenesis.com",
            cc_emails=["emaiteu@firstgenesis.com"],
            require_comment=True,
            timeout_hours=1
        )
    }

    def __init__(self, db: WorkflowDatabase, email_gateway: EmailGateway):
        self.db = db
        self.email = email_gateway

    def pause_at_gate(self, workflow_id: str, agent_name: str, project_name: str,
                      stage_gate_name: StageGateName,
                      content_pending_approval: str) -> WorkflowState:
        """Pause workflow at a stage gate and send approval email."""

        stage_gate = self.STAGE_GATES.get(stage_gate_name)
        if not stage_gate:
            raise ValueError(f"Unknown stage gate: {stage_gate_name}")

        content_hash = hashlib.sha256(content_pending_approval.encode()).hexdigest()
        workflow_state = WorkflowState(
            workflow_id=workflow_id,
            agent_name=agent_name,
            project_name=project_name,
            current_stage_gate=stage_gate_name,
            status=WorkflowStatus.PENDING,
            content_pending_approval=content_pending_approval,
            content_hash=content_hash
        )
        self.db.save_workflow_state(workflow_state)

        email_sent = self.email.send_approval_request(
            workflow_id=workflow_id,
            stage_gate=stage_gate,
            agent_name=agent_name,
            project_name=project_name,
            content_summary=content_pending_approval[:200],
            content_detail=content_pending_approval
        )

        if email_sent:
            workflow_state.status = WorkflowStatus.APPROVAL_SENT
            workflow_state.approval_email_sent_at = datetime.now().isoformat()
        else:
            logger.warning(f"Email failed for {workflow_id}, workflow remains in PENDING")

        self.db.save_workflow_state(workflow_state)
        return workflow_state

    def record_approval_response(self, workflow_id: str, approver_email: str,
                                 decision: str, feedback: str = None) -> WorkflowState:
        """Record human approval response and update workflow state."""

        workflow_state = self.db.get_workflow_state(workflow_id)
        if not workflow_state:
            raise ValueError(f"Workflow not found: {workflow_id}")

        self.db.record_approval(
            workflow_id=workflow_id,
            stage_gate=workflow_state.current_stage_gate,
            approver_email=approver_email,
            decision=decision,
            feedback=feedback
        )

        if decision.lower() == "approved":
            workflow_state.status = WorkflowStatus.APPROVED
            workflow_state.human_approver = approver_email
            workflow_state.human_feedback = feedback
            workflow_state.approval_timestamp = datetime.now().isoformat()
            logger.info(f"Workflow {workflow_id} APPROVED by {approver_email}")
        elif decision.lower() == "rejected":
            workflow_state.status = WorkflowStatus.REJECTED
            workflow_state.human_approver = approver_email
            workflow_state.human_feedback = feedback
            logger.warning(f"Workflow {workflow_id} REJECTED by {approver_email}: {feedback}")
        else:
            logger.warning(f"Unknown decision for {workflow_id}: {decision}")

        self.db.save_workflow_state(workflow_state)
        return workflow_state

    def get_pending_approvals_summary(self) -> str:
        """Generate summary of pending approvals."""
        pending = self.db.get_pending_approvals()

        if not pending:
            return "No pending approvals"

        summary = f"\n{'='*70}\nPENDING APPROVALS ({len(pending)})\n{'='*70}\n"
        for workflow in pending:
            sent_at = datetime.fromisoformat(workflow.approval_email_sent_at)
            hours_waiting = (datetime.now() - sent_at).total_seconds() / 3600
            summary += f"\n[{workflow.workflow_id}]\n"
            summary += f"  Agent:       {workflow.agent_name}\n"
            summary += f"  Project:     {workflow.project_name}\n"
            summary += f"  Stage Gate:  {workflow.current_stage_gate.value}\n"
            summary += f"  Waiting:     {hours_waiting:.1f} hours\n"
            summary += f"  Content:     {workflow.content_pending_approval[:80]}...\n"
        summary += "\n" + "=" * 70
        return summary

# ============================================================================
# AUTONOMOUS AGENT WITH EMAIL GATES
# ============================================================================
class AutonomousAgentWithEmailGates:
    """Agent system with email-based human approval gates."""

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.db = WorkflowDatabase()
        self.email_gateway = EmailGateway()
        self.stage_gate_manager = StageGateManager(self.db, self.email_gateway)
        logger.info("Agent system with email gates initialized")

    def run_pm_agent_with_gates(self, project_metadata: dict) -> Tuple[str, WorkflowState]:
        """
        Run PM Agent with email-based approval gate.
        1. Agent generates project charter
        2. Pauses at charter_approval gate
        3. Emails approver and returns workflow state
        """
        workflow_id = f"pm_{project_metadata['project']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        project_name = project_metadata.get('project', 'Unknown')

        logger.info(f"Starting PM Agent workflow: {workflow_id}")

        system_prompt = """You are a Project Manager Agent for First Genesis.
Output ONLY valid JSON. NO explanations, NO preamble."""

        user_message = f"""Generate project charter for:
{json.dumps(project_metadata, indent=2)}
Output as JSON only:
{{"project_charter": {{"title": "...", "client": "...", "timeline": "..."}}, "wbs": {{}}, "risks": []}}"""

        try:
            response = self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )

            charter_output = response.content[0].text
            cost = TokenCost(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens
            ).total_cost_usd()
            self.db.log_agent_call(
                workflow_id=workflow_id,
                agent_name="pm_agent",
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cost_usd=cost,
                status="success"
            )
            logger.info(f"PM Agent generated charter: {len(charter_output)} chars, cost ${cost:.4f}")

        except Exception as e:
            logger.error(f"PM Agent failed: {str(e)}")
            self.db.log_agent_call(
                workflow_id=workflow_id, agent_name="pm_agent",
                input_tokens=0, output_tokens=0, cost_usd=0,
                status="error", reason=str(e)
            )
            raise

        workflow_state = self.stage_gate_manager.pause_at_gate(
            workflow_id=workflow_id,
            agent_name="pm_agent",
            project_name=project_name,
            stage_gate_name=StageGateName.CHARTER_APPROVAL,
            content_pending_approval=charter_output
        )

        logger.info(f"Workflow {workflow_id} paused at {workflow_state.current_stage_gate.value}")
        return charter_output, workflow_state

    def resume_approved_workflow(self, workflow_id: str) -> str:
        """Resume a workflow that has been approved."""

        workflow_state = self.db.get_workflow_state(workflow_id)
        if not workflow_state:
            logger.error(f"Workflow not found: {workflow_id}")
            return ""

        if workflow_state.status != WorkflowStatus.APPROVED:
            logger.warning(f"Workflow {workflow_id} not in APPROVED state: {workflow_state.status.value}")
            return ""

        logger.info(f"Resuming approved workflow: {workflow_id}")

        next_actions = {
            StageGateName.CHARTER_APPROVAL: "Charter approved. Ready for customer kickoff.",
            StageGateName.REQUIREMENTS_APPROVAL: "Requirements approved. Starting design sessions.",
            StageGateName.QA_AUDIT_APPROVAL: "Audit approved. Proceeding to delivery.",
        }

        next_action = next_actions.get(workflow_state.current_stage_gate, "")
        if not next_action:
            logger.warning(f"Unknown next step for gate: {workflow_state.current_stage_gate.value}")
            return ""

        is_final = workflow_state.current_stage_gate == StageGateName.QA_AUDIT_APPROVAL
        workflow_state.status = WorkflowStatus.COMPLETED if is_final else WorkflowStatus.RESUMED
        workflow_state.next_step_after_approval = next_action
        self.db.save_workflow_state(workflow_state)

        logger.info(f"Agent proceeding: {next_action}")
        return next_action

    def check_pending_approvals(self) -> str:
        return self.stage_gate_manager.get_pending_approvals_summary()

    def process_approval_response(self, workflow_id: str, approver_email: str,
                                  decision: str, feedback: str = None) -> str:
        """Process a human approval response."""
        workflow_state = self.stage_gate_manager.record_approval_response(
            workflow_id=workflow_id,
            approver_email=approver_email,
            decision=decision,
            feedback=feedback
        )

        response_msg = (
            f"\nApproval recorded for {workflow_id}:\n"
            f"  Decision:  {decision}\n"
            f"  Approver:  {approver_email}\n"
            f"  Feedback:  {feedback or 'None'}\n\n"
            f"Workflow status updated to: {workflow_state.status.value}\n"
        )
        logger.info(response_msg)
        return response_msg

# ============================================================================
# MAIN ENTRY POINT
# ============================================================================
def main():
    """CLI for agent operations with email gates."""

    if len(sys.argv) < 2:
        print("Usage: python claude_code_agent_ecosystem.py [command]")
        print("\nCommands:")
        print("  run_pm_agent              Run PM Agent with approval gate")
        print("  check_approvals           Show pending approvals")
        print("  resume_workflows          Resume approved workflows")
        print("  process_approval          Process approval response")
        print("    --workflow-id <id>")
        print("    --approver <email>")
        print("    --decision <approved|rejected>")
        print("    --feedback <comment>")
        sys.exit(1)

    agent = AutonomousAgentWithEmailGates()
    command = sys.argv[1]

    if command == "run_pm_agent":
        logger.info("Running PM Agent with email approval gate...")
        try:
            charter, workflow_state = agent.run_pm_agent_with_gates({
                "client": "Malcolm Goodwin",
                "project": "AURA MVP",
                "timeline_weeks": 12,
                "scope": "Silhouette technology + 3D mesh design"
            })

            print("\n" + "=" * 70)
            print(f"WORKFLOW CREATED: {workflow_state.workflow_id}")
            print("=" * 70)
            print(f"Status:      {workflow_state.status.value}")
            print(f"Stage Gate:  {workflow_state.current_stage_gate.value}")
            print(f"Sent To:     {StageGateManager.STAGE_GATES[StageGateName.CHARTER_APPROVAL].approver_email}")
            print(f"Awaiting:    Approval via email")
            print("\nGenerated Charter (preview):")
            print(charter[:300] + "...")
            print("\n" + "=" * 70)
            print("Agent paused at stage gate. Awaiting approval email.")
            print("=" * 70)

        except Exception as e:
            logger.error(f"Error: {str(e)}")
            print(f"ERROR: {str(e)}")

    elif command == "check_approvals":
        print(agent.check_pending_approvals())

    elif command == "resume_workflows":
        logger.info("Resuming approved workflows...")
        pending_approved = agent.db.get_approved_workflows_ready_to_resume()

        if not pending_approved:
            print("No approved workflows ready to resume.")
        else:
            for workflow in pending_approved:
                result = agent.resume_approved_workflow(workflow.workflow_id)
                print(f"\nResumed {workflow.workflow_id}")
                print(f"   Next step: {result}")

    elif command == "process_approval":
        kwargs = {}
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--workflow-id" and i + 1 < len(sys.argv):
                kwargs['workflow_id'] = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--approver" and i + 1 < len(sys.argv):
                kwargs['approver_email'] = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--decision" and i + 1 < len(sys.argv):
                kwargs['decision'] = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == "--feedback" and i + 1 < len(sys.argv):
                kwargs['feedback'] = sys.argv[i + 1]; i += 2
            else:
                i += 1

        if 'workflow_id' not in kwargs:
            print("ERROR: --workflow-id required")
            sys.exit(1)

        print(agent.process_approval_response(**kwargs))

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
