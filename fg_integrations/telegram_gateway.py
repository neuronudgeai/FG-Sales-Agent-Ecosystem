"""
fg_integrations/telegram_gateway.py
─────────────────────────────────────
Telegram bot integration for First Genesis stage gate approval notifications.

Outbound: sends formatted messages to approvers' Telegram chat IDs with
          instructions to reply using bot commands.
Inbound:  /approve {workflow_id} [feedback]
          /reject  {workflow_id} [feedback]
          Both handled by POST /webhooks/telegram in dashboard_server.py.

Required env vars:
  TELEGRAM_BOT_TOKEN     Bot token from @BotFather
  TELEGRAM_APPROVER_MAP  JSON: {"email": "chat_id_as_string", ...}
  TELEGRAM_WEBHOOK_SECRET (optional) — X-Telegram-Bot-Api-Secret-Token validation
"""

from __future__ import annotations

import json
import logging
import os
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_TELEGRAM_AVAILABLE = False
try:
    import telegram  # python-telegram-bot
    _TELEGRAM_AVAILABLE = True
except ImportError:
    pass

_TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"


class TelegramGateway:
    """Send approval notifications via Telegram and parse inbound bot commands."""

    def __init__(self):
        self.token          = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.webhook_secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "")
        self.approver_map: dict = json.loads(
            os.environ.get("TELEGRAM_APPROVER_MAP", "{}")
        )
        self.enabled = bool(self.token)

    def _api(self, method: str, **kwargs) -> dict:
        url = _TELEGRAM_API_BASE.format(token=self.token, method=method)
        try:
            resp = requests.post(url, json=kwargs, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning(f"TelegramGateway: API call {method} failed — {exc}")
            return {}

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
        """Send an approval request message to the approver's Telegram chat."""
        if not self.enabled:
            return False

        chat_id = self.approver_map.get(approver_email)
        if not chat_id:
            logger.info(f"TelegramGateway: no chat_id for {approver_email} — skipping")
            return False

        summary_truncated = content_summary[:400] + "..." if len(content_summary) > 400 else content_summary
        text = (
            f"🔔 *Approval Required — {project_name}*\n\n"
            f"*Agent:* `{agent_name}`\n"
            f"*Gate:* `{stage_gate_name}`\n"
            f"*Workflow:* `{workflow_id}`\n\n"
            f"*Summary:*\n{summary_truncated}\n\n"
            f"Reply with:\n"
            f"`/approve {workflow_id} [optional feedback]`\n"
            f"`/reject {workflow_id} [optional feedback]`"
        )

        result = self._api(
            "sendMessage",
            chat_id=chat_id,
            text=text,
            parse_mode="Markdown",
        )
        success = bool(result.get("ok"))
        if success:
            logger.info(f"TelegramGateway: sent to chat_id {chat_id} for {workflow_id}")
        return success

    def send_confirmation(self, chat_id: str, workflow_id: str, decision: str) -> None:
        """Send a short confirmation back to the approver."""
        if not self.enabled:
            return
        emoji = "✅" if decision == "approved" else "❌"
        text = f"{emoji} *{decision.title()}* recorded for `{workflow_id}`."
        self._api("sendMessage", chat_id=chat_id, text=text, parse_mode="Markdown")

    # ── Inbound ───────────────────────────────────────────────────────────────

    def verify_secret(self, header_token: str) -> bool:
        """Verify the X-Telegram-Bot-Api-Secret-Token header."""
        if not self.webhook_secret:
            return True  # Skip verification if not configured
        return header_token == self.webhook_secret

    def parse_update(self, update: dict) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """
        Parse a Telegram update object.
        Returns (chat_id, workflow_id, decision, feedback).
        decision is "approved" | "rejected" | None.
        """
        msg = update.get("message", {})
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "").strip()

        if not text or not chat_id:
            return None, None, None, ""

        # /approve <workflow_id> [feedback]
        if text.startswith("/approve"):
            parts = text.split(None, 2)
            workflow_id = parts[1] if len(parts) > 1 else None
            feedback = parts[2] if len(parts) > 2 else ""
            return chat_id, workflow_id, "approved", feedback

        # /reject <workflow_id> [feedback]
        if text.startswith("/reject"):
            parts = text.split(None, 2)
            workflow_id = parts[1] if len(parts) > 1 else None
            feedback = parts[2] if len(parts) > 2 else ""
            return chat_id, workflow_id, "rejected", feedback

        return chat_id, None, None, ""

    def get_approver_email_from_chat_id(self, chat_id: str) -> Optional[str]:
        """Reverse-lookup email from chat_id using approver_map."""
        for email, cid in self.approver_map.items():
            if str(cid) == str(chat_id):
                return email
        return None
