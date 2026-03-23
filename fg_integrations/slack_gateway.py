"""
fg_integrations/slack_gateway.py
─────────────────────────────────
Slack integration for First Genesis stage gate approval notifications.

Outbound: posts Block Kit messages with Approve / Reject buttons to a channel
          and/or DMs the specific approver.
Inbound:  slash command  /fg-approve {workflow_id} approved|rejected [feedback]
          interactive    button click on the Block Kit message
          Both handled by POST /webhooks/slack in dashboard_server.py.

Required env vars:
  SLACK_BOT_TOKEN        xoxb-... (Bot OAuth token)
  SLACK_SIGNING_SECRET   used to verify incoming Slack requests
  SLACK_APPROVAL_CHANNEL #fg-approvals (channel name or ID)
  SLACK_APPROVER_MAP     JSON: {"email": "slack_user_id", ...}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_SLACK_AVAILABLE = False
try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError
    _SLACK_AVAILABLE = True
except ImportError:
    pass


class SlackGateway:
    """Send approval notifications to Slack and parse inbound approval replies."""

    def __init__(self):
        self.token          = os.environ.get("SLACK_BOT_TOKEN", "")
        self.signing_secret = os.environ.get("SLACK_SIGNING_SECRET", "")
        self.channel        = os.environ.get("SLACK_APPROVAL_CHANNEL", "#fg-approvals")
        self.approver_map: dict = json.loads(
            os.environ.get("SLACK_APPROVER_MAP", "{}")
        )
        self.enabled = bool(self.token and _SLACK_AVAILABLE)
        self._client = WebClient(token=self.token) if self.enabled else None
        if not _SLACK_AVAILABLE:
            logger.info("SlackGateway: slack_sdk not installed — Slack disabled")

    # ── Outbound ──────────────────────────────────────────────────────────────

    def send_approval_request(
        self,
        workflow_id: str,
        stage_gate_name: str,
        agent_name: str,
        project_name: str,
        content_summary: str,
        approver_email: str,
    ) -> bool:
        """Post a Block Kit approval card to the channel and DM the approver."""
        if not self.enabled:
            return False

        blocks = self._build_approval_blocks(
            workflow_id, stage_gate_name, agent_name, project_name, content_summary
        )

        success = False
        # 1. Post to approval channel
        try:
            self._client.chat_postMessage(channel=self.channel, blocks=blocks, text=f"[{project_name}] Approval needed")
            success = True
            logger.info(f"SlackGateway: posted to {self.channel} for {workflow_id}")
        except Exception as exc:
            logger.warning(f"SlackGateway: channel post failed — {exc}")

        # 2. DM the specific approver if their Slack ID is known
        slack_uid = self.approver_map.get(approver_email)
        if slack_uid:
            try:
                self._client.chat_postMessage(channel=slack_uid, blocks=blocks, text=f"[{project_name}] Your approval needed")
                success = True
                logger.info(f"SlackGateway: DM sent to {slack_uid} for {workflow_id}")
            except Exception as exc:
                logger.warning(f"SlackGateway: DM failed — {exc}")

        return success

    def _build_approval_blocks(
        self,
        workflow_id: str,
        stage_gate_name: str,
        agent_name: str,
        project_name: str,
        content_summary: str,
    ) -> list:
        summary_truncated = content_summary[:300] + "..." if len(content_summary) > 300 else content_summary
        return [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Approval Required — {project_name}"},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Agent:*\n{agent_name}"},
                    {"type": "mrkdwn", "text": f"*Gate:*\n{stage_gate_name}"},
                    {"type": "mrkdwn", "text": f"*Workflow ID:*\n`{workflow_id}`"},
                    {"type": "mrkdwn", "text": f"*Project:*\n{project_name}"},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Summary:*\n{summary_truncated}"},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "✅ Approve"},
                        "style": "primary",
                        "action_id": "fg_approve",
                        "value": f"{workflow_id}|approved",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "❌ Reject"},
                        "style": "danger",
                        "action_id": "fg_reject",
                        "value": f"{workflow_id}|rejected",
                    },
                ],
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Or use slash command: `/fg-approve {workflow_id} approved [feedback]`"},
                ],
            },
        ]

    # ── Inbound ───────────────────────────────────────────────────────────────

    def verify_signature(self, body_bytes: bytes, timestamp: str, signature: str) -> bool:
        """Verify an incoming Slack request signature."""
        if not self.signing_secret:
            return True  # Skip verification in dev mode
        base = f"v0:{timestamp}:{body_bytes.decode('utf-8')}"
        expected = "v0=" + hmac.new(
            self.signing_secret.encode(), base.encode(), hashlib.sha256
        ).hexdigest()
        # Prevent replay attacks (5-min window)
        if abs(time.time() - int(timestamp)) > 300:
            return False
        return hmac.compare_digest(expected, signature)

    def parse_slash_command(self, form_data: dict) -> Tuple[Optional[str], Optional[str], str]:
        """
        Parse /fg-approve slash command payload.
        Returns (workflow_id, decision, feedback).
        decision is "approved" | "rejected" | None.
        """
        text = form_data.get("text", "").strip()
        parts = text.split(None, 2)
        if len(parts) < 2:
            return None, None, ""
        workflow_id = parts[0]
        decision_raw = parts[1].lower()
        feedback = parts[2] if len(parts) > 2 else ""
        decision = "approved" if "approv" in decision_raw else "rejected" if "reject" in decision_raw else None
        return workflow_id, decision, feedback

    def parse_interactive(self, payload_json: str) -> Tuple[Optional[str], Optional[str], str]:
        """
        Parse an interactive Block Kit button-click payload.
        Returns (workflow_id, decision, feedback).
        """
        try:
            payload = json.loads(payload_json)
            action = payload.get("actions", [{}])[0]
            value = action.get("value", "")
            parts = value.split("|", 1)
            if len(parts) != 2:
                return None, None, ""
            workflow_id, decision = parts
            return workflow_id, decision, ""
        except Exception as exc:
            logger.warning(f"SlackGateway: interactive parse failed — {exc}")
            return None, None, ""

    def get_approver_email_from_slack_id(self, slack_user_id: str) -> Optional[str]:
        """Reverse-lookup email from Slack user ID using approver_map."""
        for email, uid in self.approver_map.items():
            if uid == slack_user_id:
                return email
        return None

    def send_confirmation(self, channel_or_user: str, workflow_id: str, decision: str) -> None:
        """Send a short confirmation message back to the approver."""
        if not self.enabled:
            return
        emoji = "✅" if decision == "approved" else "❌"
        text = f"{emoji} *{decision.title()}* recorded for workflow `{workflow_id}`."
        try:
            self._client.chat_postMessage(channel=channel_or_user, text=text)
        except Exception as exc:
            logger.debug(f"SlackGateway: confirmation send failed — {exc}")
