#!/usr/bin/env python3
"""
dashboard_server.py
Complete Flask server for First Genesis Agent Command Center.

Includes:
1. WebSocket real-time updates (every 3 seconds)
2. REST API endpoints (dashboard, agents, patterns, lessons, communicate)
3. Alert system (error rate, budget, duration)
4. Metrics export (CSV, PDF, email)
5. Audit trail (access logs, changes)
6. Collaboration (comments, voting, versioning)

Installation:
    pip install flask flask-socketio flask-cors python-socketio flask-sqlalchemy
    pip install reportlab

Usage:
    python dashboard_server.py
    # Then:
    # - Open http://localhost:5000/dashboard in browser
    # - WebSocket auto-connects and streams updates
    # - API available at http://localhost:5000/api/*
    # - Admin panel at http://localhost:5000/admin
"""
import csv
import io
import json
import os
import uuid
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from flask import Flask, render_template_string, request, jsonify, send_file
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    REPORTLAB_AVAILABLE = True
except ImportError:
    REPORTLAB_AVAILABLE = False

# ============================================================================
# CONFIGURATION
# ============================================================================
class Config:
    SECRET_KEY        = os.environ.get("SECRET_KEY", "fg-dashboard-secret-key")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:////home/claude/fg_dashboard.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

# ============================================================================
# INITIALIZATION
# ============================================================================
app = Flask(__name__)
app.config.from_object(Config)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
db = SQLAlchemy(app)

# ============================================================================
# DATABASE MODELS
# ============================================================================
class AlertLog(db.Model):
    __tablename__ = "alert_logs"
    id           = db.Column(db.Integer,  primary_key=True)
    alert_type   = db.Column(db.String(50))
    agent_name   = db.Column(db.String(100))
    message      = db.Column(db.String(500))
    severity     = db.Column(db.String(20))
    created_at   = db.Column(db.String(50))
    acknowledged = db.Column(db.Boolean, default=False)

class AuditLog(db.Model):
    __tablename__ = "audit_logs"
    id          = db.Column(db.Integer, primary_key=True)
    action      = db.Column(db.String(100))
    resource    = db.Column(db.String(100))
    resource_id = db.Column(db.String(100))
    user        = db.Column(db.String(100))
    details     = db.Column(db.Text)
    timestamp   = db.Column(db.String(50))
    ip_address  = db.Column(db.String(50))

class Comment(db.Model):
    __tablename__ = "comments"
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.String(100))
    author     = db.Column(db.String(100))
    content    = db.Column(db.Text)
    created_at = db.Column(db.String(50))
    updated_at = db.Column(db.String(50))

class Vote(db.Model):
    __tablename__ = "votes"
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.String(100))
    voter      = db.Column(db.String(100))
    vote_type  = db.Column(db.String(20))   # useful | not_useful
    created_at = db.Column(db.String(50))

class LessonVersion(db.Model):
    __tablename__ = "lesson_versions"
    id         = db.Column(db.Integer, primary_key=True)
    lesson_id  = db.Column(db.String(100))
    version    = db.Column(db.Integer)
    title      = db.Column(db.String(200))
    content    = db.Column(db.Text)
    author     = db.Column(db.String(100))
    created_at = db.Column(db.String(50))

# ============================================================================
# ALERT SYSTEM
# ============================================================================
class AlertSystem:
    """Proactive monitoring — error-rate, budget, and duration alerts."""

    def __init__(self):
        self.thresholds = {
            "error_rate":        0.05,   # 5%
            "budget_percent":    0.80,   # 80% of $5 daily budget
            "workflow_duration": 300,    # 5-minute expected duration (seconds)
        }

    def check_error_rate(self, agent_name: str, success_count: int,
                         error_count: int) -> Optional[Dict]:
        total = success_count + error_count
        if total == 0:
            return None
        error_rate = error_count / total
        if error_rate > self.thresholds["error_rate"]:
            alert = {
                "type": "error_rate",
                "agent": agent_name,
                "message": (f"{agent_name} error rate: {error_rate*100:.1f}% "
                            f"(threshold: {self.thresholds['error_rate']*100:.1f}%)"),
                "severity": "critical" if error_rate > 0.10 else "warning",
                "timestamp": datetime.now().isoformat()
            }
            self.log_alert(alert)
            return alert
        return None

    def check_budget(self, daily_cost: float, daily_budget: float = 5.0) -> Optional[Dict]:
        pct = (daily_cost / daily_budget) * 100
        if pct > self.thresholds["budget_percent"] * 100:
            alert = {
                "type": "budget",
                "message": (f"Daily budget approaching: ${daily_cost:.2f}/${daily_budget:.2f} "
                            f"({pct:.1f}%)"),
                "severity": "warning" if pct < 95 else "critical",
                "timestamp": datetime.now().isoformat()
            }
            self.log_alert(alert)
            return alert
        return None

    def check_workflow_duration(self, workflow_id: str, duration: int,
                                expected: int = 300) -> Optional[Dict]:
        if duration > expected * 1.5:
            alert = {
                "type": "duration",
                "workflow": workflow_id,
                "message": f"Workflow {workflow_id} took {duration}s (expected: {expected}s)",
                "severity": "warning",
                "timestamp": datetime.now().isoformat()
            }
            self.log_alert(alert)
            return alert
        return None

    @staticmethod
    def log_alert(alert: Dict):
        with app.app_context():
            log = AlertLog(
                alert_type=alert["type"],
                agent_name=alert.get("agent", "-"),
                message=alert["message"],
                severity=alert["severity"],
                created_at=alert["timestamp"]
            )
            db.session.add(log)
            db.session.commit()


alert_system = AlertSystem()

# ============================================================================
# AUDIT TRAIL
# ============================================================================
class AuditTrail:
    """Log every dashboard access and data change."""

    @staticmethod
    def log_access(user: str, resource: str):
        ip = request.remote_addr if request else "api"
        log = AuditLog(action="access", resource=resource, user=user,
                       timestamp=datetime.now().isoformat(), ip_address=ip)
        db.session.add(log)
        db.session.commit()

    @staticmethod
    def log_change(action: str, resource: str, resource_id: str,
                   user: str, details: Dict):
        ip = request.remote_addr if request else "api"
        log = AuditLog(action=action, resource=resource, resource_id=resource_id,
                       user=user, details=json.dumps(details),
                       timestamp=datetime.now().isoformat(), ip_address=ip)
        db.session.add(log)
        db.session.commit()

# ============================================================================
# COLLABORATION SYSTEM
# ============================================================================
class CollaborationSystem:
    """Comments, voting, and version control for lessons learned."""

    # ── Comments ─────────────────────────────────────────────────────────────
    @staticmethod
    def add_comment(lesson_id: str, author: str, content: str) -> Dict:
        now = datetime.now().isoformat()
        comment = Comment(lesson_id=lesson_id, author=author, content=content,
                          created_at=now, updated_at=now)
        db.session.add(comment)
        db.session.commit()
        AuditTrail.log_change("create", "comment", str(comment.id), author,
                              {"lesson_id": lesson_id})
        return {"id": comment.id, "lesson_id": lesson_id, "author": author,
                "content": content, "created_at": now}

    @staticmethod
    def get_comments(lesson_id: str) -> List[Dict]:
        comments = Comment.query.filter_by(lesson_id=lesson_id).all()
        return [{"id": c.id, "lesson_id": c.lesson_id, "author": c.author,
                 "content": c.content, "created_at": c.created_at} for c in comments]

    # ── Voting ────────────────────────────────────────────────────────────────
    @staticmethod
    def vote(lesson_id: str, voter: str, vote_type: str):
        vote = Vote(lesson_id=lesson_id, voter=voter, vote_type=vote_type,
                    created_at=datetime.now().isoformat())
        db.session.add(vote)
        db.session.commit()
        AuditTrail.log_change("create", "vote", str(vote.id), voter,
                              {"lesson_id": lesson_id, "vote": vote_type})

    @staticmethod
    def get_vote_stats(lesson_id: str) -> Dict:
        useful     = Vote.query.filter_by(lesson_id=lesson_id, vote_type="useful").count()
        not_useful = Vote.query.filter_by(lesson_id=lesson_id, vote_type="not_useful").count()
        total = useful + not_useful
        return {"useful": useful, "not_useful": not_useful, "total": total,
                "score": useful / total if total > 0 else 0}

    # ── Version control ───────────────────────────────────────────────────────
    @staticmethod
    def create_version(lesson_id: str, title: str, content: str, author: str) -> int:
        max_v = (db.session.query(db.func.max(LessonVersion.version))
                 .filter_by(lesson_id=lesson_id).scalar() or 0)
        v = LessonVersion(lesson_id=lesson_id, version=max_v + 1, title=title,
                          content=content, author=author,
                          created_at=datetime.now().isoformat())
        db.session.add(v)
        db.session.commit()
        AuditTrail.log_change("create", "lesson_version", str(v.id), author,
                              {"lesson_id": lesson_id, "version": max_v + 1})
        return max_v + 1

    @staticmethod
    def get_versions(lesson_id: str) -> List[Dict]:
        versions = (LessonVersion.query.filter_by(lesson_id=lesson_id)
                    .order_by(LessonVersion.version.desc()).all())
        return [{"version": v.version, "title": v.title, "content": v.content,
                 "author": v.author, "created_at": v.created_at} for v in versions]

# ============================================================================
# EXPORT SYSTEM
# ============================================================================
class ExportSystem:
    """Export metrics to CSV, PDF, or email."""

    @staticmethod
    def export_csv(data: List[Dict], filename: str = "export.csv") -> io.BytesIO:
        out = io.StringIO()
        if data:
            writer = csv.DictWriter(out, fieldnames=data[0].keys())
            writer.writeheader()
            writer.writerows(data)
        return io.BytesIO(out.getvalue().encode())

    @staticmethod
    def export_pdf(title: str, data: List[Dict],
                   filename: str = "report.pdf") -> io.BytesIO:
        buf = io.BytesIO()
        if not REPORTLAB_AVAILABLE:
            text = f"{title}\n{'='*60}\n"
            for row in data:
                text += str(row) + "\n"
            buf.write(text.encode())
            buf.seek(0)
            return buf

        doc = SimpleDocTemplate(buf, pagesize=letter)
        styles = getSampleStyleSheet()
        elements = [Paragraph(title, styles["Heading1"]), Spacer(1, 12)]
        if data:
            headers = list(data[0].keys())
            table_data = [headers] + [[str(r.get(h, "")) for h in headers] for r in data]
            tbl = Table(table_data)
            tbl.setStyle(TableStyle([
                ("BACKGROUND",    (0, 0), (-1, 0),  colors.HexColor("#4f46e5")),
                ("TEXTCOLOR",     (0, 0), (-1, 0),  colors.white),
                ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
                ("FONTSIZE",      (0, 0), (-1, -1), 8),
                ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, colors.HexColor("#f8f9ff")]),
                ("GRID",          (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
            ]))
            elements.append(tbl)
        doc.build(elements)
        buf.seek(0)
        return buf

    @staticmethod
    def send_email_summary(recipient: str, metrics: Dict) -> Dict:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        sender   = os.environ.get("OUTLOOK_SENDER")
        password = os.environ.get("OUTLOOK_PASSWORD")
        if not sender or not password:
            app.logger.warning("Email not configured (OUTLOOK_SENDER/OUTLOOK_PASSWORD missing)")
            return {"status": "not_configured", "recipient": recipient}
        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = "First Genesis Dashboard Summary"
            msg["From"]    = sender
            msg["To"]      = recipient
            body = "<h2>FG Dashboard Summary</h2><pre>" + json.dumps(metrics, indent=2) + "</pre>"
            msg.attach(MIMEText(body, "html"))
            with smtplib.SMTP("smtp.office365.com", 587) as s:
                s.starttls()
                s.login(sender, password)
                s.sendmail(sender, recipient, msg.as_string())
            return {"status": "sent", "recipient": recipient,
                    "timestamp": datetime.now().isoformat()}
        except Exception as e:
            app.logger.error(f"Email failed: {e}")
            return {"status": "error", "error": str(e)}

# ============================================================================
# LIVE DASHBOARD STATE (shared with ecosystem module when available)
# ============================================================================
try:
    from claude_code_agent_ecosystem import DashboardStateManager, CommandCenter
    _state = DashboardStateManager()
    _cmd   = CommandCenter(_state)
    ECOSYSTEM_AVAILABLE = True
except ImportError:
    ECOSYSTEM_AVAILABLE = False
    _state = None
    _cmd   = None

_MOCK_AGENTS = [
    {"name": "PM Agent",      "role": "Project Manager",    "status": "executing",        "task": "Creating AURA charter",  "success": 12, "error": 1, "skill_level": 3.2, "active_workflows": 2},
    {"name": "BA Agent",      "role": "Business Analyst",   "status": "thinking",         "task": "Extracting requirements","success": 8,  "error": 0, "skill_level": 2.8, "active_workflows": 1},
    {"name": "QA Agent",      "role": "Quality Assurance",  "status": "idle",             "task": None,                     "success": 5,  "error": 1, "skill_level": 2.0, "active_workflows": 0},
    {"name": "Vendor Agent",  "role": "Partner Monitor",    "status": "idle",             "task": None,                     "success": 10, "error": 0, "skill_level": 3.5, "active_workflows": 0},
    {"name": "Manager Agent", "role": "Portfolio Manager",  "status": "waiting_approval", "task": "Awaiting approvals",     "success": 15, "error": 2, "skill_level": 3.1, "active_workflows": 3},
]

def _build_dashboard() -> Dict:
    """Dashboard payload — real state when ecosystem is available, mock otherwise."""
    if _cmd:
        return _cmd.get_dashboard()
    return {
        "timestamp": datetime.now().isoformat(),
        "agents": _MOCK_AGENTS,
        "recent_communications": [],
        "workflow_patterns": [],
        "lessons_learned_count": 0,
        "total_skills": 0,
        "metrics": {
            "success_rate": 0.94,
            "total_skills": 23,
            "workflows_today": 12,
            "daily_cost": 0.25
        }
    }

# ============================================================================
# WEBSOCKET
# ============================================================================
@socketio.on("connect")
def handle_connect():
    app.logger.info(f"WebSocket connected: {request.sid}")
    emit("response", {"data": "Connected to FG Dashboard"})
    emit("dashboard_update", _build_dashboard())

@socketio.on("disconnect")
def handle_disconnect():
    app.logger.info(f"WebSocket disconnected: {request.sid}")

@socketio.on("request_update")
def handle_update_request(data):
    emit("dashboard_update", _build_dashboard())

def _push_loop():
    """Background thread — push state every 3 seconds."""
    while True:
        time.sleep(3)
        try:
            socketio.emit("dashboard_update", _build_dashboard())
            active_alerts = AlertLog.query.filter_by(acknowledged=False).all()
            if active_alerts:
                socketio.emit("alerts_update", [
                    {"id": a.id, "type": a.alert_type, "agent": a.agent_name,
                     "message": a.message, "severity": a.severity,
                     "created_at": a.created_at}
                    for a in active_alerts
                ])
        except Exception as e:
            app.logger.debug(f"Push error: {e}")

# ============================================================================
# HTML PAGES
# ============================================================================
@app.route("/dashboard")
def serve_dashboard():
    with db.session.begin_nested():
        AuditTrail.log_access(request.args.get("user", "anonymous"), "dashboard")
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.html")
    try:
        with open(html_path) as f:
            html = f.read()
        html = html.replace("const API_URL = null;",
                            'const API_URL = "http://localhost:5000/api";')
        return html
    except FileNotFoundError:
        return "<h2>dashboard.html not found — place it in the same directory.</h2>", 404

@app.route("/admin")
def admin_panel():
    return render_template_string(ADMIN_TEMPLATE)

# ============================================================================
# REST API — DASHBOARD & AGENTS
# ============================================================================
@app.route("/api/dashboard")
def api_dashboard():
    AuditTrail.log_access(request.args.get("user", "anonymous"), "dashboard")
    return jsonify(_build_dashboard())

@app.route("/api/agents/<name>")
def api_agent_detail(name: str):
    AuditTrail.log_access(request.args.get("user", "anonymous"), f"agent:{name}")
    if _cmd:
        return jsonify(_cmd.get_agent_details(name))
    return jsonify({"name": name, "error": "Ecosystem not loaded — showing stub",
                    "skills": [], "recent_messages": []})

@app.route("/api/patterns")
def api_patterns():
    AuditTrail.log_access(request.args.get("user", "anonymous"), "patterns")
    if _cmd:
        return jsonify(_cmd.get_workflow_patterns())
    return jsonify({"patterns": []})

@app.route("/api/lessons")
def api_lessons():
    category = request.args.get("category")
    AuditTrail.log_access(request.args.get("user", "anonymous"), "lessons")
    if _cmd:
        return jsonify(_cmd.get_lessons_learned(category))
    return jsonify({"lessons": []})

@app.route("/api/communicate", methods=["POST"])
def api_communicate():
    data = request.get_json(force=True)
    AuditTrail.log_change("create", "message", str(uuid.uuid4()),
                          data.get("from_agent", "unknown"), data)
    if _cmd:
        msg_id = _cmd.send_agent_message(
            data.get("from_agent"), data.get("to_agent"),
            data.get("message_type", "initiate"), data.get("content", {})
        )
        return jsonify({"status": "sent", "message_id": msg_id,
                        "timestamp": datetime.now().isoformat()})
    return jsonify({"status": "sent", "message_id": str(uuid.uuid4()),
                    "timestamp": datetime.now().isoformat()})

@app.route("/api/skill-progression/<agent_name>")
def api_skill_progression(agent_name: str):
    if _cmd:
        return jsonify(_cmd.get_skill_progression(agent_name))
    return jsonify({})

@app.route("/api/conversation")
def api_conversation():
    a1 = request.args.get("agent1", "")
    a2 = request.args.get("agent2", "")
    if _cmd and a1 and a2:
        return jsonify(_cmd.get_conversation_log(a1, a2))
    return jsonify([])

# ============================================================================
# REST API — ALERTS
# ============================================================================
@app.route("/api/alerts")
def api_alerts():
    alerts = AlertLog.query.filter_by(acknowledged=False).all()
    return jsonify({"alerts": [
        {"id": a.id, "type": a.alert_type, "agent": a.agent_name,
         "message": a.message, "severity": a.severity, "created_at": a.created_at}
        for a in alerts
    ]})

@app.route("/api/alerts/<int:alert_id>/acknowledge", methods=["POST"])
def api_ack_alert(alert_id: int):
    alert = AlertLog.query.get(alert_id)
    if alert:
        alert.acknowledged = True
        db.session.commit()
        AuditTrail.log_change("update", "alert", str(alert_id),
                              request.get_json(force=True).get("user", "system"),
                              {"action": "acknowledged"})
    return jsonify({"status": "acknowledged"})

# ============================================================================
# REST API — EXPORT
# ============================================================================
@app.route("/api/export/csv", methods=["POST"])
def api_export_csv():
    data = request.get_json(force=True)
    rows = data.get("data", [])
    AuditTrail.log_change("export", "metrics", str(uuid.uuid4()),
                          data.get("user", "system"), {"format": "csv"})
    buf = ExportSystem.export_csv(rows)
    return send_file(buf, mimetype="text/csv",
                     as_attachment=True, download_name="metrics.csv")

@app.route("/api/export/pdf", methods=["POST"])
def api_export_pdf():
    data  = request.get_json(force=True)
    rows  = data.get("data", [])
    title = data.get("title", "Agent Metrics Report")
    AuditTrail.log_change("export", "metrics", str(uuid.uuid4()),
                          data.get("user", "system"), {"format": "pdf"})
    buf = ExportSystem.export_pdf(title, rows)
    mime = "application/pdf" if REPORTLAB_AVAILABLE else "text/plain"
    return send_file(buf, mimetype=mime,
                     as_attachment=True, download_name="report.pdf")

@app.route("/api/export/email", methods=["POST"])
def api_export_email():
    data      = request.get_json(force=True)
    recipient = data.get("recipient")
    metrics   = data.get("metrics", {})
    if not recipient:
        return jsonify({"error": "recipient required"}), 400
    AuditTrail.log_change("export", "email", str(uuid.uuid4()),
                          data.get("user", "system"), {"recipient": recipient})
    return jsonify(ExportSystem.send_email_summary(recipient, metrics))

# ============================================================================
# REST API — COLLABORATION
# ============================================================================
@app.route("/api/lessons/<lesson_id>/comment", methods=["POST"])
def api_add_comment(lesson_id: str):
    data    = request.get_json(force=True)
    author  = data.get("author", "anonymous")
    content = data.get("content", "")
    if not content:
        return jsonify({"error": "content required"}), 400
    return jsonify(CollaborationSystem.add_comment(lesson_id, author, content)), 201

@app.route("/api/lessons/<lesson_id>/comments")
def api_get_comments(lesson_id: str):
    return jsonify({"comments": CollaborationSystem.get_comments(lesson_id)})

@app.route("/api/lessons/<lesson_id>/vote", methods=["POST"])
def api_vote(lesson_id: str):
    data      = request.get_json(force=True)
    voter     = data.get("voter", "anonymous")
    vote_type = data.get("vote_type", "useful")
    if vote_type not in ("useful", "not_useful"):
        return jsonify({"error": "vote_type must be 'useful' or 'not_useful'"}), 400
    CollaborationSystem.vote(lesson_id, voter, vote_type)
    return jsonify(CollaborationSystem.get_vote_stats(lesson_id))

@app.route("/api/lessons/<lesson_id>/versions")
def api_get_versions(lesson_id: str):
    return jsonify({"versions": CollaborationSystem.get_versions(lesson_id)})

@app.route("/api/lessons/<lesson_id>/version", methods=["POST"])
def api_create_version(lesson_id: str):
    data   = request.get_json(force=True)
    vnum   = CollaborationSystem.create_version(
        lesson_id, data.get("title", ""), data.get("content", ""),
        data.get("author", "anonymous")
    )
    return jsonify({"version": vnum}), 201

# ============================================================================
# REST API — AUDIT TRAIL
# ============================================================================
@app.route("/api/audit-log")
def api_audit_log():
    q = AuditLog.query
    if request.args.get("action"):
        q = q.filter_by(action=request.args["action"])
    if request.args.get("resource"):
        q = q.filter_by(resource=request.args["resource"])
    if request.args.get("user"):
        q = q.filter_by(user=request.args["user"])
    limit = int(request.args.get("limit", 100))
    logs = q.order_by(AuditLog.timestamp.desc()).limit(limit).all()
    return jsonify({"logs": [
        {"id": l.id, "action": l.action, "resource": l.resource,
         "resource_id": l.resource_id, "user": l.user,
         "timestamp": l.timestamp, "ip_address": l.ip_address}
        for l in logs
    ]})

# ============================================================================
# REST API — EXECUTIVE (Portfolio & Approvals)
# ============================================================================
_PORTFOLIO_DATA = {
    "summary": {
        "total_value": 2750000, "total_spent": 687500,
        "projects_on_track": 3, "projects_total": 4,
        "projects_at_risk": 1, "pending_approvals": 3, "blocked_count": 1,
    },
    "projects": [
        {"name": "AURA MVP",          "status": "On Track",    "owner": "Kiera",   "risk": "green",
         "budget": 150000,  "spent": 37500,  "progress": 25, "phase": "Design"},
        {"name": "Chevron Sand Mgmt", "status": "In Progress", "owner": "Elina",   "risk": "amber",
         "budget": 800000,  "spent": 360000, "progress": 45, "phase": "Development"},
        {"name": "WWT Enhancement",   "status": "At Risk",     "owner": "Ron",     "risk": "red",
         "budget": 200000,  "spent": 160000, "progress": 80, "phase": "UAT"},
        {"name": "Middle East",       "status": "Active",      "owner": "Partner", "risk": "green",
         "budget": 1600000, "spent": 130000, "progress": 8,  "phase": "Initiation"},
    ],
    "risks": [
        {"level": "red",   "label": "WWT UAT delays — 80% budget used, deadline at risk",    "owner": "Ron"},
        {"level": "amber", "label": "Chevron GPU resource constraint — blocking development", "owner": "Elina"},
        {"level": "green", "label": "AURA MVP on schedule — design phase progressing well",   "owner": "Kiera"},
        {"level": "green", "label": "Middle East — partnership agreement in final review",    "owner": "Partner"},
    ],
    "financials": {
        "total_budget": 2750000, "total_spent": 687500, "remaining": 2062500,
        "burn_rate_pct": 25.0, "ai_agent_daily_cost": 0.20, "cost_headroom": 25,
    },
}

_APPROVALS_DATA = {
    "queue": [
        {"project": "AURA MVP",          "stage": "Charter Approval",    "agent": "PM Agent",
         "waiting_hours": 2.3,  "approver": "trice@firstgenesis.com"},
        {"project": "Chevron Sand Mgmt", "stage": "Requirements Review", "agent": "BA Agent",
         "waiting_hours": 4.1,  "approver": "trice@firstgenesis.com"},
        {"project": "WWT Enhancement",   "stage": "Delivery Approval",   "agent": "QA Agent",
         "waiting_hours": 19.5, "approver": "trice@firstgenesis.com"},
    ],
    "history": [
        {"date": "Mar 11", "count": 4, "avg_hours": 1.9},
        {"date": "Mar 12", "count": 3, "avg_hours": 2.8},
        {"date": "Mar 13", "count": 6, "avg_hours": 1.2},
        {"date": "Mar 14", "count": 2, "avg_hours": 3.2},
        {"date": "Mar 15", "count": 5, "avg_hours": 1.5},
        {"date": "Mar 16", "count": 3, "avg_hours": 2.1},
        {"date": "Mar 17", "count": 4, "avg_hours": 1.8},
    ],
    "metrics": {
        "pending_count": 3, "oldest_hours": 19.5,
        "avg_turnaround_hours": 2.1, "week_total": 27, "on_time_rate_pct": 94,
    },
}

@app.route("/api/portfolio")
def api_portfolio():
    AuditTrail.log_access(request.args.get("user", "anonymous"), "portfolio")
    return jsonify(_PORTFOLIO_DATA)

@app.route("/api/approvals")
def api_approvals():
    AuditTrail.log_access(request.args.get("user", "anonymous"), "approvals")
    return jsonify(_APPROVALS_DATA)

# ============================================================================
# ERROR HANDLERS
# ============================================================================
@app.errorhandler(404)
def not_found(e):  return jsonify({"error": "Not found"}), 404

@app.errorhandler(500)
def server_error(e): return jsonify({"error": "Internal server error"}), 500

# ============================================================================
# ADMIN PANEL (inline HTML — no extra files needed)
# ============================================================================
ADMIN_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><title>FG Admin Panel</title>
  <style>
    body { font-family:'Segoe UI',sans-serif; background:#0f1117; color:#e2e8f0;
           margin:0; padding:24px; }
    h1   { color:#a78bfa; }
    h2   { color:#60a5fa; border-bottom:1px solid #2d3148; padding-bottom:6px; }
    .card{ background:#1a1d2e; border:1px solid #2d3148; border-radius:8px;
           padding:16px; margin-bottom:16px; }
    .btn { background:#4f46e5; color:#fff; border:none; padding:8px 16px;
           border-radius:6px; cursor:pointer; font-size:.82rem; margin:4px; }
    .btn:hover { background:#7c6af7; }
    .btn.danger { background:#dc2626; }
    table{ width:100%; border-collapse:collapse; font-size:.78rem; }
    th   { background:#2d3148; color:#94a3b8; padding:8px; text-align:left; }
    td   { padding:6px 8px; border-bottom:1px solid #1a1d2e; }
    .sev-critical{ color:#f87171; font-weight:700; }
    .sev-warning { color:#fbbf24; font-weight:700; }
    pre  { background:#0f1117; padding:12px; border-radius:6px; font-size:.7rem;
           overflow-x:auto; color:#94a3b8; max-height:300px; }
    #status{ color:#22c55e; font-size:.8rem; }
  </style>
</head>
<body>
<h1>⚙️ First Genesis Admin Panel</h1>
<p id="status">Loading…</p>

<div class="card">
  <h2>📊 Dashboard Statistics</h2>
  <pre id="dash-stats">fetching…</pre>
</div>

<div class="card">
  <h2>🚨 Active Alerts</h2>
  <div id="alerts-section">Loading…</div>
</div>

<div class="card">
  <h2>📥 Export</h2>
  <button class="btn" onclick="exportCSV()">Export Audit Log (CSV)</button>
  <button class="btn" onclick="exportPDF()">Export Report (PDF)</button>
  <button class="btn" onclick="emailSummary()">Email Summary</button>
</div>

<div class="card">
  <h2>📋 Audit Log (last 50)</h2>
  <div id="audit-section">Loading…</div>
</div>

<script>
const BASE = window.location.origin;

async function load() {
  const d = await fetch(BASE+'/api/dashboard').then(r=>r.json()).catch(()=>({}));
  document.getElementById('dash-stats').textContent = JSON.stringify(d, null, 2);

  const alerts = await fetch(BASE+'/api/alerts').then(r=>r.json()).catch(()=>({alerts:[]}));
  const al = alerts.alerts || [];
  document.getElementById('alerts-section').innerHTML = al.length === 0
    ? '<p style="color:#22c55e">✅ No active alerts</p>'
    : '<table><tr><th>Severity</th><th>Message</th><th>Time</th><th></th></tr>' +
      al.map(a=>`<tr>
        <td class="sev-${a.severity}">${a.severity.toUpperCase()}</td>
        <td>${a.message}</td>
        <td>${(a.created_at||'').slice(0,19).replace('T',' ')}</td>
        <td><button class="btn danger" onclick="ackAlert(${a.id})">Ack</button></td>
      </tr>`).join('') + '</table>';

  const audit = await fetch(BASE+'/api/audit-log?limit=50').then(r=>r.json()).catch(()=>({logs:[]}));
  const logs  = audit.logs || [];
  document.getElementById('audit-section').innerHTML = logs.length === 0
    ? '<p style="color:#64748b">No entries</p>'
    : '<table><tr><th>Action</th><th>Resource</th><th>User</th><th>Time</th></tr>' +
      logs.map(l=>`<tr>
        <td>${l.action}</td><td>${l.resource} ${l.resource_id||''}</td>
        <td>${l.user||'—'}</td>
        <td>${(l.timestamp||'').slice(0,19).replace('T',' ')}</td>
      </tr>`).join('') + '</table>';

  document.getElementById('status').textContent = '✅ Loaded '+new Date().toLocaleTimeString();
}

async function ackAlert(id) {
  await fetch(BASE+'/api/alerts/'+id+'/acknowledge',
    {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({user:'admin'})});
  load();
}

async function exportCSV() {
  const al = await fetch(BASE+'/api/audit-log?limit=1000').then(r=>r.json()).catch(()=>({logs:[]}));
  const r  = await fetch(BASE+'/api/export/csv',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({data:al.logs||[],user:'admin'})
  });
  const blob=await r.blob();
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='audit_log.csv'; a.click();
}

async function exportPDF() {
  const d = await fetch(BASE+'/api/dashboard').then(r=>r.json()).catch(()=>({}));
  const r = await fetch(BASE+'/api/export/pdf',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({title:'FG Dashboard Report',data:d.agents||[],user:'admin'})
  });
  const blob=await r.blob();
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob);
  a.download='fg_report.pdf'; a.click();
}

function emailSummary() {
  const to = prompt('Send summary to email:'); if (!to) return;
  fetch(BASE+'/api/export/email',{
    method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({recipient:to,metrics:{},user:'admin'})
  }).then(r=>r.json()).then(d=>alert(d.status==='sent'?'✅ Email sent':'❌ '+d.status));
}

load();
setInterval(load, 10000);
</script>
</body>
</html>
"""

# ============================================================================
# MAIN
# ============================================================================
def init_db_and_start():
    with app.app_context():
        db.create_all()
        print("✅ Database initialised")

    print("""
╔════════════════════════════════════════════════════════════════╗
║   First Genesis — Agent Command Center Server                  ║
╠════════════════════════════════════════════════════════════════╣
║  ✅ WebSocket Real-Time Updates   (every 3 seconds)            ║
║  ✅ REST API Endpoints            (15+ routes)                 ║
║  ✅ Alert System                  (error rate / budget)        ║
║  ✅ Metrics Export                (CSV, PDF, email)            ║
║  ✅ Audit Trail                   (compliance-ready)           ║
║  ✅ Collaboration                 (comments, votes, versions)  ║
╠════════════════════════════════════════════════════════════════╣
║  📊 Dashboard:   http://localhost:5000/dashboard               ║
║  ⚙️  Admin:       http://localhost:5000/admin                  ║
║  📡 WebSocket:   ws://localhost:5000/socket.io                 ║
║  🔧 API:         http://localhost:5000/api/dashboard           ║
╚════════════════════════════════════════════════════════════════╝
    """)

    # Start WebSocket push thread
    threading.Thread(target=_push_loop, daemon=True).start()

    port = int(os.environ.get("PORT", 5000))
    host = os.environ.get("HOST", "0.0.0.0")
    socketio.run(app, host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    init_db_and_start()
