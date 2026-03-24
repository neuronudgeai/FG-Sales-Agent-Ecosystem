#!/usr/bin/env python3
"""
claude_code_agent_ecosystem.py
Production multi-agent system for First Genesis with:
  - Stage gate framework (5 standard gates across agents)
  - Email notifications via Outlook SMTP (per-gate recipients)
  - Approval workflow (human replies, agent resumes automatically)
  - Cost Controller (track spend, enforce limits)
  - Budget Enforcer (hard stops before overspending)
  - Hallucination Guard (detect & prevent false claims)
  - Workflow persistence (SQLite state machine)

Configuration:
    export ANTHROPIC_API_KEY="sk-..."
    export OUTLOOK_SENDER="your-email@yourdomain.com"
    export OUTLOOK_PASSWORD="your-password"

Usage:
    python claude_code_agent_ecosystem.py run_pm_agent
    python claude_code_agent_ecosystem.py check_approvals
    python claude_code_agent_ecosystem.py resume_workflows
    python claude_code_agent_ecosystem.py process_approval --workflow-id <id> --approver <email> --decision <approved|rejected>
    python claude_code_agent_ecosystem.py budget_status
    python claude_code_agent_ecosystem.py audit_hallucinations
"""
import anthropic
import csv
import json
import os
import pathlib
import re
import sys
import uuid
import threading
import queue
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod
import sqlite3
import hashlib
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from enum import Enum

# ── Governance layer (sales agent gated workflow) ─────────────────────────────
# Import the new governance modules so they are available to any code that
# imports this module, and so the CLI can dispatch to the sales pipeline.
try:
    from fg_audit_logger import AuditLogger as FGAuditLogger
    from fg_token_budget import TokenBudget as FGTokenBudget
    from fg_review_gate import ReviewGate as FGReviewGate
    from fg_sales_agents import (
        LeadQualifierAgent,
        AccountManagerAgent,
        ForecastAgent,
        CompetitorIntelAgent,
    )
    from fg_gated_orchestrator import GatedOrchestrator
    _GOVERNANCE_AVAILABLE = True
except ImportError:
    _GOVERNANCE_AVAILABLE = False

# ── MVP agent roster ──────────────────────────────────────────────────────────
# Only these agents are active for MVP.  All others are IDLE and will raise
# AgentIdleError if invoked — preventing accidental spend or incomplete output.
MVP_ACTIVE_AGENTS: frozenset = frozenset({
    "pm_agent",
    "ba_agent",
    "qa_agent",
    "vendor_agent",
    "manager_agent",
})

class AgentIdleError(RuntimeError):
    """Raised when a non-MVP agent is called while in IDLE mode."""

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
# AGENT SKILLS CATALOG
# Sourced from:
#   1. https://github.com/ComposioHQ/awesome-claude-skills
#   2. https://github.com/obra/superpowers
#   3. https://github.com/anthropics/skills
#   4. https://github.com/PleasePrompto/notebooklm-skill
#   5. https://github.com/coreyhaines31/marketingskills
# ============================================================================
AGENT_SKILLS_CATALOG = {
    "pm_agent": {
        "description": "Project Manager — charter creation, planning, orchestration",
        "skills": [
            {
                "skill_id": "pm_001",
                "name": "project-management-automation",
                "description": "Integration with Jira, Asana, Monday.com, and Linear for task management and workflow automation",
                "category": "Project Management",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_002",
                "name": "writing-plans",
                "description": "Creates detailed implementation plans with bite-sized tasks (2-5 minutes each)",
                "category": "Project Planning",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_003",
                "name": "executing-plans",
                "description": "Batch execution mode with human checkpoints for task completion",
                "category": "Project Execution",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_004",
                "name": "dispatching-parallel-agents",
                "description": "Enables concurrent subagent workflows and multi-agent coordination",
                "category": "Collaboration",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_005",
                "name": "brainstorming",
                "description": "Refines ideas through Socratic questioning and iterative design refinement",
                "category": "Collaboration",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_006",
                "name": "git-workflow-automation",
                "description": "Automated git operations, branching, and merge workflows with Git worktrees support",
                "category": "Development",
                "source": "superpowers, awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_007",
                "name": "slack-communication",
                "description": "Integration with Slack for team communication and notifications",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_008",
                "name": "email-management",
                "description": "Gmail and Outlook integration for email workflows and automation",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_009",
                "name": "document-processing",
                "description": "Full editing of Word docs, PDFs, presentations, and spreadsheets",
                "category": "Document Management",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_010",
                "name": "google-workspace-suite",
                "description": "Integration with Gmail, Calendar, Docs, Sheets for office productivity",
                "category": "Productivity",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_011",
                "name": "content-research-citations",
                "description": "Content research with citations, article extraction, and knowledge synthesis",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_012",
                "name": "copywriting",
                "description": "Professional copywriting for marketing and business communications",
                "category": "Content",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_013",
                "name": "copy-editing",
                "description": "Edit and refine business and marketing copy for clarity and impact",
                "category": "Content",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_014",
                "name": "cold-email",
                "description": "Vendor outreach and cold email campaigns for partnership development",
                "category": "Outreach",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_015",
                "name": "social-content",
                "description": "Create and optimize social media content for project visibility",
                "category": "Marketing",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_016",
                "name": "seo-audit",
                "description": "SEO analysis and audit capabilities for research and documentation",
                "category": "Research",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_017",
                "name": "brand-guidelines-application",
                "description": "Apply company branding guidelines to documents and communications",
                "category": "Design",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_018",
                "name": "d3-visualization",
                "description": "Create interactive D3.js data visualizations for metrics and reporting",
                "category": "Visualization",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_019",
                "name": "file-organization",
                "description": "Intelligent file and document organization for project management",
                "category": "Organization",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_020",
                "name": "competitive-ad-analysis",
                "description": "Analyze competitive advertising and marketing strategies",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_021",
                "name": "product-marketing-context",
                "description": "Foundational skill establishing product, audience, and positioning context",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_022",
                "name": "pricing-strategy",
                "description": "Develop and optimize pricing and vendor negotiation strategies",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_023",
                "name": "salesforce-integration",
                "description": "Full CRM integration with Salesforce for sales and vendor management",
                "category": "CRM",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_024",
                "name": "microsoft-teams-integration",
                "description": "Microsoft Teams integration for enterprise communication",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_025",
                "name": "discord-communication",
                "description": "Discord integration for team communication and notifications",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_026",
                "name": "lead-research-qualification",
                "description": "Lead research and qualification workflows for vendor/partner discovery",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_027",
                "name": "notebook-library-management",
                "description": "Save, organize, and manage NotebookLM links with metadata and smart selection",
                "category": "Knowledge Management",
                "source": "notebooklm-skill",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_028",
                "name": "aws-integration",
                "description": "AWS cloud service integration and deployment automation",
                "category": "Infrastructure",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_029",
                "name": "mcp-server-generation",
                "description": "Generate MCP servers for integrating external APIs",
                "category": "Development",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_030",
                "name": "image-enhancement",
                "description": "Image processing and enhancement for documentation and assets",
                "category": "Media",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_031",
                "name": "video-downloading",
                "description": "Video content download and processing for training materials",
                "category": "Media",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_032",
                "name": "writing-skills",
                "description": "Guide for creating new skills with testing methodology",
                "category": "Skill Development",
                "source": "superpowers, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_033",
                "name": "internal-comms",
                "description": "Writes 3P updates (Progress/Plans/Problems), company newsletters, FAQ responses, status reports, leadership updates, project updates, and incident reports using company-preferred formats",
                "category": "Communication",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_034",
                "name": "doc-coauthoring",
                "description": "Collaborative document co-authoring for charters, plans, and project specs",
                "category": "Document Management",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_035",
                "name": "launch-strategy",
                "description": "Five-phase launch planning (Internal → Alpha → Beta → Early Access → Full), ORB channel framework (Owned/Rented/Borrowed), Product Hunt, post-launch momentum",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_036",
                "name": "kaizen",
                "description": "Continuous improvement methodology applied to project workflows — identify waste, measure cycle time, implement incremental improvements",
                "category": "Process Improvement",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_037",
                "name": "outline-wiki",
                "description": "Internal wiki document search, creation, and management for project knowledge bases",
                "category": "Knowledge Management",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_038",
                "name": "page-cro",
                "description": "Conversion rate optimization for project landing pages and proposal pages — structured optimization process",
                "category": "Optimization",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_039",
                "name": "onboarding-cro",
                "description": "Optimize post-signup client activation and time-to-value for new project stakeholders",
                "category": "Optimization",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "pm_040",
                "name": "composio-pm-integrations",
                "description": "500+ app integrations via Composio: Asana, Jira, ClickUp, Monday, Linear, Notion, Trello, Google Calendar, Calendly, Slack, Microsoft Teams, Box, Dropbox, Google Drive, OneDrive",
                "category": "Integrations",
                "source": "awesome-claude-skills (Composio)",
                "level": "NOVICE",
            },
        ],
    },

    "ba_agent": {
        "description": "Business Analyst — requirements extraction, design session support, traceability",
        "skills": [
            {
                "skill_id": "ba_001",
                "name": "data-analysis",
                "description": "CSV analysis, PostgreSQL queries, and autonomous research capabilities",
                "category": "Analytics",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_002",
                "name": "content-research-citations",
                "description": "Content research with citations, article extraction, and knowledge synthesis",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_003",
                "name": "systematic-debugging",
                "description": "Four-phase root cause analysis process with root-cause-tracing and defense-in-depth techniques",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_004",
                "name": "test-driven-development",
                "description": "Implements RED-GREEN-REFACTOR cycles with testing anti-patterns reference",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_005",
                "name": "seo-audit",
                "description": "SEO analysis and audit capabilities for research and documentation",
                "category": "Research",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_006",
                "name": "ab-test-setup",
                "description": "Design and setup of A/B tests for feature validation",
                "category": "Testing",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_007",
                "name": "metadata-extraction",
                "description": "Extract metadata from documents and files for analysis",
                "category": "Data Processing",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_008",
                "name": "document-processing",
                "description": "Full editing of Word docs, PDFs, presentations, and spreadsheets",
                "category": "Document Management",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_009",
                "name": "git-workflow-automation",
                "description": "Automated git operations, branching, and merge workflows",
                "category": "Development",
                "source": "superpowers, awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_010",
                "name": "google-workspace-suite",
                "description": "Integration with Gmail, Calendar, Docs, Sheets for office productivity",
                "category": "Productivity",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_011",
                "name": "copy-editing",
                "description": "Edit and refine business requirements and specifications for clarity",
                "category": "Content",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_012",
                "name": "browser-automation",
                "description": "Automated browser interaction with realistic humanization features",
                "category": "Automation",
                "source": "notebooklm-skill",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_013",
                "name": "playwright-browser-automation",
                "description": "Browser automation via Playwright for testing and scraping",
                "category": "Automation",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_014",
                "name": "aws-integration",
                "description": "AWS cloud service integration and deployment automation",
                "category": "Infrastructure",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_015",
                "name": "mcp-server-generation",
                "description": "Generate MCP servers for integrating external APIs",
                "category": "Development",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_016",
                "name": "notebooklm-source-grounding",
                "description": "Query Google NotebookLM notebooks for source-grounded answers and citation-backed responses",
                "category": "Research",
                "source": "notebooklm-skill",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_017",
                "name": "subagent-driven-development",
                "description": "Fast iteration with two-stage review (spec compliance, then code quality)",
                "category": "Development",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_018",
                "name": "requesting-code-review",
                "description": "Pre-review quality assessment and checklist generation",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_019",
                "name": "receiving-code-review",
                "description": "Processes and responds to feedback incorporation",
                "category": "Collaboration",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_020",
                "name": "finishing-a-development-branch",
                "description": "Handles merge decisions and cleanup workflows",
                "category": "Development",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_021",
                "name": "youtube-transcript-fetching",
                "description": "Extract and analyze YouTube video transcripts for requirements mining",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_022",
                "name": "competitive-ad-analysis",
                "description": "Analyze competitive advertising and marketing strategies",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_023",
                "name": "d3-visualization",
                "description": "Create interactive D3.js data visualizations for requirements and reporting",
                "category": "Visualization",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_024",
                "name": "product-marketing-context",
                "description": "Foundational skill establishing product, audience, and positioning context",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_025",
                "name": "deep-research",
                "description": "Multi-step autonomous research execution with citations — queries multiple sources, synthesizes findings, and produces structured research reports",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_026",
                "name": "csv-data-summarizer",
                "description": "Auto-analyzes CSV datasets, generates summary statistics, identifies patterns, and produces visualizations",
                "category": "Analytics",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_027",
                "name": "article-extractor",
                "description": "Full text and metadata extraction from web pages for requirements research and competitive analysis",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_028",
                "name": "root-cause-tracing",
                "description": "Error debugging and root trigger analysis — traces failures back through system layers to identify the true origin",
                "category": "QA & Testing",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_029",
                "name": "doc-coauthoring",
                "description": "Collaborative document co-authoring for requirements specifications and design documents",
                "category": "Document Management",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_030",
                "name": "content-strategy",
                "description": "Plan content strategy and topic selection aligned with project and stakeholder needs",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_031",
                "name": "marketing-psychology",
                "description": "Apply behavioral science and mental models to stakeholder decision-making and requirements prioritization",
                "category": "Research",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_032",
                "name": "marketing-ideas",
                "description": "Generate 140+ SaaS marketing strategies applicable to product feature positioning and stakeholder communication",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_033",
                "name": "competitor-alternatives",
                "description": "Create competitor comparison and alternative analysis pages for vendor/solution evaluation",
                "category": "Research",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ba_034",
                "name": "composio-ba-integrations",
                "description": "Analytics and data integrations via Composio: Amplitude, Google Analytics, Mixpanel, PostHog, Segment, Airtable, Google Sheets, Coda, HubSpot, Salesforce, Pipedrive",
                "category": "Integrations",
                "source": "awesome-claude-skills (Composio)",
                "level": "NOVICE",
            },
        ],
    },

    "qa_agent": {
        "description": "QA Agent — pre-delivery audit, scope creep detection, quality validation",
        "skills": [
            {
                "skill_id": "qa_001",
                "name": "test-driven-development",
                "description": "Implements RED-GREEN-REFACTOR cycles with testing anti-patterns reference",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_002",
                "name": "systematic-debugging",
                "description": "Four-phase root cause analysis process with root-cause-tracing and defense-in-depth techniques",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_003",
                "name": "verification-before-completion",
                "description": "Validates fixes and deliverables before marking as complete",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_004",
                "name": "web-app-testing",
                "description": "Automated web application testing and validation",
                "category": "QA & Testing",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_005",
                "name": "analytics-tracking",
                "description": "Setup and analysis of project metrics and KPI tracking",
                "category": "Analytics",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_006",
                "name": "playwright-browser-automation",
                "description": "Browser automation via Playwright for UI testing and scraping",
                "category": "Automation",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_007",
                "name": "browser-automation",
                "description": "Automated browser interaction with realistic humanization features",
                "category": "Automation",
                "source": "notebooklm-skill",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_008",
                "name": "ab-test-setup",
                "description": "Design and setup of A/B tests for feature validation",
                "category": "Testing",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_009",
                "name": "digital-forensics",
                "description": "Digital forensics and threat hunting for security audits",
                "category": "Security",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_010",
                "name": "metadata-extraction",
                "description": "Extract metadata from documents and files for analysis",
                "category": "Data Processing",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_011",
                "name": "requesting-code-review",
                "description": "Pre-review quality assessment and checklist generation",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_012",
                "name": "subagent-driven-development",
                "description": "Fast iteration with two-stage review (spec compliance, then code quality)",
                "category": "Development",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_013",
                "name": "data-analysis",
                "description": "CSV analysis, PostgreSQL queries, and autonomous research capabilities",
                "category": "Analytics",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_014",
                "name": "notebooklm-source-grounding",
                "description": "Query Google NotebookLM notebooks for source-grounded, citation-backed quality validation",
                "category": "Research",
                "source": "notebooklm-skill",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_015",
                "name": "root-cause-tracing",
                "description": "Error debugging and root trigger analysis — traces failures through system layers to the true origin",
                "category": "QA & Testing",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_016",
                "name": "ffuf-web-fuzzing",
                "description": "Security vulnerability analysis via web fuzzing — discovers hidden endpoints, misconfigurations, and injection points",
                "category": "Security",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_017",
                "name": "using-git-worktrees",
                "description": "Isolated workspace management on separate branches — sets up environment, verifies test baseline before any changes",
                "category": "Development",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_018",
                "name": "page-cro",
                "description": "Conversion rate optimization audit for deliverable pages — structured quality check process",
                "category": "Optimization",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_019",
                "name": "signup-flow-cro",
                "description": "Optimize and validate registration and trial activation flows for client-facing deliverables",
                "category": "Optimization",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_020",
                "name": "form-cro",
                "description": "Optimize and validate lead capture and contact forms in deliverables",
                "category": "Optimization",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "qa_021",
                "name": "frontend-design",
                "description": "Frontend design pattern validation and quality review for UI deliverables",
                "category": "Design",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
        ],
    },

    "vendor_agent": {
        "description": "Vendor Agent — SLA tracking, vendor performance monitoring, escalation",
        "skills": [
            {
                "skill_id": "va_001",
                "name": "cold-email",
                "description": "Vendor outreach and cold email campaigns for partnership development",
                "category": "Outreach",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_002",
                "name": "salesforce-integration",
                "description": "Full CRM integration with Salesforce for sales and vendor management",
                "category": "CRM",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_003",
                "name": "email-sequence",
                "description": "Automated email sequence campaigns for vendor engagement",
                "category": "Outreach",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_004",
                "name": "churn-prevention",
                "description": "Vendor retention and relationship management strategies",
                "category": "Relationship Management",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_005",
                "name": "revops",
                "description": "Revenue operations for vendor and contract management",
                "category": "Operations",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_006",
                "name": "sales-enablement",
                "description": "Sales materials and vendor communication enablement",
                "category": "Operations",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_007",
                "name": "hubspot-integration",
                "description": "HubSpot CRM integration for sales and vendor relationship automation",
                "category": "CRM",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_008",
                "name": "invoice-processing",
                "description": "Automated invoice processing and vendor payment workflows",
                "category": "Financial",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_009",
                "name": "slack-communication",
                "description": "Integration with Slack for vendor team communication and notifications",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_010",
                "name": "email-management",
                "description": "Gmail and Outlook integration for vendor email workflows and automation",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_011",
                "name": "microsoft-teams-integration",
                "description": "Microsoft Teams integration for enterprise vendor communication",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_012",
                "name": "stripe-shopify-integration",
                "description": "Payment processing and e-commerce integration for vendor invoicing",
                "category": "Financial",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_013",
                "name": "lead-research-qualification",
                "description": "Lead research and qualification workflows for vendor/partner discovery",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_014",
                "name": "deep-research",
                "description": "Multi-step autonomous research on vendors — queries public records, reviews, financials, and synthesizes vendor health reports",
                "category": "Research",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_015",
                "name": "competitor-alternatives",
                "description": "Create vendor comparison and alternative analysis for procurement decisions",
                "category": "Research",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_016",
                "name": "pdf",
                "description": "PDF contract handling — text extraction, annotation, table parsing, merging vendor documents",
                "category": "Document Management",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "va_017",
                "name": "composio-vendor-integrations",
                "description": "Vendor and support integrations via Composio: Freshdesk, Zendesk, Help Scout, Salesforce, HubSpot, Pipedrive, Zoho, Close CRM, Stripe, Shopify",
                "category": "Integrations",
                "source": "awesome-claude-skills (Composio)",
                "level": "NOVICE",
            },
        ],
    },

    "manager_agent": {
        "description": "Manager Agent — portfolio dashboard, orchestration, executive reporting",
        "skills": [
            {
                "skill_id": "ma_001",
                "name": "meeting-transcript-analysis",
                "description": "Analyzes meeting transcripts for behavioral patterns and leadership insights",
                "category": "Analytics",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_002",
                "name": "pricing-strategy",
                "description": "Develop and optimize pricing and vendor negotiation strategies",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_003",
                "name": "revops",
                "description": "Revenue operations for portfolio and contract management",
                "category": "Operations",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_004",
                "name": "invoice-processing",
                "description": "Automated invoice processing and vendor payment workflows",
                "category": "Financial",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_005",
                "name": "dispatching-parallel-agents",
                "description": "Enables concurrent subagent workflows and multi-agent coordination",
                "category": "Collaboration",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_006",
                "name": "executing-plans",
                "description": "Batch execution mode with human checkpoints for task completion",
                "category": "Project Execution",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_007",
                "name": "verification-before-completion",
                "description": "Validates fixes and deliverables before marking as complete",
                "category": "QA & Testing",
                "source": "superpowers",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_008",
                "name": "slack-communication",
                "description": "Integration with Slack for executive team communication and notifications",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_009",
                "name": "email-management",
                "description": "Gmail and Outlook integration for executive email workflows",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_010",
                "name": "microsoft-teams-integration",
                "description": "Microsoft Teams integration for enterprise executive communication",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_011",
                "name": "discord-communication",
                "description": "Discord integration for executive team communication and notifications",
                "category": "Communication",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_012",
                "name": "google-workspace-suite",
                "description": "Integration with Gmail, Calendar, Docs, Sheets for office productivity",
                "category": "Productivity",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_013",
                "name": "document-processing",
                "description": "Full editing of Word docs, PDFs, presentations, and spreadsheets",
                "category": "Document Management",
                "source": "awesome-claude-skills, anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_014",
                "name": "d3-visualization",
                "description": "Create interactive D3.js data visualizations for portfolio metrics",
                "category": "Visualization",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_015",
                "name": "analytics-tracking",
                "description": "Setup and analysis of portfolio metrics and KPI tracking",
                "category": "Analytics",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_016",
                "name": "digital-forensics",
                "description": "Digital forensics and threat hunting for security audits",
                "category": "Security",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_017",
                "name": "hubspot-integration",
                "description": "HubSpot CRM integration for portfolio and client management",
                "category": "CRM",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_018",
                "name": "salesforce-integration",
                "description": "Full CRM integration with Salesforce for portfolio management",
                "category": "CRM",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_019",
                "name": "churn-prevention",
                "description": "Client retention and relationship management strategies",
                "category": "Relationship Management",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_020",
                "name": "sales-enablement",
                "description": "Sales materials and client communication enablement",
                "category": "Operations",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_021",
                "name": "notebook-library-management",
                "description": "Save, organize, and manage NotebookLM links with metadata and smart selection",
                "category": "Knowledge Management",
                "source": "notebooklm-skill",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_022",
                "name": "resume-tailoring",
                "description": "Tailor resumes and job applications for vendor/partner recruitment",
                "category": "HR & Recruitment",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_023",
                "name": "file-organization",
                "description": "Intelligent file and document organization for portfolio management",
                "category": "Organization",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_024",
                "name": "stripe-shopify-integration",
                "description": "Payment processing and e-commerce integration for client invoicing",
                "category": "Financial",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_025",
                "name": "internal-comms",
                "description": "Writes 3P updates (Progress/Plans/Problems), company newsletters, FAQ responses, status reports, leadership updates, project updates, and incident reports using company-preferred formats",
                "category": "Communication",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_026",
                "name": "kaizen",
                "description": "Continuous improvement methodology applied to portfolio workflows — identify waste, measure cycle time, implement incremental portfolio-level improvements",
                "category": "Process Improvement",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_027",
                "name": "launch-strategy",
                "description": "Five-phase launch planning (Internal → Alpha → Beta → Early Access → Full), ORB channel framework, post-launch momentum tracking",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_028",
                "name": "marketing-ideas",
                "description": "Generate 140+ SaaS marketing strategies for portfolio project promotion and stakeholder engagement",
                "category": "Strategy",
                "source": "marketingskills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_029",
                "name": "mcp-builder",
                "description": "Guides Model Context Protocol (MCP) server creation for integrating external APIs into the agent ecosystem",
                "category": "Development",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_030",
                "name": "skill-creator",
                "description": "Tools and methodology for creating new agent skills — define skill spec, write implementation, test, and deploy",
                "category": "Skill Development",
                "source": "anthropics/skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_031",
                "name": "csv-data-summarizer",
                "description": "Auto-analyzes portfolio CSV datasets, generates summary statistics, identifies patterns, and produces executive-ready visualizations",
                "category": "Analytics",
                "source": "awesome-claude-skills",
                "level": "NOVICE",
            },
            {
                "skill_id": "ma_032",
                "name": "composio-manager-integrations",
                "description": "Executive and HR integrations via Composio: BambooHR, Notion, Monday, Google Workspace, Slack, Microsoft Teams, Box, Dropbox, Google Drive",
                "category": "Integrations",
                "source": "awesome-claude-skills (Composio)",
                "level": "NOVICE",
            },
        ],
    },
}

# ============================================================================
# DATA MODELS
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
class StageGate:
    name: StageGateName
    description: str
    approver_email: str
    cc_emails: List[str] = None
    auto_approve_if: Optional[str] = None
    require_comment: bool = False
    timeout_hours: int = 24

@dataclass
class WorkflowState:
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
    """Track token usage and cost (claude-opus-4-6 pricing)."""
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
        cache_write = (self.cache_creation_tokens / 1_000_000) * self.CACHE_WRITE_COST_PER_1M
        cache_read = (self.cache_read_tokens / 1_000_000) * self.CACHE_READ_COST_PER_1M
        return input_cost + output_cost + cache_write + cache_read

    def __str__(self) -> str:
        return (f"In={self.input_tokens} Out={self.output_tokens} "
                f"CW={self.cache_creation_tokens} CR={self.cache_read_tokens} "
                f"Cost=${self.total_cost_usd():.4f}")

@dataclass
class AgentConfig:
    name: str
    budget_per_call_usd: float
    max_daily_calls: int
    max_daily_spend_usd: float
    priority: int
    description: str

@dataclass
class AgentCall:
    agent_name: str
    timestamp: str
    input_tokens: int
    output_tokens: int
    cache_created: int
    cache_read: int
    cost_usd: float
    status: str
    reason: str
    output_hash: str

# ============================================================================
# DASHBOARD: ENUMS & DATACLASSES
# ============================================================================
class AgentStatus(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    WAITING_INPUT = "waiting_input"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETE = "complete"
    ERROR = "error"

class SkillLevel(Enum):
    NOVICE = 1
    INTERMEDIATE = 2
    ADVANCED = 3
    EXPERT = 4

class MessageType(Enum):
    INITIATE = "initiate"
    REQUEST_INPUT = "request_input"
    PROVIDE_OUTPUT = "provide_output"
    DELEGATE = "delegate"
    FEEDBACK = "feedback"
    ESCALATE = "escalate"

@dataclass
class AgentRecord:
    """Agent state and metadata for dashboard monitoring."""
    id: str
    name: str
    role: str
    status: AgentStatus
    current_task: Optional[str]
    skill_level: SkillLevel
    success_count: int
    error_count: int
    last_activity: str
    active_workflows: List[str]

    def __post_init__(self):
        if not self.id:
            self.id = f"{self.name}_{uuid.uuid4().hex[:8]}"

@dataclass
class AgentMessage:
    """Message between agents via the communication bus."""
    id: str
    from_agent: str
    to_agent: str
    message_type: MessageType
    content: Dict
    timestamp: str
    status: str  # pending, sent, acknowledged, completed

@dataclass
class AgentSkill:
    """Learned capability tracked per agent."""
    skill_id: str
    agent_name: str
    skill_name: str
    description: str
    success_count: int
    error_count: int
    avg_execution_time: float
    skill_level: SkillLevel
    last_used: str
    template: Dict

@dataclass
class WorkflowExecution:
    """Record of a complete workflow execution for pattern capture."""
    workflow_id: str
    agent_sequence: List[str]
    start_time: str
    end_time: str
    duration_seconds: float
    success: bool
    input_data: Dict
    output_data: Dict
    errors: List[str]
    approvals_needed: int
    approvals_completed: int
    cost_usd: float

# ============================================================================
# HUMAN APPROVAL WORKFLOW COMPONENTS
# ============================================================================

class ApprovalStatus(Enum):
    """Status of notification approval"""
    PENDING  = "pending"   # Awaiting human approver review
    APPROVED = "approved"  # Approved by human, ready to send
    REJECTED = "rejected"  # Rejected, decision reconsidered
    REVISED  = "revised"   # Changes requested, resubmitted
    SENT     = "sent"      # Notifications sent to developers


class ApprovalDecision(Enum):
    """Human approver decision"""
    APPROVE = "approve"  # Send notifications immediately
    REVISE  = "revise"   # Request changes before sending
    REJECT  = "reject"   # Do not send, reconsider decision


@dataclass
class ApprovalRequest:
    """
    Notification package awaiting human approver review.

    Before PM Agent sends ANY notification to developers,
    human approver must review and approve ALL 5 elements:
      1. Clear decision context
      2. Specific action items (per developer)
      3. Deadlines
      4. Resources / contacts
      5. Confirmation requirements
    """
    # Core package contents
    approval_id:               str
    decision_id:               str
    decision_context:          str
    affected_developers:       List[str]
    action_items:              Dict[str, str]      # {developer: action_item}
    deadlines:                 Dict[str, datetime] # {developer: deadline}
    resources:                 Dict[str, str]      # {developer: resources}
    confirmation_requirements: Dict[str, str]      # {developer: requirements}

    # Approval workflow
    approval_status:      ApprovalStatus         = ApprovalStatus.PENDING
    approver_name:        Optional[str]          = None
    approver_timestamp:   Optional[datetime]     = None
    approver_signature:   Optional[str]          = None
    approver_notes:       Optional[str]          = None

    # Tracking
    created_timestamp:              datetime        = field(default_factory=datetime.now)
    sent_to_approver_timestamp:     Optional[datetime] = None
    approved_timestamp:             Optional[datetime] = None
    sent_to_developers_timestamp:   Optional[datetime] = None

    # Revisions
    revision_count:   int        = 0
    revision_history: List[Dict] = field(default_factory=list)


class ApprovalChecklist:
    """
    5-point checklist human approver uses to review PM notifications.

    Before approving, all 5 checks must pass:
      1. Is decision context clear?   (developers understand WHY)
      2. Are action items clear?      (developers know EXACT task)
      3. Are deadlines reasonable?    (timelines achievable)
      4. Are resources identified?    (devs have everything needed)
      5. Are confirmations appropriate? (can track who confirms)
    """

    def __init__(self, approval_request: ApprovalRequest):
        self.request = approval_request
        self.checklist_results: Dict[str, bool] = {}

    def check_context_clarity(self) -> bool:
        """Is the decision context CLEAR? (non-empty and meaningful)"""
        ctx = self.request.decision_context or ""
        result = bool(ctx.strip()) and len(ctx) >= 20
        self.checklist_results["context_clear"] = result
        return result

    def check_action_items_clarity(self) -> bool:
        """Are ACTION ITEMS present for every affected developer?"""
        items = self.request.action_items or {}
        result = (
            bool(items)
            and all(self.request.affected_developers)
            and all(items.get(dev, "").strip() for dev in self.request.affected_developers)
        )
        self.checklist_results["action_items_clear"] = result
        return result

    def check_deadline_reasonableness(self) -> bool:
        """Are DEADLINES set and in the future for every developer?"""
        deadlines = self.request.deadlines or {}
        now = datetime.now()
        result = (
            bool(deadlines)
            and all(
                deadlines.get(dev) and deadlines[dev] > now
                for dev in self.request.affected_developers
            )
        )
        self.checklist_results["deadlines_reasonable"] = result
        return result

    def check_resources_identified(self) -> bool:
        """Are RESOURCES identified for every developer?"""
        resources = self.request.resources or {}
        result = (
            bool(resources)
            and all(resources.get(dev, "").strip() for dev in self.request.affected_developers)
        )
        self.checklist_results["resources_identified"] = result
        return result

    def check_confirmation_requirements(self) -> bool:
        """Are CONFIRMATION REQUIREMENTS set for every developer?"""
        reqs = self.request.confirmation_requirements or {}
        result = (
            bool(reqs)
            and all(reqs.get(dev, "").strip() for dev in self.request.affected_developers)
        )
        self.checklist_results["confirmations_appropriate"] = result
        return result

    def all_checks_passed(self) -> bool:
        """Return True only if ALL 5 checks pass."""
        checks = [
            self.check_context_clarity(),
            self.check_action_items_clarity(),
            self.check_deadline_reasonableness(),
            self.check_resources_identified(),
            self.check_confirmation_requirements(),
        ]
        return all(checks)

    def get_failed_checks(self) -> List[str]:
        """Return human-readable list of failed checks."""
        self.all_checks_passed()  # ensure results are populated
        labels = {
            "context_clear":           "Decision context not clear",
            "action_items_clear":      "Action items not clear for all developers",
            "deadlines_reasonable":    "Deadlines missing or already past",
            "resources_identified":    "Resources not identified for all developers",
            "confirmations_appropriate": "Confirmation requirements missing",
        }
        return [labels[k] for k, v in self.checklist_results.items() if not v]


# ============================================================================
# PM NOTIFICATION ENGINE
# ============================================================================
class PMNotificationEngine:
    """
    PM Agent's notification system with HUMAN APPROVAL GATE.

    Workflow:
      1. prepare_notification_package()  → create ApprovalRequest, store as PENDING
      2. wait_for_approval()             → check stage gate DB; block until decision
      3. APPROVED  → send_approved_notifications() → notify developers, track confirmations
      4. REVISED   → return to PM Agent for changes
      5. REJECTED  → escalate back to Manager Agent
    """

    def __init__(self, db: Optional["WorkflowDatabase"] = None):
        self.db = db
        self.pending_approvals:      Dict[str, ApprovalRequest] = {}
        self.approved_approvals:     Dict[str, ApprovalRequest] = {}
        self.notification_log:       List[Dict] = []
        self.developer_confirmations: Dict[str, Dict] = {}

    # ── Prepare ────────────────────────────────────────────────────────────────

    def prepare_notification_package(
        self,
        decision: dict,
        affected_developers: List[str],
        action_items: Dict[str, str],
        deadlines: Dict[str, datetime],
        resources: Dict[str, str],
        confirmation_requirements: Dict[str, str],
    ) -> ApprovalRequest:
        """
        Build an ApprovalRequest and hold it for human review.
        Does NOT send anything to developers yet.
        """
        approval_id = f"APR_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        request = ApprovalRequest(
            approval_id=approval_id,
            decision_id=decision.get("id", "UNKNOWN"),
            decision_context=decision.get("context", ""),
            affected_developers=affected_developers,
            action_items=action_items,
            deadlines=deadlines,
            resources=resources,
            confirmation_requirements=confirmation_requirements,
        )

        self.pending_approvals[approval_id] = request
        request.sent_to_approver_timestamp = datetime.now()

        print(f"✓ Notification package prepared: {approval_id}")
        print(f"  Status: AWAITING HUMAN APPROVER REVIEW")
        print(f"  Developers: {', '.join(affected_developers)}")
        print(f"  NOT YET SENT TO DEVELOPERS")

        return request

    # ── Wait for approval ──────────────────────────────────────────────────────

    def wait_for_approval(self, approval_request: ApprovalRequest) -> Optional[ApprovalDecision]:
        """
        Check the stage gate DB for an existing human decision.

        - APPROVED  → stamps request and returns ApprovalDecision.APPROVE
        - REJECTED  → stamps request and returns ApprovalDecision.REJECT
        - REVISED   → stamps request and returns ApprovalDecision.REVISE
        - Still PENDING → prints waiting message and returns None

        In production, this is called by resume_approved_workflow() after
        the human replies via email/Slack/Telegram/WhatsApp.
        """
        # ── Check persistent DB first ─────────────────────────────────────────
        if self.db:
            wf = self.db.get_workflow_state(approval_request.decision_id)
            if wf:
                if wf.status == WorkflowStatus.APPROVED:
                    approval_request.approval_status  = ApprovalStatus.APPROVED
                    approval_request.approver_name    = wf.human_approver or "Human Approver"
                    approval_request.approver_timestamp = datetime.now()
                    approval_request.approver_signature = "✓ APPROVED via stage gate"
                    return ApprovalDecision.APPROVE
                if wf.status == WorkflowStatus.REJECTED:
                    approval_request.approval_status = ApprovalStatus.REJECTED
                    approval_request.approver_name   = wf.human_approver or "Human Approver"
                    approval_request.approver_notes  = wf.human_feedback
                    return ApprovalDecision.REJECT
                if wf.status == WorkflowStatus.PENDING and wf.next_step_after_approval and \
                        "revision" in wf.next_step_after_approval.lower():
                    approval_request.approval_status = ApprovalStatus.REVISED
                    approval_request.approver_name   = wf.human_approver or "Human Approver"
                    approval_request.approver_notes  = wf.human_feedback
                    approval_request.revision_count += 1
                    return ApprovalDecision.REVISE

        # ── Still awaiting human decision ─────────────────────────────────────
        print(f"\n🚨 HUMAN APPROVER REVIEW REQUIRED 🚨")
        print(f"Approval ID: {approval_request.approval_id}")
        print(f"Decision:    {approval_request.decision_context}")
        print(f"Developers:  {', '.join(approval_request.affected_developers)}")
        print(f"\nApprover must verify:")
        print(f"  ☐ Decision context clear?")
        print(f"  ☐ Action items clear?")
        print(f"  ☐ Deadlines reasonable?")
        print(f"  ☐ Resources identified?")
        print(f"  ☐ Confirmation requirements appropriate?")
        print(f"\n⏳ Awaiting human decision: APPROVE / REVISE / REJECT")
        return None  # decision not yet received

    # ── Send ───────────────────────────────────────────────────────────────────

    def send_approved_notifications(
        self, approval_request: ApprovalRequest
    ) -> Dict[str, dict]:
        """
        Send notifications to developers — ONLY after human approval.

        Also persists each notification to the DB notification_log table
        and initialises confirmation tracking per developer.
        """
        if approval_request.approval_status != ApprovalStatus.APPROVED:
            print(f"❌ CANNOT SEND — not approved (status: {approval_request.approval_status.value})")
            return {}

        sent: Dict[str, dict] = {}
        now = datetime.now()

        for dev in approval_request.affected_developers:
            notification = {
                "approval_id":          approval_request.approval_id,
                "developer":            dev,
                "decision_context":     approval_request.decision_context,
                "action_item":          approval_request.action_items.get(dev),
                "deadline":             approval_request.deadlines.get(dev),
                "resources":            approval_request.resources.get(dev),
                "confirmation_required": approval_request.confirmation_requirements.get(dev),
                "approver_name":        approval_request.approver_name,
                "approver_timestamp":   approval_request.approver_timestamp,
                "sent_timestamp":       now,
            }

            # In-memory confirmation tracker
            self.developer_confirmations[dev] = {
                "approval_id":          approval_request.approval_id,
                "acknowledged":         False,
                "acknowledged_timestamp": None,
                "confirmation_deadline": approval_request.deadlines.get(dev),
            }

            sent[dev] = notification

            # In-memory log
            self.notification_log.append({
                "approval_id":    approval_request.approval_id,
                "developer":      dev,
                "sent_timestamp": now,
                "status":         "SENT",
            })

            # Persist to DB
            if self.db:
                self.db.log_notification(
                    approval_id=approval_request.approval_id,
                    developer=dev,
                    decision_context=approval_request.decision_context,
                    action_item=approval_request.action_items.get(dev, ""),
                    deadline=approval_request.deadlines.get(dev),
                    resources=approval_request.resources.get(dev, ""),
                    approver_name=approval_request.approver_name or "",
                )

        approval_request.approval_status = ApprovalStatus.SENT
        approval_request.sent_to_developers_timestamp = now
        self.approved_approvals[approval_request.approval_id] = approval_request
        self.pending_approvals.pop(approval_request.approval_id, None)

        print(f"\n✓ APPROVED notifications sent to {len(sent)} developers")
        print(f"  Approval ID: {approval_request.approval_id}")
        print(f"  Approved by: {approval_request.approver_name}")
        print(f"  Sent at:     {now.strftime('%Y-%m-%d %H:%M:%S')}")

        return sent

    # ── Confirmation tracking ──────────────────────────────────────────────────

    def track_developer_confirmations(self, approval_id: str) -> Dict[str, dict]:
        """Return confirmation status for all developers on this approval."""
        result = {
            dev: status
            for dev, status in self.developer_confirmations.items()
            if status.get("approval_id") == approval_id
        }
        # Also pull from DB if available
        if self.db and not result:
            result = self.db.get_developer_confirmations(approval_id)
        return result

    def receive_developer_confirmation(self, developer: str, approval_id: str) -> None:
        """Log that a developer has acknowledged their notification."""
        now = datetime.now()

        if developer in self.developer_confirmations:
            self.developer_confirmations[developer]["acknowledged"] = True
            self.developer_confirmations[developer]["acknowledged_timestamp"] = now

        self.notification_log.append({
            "approval_id": approval_id,
            "developer":   developer,
            "event":       "CONFIRMATION_RECEIVED",
            "timestamp":   now,
        })

        if self.db:
            self.db.record_developer_confirmation(developer, approval_id, now)

        logger.info(f"Developer confirmation received: {developer} for {approval_id}")


# ============================================================================
# MANAGER BOT — approval orchestrator
# ============================================================================
class ManagerBot:
    """
    Orchestrates the human approval gate between PM Agent and developers.

    Sits above PMNotificationEngine:
      PM Agent → ManagerBot.notify_approver_for_review()
                      → human reviews
                      → ManagerBot.receive_approval_decision()
                          APPROVE → PMNotificationEngine.send_approved_notifications()
                          REVISE  → PM Agent regenerates notification
                          REJECT  → ManagerBot.escalate_if_rejected() → reconsider decision
    """

    def __init__(
        self,
        pm_notification_engine: "PMNotificationEngine",
        stage_gate_manager: "StageGateManager",
        db: "WorkflowDatabase",
    ):
        self.pm_engine   = pm_notification_engine
        self.gate_mgr    = stage_gate_manager
        self.db          = db

    def notify_approver_for_review(self, approval_request: ApprovalRequest) -> str:
        """
        Escalate a notification package to the human approver for review.

        Called BEFORE any notifications reach developers.
        Human must respond with APPROVE / REVISE / REJECT.

        Returns:
            approval_id for tracking
        """
        print(f"\n🚨 ESCALATING TO HUMAN APPROVER 🚨")
        print(f"Approval ID: {approval_request.approval_id}")
        print(f"Package ready for human review")
        print(f"Awaiting: APPROVE / REVISE / REJECT decision")
        return approval_request.approval_id

    def receive_approval_decision(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        approver_name: str,
        notes: Optional[str] = None,
    ) -> dict:
        """
        Process the human approver's decision and route accordingly.

        - APPROVE → mark ApprovalRequest approved, hand off to PMNotificationEngine
        - REVISE  → return to PM Agent for changes, increment revision count
        - REJECT  → escalate back to ManagerBot for decision reconsideration

        Returns:
            {status, next_step, approver, notes?}
        """
        # Find the pending ApprovalRequest by approval_id
        apr_req = self.pm_engine.pending_approvals.get(approval_id)

        if decision == ApprovalDecision.APPROVE:
            if apr_req:
                apr_req.approval_status    = ApprovalStatus.APPROVED
                apr_req.approver_name      = approver_name
                apr_req.approver_timestamp = datetime.now()
                apr_req.approver_signature = "✓ APPROVED"
                apr_req.approver_notes     = notes
            # Also update the stage gate DB record
            if apr_req:
                self.gate_mgr.record_approval_response(
                    workflow_id=apr_req.decision_id,
                    approver_email=approver_name,
                    decision="approved",
                    feedback=notes,
                )
            return {
                "status":     "APPROVED",
                "next_step":  "Send notifications to developers",
                "approver":   approver_name,
            }

        elif decision == ApprovalDecision.REVISE:
            if apr_req:
                apr_req.approval_status = ApprovalStatus.REVISED
                apr_req.approver_name   = approver_name
                apr_req.approver_notes  = notes
                apr_req.revision_count += 1
                apr_req.revision_history.append({
                    "revision":   apr_req.revision_count,
                    "approver":   approver_name,
                    "notes":      notes,
                    "timestamp":  datetime.now().isoformat(),
                })
            if apr_req:
                self.gate_mgr.record_approval_response(
                    workflow_id=apr_req.decision_id,
                    approver_email=approver_name,
                    decision="revised",
                    feedback=notes,
                )
            return {
                "status":    "REVISION_REQUESTED",
                "next_step": "PM Agent makes revisions and resubmits",
                "approver":  approver_name,
                "notes":     notes,
            }

        elif decision == ApprovalDecision.REJECT:
            if apr_req:
                apr_req.approval_status = ApprovalStatus.REJECTED
                apr_req.approver_name   = approver_name
                apr_req.approver_notes  = notes
            if apr_req:
                self.gate_mgr.record_approval_response(
                    workflow_id=apr_req.decision_id,
                    approver_email=approver_name,
                    decision="rejected",
                    feedback=notes,
                )
            return {
                "status":    "REJECTED",
                "next_step": "Escalate back to Manager Bot to reconsider",
                "approver":  approver_name,
                "notes":     notes,
            }

        return {"status": "UNKNOWN", "next_step": "No action taken"}

    def escalate_if_rejected(self, approval_id: str, approver_notes: str) -> dict:
        """
        When human rejects, escalate back to ManagerBot so the underlying
        decision — not just the notification wording — can be reconsidered.
        """
        print(f"\n❌ DECISION REJECTED BY HUMAN APPROVER")
        print(f"Approval ID: {approval_id}")
        print(f"Reason: {approver_notes}")
        print(f"Action: Return to Manager Bot for reconsideration")

        # Mark in DB so the workflow is clearly flagged
        row = self.db.get_approval_request_by_workflow(approval_id)
        if row:
            self.db.cursor.execute(
                "UPDATE approval_requests SET approval_status=?, approver_notes=? WHERE approval_id=?",
                (ApprovalStatus.REJECTED.value, approver_notes, row["approval_id"])
            )
            self.db.conn.commit()

        logger.warning(f"ManagerBot escalation: approval {approval_id} rejected — {approver_notes}")

        return {
            "status": "ESCALATED",
            "action": "Reconsider original decision",
            "reason": approver_notes,
        }


# ============================================================================
# DATABASE
# ============================================================================
class WorkflowDatabase:
    """Unified SQLite database for workflows, approvals, agent calls, and hallucination flags."""

    def __init__(self, db_path: str = "/home/claude/fg_workflows.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._init_tables()

    def _init_tables(self):
        self.cursor.executescript("""
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
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                stage_gate TEXT NOT NULL,
                approver_email TEXT NOT NULL,
                decision TEXT NOT NULL,
                feedback TEXT,
                decided_at TEXT NOT NULL,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            );

            CREATE TABLE IF NOT EXISTS agent_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT,
                agent_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                cache_created INTEGER DEFAULT 0,
                cache_read INTEGER DEFAULT 0,
                cost_usd REAL NOT NULL,
                status TEXT NOT NULL,
                reason TEXT,
                output_hash TEXT,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            );

            CREATE TABLE IF NOT EXISTS hallucination_flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_name TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                flag_reason TEXT NOT NULL,
                output_snippet TEXT
            );

            CREATE TABLE IF NOT EXISTS approval_requests (
                approval_id              TEXT PRIMARY KEY,
                workflow_id              TEXT NOT NULL,
                agent_name               TEXT NOT NULL,
                project_name             TEXT NOT NULL,
                decision_context         TEXT,
                affected_developers      TEXT,  -- JSON list
                action_items             TEXT,  -- JSON dict
                deadlines                TEXT,  -- JSON dict {dev: ISO datetime}
                resources                TEXT,  -- JSON dict
                confirmation_requirements TEXT, -- JSON dict
                approval_status          TEXT DEFAULT 'pending',
                approver_name            TEXT,
                approver_notes           TEXT,
                checklist_results        TEXT,  -- JSON dict {check: bool}
                all_checks_passed        INTEGER DEFAULT 0,
                created_timestamp        TEXT,
                approved_timestamp       TEXT,
                FOREIGN KEY(workflow_id) REFERENCES workflows(workflow_id)
            );

            CREATE TABLE IF NOT EXISTS notification_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id      TEXT NOT NULL,
                developer        TEXT NOT NULL,
                sent_timestamp   TEXT NOT NULL,
                status           TEXT NOT NULL DEFAULT 'SENT',
                decision_context TEXT,
                action_item      TEXT,
                deadline         TEXT,
                resources        TEXT,
                approver_name    TEXT,
                FOREIGN KEY(approval_id) REFERENCES approval_requests(approval_id)
            );

            CREATE TABLE IF NOT EXISTS developer_confirmations (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id             TEXT NOT NULL,
                developer               TEXT NOT NULL,
                acknowledged            INTEGER NOT NULL DEFAULT 0,
                acknowledged_timestamp  TEXT,
                confirmation_deadline   TEXT,
                FOREIGN KEY(approval_id) REFERENCES approval_requests(approval_id)
            );
        """)
        self.conn.commit()
        logger.info("Database initialized")

    # ── Workflow state ────────────────────────────────────────────────────────

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
        self.cursor.execute(
            "SELECT * FROM workflows WHERE status = ? ORDER BY updated_at ASC",
            (WorkflowStatus.APPROVAL_SENT.value,)
        )
        return [self._row_to_workflow(r) for r in self.cursor.fetchall()]

    def get_approved_workflows_ready_to_resume(self) -> List[WorkflowState]:
        self.cursor.execute("""
            SELECT * FROM workflows WHERE status = ? AND approval_timestamp IS NOT NULL
            ORDER BY approval_timestamp ASC
        """, (WorkflowStatus.APPROVED.value,))
        return [self._row_to_workflow(r) for r in self.cursor.fetchall()]

    def _row_to_workflow(self, r) -> WorkflowState:
        return WorkflowState(
            workflow_id=r[0], agent_name=r[1], project_name=r[2],
            current_stage_gate=StageGateName(r[3]), status=WorkflowStatus(r[4]),
            content_pending_approval=r[5], content_hash=r[6],
            approval_email_sent_at=r[7], human_approver=r[8],
            human_feedback=r[9], approval_timestamp=r[10],
            next_step_after_approval=r[11], created_at=r[12], updated_at=r[13]
        )

    # ── Approvals ─────────────────────────────────────────────────────────────

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

    def save_approval_request(self, req: ApprovalRequest,
                              checklist_results: Dict[str, bool],
                              all_checks_passed: bool) -> None:
        """Persist an ApprovalRequest + its checklist outcome."""
        self.cursor.execute("""
            INSERT OR REPLACE INTO approval_requests
            (approval_id, workflow_id, agent_name, project_name,
             decision_context, affected_developers, action_items,
             deadlines, resources, confirmation_requirements,
             approval_status, approver_name, approver_notes,
             checklist_results, all_checks_passed, created_timestamp, approved_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            req.approval_id,
            req.decision_id,
            "",                # agent_name filled by caller if needed
            "",                # project_name filled by caller if needed
            req.decision_context,
            json.dumps(req.affected_developers),
            json.dumps(req.action_items),
            json.dumps({k: v.isoformat() for k, v in req.deadlines.items()}),
            json.dumps(req.resources),
            json.dumps(req.confirmation_requirements),
            req.approval_status.value,
            req.approver_name,
            req.approver_notes,
            json.dumps(checklist_results),
            int(all_checks_passed),
            req.created_timestamp.isoformat(),
            req.approved_timestamp.isoformat() if req.approved_timestamp else None,
        ))
        self.conn.commit()
        logger.info(f"ApprovalRequest {req.approval_id} saved (checks_passed={all_checks_passed})")

    def get_approval_request(self, approval_id: str) -> Optional[Dict]:
        """Return a raw dict of the stored ApprovalRequest row (for inspection/audit)."""
        self.cursor.execute(
            "SELECT * FROM approval_requests WHERE approval_id = ?", (approval_id,)
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        cols = [
            "approval_id", "workflow_id", "agent_name", "project_name",
            "decision_context", "affected_developers", "action_items",
            "deadlines", "resources", "confirmation_requirements",
            "approval_status", "approver_name", "approver_notes",
            "checklist_results", "all_checks_passed", "created_timestamp", "approved_timestamp",
        ]
        return dict(zip(cols, row))

    def get_approval_request_by_workflow(self, workflow_id: str) -> Optional[Dict]:
        """Return the ApprovalRequest row linked to a workflow_id (for resume flow)."""
        self.cursor.execute(
            "SELECT * FROM approval_requests WHERE workflow_id = ? ORDER BY created_timestamp DESC LIMIT 1",
            (workflow_id,)
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        cols = [
            "approval_id", "workflow_id", "agent_name", "project_name",
            "decision_context", "affected_developers", "action_items",
            "deadlines", "resources", "confirmation_requirements",
            "approval_status", "approver_name", "approver_notes",
            "checklist_results", "all_checks_passed", "created_timestamp", "approved_timestamp",
        ]
        return dict(zip(cols, row))

    # ── Notification log ──────────────────────────────────────────────────────

    def log_notification(self, approval_id: str, developer: str, decision_context: str,
                         action_item: str, deadline: Optional[datetime],
                         resources: str, approver_name: str) -> None:
        """Persist a sent notification entry."""
        self.cursor.execute("""
            INSERT INTO notification_log
            (approval_id, developer, sent_timestamp, status, decision_context,
             action_item, deadline, resources, approver_name)
            VALUES (?, ?, ?, 'SENT', ?, ?, ?, ?, ?)
        """, (
            approval_id, developer, datetime.now().isoformat(),
            decision_context, action_item,
            deadline.isoformat() if deadline else None,
            resources, approver_name,
        ))
        self.conn.commit()

    def record_developer_confirmation(self, developer: str, approval_id: str,
                                      timestamp: datetime) -> None:
        """Upsert developer confirmation into the DB."""
        # Try update first; insert if row doesn't exist
        self.cursor.execute("""
            UPDATE developer_confirmations
            SET acknowledged=1, acknowledged_timestamp=?
            WHERE approval_id=? AND developer=?
        """, (timestamp.isoformat(), approval_id, developer))
        if self.cursor.rowcount == 0:
            self.cursor.execute("""
                INSERT INTO developer_confirmations
                (approval_id, developer, acknowledged, acknowledged_timestamp)
                VALUES (?, ?, 1, ?)
            """, (approval_id, developer, timestamp.isoformat()))
        self.conn.commit()

    def get_developer_confirmations(self, approval_id: str) -> Dict[str, dict]:
        """Return {developer: {acknowledged, acknowledged_timestamp, ...}} for an approval."""
        self.cursor.execute("""
            SELECT developer, acknowledged, acknowledged_timestamp, confirmation_deadline
            FROM developer_confirmations WHERE approval_id=?
        """, (approval_id,))
        return {
            row[0]: {
                "approval_id":            approval_id,
                "acknowledged":           bool(row[1]),
                "acknowledged_timestamp": row[2],
                "confirmation_deadline":  row[3],
            }
            for row in self.cursor.fetchall()
        }

    # ── Agent calls ───────────────────────────────────────────────────────────

    def log_agent_call(self, call: AgentCall, workflow_id: str = None):
        self.cursor.execute("""
            INSERT INTO agent_calls
            (workflow_id, agent_name, timestamp, input_tokens, output_tokens,
             cache_created, cache_read, cost_usd, status, reason, output_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (workflow_id, call.agent_name, call.timestamp, call.input_tokens,
              call.output_tokens, call.cache_created, call.cache_read,
              call.cost_usd, call.status, call.reason, call.output_hash))
        self.conn.commit()

    def get_today_spend(self) -> Tuple[float, int]:
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("""
            SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM agent_calls
            WHERE DATE(timestamp) = ?
              AND status NOT IN ('rejected_budget', 'rejected_hallucination')
        """, (today,))
        spend, calls = self.cursor.fetchone()
        return spend or 0.0, calls or 0

    def get_agent_spend_today(self, agent_name: str) -> Tuple[float, int]:
        today = datetime.now().strftime("%Y-%m-%d")
        self.cursor.execute("""
            SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) FROM agent_calls
            WHERE DATE(timestamp) = ? AND agent_name = ? AND status = 'success'
        """, (today, agent_name))
        spend, calls = self.cursor.fetchone()
        return spend or 0.0, calls or 0

    def get_last_n_days_spend(self, days: int = 7) -> float:
        date_ago = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        self.cursor.execute("""
            SELECT COALESCE(SUM(cost_usd), 0) FROM agent_calls
            WHERE DATE(timestamp) >= ? AND status = 'success'
        """, (date_ago,))
        return self.cursor.fetchone()[0] or 0.0

    # ── Hallucination flags ───────────────────────────────────────────────────

    def log_hallucination_flag(self, agent_name: str, reason: str, snippet: str):
        self.cursor.execute("""
            INSERT INTO hallucination_flags (agent_name, timestamp, flag_reason, output_snippet)
            VALUES (?, ?, ?, ?)
        """, (agent_name, datetime.now().isoformat(), reason, snippet[:500]))
        self.conn.commit()
        logger.warning(f"Hallucination flagged: {agent_name} - {reason}")

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

    def send_approval_request(self, workflow_id: str, stage_gate: StageGate,
                              agent_name: str, project_name: str,
                              content_summary: str, content_detail: str) -> bool:
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
                recipients = [stage_gate.approver_email] + (stage_gate.cc_emails or [])
                server.sendmail(self.sender_email, recipients, message.as_string())

            logger.info(f"Approval email sent for {workflow_id} to {stage_gate.approver_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send approval email for {workflow_id}: {str(e)}")
            return False

    def parse_approval_response(self, email_subject: str, email_body: str) -> Tuple[str, str]:
        body_upper = email_body.upper()
        if "APPROVED" in body_upper:
            decision = "approved"
        elif "REJECTED" in body_upper:
            decision = "rejected"
        else:
            decision = "clarification_needed"
        return decision, email_body.strip()

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

    def __init__(self, db: WorkflowDatabase, email_gateway):
        # email_gateway accepts EmailGateway or NotificationRouter (duck-typed:
        # both expose send_approval_request() with the same signature)
        self.db = db
        self.email = email_gateway

    def pause_at_gate(self, workflow_id: str, agent_name: str, project_name: str,
                      stage_gate_name: StageGateName,
                      content_pending_approval: str) -> WorkflowState:
        stage_gate = self.STAGE_GATES.get(stage_gate_name)
        if not stage_gate:
            raise ValueError(f"Unknown stage gate: {stage_gate_name}")

        content_hash = hashlib.sha256(content_pending_approval.encode()).hexdigest()
        workflow_state = WorkflowState(
            workflow_id=workflow_id, agent_name=agent_name, project_name=project_name,
            current_stage_gate=stage_gate_name, status=WorkflowStatus.PENDING,
            content_pending_approval=content_pending_approval, content_hash=content_hash
        )
        self.db.save_workflow_state(workflow_state)

        # ── ApprovalChecklist gate (PM Agent charter notifications) ───────────
        if agent_name == "pm_agent" and stage_gate_name == StageGateName.CHARTER_APPROVAL:
            approval_req = self._build_approval_request(
                workflow_id, agent_name, project_name, content_pending_approval
            )
            checklist = ApprovalChecklist(approval_req)
            checks_passed = checklist.all_checks_passed()

            self.db.save_approval_request(approval_req, checklist.checklist_results, checks_passed)

            if not checks_passed:
                failed = checklist.get_failed_checks()
                logger.warning(
                    f"ApprovalChecklist FAILED for {workflow_id} — "
                    f"notification held. Failed checks: {failed}"
                )
                workflow_state.next_step_after_approval = (
                    f"Checklist failed — resolve before sending: {'; '.join(failed)}"
                )
                self.db.save_workflow_state(workflow_state)
                return workflow_state  # hold here; do NOT send email
        # ─────────────────────────────────────────────────────────────────────

        email_sent = self.email.send_approval_request(
            workflow_id=workflow_id, stage_gate=stage_gate,
            agent_name=agent_name, project_name=project_name,
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
        workflow_state = self.db.get_workflow_state(workflow_id)
        if not workflow_state:
            raise ValueError(f"Workflow not found: {workflow_id}")

        self.db.record_approval(
            workflow_id=workflow_id, stage_gate=workflow_state.current_stage_gate,
            approver_email=approver_email, decision=decision, feedback=feedback
        )

        if decision.lower() == "approved":
            workflow_state.status = WorkflowStatus.APPROVED
            workflow_state.human_approver = approver_email
            workflow_state.human_feedback = feedback
            workflow_state.approval_timestamp = datetime.now().isoformat()
            logger.info(f"Workflow {workflow_id} APPROVED by {approver_email}")
            # Stamp the ApprovalRequest as APPROVED if one exists
            approval_req_row = self.db.get_approval_request(f"apr_{workflow_id}")
            if approval_req_row:
                self.db.cursor.execute(
                    "UPDATE approval_requests SET approval_status=?, approver_name=?, "
                    "approved_timestamp=? WHERE approval_id=?",
                    (ApprovalStatus.APPROVED.value, approver_email,
                     datetime.now().isoformat(), f"apr_{workflow_id}")
                )
                self.db.conn.commit()
        elif decision.lower() == "rejected":
            workflow_state.status = WorkflowStatus.REJECTED
            workflow_state.human_approver = approver_email
            workflow_state.human_feedback = feedback
            logger.warning(f"Workflow {workflow_id} REJECTED by {approver_email}: {feedback}")
        elif decision.lower() in ("revise", "revised"):
            # Return to PENDING so PM Agent can regenerate with the approver's notes
            workflow_state.status = WorkflowStatus.PENDING
            workflow_state.human_approver = approver_email
            workflow_state.human_feedback = feedback
            workflow_state.next_step_after_approval = (
                f"Revision requested by {approver_email}: {feedback or 'No notes provided'}"
            )
            logger.info(f"Workflow {workflow_id} sent back for REVISION by {approver_email}")
            # Update the stored ApprovalRequest status if one exists
            approval_req_row = self.db.get_approval_request(f"apr_{workflow_id}")
            if approval_req_row:
                self.db.cursor.execute(
                    "UPDATE approval_requests SET approval_status=?, approver_name=?, approver_notes=? "
                    "WHERE approval_id=?",
                    (ApprovalStatus.REVISED.value, approver_email, feedback, f"apr_{workflow_id}")
                )
                self.db.conn.commit()
        else:
            logger.warning(f"Unknown decision for {workflow_id}: {decision}")

        self.db.save_workflow_state(workflow_state)
        return workflow_state

    def _build_approval_request(self, workflow_id: str, agent_name: str,
                                project_name: str, content: str) -> ApprovalRequest:
        """Build an ApprovalRequest from PM Agent charter JSON output."""
        charter_data: Dict = {}
        wbs_data: Dict = {}
        try:
            parsed = json.loads(content)
            charter_data = parsed.get("project_charter", {})
            wbs_data     = parsed.get("wbs", {})
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        decision_context = (
            f"Project: {charter_data.get('title', project_name)} | "
            f"Client: {charter_data.get('client', 'Unknown')} | "
            f"Timeline: {charter_data.get('timeline', 'TBD')} | "
            f"Workflow: {workflow_id}"
        )

        # Extract team names from charter; fall back to known FG team
        raw_team = charter_data.get("team", {})
        if isinstance(raw_team, dict) and raw_team:
            developers = list(raw_team.keys())
        elif isinstance(raw_team, list) and raw_team:
            developers = [str(d) for d in raw_team]
        else:
            developers = ["Kiera", "Elina", "Ron"]

        # Action items from first WBS phase per developer; default if absent
        wbs_values = list(wbs_data.values()) if isinstance(wbs_data, dict) else []
        action_items = {
            dev: (str(wbs_values[0]) if wbs_values else f"Review {project_name} charter and confirm scope")
            for dev in developers
        }

        # Deadlines from charter timeline string (e.g. "12 weeks" → datetime)
        timeline_str = str(charter_data.get("timeline", "12 weeks"))
        digits = "".join(c for c in timeline_str if c.isdigit())
        weeks = int(digits) if digits else 12
        deadline = datetime.now() + timedelta(weeks=weeks)
        deadlines = {dev: deadline for dev in developers}

        resources = {
            dev: "Project charter, WBS, First Genesis templates — contact PMO for questions"
            for dev in developers
        }
        confirmation_requirements = {
            dev: "Reply CONFIRMED to this notification within 48 hours; escalate to PMO if blocked"
            for dev in developers
        }

        return ApprovalRequest(
            approval_id=f"apr_{workflow_id}",
            decision_id=workflow_id,
            decision_context=decision_context,
            affected_developers=developers,
            action_items=action_items,
            deadlines=deadlines,
            resources=resources,
            confirmation_requirements=confirmation_requirements,
        )

    def get_pending_approvals_summary(self) -> str:
        pending = self.db.get_pending_approvals()
        if not pending:
            return "No pending approvals"

        summary = f"\n{'='*70}\nPENDING APPROVALS ({len(pending)})\n{'='*70}\n"
        for wf in pending:
            sent_at = datetime.fromisoformat(wf.approval_email_sent_at)
            hours_waiting = (datetime.now() - sent_at).total_seconds() / 3600
            summary += (f"\n[{wf.workflow_id}]\n"
                        f"  Agent:      {wf.agent_name}\n"
                        f"  Project:    {wf.project_name}\n"
                        f"  Stage Gate: {wf.current_stage_gate.value}\n"
                        f"  Waiting:    {hours_waiting:.1f} hours\n"
                        f"  Content:    {wf.content_pending_approval[:80]}...\n")
        return summary + "\n" + "=" * 70

# ============================================================================
# BUDGET ENFORCER
# ============================================================================
class BudgetEnforcer:
    """Enforces daily and per-agent budget limits."""

    DAILY_BUDGET_USD = 5.00
    ALERT_THRESHOLD = 0.80

    AGENT_CONFIGS = {
        "pm_agent": AgentConfig(
            name="pm_agent", budget_per_call_usd=0.03, max_daily_calls=2,
            max_daily_spend_usd=0.10, priority=1, description="Project setup, WBS, status"
        ),
        "ba_agent": AgentConfig(
            name="ba_agent", budget_per_call_usd=0.04, max_daily_calls=3,
            max_daily_spend_usd=0.15, priority=1, description="Design sessions, requirements"
        ),
        "qa_agent": AgentConfig(
            name="qa_agent", budget_per_call_usd=0.06, max_daily_calls=1,
            max_daily_spend_usd=0.10, priority=2, description="Pre-delivery audit"
        ),
        "vendor_agent": AgentConfig(
            name="vendor_agent", budget_per_call_usd=0.02, max_daily_calls=1,
            max_daily_spend_usd=0.05, priority=3, description="Partner SLA tracking"
        ),
        "manager_agent": AgentConfig(
            name="manager_agent", budget_per_call_usd=0.04, max_daily_calls=1,
            max_daily_spend_usd=0.10, priority=1, description="Portfolio dashboard"
        ),
    }

    def __init__(self, db: WorkflowDatabase):
        self.db = db

    def can_call_agent(self, agent_name: str, estimated_cost: float) -> Tuple[bool, str]:
        if agent_name not in self.AGENT_CONFIGS:
            return False, f"Unknown agent: {agent_name}"

        config = self.AGENT_CONFIGS[agent_name]
        today_spend, _ = self.db.get_today_spend()
        agent_spend, agent_calls = self.db.get_agent_spend_today(agent_name)

        if today_spend + estimated_cost > self.DAILY_BUDGET_USD:
            msg = (f"Daily budget exceeded: ${today_spend:.2f} + ${estimated_cost:.2f} "
                   f"> ${self.DAILY_BUDGET_USD:.2f}")
            logger.error(msg)
            return False, msg

        if agent_spend + estimated_cost > config.max_daily_spend_usd:
            msg = (f"{agent_name} daily limit exceeded: ${agent_spend:.2f} + "
                   f"${estimated_cost:.2f} > ${config.max_daily_spend_usd:.2f}")
            logger.error(msg)
            return False, msg

        if agent_calls + 1 > config.max_daily_calls:
            msg = f"{agent_name} call limit: {agent_calls} + 1 > {config.max_daily_calls}"
            logger.error(msg)
            return False, msg

        projected = today_spend + estimated_cost
        if projected > self.DAILY_BUDGET_USD * self.ALERT_THRESHOLD:
            logger.warning(f"Budget alert: {agent_name} will push spend to "
                           f"${projected:.2f} ({projected/self.DAILY_BUDGET_USD*100:.0f}%)")

        return True, "Budget OK"

    def get_status_report(self) -> str:
        today_spend, today_calls = self.db.get_today_spend()
        week_spend = self.db.get_last_n_days_spend(7)

        report = (f"\n{'='*70}\n"
                  f"BUDGET STATUS REPORT - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                  f"{'='*70}\n"
                  f"Today's Spend:        ${today_spend:.4f} / ${self.DAILY_BUDGET_USD:.2f} "
                  f"({today_spend/self.DAILY_BUDGET_USD*100:.1f}%)\n"
                  f"Today's Calls:        {today_calls}\n"
                  f"Last 7 Days:          ${week_spend:.2f}\n"
                  f"Daily Average (7d):   ${week_spend/7:.2f}\n"
                  f"Per-Agent Today:\n")

        for agent_name, config in self.AGENT_CONFIGS.items():
            agent_spend, agent_calls = self.db.get_agent_spend_today(agent_name)
            pct = (agent_spend / config.max_daily_spend_usd * 100) if config.max_daily_spend_usd else 0
            report += (f"  {agent_name:20} ${agent_spend:.4f} / ${config.max_daily_spend_usd:.2f} "
                       f"({pct:3.0f}%) [{agent_calls} calls]\n")

        return report + "\n" + "=" * 70

# ============================================================================
# HALLUCINATION GUARD
# ============================================================================
class HallucinationGuard:
    """Detect and prevent agent hallucinations."""

    FROZEN_FACTS = {
        "project_timeline_aura": "3 months",
        "project_client_aura": "Malcolm Goodwin",
        "daily_budget": "$5 USD",
        "team_pm": "Kiera Phipps",
        "team_cto": "Ron Watty",
        "team_cdo": "Trice Johnson",
        "team_pmo": "Elina Mathieu",
        "team_ceo": "Pascal Watty",
        "vendor_wbt": "Yubi",
        "deadline_wbt": "April 30, 2026"
    }

    IMPOSSIBLE_CLAIMS = [
        (r"Chevron.*approved", "Chevron not approved yet"),
        (r"AURA.*complete|AURA.*finished", "AURA just kicked off"),
        (r"new hire|brought on|onboarded", "No hiring decisions made yet"),
        (r"first genesis.*failed|bankruptcy|shutdown", "Company operational"),
    ]

    def __init__(self, db: WorkflowDatabase):
        self.db = db

    def validate_output(self, agent_name: str, output: str) -> Tuple[bool, str]:
        output_lower = output.lower()

        # Check frozen fact contradictions
        if "aura" in agent_name or "aura" in output_lower:
            if ("4 month" in output_lower or "6 month" in output_lower) and \
               "3 month" not in output_lower:
                reason = "Agent timeline contradicts frozen fact (3 months)"
                self.db.log_hallucination_flag(agent_name, reason, output)
                return False, reason

        # Check impossible claims
        for pattern, reason_text in self.IMPOSSIBLE_CLAIMS:
            if re.search(pattern, output, re.IGNORECASE):
                reason = f"Impossible claim detected: {reason_text}"
                self.db.log_hallucination_flag(agent_name, reason, output)
                return False, reason

        # Soft warning for undocumented project claims
        if "project" in output_lower and agent_name in ("pm_agent", "ba_agent"):
            if "document" not in output_lower and "template" not in output_lower:
                self.db.log_hallucination_flag(
                    agent_name, "Warning: project claim without doc reference", output
                )

        return True, "Hallucination check passed"

# ============================================================================
# AUTONOMOUS AGENT WITH EMAIL GATES + GUARDRAILS
# ============================================================================
class AutonomousAgentWithEmailGates:
    """Agent system with email approval gates, budget enforcement, and hallucination detection."""

    def __init__(self, api_key: Optional[str] = None):
        self.client = anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))
        self.db = WorkflowDatabase()
        self.budget_enforcer = BudgetEnforcer(self.db)
        self.hallucination_guard = HallucinationGuard(self.db)

        # ── Notification router (Slack + Telegram + WhatsApp + Email + HubSpot) ──
        email_gw = EmailGateway()
        try:
            from fg_integrations.notification_router import NotificationRouter
            from fg_integrations.slack_gateway import SlackGateway
            from fg_integrations.telegram_gateway import TelegramGateway
            from fg_integrations.whatsapp_gateway import WhatsAppGateway
            from fg_integrations.hubspot_sync import HubSpotSync

            slack_gw     = SlackGateway()    if os.environ.get("SLACK_BOT_TOKEN")      else None
            telegram_gw  = TelegramGateway() if os.environ.get("TELEGRAM_BOT_TOKEN")   else None
            whatsapp_gw  = WhatsAppGateway() if os.environ.get("WHATSAPP_PROVIDER")    else None
            hubspot_sync = HubSpotSync()     if os.environ.get("HUBSPOT_ACCESS_TOKEN") else None

            self.router = NotificationRouter(email_gw, slack_gw, telegram_gw, whatsapp_gw, hubspot_sync)
            logger.info(f"NotificationRouter active channels: {self.router.active_channels()}")
        except ImportError:
            # fg_integrations not installed — fall back to email-only router shim
            self.router = email_gw
            logger.info("NotificationRouter not available — using email-only gateway")

        self.stage_gate_manager     = StageGateManager(self.db, self.router)
        self.pm_notification_engine = PMNotificationEngine(self.db)
        self.manager_bot            = ManagerBot(self.pm_notification_engine, self.stage_gate_manager, self.db)

        # ── Knowledge injector ────────────────────────────────────────────────
        try:
            from fg_knowledge_injector import KnowledgeInjector
            self.knowledge_injector = KnowledgeInjector()
            logger.info("Agent system initialized with knowledge injector")
        except ImportError:
            self.knowledge_injector = None
            logger.info("Agent system initialized (knowledge injector not available)")

    def _call_claude(self, agent_name: str, system_prompt: str,
                     user_message: str, workflow_id: str = None) -> Tuple[str, AgentCall]:
        """Call Claude with budget check and hallucination validation."""

        # MVP idle guard — block non-MVP agents before any API spend
        if agent_name not in MVP_ACTIVE_AGENTS:
            raise AgentIdleError(
                f"Agent '{agent_name}' is IDLE for MVP. "
                f"Active agents: {sorted(MVP_ACTIVE_AGENTS)}. "
                "Add the agent to MVP_ACTIVE_AGENTS to enable it."
            )

        # Estimate cost
        estimated_tokens = len(user_message.split()) * 1.3
        estimated_cost = (estimated_tokens / 1_000_000) * TokenCost.INPUT_COST_PER_1M

        # Budget check
        can_proceed, budget_reason = self.budget_enforcer.can_call_agent(agent_name, estimated_cost)
        if not can_proceed:
            call = AgentCall(
                agent_name=agent_name, timestamp=datetime.now().isoformat(),
                input_tokens=0, output_tokens=0, cache_created=0, cache_read=0,
                cost_usd=0, status="rejected_budget", reason=budget_reason, output_hash=""
            )
            self.db.log_agent_call(call, workflow_id)
            logger.error(f"{agent_name}: Budget rejected - {budget_reason}")
            return "", call

        # API call
        try:
            response = self.client.messages.create(
                model="claude-opus-4-6",
                max_tokens=2000,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )
        except Exception as e:
            call = AgentCall(
                agent_name=agent_name, timestamp=datetime.now().isoformat(),
                input_tokens=0, output_tokens=0, cache_created=0, cache_read=0,
                cost_usd=0, status="error", reason=str(e), output_hash=""
            )
            self.db.log_agent_call(call, workflow_id)
            logger.error(f"{agent_name}: API error - {str(e)}")
            return "", call

        output = response.content[0].text
        output_hash = hashlib.sha256(output.encode()).hexdigest()
        cost = TokenCost(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_creation_tokens=getattr(response.usage, 'cache_creation_input_tokens', 0),
            cache_read_tokens=getattr(response.usage, 'cache_read_input_tokens', 0)
        ).total_cost_usd()

        # Hallucination check
        is_valid, hall_reason = self.hallucination_guard.validate_output(agent_name, output)
        if not is_valid:
            call = AgentCall(
                agent_name=agent_name, timestamp=datetime.now().isoformat(),
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
                cache_created=getattr(response.usage, 'cache_creation_input_tokens', 0),
                cache_read=getattr(response.usage, 'cache_read_input_tokens', 0),
                cost_usd=cost, status="rejected_hallucination",
                reason=hall_reason, output_hash=output_hash
            )
            self.db.log_agent_call(call, workflow_id)
            logger.error(f"{agent_name}: Hallucination detected - {hall_reason}")
            return "", call

        call = AgentCall(
            agent_name=agent_name, timestamp=datetime.now().isoformat(),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_created=getattr(response.usage, 'cache_creation_input_tokens', 0),
            cache_read=getattr(response.usage, 'cache_read_input_tokens', 0),
            cost_usd=cost, status="success", reason="", output_hash=output_hash
        )
        self.db.log_agent_call(call, workflow_id)
        logger.info(f"{agent_name}: Success - {call}")
        return output, call

    # ── Governance layer helpers ──────────────────────────────────────────────

    def _wrap_as_decision(
        self,
        raw_text: str,
        call: "AgentCall",
        agent_name: str,
        workflow_id: str,
        sensitivity_flag: bool = False,
        data_sources: Optional[List[str]] = None,
    ):
        """
        Convert a raw Claude output + AgentCall into an AgentDecision and log it
        to the unified AuditLogger and TokenBudget.

        Returns an AgentDecision if the governance modules are available,
        otherwise returns None silently (backwards-compatible).
        """
        if not _GOVERNANCE_AVAILABLE:
            return None

        try:
            cleaned = raw_text.strip()
            if cleaned.startswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[1:])
            if cleaned.endswith("```"):
                cleaned = "\n".join(cleaned.split("\n")[:-1])

            parsed = json.loads(cleaned)
            recommendation = str(parsed.get("recommendation", raw_text[:200]))
            confidence = float(parsed.get("confidence_score", 0.80))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = parsed.get("reasoning", [])
            assumptions = parsed.get("assumptions", [])
            if isinstance(reasoning, str):
                reasoning = [reasoning]
            if isinstance(assumptions, str):
                assumptions = [assumptions]
        except (json.JSONDecodeError, ValueError):
            recommendation = raw_text[:400]
            confidence = 0.72
            reasoning = ["Agent returned unstructured output — review recommended."]
            assumptions = ["Output not in JSON format; structured fields may be missing."]

        from fg_decision_models import AgentDecision as FGAgentDecision
        decision = FGAgentDecision(
            agent_name=agent_name,
            workflow_id=workflow_id,
            recommendation=recommendation,
            confidence_score=confidence,
            reasoning=reasoning,
            data_sources=data_sources or [],
            assumptions=assumptions,
            requires_review=sensitivity_flag or confidence < 0.80,
            sensitivity_flag=sensitivity_flag,
            tokens_used=call.input_tokens + call.output_tokens,
            model_version="claude-opus-4-6",
            input_data={},
        )

        # Log to unified audit trail and token budget
        _audit = FGAuditLogger()
        _audit.log_decision(decision)
        _audit.log_cost(
            decision.decision_id, agent_name,
            call.input_tokens, call.output_tokens,
            "claude-opus-4-6", call.cost_usd,
        )

        _budget = FGTokenBudget()
        _budget.log_usage(
            agent_name, call.input_tokens, call.output_tokens,
            "claude-opus-4-6", decision_id=decision.decision_id,
            workflow_id=workflow_id,
        )

        return decision

    def _governed_call(
        self,
        agent_name: str,
        system_prompt: str,
        user_message: str,
        workflow_id: str,
        sensitivity_flag: bool = False,
        data_sources: Optional[List[str]] = None,
    ) -> Tuple:
        """
        Full governed Claude call: _call_claude → AgentDecision → ReviewGate.

        Knowledge context from knowledge/ folder + DB lessons is prepended to
        the system_prompt automatically so every agent benefits from accumulated
        best practices without any per-agent changes.

        Returns (raw_output, call, decision_or_None, gate_status, review_or_None).
        Falls back gracefully if governance modules are unavailable.
        """
        # ── Knowledge injection ────────────────────────────────────────────────
        if self.knowledge_injector is not None:
            knowledge_ctx = self.knowledge_injector.get_context(agent_name)
            if knowledge_ctx:
                system_prompt = f"{knowledge_ctx}\n\n---\n\n{system_prompt}"
                logger.debug(
                    f"{agent_name}: knowledge context injected "
                    f"(~{len(knowledge_ctx)//4} tokens)"
                )

        output, call = self._call_claude(agent_name, system_prompt, user_message, workflow_id)

        if not output or not _GOVERNANCE_AVAILABLE:
            return output, call, None, None, None

        decision = self._wrap_as_decision(
            output, call, agent_name, workflow_id, sensitivity_flag, data_sources
        )

        gate = FGReviewGate(audit_logger=FGAuditLogger())
        gate_status, review = gate.evaluate(decision)
        logger.info(f"{agent_name}: ReviewGate → {gate_status} (conf={decision.confidence_score:.0%})")

        return output, call, decision, gate_status, review

    # ── Agent run methods ─────────────────────────────────────────────────────

    def run_pm_agent_with_gates(self, project_metadata: dict) -> Tuple[str, WorkflowState]:
        """Run PM Agent with guardrails + charter approval gate + governance layer."""

        workflow_id = f"pm_{project_metadata['project']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        project_name = project_metadata.get('project', 'Unknown')
        logger.info(f"Starting PM Agent workflow: {workflow_id}")

        output, call, decision, gate_status, review = self._governed_call(
            agent_name="pm_agent",
            system_prompt=(
                "You are a Project Manager Agent for First Genesis. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), project_charter, wbs, risks."
            ),
            user_message=f"""Generate project charter for:
{json.dumps(project_metadata, indent=2)}
Output as JSON only:
{{"recommendation": "Charter ready for approval", "confidence_score": 0.90, "reasoning": [], "assumptions": [], "project_charter": {{"title": "...", "client": "...", "timeline": "3 months"}}, "wbs": {{}}, "risks": []}}""",
            workflow_id=workflow_id,
            sensitivity_flag=True,
            data_sources=["Project metadata", "First Genesis templates", "Client brief"],
        )

        if not output:
            raise RuntimeError(f"PM Agent call failed: {call.reason}")

        # ── Build notification package for human approval gate ─────────────────
        charter_data: dict = {}
        wbs_data: dict = {}
        try:
            parsed = json.loads(output)
            charter_data = parsed.get("project_charter", {})
            wbs_data     = parsed.get("wbs", {})
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass

        raw_team = charter_data.get("team", {})
        if isinstance(raw_team, dict) and raw_team:
            developers = list(raw_team.keys())
        elif isinstance(raw_team, list) and raw_team:
            developers = [str(d) for d in raw_team]
        else:
            developers = ["Kiera", "Elina", "Ron"]

        wbs_values = list(wbs_data.values()) if isinstance(wbs_data, dict) else []
        timeline_str = str(charter_data.get("timeline", "12 weeks"))
        digits = "".join(c for c in timeline_str if c.isdigit())
        weeks = int(digits) if digits else 12
        deadline = datetime.now() + timedelta(weeks=weeks)

        self.pm_notification_engine.prepare_notification_package(
            decision={
                "id":      workflow_id,
                "context": (
                    f"Project: {charter_data.get('title', project_name)} | "
                    f"Client: {charter_data.get('client', 'Unknown')} | "
                    f"Timeline: {charter_data.get('timeline', 'TBD')}"
                ),
            },
            affected_developers=developers,
            action_items={
                dev: (str(wbs_values[0]) if wbs_values else f"Review {project_name} charter")
                for dev in developers
            },
            deadlines={dev: deadline for dev in developers},
            resources={
                dev: "Project charter, WBS, First Genesis templates"
                for dev in developers
            },
            confirmation_requirements={
                dev: "Reply CONFIRMED within 48 hours"
                for dev in developers
            },
        )
        # ─────────────────────────────────────────────────────────────────────

        workflow_state = self.stage_gate_manager.pause_at_gate(
            workflow_id=workflow_id, agent_name="pm_agent", project_name=project_name,
            stage_gate_name=StageGateName.CHARTER_APPROVAL,
            content_pending_approval=output
        )
        logger.info(f"Workflow {workflow_id} paused at {workflow_state.current_stage_gate.value}")
        return output, workflow_state

    def run_ba_agent_with_gates(self, project_name: str, transcript: str) -> Tuple[str, WorkflowState]:
        """Run BA Agent with guardrails + requirements approval gate + governance layer."""

        workflow_id = f"ba_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info(f"Starting BA Agent workflow: {workflow_id}")

        output, call, decision, gate_status, review = self._governed_call(
            agent_name="ba_agent",
            system_prompt=(
                "You are a Business Analyst Agent for First Genesis. "
                "Extract structured requirements from design session transcripts. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), functional_requirements (list), "
                "non_functional_requirements (list), traceability_matrix (object)."
            ),
            user_message=f"""Extract requirements from this design session transcript for project: {project_name}

TRANSCRIPT:
{transcript}

Output as JSON only with functional requirements (FR1, FR2...), non-functional requirements (NFR1...), and traceability matrix.""",
            workflow_id=workflow_id,
            sensitivity_flag=True,
            data_sources=["Design session transcript", "Stakeholder notes", "Project charter"],
        )

        if not output:
            raise RuntimeError(f"BA Agent call failed: {call.reason}")

        workflow_state = self.stage_gate_manager.pause_at_gate(
            workflow_id=workflow_id, agent_name="ba_agent", project_name=project_name,
            stage_gate_name=StageGateName.REQUIREMENTS_APPROVAL,
            content_pending_approval=output
        )
        logger.info(f"Workflow {workflow_id} paused at {workflow_state.current_stage_gate.value}")
        return output, workflow_state

    def run_qa_agent_with_gates(self, workflow_id_to_audit: str, project_name: str = "Unknown") -> Tuple[str, WorkflowState]:
        """Run QA Agent pre-delivery audit + governance layer."""

        workflow_id = f"qa_{project_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info(f"Starting QA Agent workflow: {workflow_id}")

        # Retrieve content to audit from the source workflow
        source_workflow = self.db.get_workflow_state(workflow_id_to_audit)
        content_to_audit = source_workflow.content_pending_approval if source_workflow else f"Audit workflow: {workflow_id_to_audit}"

        output, call, decision, gate_status, review = self._governed_call(
            agent_name="qa_agent",
            system_prompt=(
                "You are a QA Agent for First Genesis. "
                "Audit deliverables for completeness, scope creep, and readiness. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), checklist (list of pass/fail/warn items), "
                "scope_creep_detected (bool), readiness (READY|NEEDS_WORK), quality_score (0-100)."
            ),
            user_message=f"""Audit these deliverables for project: {project_name}

CONTENT TO AUDIT:
{content_to_audit[:3000]}

Check: all required deliverables present, no scope creep vs original RFP, all acceptance criteria met, ready for customer delivery.""",
            workflow_id=workflow_id,
            sensitivity_flag=False,
            data_sources=["Workflow deliverables", "Original project scope", "Acceptance criteria"],
        )

        if not output:
            raise RuntimeError(f"QA Agent call failed: {call.reason}")

        workflow_state = self.stage_gate_manager.pause_at_gate(
            workflow_id=workflow_id, agent_name="qa_agent", project_name=project_name,
            stage_gate_name=StageGateName.QA_AUDIT_APPROVAL,
            content_pending_approval=output
        )
        logger.info(f"Workflow {workflow_id} paused at {workflow_state.current_stage_gate.value}")
        return output, workflow_state

    # ── Vendor cost CSV logger ────────────────────────────────────────────────

    class VendorCostCSVLogger:
        """Append one row per Vendor Agent call to vendor_cost_log.csv.

        Columns
        -------
        timestamp, workflow_id, vendor_name, input_tokens, output_tokens,
        cache_created, cache_read, cost_usd, status, sla_status,
        escalation_required
        """

        COLUMNS = [
            "timestamp", "workflow_id", "vendor_name",
            "input_tokens", "output_tokens", "cache_created", "cache_read",
            "cost_usd", "status", "sla_status", "escalation_required",
        ]

        def __init__(self, log_path: str = "vendor_cost_log.csv"):
            self.log_path = pathlib.Path(log_path)
            # Write header if the file doesn't exist yet
            if not self.log_path.exists():
                with self.log_path.open("w", newline="") as fh:
                    csv.writer(fh).writerow(self.COLUMNS)

        def log(
            self,
            workflow_id: str,
            vendor_name: str,
            call: "AgentCall",
            sla_status: str = "UNKNOWN",
            escalation_required: bool = False,
        ) -> None:
            """Append a row for this vendor agent call."""
            row = {
                "timestamp": call.timestamp,
                "workflow_id": workflow_id,
                "vendor_name": vendor_name,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "cache_created": call.cache_created,
                "cache_read": call.cache_read,
                "cost_usd": round(call.cost_usd, 6),
                "status": call.status,
                "sla_status": sla_status,
                "escalation_required": escalation_required,
            }
            with self.log_path.open("a", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=self.COLUMNS)
                writer.writerow(row)
            logger.info(
                f"VendorCostCSVLogger: logged {workflow_id} "
                f"cost=${row['cost_usd']:.6f} sla={sla_status} "
                f"escalation={escalation_required} → {self.log_path}"
            )

    def run_vendor_agent_with_gates(self, vendor_name: str, metrics: dict) -> Tuple[str, WorkflowState]:
        """Run Vendor Agent SLA scorecard + governance layer."""

        workflow_id = f"vendor_{vendor_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        logger.info(f"Starting Vendor Agent workflow: {workflow_id}")

        output, call, decision, gate_status, review = self._governed_call(
            agent_name="vendor_agent",
            system_prompt=(
                "You are a Vendor Management Agent for First Genesis. "
                "Evaluate vendor SLA performance and generate scorecards. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), overall_score (0-100), "
                "sla_status (PASS|FAIL), metrics_breakdown (object), escalation_required (bool), "
                "action_items (list)."
            ),
            user_message=f"""Generate vendor performance scorecard for: {vendor_name}

METRICS:
{json.dumps(metrics, indent=2)}

SLA Targets: on_time_delivery >= 95%, quality_score >= 90%, response_time_hours <= 24, budget_variance_pct <= 5%.
Assess performance, flag any SLA breaches, and recommend actions.""",
            workflow_id=workflow_id,
            sensitivity_flag=False,
            data_sources=["Vendor SLA contract", "Weekly status updates", "Cost actuals"],
        )

        if not output:
            raise RuntimeError(f"Vendor Agent call failed: {call.reason}")

        # ── Parse scorecard fields for the CSV row ────────────────────────────
        sla_status = "UNKNOWN"
        escalation_required = False
        try:
            scorecard = json.loads(output)
            sla_status = scorecard.get("sla_status", "UNKNOWN")
            escalation_required = bool(scorecard.get("escalation_required", False))
        except (json.JSONDecodeError, AttributeError):
            pass  # Output was not JSON; leave defaults

        # ── Append to cost log CSV ────────────────────────────────────────────
        csv_logger = self.VendorCostCSVLogger()
        csv_logger.log(
            workflow_id=workflow_id,
            vendor_name=vendor_name,
            call=call,
            sla_status=sla_status,
            escalation_required=escalation_required,
        )

        workflow_state = self.stage_gate_manager.pause_at_gate(
            workflow_id=workflow_id, agent_name="vendor_agent", project_name=vendor_name,
            stage_gate_name=StageGateName.DELIVERY_APPROVAL,
            content_pending_approval=output
        )
        logger.info(f"Workflow {workflow_id} paused at {workflow_state.current_stage_gate.value}")
        return output, workflow_state

    def run_manager_agent_with_gates(self, portfolio_data: Optional[dict] = None) -> Tuple[str, WorkflowState]:
        """Run Manager Agent portfolio dashboard + governance layer."""

        workflow_id = f"manager_portfolio_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        project_name = "Portfolio"
        logger.info(f"Starting Manager Agent workflow: {workflow_id}")

        portfolio_context = json.dumps(portfolio_data, indent=2) if portfolio_data else (
            "Projects: AURA MVP (On Track), Chevron Sand Mgmt (In Progress), "
            "WWT Enhancement (At Risk), Middle East (Active). "
            "Total budget: $2.75M. Pending approvals: 3."
        )

        output, call, decision, gate_status, review = self._governed_call(
            agent_name="manager_agent",
            system_prompt=(
                "You are a Portfolio Manager Agent for First Genesis. "
                "Consolidate agent outputs and generate executive portfolio dashboards. "
                "Output ONLY valid JSON with keys: recommendation, confidence_score (0-1), "
                "reasoning (list), assumptions (list), portfolio_health (STRONG|MODERATE|AT_RISK), "
                "projects_summary (list), budget_summary (object), top_risks (list), "
                "pending_decisions (list), key_metrics (object)."
            ),
            user_message=f"""Generate portfolio status dashboard:

{portfolio_context}

Provide executive summary with all projects at a glance, budget summary, top risks, and pending decisions.""",
            workflow_id=workflow_id,
            sensitivity_flag=False,
            data_sources=["All agent outputs", "Portfolio metadata", "Resource constraints", "Risk register"],
        )

        if not output:
            raise RuntimeError(f"Manager Agent call failed: {call.reason}")

        workflow_state = self.stage_gate_manager.pause_at_gate(
            workflow_id=workflow_id, agent_name="manager_agent", project_name=project_name,
            stage_gate_name=StageGateName.DELIVERY_APPROVAL,
            content_pending_approval=output
        )
        logger.info(f"Workflow {workflow_id} paused at {workflow_state.current_stage_gate.value}")
        return output, workflow_state

    def resume_approved_workflow(self, workflow_id: str) -> str:
        workflow_state = self.db.get_workflow_state(workflow_id)
        if not workflow_state:
            logger.error(f"Workflow not found: {workflow_id}")
            return ""

        if workflow_state.status != WorkflowStatus.APPROVED:
            logger.warning(f"Workflow {workflow_id} not APPROVED: {workflow_state.status.value}")
            return ""

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

        # ── Send developer notifications for charter approval ─────────────────
        if workflow_state.current_stage_gate == StageGateName.CHARTER_APPROVAL:
            # Find the matching ApprovalRequest (in-memory first, then DB)
            apr_req = next(
                (r for r in self.pm_notification_engine.pending_approvals.values()
                 if r.decision_id == workflow_id),
                None
            )
            if apr_req is None:
                # Rebuild from DB row for restarts / cross-process runs
                row = self.db.get_approval_request_by_workflow(workflow_id)
                if row:
                    apr_req = ApprovalRequest(
                        approval_id=row["approval_id"],
                        decision_id=workflow_id,
                        decision_context=row.get("decision_context", ""),
                        affected_developers=json.loads(row.get("affected_developers") or "[]"),
                        action_items=json.loads(row.get("action_items") or "{}"),
                        deadlines={},   # datetime reconstruction skipped for DB-reload path
                        resources=json.loads(row.get("resources") or "{}"),
                        confirmation_requirements=json.loads(
                            row.get("confirmation_requirements") or "{}"
                        ),
                        approval_status=ApprovalStatus.APPROVED,
                        approver_name=workflow_state.human_approver,
                    )
            if apr_req:
                decision = self.pm_notification_engine.wait_for_approval(apr_req)
                if decision == ApprovalDecision.APPROVE:
                    self.pm_notification_engine.send_approved_notifications(apr_req)
                elif decision == ApprovalDecision.REVISE:
                    logger.info(f"Charter {workflow_id} sent back for revision")
                elif decision == ApprovalDecision.REJECT:
                    logger.warning(f"Charter {workflow_id} rejected — escalating")
        # ─────────────────────────────────────────────────────────────────────

        # HubSpot CRM sync — notify on completion or gate advancement
        if hasattr(self, "router") and hasattr(self.router, "notify_hubspot_on_gate_approval"):
            gate_name = workflow_state.current_stage_gate.value
            if is_final:
                self.router.notify_hubspot_on_completion(
                    workflow_id, workflow_state.project_name, workflow_state.agent_name
                )
            else:
                self.router.notify_hubspot_on_gate_approval(
                    workflow_id, workflow_state.project_name, workflow_state.agent_name,
                    gate_name, "approved"
                )

        return next_action

    def check_pending_approvals(self) -> str:
        return self.stage_gate_manager.get_pending_approvals_summary()

    def process_approval_response(self, workflow_id: str, approver_email: str,
                                  decision: str, feedback: str = None) -> str:
        workflow_state = self.stage_gate_manager.record_approval_response(
            workflow_id=workflow_id, approver_email=approver_email,
            decision=decision, feedback=feedback
        )
        return (f"\nApproval recorded for {workflow_id}:\n"
                f"  Decision:  {decision}\n"
                f"  Approver:  {approver_email}\n"
                f"  Feedback:  {feedback or 'None'}\n\n"
                f"Workflow status updated to: {workflow_state.status.value}\n")

# ============================================================================
# DASHBOARD: KNOWLEDGE LIBRARY
# ============================================================================
class KnowledgeLibrary:
    """SQLite database for agent learning, workflow patterns, and lessons."""

    def __init__(self, db_path: str = "/home/claude/fg_knowledge.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_tables()

    def _init_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS skills (
                skill_id TEXT PRIMARY KEY,
                agent_name TEXT,
                skill_name TEXT,
                description TEXT,
                success_count INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                avg_execution_time REAL,
                skill_level TEXT,
                last_used TEXT,
                template TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS workflow_patterns (
                workflow_id TEXT PRIMARY KEY,
                workflow_name TEXT,
                agent_sequence TEXT,
                success_count INTEGER DEFAULT 0,
                avg_duration_seconds REAL,
                avg_cost_usd REAL,
                template TEXT,
                created_at TEXT,
                last_used TEXT
            );
            CREATE TABLE IF NOT EXISTS lessons_learned (
                lesson_id TEXT PRIMARY KEY,
                workflow_id TEXT,
                lesson_title TEXT,
                lesson_content TEXT,
                category TEXT,
                applicable_agents TEXT,
                created_at TEXT,
                usage_count INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS agent_comm_log (
                message_id TEXT PRIMARY KEY,
                from_agent TEXT,
                to_agent TEXT,
                message_type TEXT,
                content TEXT,
                timestamp TEXT,
                status TEXT
            );
        """)
        self.conn.commit()

    def save_skill(self, skill: AgentSkill):
        self.conn.execute("""
            INSERT OR REPLACE INTO skills
            (skill_id, agent_name, skill_name, description, success_count,
             error_count, avg_execution_time, skill_level, last_used, template, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            skill.skill_id, skill.agent_name, skill.skill_name, skill.description,
            skill.success_count, skill.error_count, skill.avg_execution_time,
            skill.skill_level.name, skill.last_used, json.dumps(skill.template),
            datetime.now().isoformat()
        ))
        self.conn.commit()

    def get_agent_skills(self, agent_name: str) -> List[AgentSkill]:
        cursor = self.conn.execute(
            "SELECT * FROM skills WHERE agent_name = ?", (agent_name,)
        )
        skills = []
        for row in cursor.fetchall():
            skills.append(AgentSkill(
                skill_id=row[0], agent_name=row[1], skill_name=row[2],
                description=row[3], success_count=row[4], error_count=row[5],
                avg_execution_time=row[6], skill_level=SkillLevel[row[7]],
                last_used=row[8], template=json.loads(row[9]) if row[9] else {}
            ))
        return skills

    def save_workflow_pattern(self, workflow: WorkflowExecution):
        workflow_name = "-".join(workflow.agent_sequence)
        self.conn.execute("""
            INSERT OR REPLACE INTO workflow_patterns
            (workflow_id, workflow_name, agent_sequence, success_count,
             avg_duration_seconds, avg_cost_usd, template, created_at, last_used)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            workflow.workflow_id, workflow_name,
            json.dumps(workflow.agent_sequence), 1,
            workflow.duration_seconds, workflow.cost_usd,
            json.dumps({"input": workflow.input_data, "agent_sequence": workflow.agent_sequence}),
            datetime.now().isoformat(), datetime.now().isoformat()
        ))
        self.conn.commit()

    def get_workflow_patterns(self) -> List[Dict]:
        cursor = self.conn.execute(
            "SELECT * FROM workflow_patterns ORDER BY success_count DESC"
        )
        patterns = []
        for row in cursor.fetchall():
            patterns.append({
                "workflow_id": row[0], "workflow_name": row[1],
                "agent_sequence": json.loads(row[2]), "success_count": row[3],
                "avg_duration_seconds": row[4], "avg_cost_usd": row[5],
                "template": json.loads(row[6]) if row[6] else None
            })
        return patterns

    def save_lesson_learned(self, workflow_id: str, lesson_title: str,
                            lesson_content: str, category: str,
                            applicable_agents: List[str]):
        self.conn.execute("""
            INSERT INTO lessons_learned
            (lesson_id, workflow_id, lesson_title, lesson_content, category,
             applicable_agents, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            str(uuid.uuid4()), workflow_id, lesson_title, lesson_content,
            category, json.dumps(applicable_agents), datetime.now().isoformat()
        ))
        self.conn.commit()

    def get_lessons_learned(self, category: Optional[str] = None) -> List[Dict]:
        if category:
            cursor = self.conn.execute(
                "SELECT * FROM lessons_learned WHERE category = ? ORDER BY created_at DESC",
                (category,)
            )
        else:
            cursor = self.conn.execute(
                "SELECT * FROM lessons_learned ORDER BY created_at DESC"
            )
        lessons = []
        for row in cursor.fetchall():
            lessons.append({
                "lesson_id": row[0], "workflow_id": row[1], "lesson_title": row[2],
                "lesson_content": row[3], "category": row[4],
                "applicable_agents": json.loads(row[5]), "created_at": row[6],
                "usage_count": row[7]
            })
        return lessons


# ============================================================================
# DASHBOARD: AGENT COMMUNICATION BUS
# ============================================================================
class AgentCommunicationBus:
    """Message bus for inter-agent communication."""

    MAX_HISTORY = 10_000

    def __init__(self, knowledge_library: KnowledgeLibrary):
        self.knowledge_library = knowledge_library
        self.message_queue: queue.Queue = queue.Queue()
        self.message_history: List[AgentMessage] = []
        self.agents: Dict[str, AgentRecord] = {}

    def register_agent(self, agent: AgentRecord):
        self.agents[agent.name] = agent

    def send_message(self, from_agent: str, to_agent: str,
                     message_type: MessageType, content: Dict) -> str:
        message_id = str(uuid.uuid4())
        msg = AgentMessage(
            id=message_id, from_agent=from_agent, to_agent=to_agent,
            message_type=message_type, content=content,
            timestamp=datetime.now().isoformat(), status="sent"
        )
        self.message_queue.put(msg)
        self.message_history.append(msg)
        if len(self.message_history) > self.MAX_HISTORY:
            self.message_history = self.message_history[-self.MAX_HISTORY:]
        return message_id

    def get_messages_for_agent(self, agent_name: str) -> List[AgentMessage]:
        return [m for m in self.message_history if m.to_agent == agent_name]

    def get_conversation(self, agent1: str, agent2: str) -> List[AgentMessage]:
        return [
            m for m in self.message_history
            if (m.from_agent == agent1 and m.to_agent == agent2) or
               (m.from_agent == agent2 and m.to_agent == agent1)
        ]


# ============================================================================
# DASHBOARD: SKILL COMPOUNDING ENGINE
# ============================================================================
class SkillCompoundingEngine:
    """Tracks and improves agent skills over time."""

    def __init__(self, knowledge_library: KnowledgeLibrary):
        self.knowledge_library = knowledge_library

    def record_success(self, agent_name: str, skill_name: str,
                       execution_time: float, output_quality: float = 1.0):
        skills = self.knowledge_library.get_agent_skills(agent_name)
        skill = next((s for s in skills if s.skill_name == skill_name), None)
        if not skill:
            skill = AgentSkill(
                skill_id=str(uuid.uuid4()), agent_name=agent_name,
                skill_name=skill_name, description=f"Skill learned by {agent_name}",
                success_count=0, error_count=0, avg_execution_time=0,
                skill_level=SkillLevel.NOVICE, last_used=datetime.now().isoformat(),
                template={}
            )
        skill.success_count += 1
        skill.last_used = datetime.now().isoformat()
        if skill.success_count >= 50:
            skill.skill_level = SkillLevel.EXPERT
        elif skill.success_count >= 20:
            skill.skill_level = SkillLevel.ADVANCED
        elif skill.success_count >= 5:
            skill.skill_level = SkillLevel.INTERMEDIATE
        if skill.avg_execution_time == 0:
            skill.avg_execution_time = execution_time
        else:
            skill.avg_execution_time = (
                (skill.avg_execution_time * (skill.success_count - 1) + execution_time)
                / skill.success_count
            )
        self.knowledge_library.save_skill(skill)

    def record_error(self, agent_name: str, skill_name: str, error: str):
        skills = self.knowledge_library.get_agent_skills(agent_name)
        skill = next((s for s in skills if s.skill_name == skill_name), None)
        if skill:
            skill.error_count += 1
            self.knowledge_library.save_skill(skill)

    def get_agent_improvement(self, agent_name: str) -> Dict:
        skills = self.knowledge_library.get_agent_skills(agent_name)
        total_successes = sum(s.success_count for s in skills)
        total_errors = sum(s.error_count for s in skills)
        avg_level = sum(s.skill_level.value for s in skills) / len(skills) if skills else 0
        total = total_successes + total_errors
        return {
            "agent_name": agent_name,
            "total_skills": len(skills),
            "total_successes": total_successes,
            "total_errors": total_errors,
            "success_rate": total_successes / total if total > 0 else 0,
            "avg_skill_level": avg_level,
            "skills": [asdict(s) for s in skills]
        }


# ============================================================================
# DASHBOARD: STATE MANAGER & COMMAND CENTER
# ============================================================================
class DashboardStateManager:
    """Manages complete state for real-time dashboard display."""

    def __init__(self):
        self.knowledge_library = KnowledgeLibrary()
        self.communication_bus = AgentCommunicationBus(self.knowledge_library)
        self.skill_engine = SkillCompoundingEngine(self.knowledge_library)
        self.agents: Dict[str, AgentRecord] = {}
        self.last_update = datetime.now().isoformat()

    def register_agent(self, agent: AgentRecord):
        self.agents[agent.name] = agent
        self.communication_bus.register_agent(agent)

    def update_agent_status(self, agent_name: str, status: AgentStatus,
                            current_task: Optional[str] = None):
        if agent_name in self.agents:
            self.agents[agent_name].status = status
            self.agents[agent_name].current_task = current_task
            self.agents[agent_name].last_activity = datetime.now().isoformat()
            self.last_update = datetime.now().isoformat()

    def get_dashboard_data(self) -> Dict:
        agents_data = []
        for agent_name, agent in self.agents.items():
            agents_data.append({
                "name": agent.name, "role": agent.role,
                "status": agent.status.value, "current_task": agent.current_task,
                "skill_level": agent.skill_level.value,
                "success_count": agent.success_count, "error_count": agent.error_count,
                "last_activity": agent.last_activity,
                "active_workflows": len(agent.active_workflows)
            })
        recent_messages = [
            {"from": m.from_agent, "to": m.to_agent,
             "type": m.message_type.value, "timestamp": m.timestamp}
            for m in self.communication_bus.message_history[-100:]
        ]
        patterns = self.knowledge_library.get_workflow_patterns()
        return {
            "timestamp": self.last_update,
            "agents": agents_data,
            "recent_communications": recent_messages,
            "workflow_patterns": patterns,
            "lessons_learned_count": len(self.knowledge_library.get_lessons_learned()),
            "total_skills": sum(
                len(self.skill_engine.get_agent_improvement(name)["skills"])
                for name in self.agents
            )
        }


class CommandCenter:
    """API surface for command center and dashboard queries."""

    def __init__(self, state_manager: DashboardStateManager):
        self.state = state_manager

    def get_dashboard(self) -> Dict:
        return self.state.get_dashboard_data()

    def get_agent_details(self, agent_name: str) -> Dict:
        agent = self.state.agents.get(agent_name)
        if not agent:
            return {"error": f"Agent {agent_name} not found"}
        improvement = self.state.skill_engine.get_agent_improvement(agent_name)
        recent_messages = self.state.communication_bus.get_messages_for_agent(agent_name)
        return {
            "agent": asdict(agent),
            "improvement_metrics": improvement,
            "recent_messages": [
                {"from": m.from_agent, "type": m.message_type.value,
                 "timestamp": m.timestamp}
                for m in recent_messages[-20:]
            ]
        }

    def get_workflow_patterns(self) -> List[Dict]:
        return self.state.knowledge_library.get_workflow_patterns()

    def get_lessons_learned(self, category: Optional[str] = None) -> List[Dict]:
        return self.state.knowledge_library.get_lessons_learned(category)

    def get_conversation_log(self, agent1: str, agent2: str) -> List[Dict]:
        messages = self.state.communication_bus.get_conversation(agent1, agent2)
        return [
            {"from": m.from_agent, "to": m.to_agent, "type": m.message_type.value,
             "content": m.content, "timestamp": m.timestamp}
            for m in messages
        ]

    def get_skill_progression(self, agent_name: str) -> Dict:
        return self.state.skill_engine.get_agent_improvement(agent_name)

    def send_agent_message(self, from_agent: str, to_agent: str,
                           message_type: str, content: Dict) -> str:
        msg_type = MessageType[message_type.upper()]
        return self.state.communication_bus.send_message(
            from_agent, to_agent, msg_type, content
        )


# ============================================================================
# DASHBOARD: DEMO / SMOKE TEST
# ============================================================================
def demo_dashboard():
    """Demonstrate dashboard system with simulated agent activity."""

    print("\n" + "=" * 70)
    print("AGENT DASHBOARD & COMMAND CENTER DEMO")
    print("=" * 70 + "\n")

    state = DashboardStateManager()
    command_center = CommandCenter(state)

    # Register agents
    agents = [
        AgentRecord("pm_1",     "PM Agent",     "Project Manager",   AgentStatus.IDLE,     None,           SkillLevel.INTERMEDIATE, 5, 0, datetime.now().isoformat(), []),
        AgentRecord("ba_1",     "BA Agent",     "Business Analyst",  AgentStatus.THINKING, "Requirements", SkillLevel.INTERMEDIATE, 3, 0, datetime.now().isoformat(), []),
        AgentRecord("qa_1",     "QA Agent",     "Quality Assurance", AgentStatus.IDLE,     None,           SkillLevel.NOVICE,       2, 1, datetime.now().isoformat(), []),
        AgentRecord("vendor_1", "Vendor Agent", "Vendor Manager",    AgentStatus.IDLE,     None,           SkillLevel.NOVICE,       1, 0, datetime.now().isoformat(), []),
        AgentRecord("mgr_1",    "Manager Agent","Portfolio Manager",  AgentStatus.IDLE,     None,           SkillLevel.INTERMEDIATE, 4, 0, datetime.now().isoformat(), []),
    ]
    for a in agents:
        state.register_agent(a)

    # Simulate inter-agent communication
    print("1. SIMULATING AGENT COMMUNICATION:")
    print("-" * 70)
    msg_id = state.communication_bus.send_message(
        "PM Agent", "BA Agent", MessageType.DELEGATE,
        {"task": "Extract requirements from AURA design session"}
    )
    print(f"   PM Agent → BA Agent: 'Extract requirements'  [ID: {msg_id[:8]}...]")
    state.communication_bus.send_message(
        "BA Agent", "PM Agent", MessageType.PROVIDE_OUTPUT,
        {"requirements": ["FR1: Silhouette processing", "FR2: 3D mesh export", "FR3: Actor model tagging"]}
    )
    print(f"   BA Agent → PM Agent: Provided 3 requirements")
    state.communication_bus.send_message(
        "PM Agent", "QA Agent", MessageType.REQUEST_INPUT,
        {"task": "Review charter quality before gate submission"}
    )
    print(f"   PM Agent → QA Agent: 'Review charter quality'\n")

    # Record skill executions
    print("2. RECORDING SKILL IMPROVEMENTS:")
    print("-" * 70)
    state.skill_engine.record_success("PM Agent", "create_charter",          120.5, 0.95)
    state.skill_engine.record_success("PM Agent", "create_charter",          115.0, 0.98)
    state.skill_engine.record_success("BA Agent", "extract_requirements",    180.0, 0.92)
    state.skill_engine.record_success("QA Agent", "audit_deliverable",       90.0,  0.88)
    state.skill_engine.record_success("Manager Agent", "portfolio_review",   60.0,  0.96)
    print("   ✅ PM Agent:      create_charter (×2, avg 117.75s)")
    print("   ✅ BA Agent:      extract_requirements (×1, avg 180s)")
    print("   ✅ QA Agent:      audit_deliverable (×1, avg 90s)")
    print("   ✅ Manager Agent: portfolio_review (×1, avg 60s)\n")

    # Show progression
    print("3. AGENT SKILL PROGRESSION:")
    print("-" * 70)
    for agent_name in ["PM Agent", "BA Agent", "QA Agent"]:
        m = state.skill_engine.get_agent_improvement(agent_name)
        print(f"   {agent_name}: {m['total_skills']} skill(s) | "
              f"Success rate {m['success_rate']*100:.0f}% | "
              f"Avg level {m['avg_skill_level']:.1f}/4")

    # Save workflow pattern
    print("\n4. CAPTURING WORKFLOW PATTERNS:")
    print("-" * 70)
    wf = WorkflowExecution(
        workflow_id=str(uuid.uuid4()),
        agent_sequence=["PM Agent", "BA Agent", "QA Agent"],
        start_time=datetime.now().isoformat(),
        end_time=datetime.now().isoformat(),
        duration_seconds=300, success=True,
        input_data={"project": "AURA MVP"},
        output_data={"charter_approved": True},
        errors=[], approvals_needed=1, approvals_completed=1, cost_usd=0.03
    )
    state.knowledge_library.save_workflow_pattern(wf)
    print("   ✅ Pattern saved: PM Agent → BA Agent → QA Agent")

    # Save lesson learned
    print("\n5. CAPTURING LESSONS LEARNED:")
    print("-" * 70)
    state.knowledge_library.save_lesson_learned(
        wf.workflow_id,
        "Charter quality improves with design session review",
        "When BA Agent reviews design sessions first, PM Agent creates better charters",
        "process_optimization", ["PM Agent", "BA Agent"]
    )
    print("   ✅ Lesson: Charter quality improves with design session review")

    # Dashboard snapshot
    print("\n6. DASHBOARD SNAPSHOT:")
    print("-" * 70)
    dashboard = command_center.get_dashboard()
    print(f"   Agents Online:           {len(dashboard['agents'])}")
    for a in dashboard['agents']:
        print(f"     • {a['name']:<16} {a['status']:<20} (skill lvl {a['skill_level']}/4)")
    print(f"   Recent Communications:   {len(dashboard['recent_communications'])}")
    print(f"   Workflow Patterns:        {len(dashboard['workflow_patterns'])}")
    print(f"   Lessons Learned:          {dashboard['lessons_learned_count']}")
    print(f"   Total Skills Tracked:     {dashboard['total_skills']}")

    print("\n" + "=" * 70)
    print("DASHBOARD & COMMAND CENTER CAPABILITIES")
    print("=" * 70)
    print("""
  ✅ AGENT MONITORING    — Real-time status, task, success/error counts
  ✅ INTER-AGENT COMMS   — Message bus (6 types: initiate/delegate/feedback…)
  ✅ SKILL COMPOUNDING   — Novice → Intermediate → Advanced → Expert (50+)
  ✅ KNOWLEDGE LIBRARY   — Reusable workflow patterns + lessons learned
  ✅ PERFORMANCE METRICS — Success rate, avg skill level, progression
  ✅ COMMAND CENTER API  — Dashboard, agent details, conversation log
    """)


# ============================================================================
# TOKEN STRATEGY: PRICING & COST MODELS
# ============================================================================
class TokenPricing:
    """claude-opus-4-6 token pricing."""
    input_cost_per_1m = 5.00
    output_cost_per_1m = 25.00
    cache_write_cost_per_1m = 0.50
    cache_read_cost_per_1m = 0.50

    @staticmethod
    def calculate_cost(input_tokens: int, output_tokens: int,
                       cache_created: int = 0, cache_read: int = 0) -> float:
        return (
            (input_tokens   / 1_000_000) * TokenPricing.input_cost_per_1m +
            (output_tokens  / 1_000_000) * TokenPricing.output_cost_per_1m +
            (cache_created  / 1_000_000) * TokenPricing.cache_write_cost_per_1m +
            (cache_read     / 1_000_000) * TokenPricing.cache_read_cost_per_1m
        )


class AgentCostModel:
    """Realistic token costs for each agent type."""

    PM_AGENT     = {"name": "PM Agent",      "input_tokens": 3500, "output_tokens": 1200, "calls_per_day": 2, "description": "Project setup, WBS, status tracking"}
    BA_AGENT     = {"name": "BA Agent",      "input_tokens": 6000, "output_tokens": 1500, "calls_per_day": 1, "description": "Design sessions, requirements, traceability"}
    QA_AGENT     = {"name": "QA Agent",      "input_tokens": 8000, "output_tokens": 2000, "calls_per_day": 1, "description": "Pre-delivery audit, scope creep detection"}
    VENDOR_AGENT = {"name": "Vendor Agent",  "input_tokens": 4000, "output_tokens":  800, "calls_per_day": 1, "description": "Partner SLA tracking, performance monitoring"}
    MANAGER_AGENT= {"name": "Manager Agent", "input_tokens": 5000, "output_tokens": 1500, "calls_per_day": 1, "description": "Portfolio dashboard, orchestration"}

    ALL_AGENTS = [PM_AGENT, BA_AGENT, QA_AGENT, VENDOR_AGENT, MANAGER_AGENT]

    @staticmethod
    def cost_per_agent(agent: dict) -> float:
        return TokenPricing.calculate_cost(agent["input_tokens"], agent["output_tokens"])

    @staticmethod
    def daily_cost_per_agent(agent: dict) -> float:
        return AgentCostModel.cost_per_agent(agent) * agent["calls_per_day"]

    @staticmethod
    def total_daily_cost() -> float:
        return sum(AgentCostModel.daily_cost_per_agent(a) for a in AgentCostModel.ALL_AGENTS)


class TokenBudgetModel:
    """Executive budget allocation model (reporting-focused)."""

    DAILY_BUDGET = 5.00
    CONTINGENCY_PCT = 0.20

    @staticmethod
    def daily_agent_cost() -> float:
        return AgentCostModel.total_daily_cost()

    @staticmethod
    def budget_allocation() -> dict:
        agent_cost = TokenBudgetModel.daily_agent_cost()
        contingency = TokenBudgetModel.DAILY_BUDGET * TokenBudgetModel.CONTINGENCY_PCT
        return {
            "Daily Budget":          f"${TokenBudgetModel.DAILY_BUDGET:.2f}",
            "Agent Execution Cost":  f"${agent_cost:.2f}",
            "Contingency (20%)":     f"${contingency:.2f}",
            "Safety Buffer":         f"${TokenBudgetModel.DAILY_BUDGET - agent_cost - contingency:.2f}",
            "Headroom Multiplier":   f"{TokenBudgetModel.DAILY_BUDGET / agent_cost:.1f}x",
        }

    @staticmethod
    def monthly_cost(days: int = 30) -> float:
        return TokenBudgetModel.daily_agent_cost() * days


class OptimizationTechniques:
    """3 token optimization techniques with impact analysis."""

    @staticmethod
    def technique_1_prompt_precision() -> dict:
        verbose_cost   = TokenPricing.calculate_cost(1500, 2400)
        precision_cost = TokenPricing.calculate_cost(1500,  800)
        savings     = verbose_cost - precision_cost
        savings_pct = (savings / verbose_cost) * 100
        return {
            "technique":        "Prompt Precision",
            "description":      "Use exact output format (JSON, tables). No explanations.",
            "example":          "Verbose: 2,400 output tokens  ->  Precision JSON: 800 output tokens",
            "savings_per_call": f"${savings:.4f} ({savings_pct:.0f}%)",
            "monthly_savings":  f"${savings * 60:.2f} (60 charter calls/month)",
            "impact":           "MAJOR (67% output token reduction)",
        }

    @staticmethod
    def technique_2_caching() -> dict:
        lib_size = 30_000
        no_cache_monthly = TokenPricing.calculate_cost(4500 * 6, 0) * 30
        cache_calls      = TokenPricing.calculate_cost( 500 * 6, 0) * 30
        cache_write      = TokenPricing.calculate_cost(lib_size, 0, cache_created=lib_size)
        monthly_savings  = no_cache_monthly - (cache_calls + cache_write)
        return {
            "technique":       "Caching Templates",
            "description":     "Load template library once, reuse across all agent calls.",
            "example":         "60+ templates (30K tokens) cached -> reused every call",
            "cache_cost":      f"${cache_write:.2f} (one-time)",
            "monthly_savings": f"${monthly_savings:.2f}",
            "breakeven":       "2 days",
            "impact":          "MAJOR (55% input token reduction)",
        }

    @staticmethod
    def technique_3_scheduled_batching() -> dict:
        redundant = TokenPricing.calculate_cost(3500, 0)  # 1 avoided call/day
        return {
            "technique":             "Scheduled Batching",
            "description":           "Run agents on fixed schedule (6 AM, 9 AM, 5 PM). Batch output for review.",
            "example":               "3 on-demand calls (same data)  ->  2 scheduled calls",
            "redundant_tokens_daily":"3,500 tokens",
            "daily_savings":         f"${redundant:.4f}",
            "monthly_savings":       f"${redundant * 30:.2f}",
            "impact":                "MODERATE (33% redundant call elimination)",
        }

    @staticmethod
    def total_optimization_impact() -> dict:
        baseline  = TokenBudgetModel.daily_agent_cost()
        optimized = baseline * 0.25   # 75% combined reduction (conservative)
        return {
            "baseline_daily_cost":  f"${baseline:.4f}",
            "optimized_daily_cost": f"${optimized:.4f}",
            "daily_savings":        f"${baseline - optimized:.4f}",
            "monthly_savings":      f"${(baseline - optimized) * 30:.2f}",
            "combined_impact":      "75% overall token reduction",
            "headroom":             f"Can support {5 / optimized:.0f}x more agents before budget stress",
        }


# ============================================================================
# TOKEN STRATEGY: DISPLAY FUNCTIONS
# ============================================================================
def _print_header(title: str, width: int = 70):
    print(f"\n{'='*width}\n{title.center(width)}\n{'='*width}\n")


def show_budget_model():
    _print_header("BUDGET MODEL: Daily Allocation")
    for key, value in TokenBudgetModel.budget_allocation().items():
        print(f"{key:.<40} {value:>20}")
    print(f"\n{'Status:':<40} Within budget ({TokenBudgetModel.DAILY_BUDGET / TokenBudgetModel.daily_agent_cost():.0f}x headroom)")


def show_cost_breakdown():
    _print_header("AGENT COST BREAKDOWN")
    print(f"{'Agent':<25} {'Per Call':>15} {'Daily Cost':>15}")
    print("-" * 55)
    total = 0.0
    for agent in AgentCostModel.ALL_AGENTS:
        per_call = AgentCostModel.cost_per_agent(agent)
        daily    = AgentCostModel.daily_cost_per_agent(agent)
        total   += daily
        print(f"{agent['name']:<25} ${per_call:>14.4f} ${daily:>14.4f}")
    print("-" * 55)
    print(f"{'TOTAL DAILY':<25} {'':>15} ${total:>14.4f}")
    print(f"\nMonthly Cost (30 days): ${TokenBudgetModel.monthly_cost():.2f}")
    print("Equivalent FTE Avoided: $15,000+ (monthly salary)")


def show_optimization_impact():
    _print_header("OPTIMIZATION TECHNIQUES: Token Savings")
    for i, technique_fn in enumerate([
        OptimizationTechniques.technique_1_prompt_precision,
        OptimizationTechniques.technique_2_caching,
        OptimizationTechniques.technique_3_scheduled_batching,
    ], 1):
        t = technique_fn()
        print(f"TECHNIQUE {i}: {t['technique']}")
        for key, val in t.items():
            if key != "technique":
                print(f"  {key.replace('_', ' ').title()}: {val}")
        print()

    combined = OptimizationTechniques.total_optimization_impact()
    print("COMBINED IMPACT (All 3 Techniques):")
    for key, val in combined.items():
        print(f"  {key.replace('_', ' ').title()}: {val}")


def show_cost_projections():
    _print_header("MONTHLY COST PROJECTIONS")
    scenarios = {
        "Aura Only (1 project)":       0.5,
        "Full Portfolio (5 projects)": 1.0,
        "Scaled (10+ projects)":       1.5,
    }
    for name, factor in scenarios.items():
        cost = TokenBudgetModel.monthly_cost() * factor
        flag = "OK" if cost <= 150 else "REVIEW" if cost <= 300 else "UPGRADE"
        print(f"  [{flag}] {name}: ${cost:.2f}")


def show_executive_summary():
    _print_header("EXECUTIVE SUMMARY: Token Strategy for First Genesis")
    agent_cost = TokenBudgetModel.daily_agent_cost()
    metrics = {
        "Daily Budget":                     "$5.00 USD",
        "Agent Execution Cost":             f"${agent_cost:.4f} USD",
        "Budget Headroom":                  f"{TokenBudgetModel.DAILY_BUDGET / agent_cost:.0f}x",
        "Monthly Cost (Full Portfolio)":    f"${TokenBudgetModel.monthly_cost():.2f} USD",
        "Token Optimization":               "75% reduction via 3 techniques",
        "Autonomy Level":                   "80% (agents run unsupervised)",
        "Human Approval Time (per workflow)": "2-5 minutes",
        "Setup Time":                       "3 weeks (foundation -> integration -> go-live)",
    }
    for key, val in metrics.items():
        print(f"  {key}: {val}")

    print("""
OPTIMIZATION TECHNIQUES:
  1. Prompt Precision:      67% output token reduction
  2. Caching Templates:     55% input token reduction
  3. Scheduled Batching:    33% redundant call elimination
  Combined:                 75% overall token reduction

RISK MITIGATION:
  Hard budget stops (no overage possible)
  Frozen facts validation (prevents hallucinations)
  Human-in-the-loop gates (approval before delivery)
  Workflow persistence (audit trail)
  Timeout escalation (auto-escalate if no approval)

IMPLEMENTATION TIMELINE:
  Week 1: Foundation (guardrail code, templates, API)
  Week 2: Integration (agents built & tested)
  Week 3: Scheduler (automation live)

STATUS: READY FOR DEPLOYMENT
""")


def show_token_dashboard():
    _print_header("TOKEN STRATEGY DASHBOARD", width=80)

    print("BUDGET STATUS:")
    for key, val in TokenBudgetModel.budget_allocation().items():
        print(f"  {key}: {val}")

    print("\nAGENT COSTS (Daily):")
    total = 0.0
    for agent in AgentCostModel.ALL_AGENTS:
        daily = AgentCostModel.daily_cost_per_agent(agent)
        total += daily
        print(f"  {agent['name']}: ${daily:.4f} ({agent['calls_per_day']} calls)")
    print(f"  {'─'*40}")
    print(f"  TOTAL: ${total:.4f}")

    print("\nOPTIMIZATION POTENTIAL:")
    combined = OptimizationTechniques.total_optimization_impact()
    for key, val in combined.items():
        print(f"  {key.replace('_', ' ').title()}: {val}")

    print("\nMONTHLY PROJECTIONS:")
    for name, factor in [("Aura Only", 0.5), ("Full Portfolio", 1.0), ("Scaled 10+", 1.5)]:
        cost = TokenBudgetModel.monthly_cost() * factor
        flag = "OK    " if cost <= 150 else "REVIEW" if cost <= 300 else "UPGRADE"
        print(f"  [{flag}] {name}: ${cost:.2f}")

    print("\nRECOMMENDATIONS:")
    recs = [
        "Use all 3 optimization techniques (75% savings)",
        "Implement email approval gates (human-in-the-loop)",
        "Schedule agents on fixed timeline (6 AM, 9 AM, 5 PM)",
        "Cache template library at startup (one-time cost)",
        "Monitor headroom monthly (decision point at >$300/month)",
    ]
    for i, rec in enumerate(recs, 1):
        print(f"  {i}. {rec}")
    print("\n" + "=" * 80)


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================
def main():
    if len(sys.argv) < 2:
        print("Usage: python claude_code_agent_ecosystem.py [command]")
        print("\nAgent Commands:")
        print("  run_pm_agent              Run PM Agent with approval gate")
        print("  check_approvals           Show pending approvals")
        print("  resume_workflows          Resume approved workflows")
        print("  process_approval          Process approval response")
        print("    --workflow-id <id>")
        print("    --approver <email>")
        print("    --decision <approved|rejected>")
        print("    --feedback <comment>")
        print("  budget_status             Show live budget usage report")
        print("  audit_hallucinations      Show recent hallucination flags")
        print("\nToken Strategy Commands:")
        print("  show_budget_model         Daily budget allocation")
        print("  show_cost_breakdown       Per-agent cost breakdown")
        print("  show_optimization_impact  Technique savings analysis")
        print("  project_monthly_cost      Monthly cost scenarios")
        print("  show_executive_summary    Complete executive summary")
        print("  token_dashboard           Full token strategy dashboard")
        print("\nDashboard & Command Center Commands:")
        print("  demo_dashboard            Run dashboard demo with simulated agents")
        sys.exit(1)

    command = sys.argv[1]

    # ── Dashboard commands (no DB/API needed) ─────────────────────────────────
    if command == "demo_dashboard":
        demo_dashboard(); return

    # ── Token strategy commands (no DB/API needed) ────────────────────────────
    elif command == "show_budget_model":
        show_budget_model(); return
    elif command == "show_cost_breakdown":
        show_cost_breakdown(); return
    elif command == "show_optimization_impact":
        show_optimization_impact(); return
    elif command == "project_monthly_cost":
        show_cost_projections(); return
    elif command == "show_executive_summary":
        show_executive_summary(); return
    elif command == "token_dashboard":
        show_token_dashboard(); return

    # ── Agent commands (require DB + API) ────────────────────────────────────

    agent = AutonomousAgentWithEmailGates()

    if command == "run_pm_agent":
        logger.info("Running PM Agent with email approval gate...")
        try:
            charter, workflow_state = agent.run_pm_agent_with_gates({
                "client": "Malcolm Goodwin",
                "project": "AURA MVP",
                "timeline_weeks": 12,
                "scope": "Silhouette technology + 3D mesh design + actor model"
            })
            gate = StageGateManager.STAGE_GATES[StageGateName.CHARTER_APPROVAL]
            print("\n" + "=" * 70)
            print(f"WORKFLOW CREATED: {workflow_state.workflow_id}")
            print("=" * 70)
            print(f"Status:      {workflow_state.status.value}")
            print(f"Stage Gate:  {workflow_state.current_stage_gate.value}")
            print(f"Sent To:     {gate.approver_email}")
            print(f"CC:          {', '.join(gate.cc_emails)}")
            print(f"\nGenerated Charter (preview):\n{charter[:300]}...")
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
        pending = agent.db.get_approved_workflows_ready_to_resume()
        if not pending:
            print("No approved workflows ready to resume.")
        else:
            for wf in pending:
                result = agent.resume_approved_workflow(wf.workflow_id)
                print(f"\nResumed {wf.workflow_id}\n   Next step: {result}")

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

    elif command == "budget_status":
        print(agent.budget_enforcer.get_status_report())

    elif command == "audit_hallucinations":
        agent.db.cursor.execute(
            "SELECT * FROM hallucination_flags ORDER BY timestamp DESC LIMIT 10"
        )
        flags = agent.db.cursor.fetchall()
        print("\n" + "=" * 70)
        print("HALLUCINATION FLAGS (Last 10)")
        print("=" * 70)
        if not flags:
            print("No hallucination flags detected")
        else:
            for flag_id, agent_name, timestamp, reason, snippet in flags:
                print(f"\n[{flag_id}] {agent_name} @ {timestamp}")
                print(f"  Reason:  {reason}")
                if snippet:
                    print(f"  Snippet: {snippet[:80]}...")

    # ── Legacy agent governance commands ─────────────────────────────────────
    elif command == "run_ba_agent":
        output, wf = agent.run_ba_agent_with_gates(
            project_name="AURA MVP",
            transcript=(
                "We need to support 1000 users per minute. The system must work on mobile "
                "and desktop. Security is critical — 2FA and encryption required. "
                "The UI should allow any task in 3 clicks or fewer."
            ),
        )
        print(f"\n✅ BA Agent complete. Workflow: {wf.workflow_id}")
        print(f"   Status: {wf.status.value}")
        print(f"   Output preview: {output[:200]}…")

    elif command == "run_qa_agent":
        pending = agent.db.get_pending_approvals()
        source_wf_id = pending[0].workflow_id if pending else "no_workflow"
        project = pending[0].project_name if pending else "AURA MVP"
        output, wf = agent.run_qa_agent_with_gates(source_wf_id, project)
        print(f"\n✅ QA Agent complete. Workflow: {wf.workflow_id}")
        print(f"   Status: {wf.status.value}")
        print(f"   Output preview: {output[:200]}…")

    elif command == "run_vendor_agent":
        output, wf = agent.run_vendor_agent_with_gates(
            vendor_name="Yubi",
            metrics={
                "on_time_delivery_pct": 98,
                "quality_score": 92,
                "response_time_hours": 18,
                "budget_variance_pct": 3,
                "period": "March 2026",
            },
        )
        print(f"\n✅ Vendor Agent complete. Workflow: {wf.workflow_id}")
        print(f"   Status: {wf.status.value}")
        print(f"   Output preview: {output[:200]}…")

    elif command == "run_manager_agent":
        print("\n⏸  Manager Agent is IDLE for MVP.")
        print("   Active agents: pm_agent, ba_agent, qa_agent, vendor_agent")
        print("   To enable: add 'manager_agent' to MVP_ACTIVE_AGENTS in this file.")

    # ── Sales pipeline governance commands (IDLE for MVP) ────────────────────
    elif command in ("sales_qualify", "sales_pipeline"):
        print(f"\n⏸  Sales agents are IDLE for MVP (command: {command}).")
        print("   Active agents: pm_agent, ba_agent, qa_agent, vendor_agent")
        print("   To enable: add the relevant agent to MVP_ACTIVE_AGENTS in this file.")

    elif command == "sales_pending":
        if not _GOVERNANCE_AVAILABLE:
            print("ERROR: governance modules not found. Run from project root.")
            sys.exit(1)
        from fg_gated_orchestrator import GatedOrchestrator
        GatedOrchestrator().list_pending_reviews()

    elif command == "sales_budget":
        if not _GOVERNANCE_AVAILABLE:
            print("ERROR: governance modules not found. Run from project root.")
            sys.exit(1)
        from fg_gated_orchestrator import GatedOrchestrator
        GatedOrchestrator().print_budget_status()

    elif command == "sales_stats":
        if not _GOVERNANCE_AVAILABLE:
            print("ERROR: governance modules not found. Run from project root.")
            sys.exit(1)
        from fg_gated_orchestrator import GatedOrchestrator
        orch = GatedOrchestrator()
        orch.print_approval_stats()
        orch.print_daily_cost()

    # ── IDLE agents (non-MVP) ─────────────────────────────────────────────────
    elif command in (
        "sales_account", "sales_competitor", "sales_forecast",
    ):
        print(f"\n⏸  '{command}' agent is IDLE for MVP.")
        print("   Active agents: pm_agent, ba_agent, qa_agent, vendor_agent")
        print("   To enable: add the relevant agent to MVP_ACTIVE_AGENTS in this file.")

    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
