"""
fg_integrations/whatsapp_gateway.py
─────────────────────────────────────
WhatsApp integration for First Genesis stage gate approval notifications.

Supports two providers, selected via WHATSAPP_PROVIDER env var:
  twilio  — uses Twilio WhatsApp API (python twilio SDK)
  meta    — uses Meta Graph API directly (requests)

Outbound: sends approval notification to approver's WhatsApp number.
Inbound:  approver replies "APPROVED {workflow_id} [feedback]"
          or             "REJECTED {workflow_id} [feedback]"
          Handled by POST /webhooks/whatsapp in dashboard_server.py.

Required env vars (Twilio):
  WHATSAPP_PROVIDER=twilio
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN
  TWILIO_WHATSAPP_FROM   e.g. whatsapp:+14155238886
  WHATSAPP_APPROVER_MAP  JSON: {"email": "+15551234567", ...}

Required env vars (Meta):
  WHATSAPP_PROVIDER=meta
  META_WHATSAPP_TOKEN
  META_PHONE_NUMBER_ID
  META_WEBHOOK_VERIFY_TOKEN  (for GET webhook verification)
  WHATSAPP_APPROVER_MAP      JSON: {"email": "+15551234567", ...}
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

_TWILIO_AVAILABLE = False
try:
    from twilio.rest import Client as TwilioClient
    from twilio.request_validator import RequestValidator as TwilioValidator
    _TWILIO_AVAILABLE = True
except ImportError:
    pass

_META_API_URL = "https://graph.facebook.com/v19.0/{phone_number_id}/messages"


class WhatsAppGateway:
    """
    Provider-agnostic WhatsApp gateway.
    Set WHATSAPP_PROVIDER=twilio or WHATSAPP_PROVIDER=meta.
    """

    def __init__(self):
        self.provider = os.environ.get("WHATSAPP_PROVIDER", "").lower()
        self.approver_map: dict = json.loads(
            os.environ.get("WHATSAPP_APPROVER_MAP", "{}")
        )

        # Twilio config
        self._twilio_sid    = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self._twilio_token  = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self._twilio_from   = os.environ.get("TWILIO_WHATSAPP_FROM", "")
        self._twilio_client = None

        # Meta config
        self._meta_token    = os.environ.get("META_WHATSAPP_TOKEN", "")
        self._meta_phone_id = os.environ.get("META_PHONE_NUMBER_ID", "")
        self._meta_verify   = os.environ.get("META_WEBHOOK_VERIFY_TOKEN", "")

        self.enabled = False
        if self.provider == "twilio" and _TWILIO_AVAILABLE and self._twilio_sid:
            self._twilio_client = TwilioClient(self._twilio_sid, self._twilio_token)
            self.enabled = True
        elif self.provider == "meta" and self._meta_token and self._meta_phone_id:
            self.enabled = True
        elif self.provider:
            logger.warning(
                f"WhatsAppGateway: provider '{self.provider}' set but dependencies/credentials missing"
            )

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
        """Send an approval request WhatsApp message to the approver."""
        if not self.enabled:
            return False

        phone = self.approver_map.get(approver_email)
        if not phone:
            logger.info(f"WhatsAppGateway: no phone for {approver_email} — skipping")
            return False

        summary_truncated = content_summary[:400] + "..." if len(content_summary) > 400 else content_summary
        body = (
            f"*First Genesis — Approval Required*\n\n"
            f"Project: {project_name}\n"
            f"Agent: {agent_name}\n"
            f"Gate: {stage_gate_name}\n"
            f"Workflow: {workflow_id}\n\n"
            f"Summary:\n{summary_truncated}\n\n"
            f"Reply:\n"
            f"APPROVED {workflow_id}\n"
            f"or\n"
            f"REJECTED {workflow_id} [your feedback]"
        )

        if self.provider == "twilio":
            return self._send_twilio(phone, body)
        elif self.provider == "meta":
            return self._send_meta(phone, body)
        return False

    def _send_twilio(self, phone: str, body: str) -> bool:
        to = f"whatsapp:{phone}" if not phone.startswith("whatsapp:") else phone
        try:
            msg = self._twilio_client.messages.create(
                from_=self._twilio_from,
                to=to,
                body=body,
            )
            logger.info(f"WhatsAppGateway (Twilio): sent {msg.sid} to {phone}")
            return True
        except Exception as exc:
            logger.warning(f"WhatsAppGateway (Twilio): send failed — {exc}")
            return False

    def _send_meta(self, phone: str, body: str) -> bool:
        phone_clean = phone.lstrip("+").replace("-", "").replace(" ", "")
        url = _META_API_URL.format(phone_number_id=self._meta_phone_id)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone_clean,
            "type": "text",
            "text": {"body": body},
        }
        headers = {"Authorization": f"Bearer {self._meta_token}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            resp.raise_for_status()
            logger.info(f"WhatsAppGateway (Meta): sent to {phone}")
            return True
        except Exception as exc:
            logger.warning(f"WhatsAppGateway (Meta): send failed — {exc}")
            return False

    def send_confirmation(self, phone: str, workflow_id: str, decision: str) -> None:
        """Send a short confirmation back to the approver."""
        if not self.enabled:
            return
        emoji = "✅" if decision == "approved" else "❌"
        body = f"{emoji} {decision.title()} recorded for {workflow_id}."
        if self.provider == "twilio":
            self._send_twilio(phone, body)
        elif self.provider == "meta":
            self._send_meta(phone, body)

    # ── Inbound ───────────────────────────────────────────────────────────────

    def verify_twilio_signature(self, url: str, params: dict, signature: str) -> bool:
        """Verify a Twilio webhook signature."""
        if not _TWILIO_AVAILABLE or not self._twilio_token:
            return True
        validator = TwilioValidator(self._twilio_token)
        return validator.validate(url, params, signature)

    def verify_meta_webhook(self, mode: str, token: str, challenge: str) -> Optional[str]:
        """Handle Meta webhook GET verification. Returns challenge string if valid."""
        if mode == "subscribe" and token == self._meta_verify:
            return challenge
        return None

    def parse_twilio_inbound(self, form_data: dict) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """
        Parse a Twilio inbound WhatsApp message.
        Returns (phone, workflow_id, decision, feedback).
        """
        phone = form_data.get("From", "").replace("whatsapp:", "")
        body  = form_data.get("Body", "").strip()
        return self._parse_body(phone, body)

    def parse_meta_inbound(self, payload: dict) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """
        Parse a Meta inbound WhatsApp webhook payload.
        Returns (phone, workflow_id, decision, feedback).
        """
        try:
            message = (
                payload["entry"][0]["changes"][0]["value"]["messages"][0]
            )
            phone = message.get("from", "")
            body  = message.get("text", {}).get("body", "").strip()
            return self._parse_body(phone, body)
        except (KeyError, IndexError):
            return None, None, None, ""

    def _parse_body(self, phone: str, body: str) -> Tuple[Optional[str], Optional[str], Optional[str], str]:
        """
        Parse "APPROVED {workflow_id} [feedback]" or "REJECTED {workflow_id} [feedback]".
        Returns (phone, workflow_id, decision, feedback).
        """
        upper = body.upper()
        if upper.startswith("APPROVED"):
            parts = body.split(None, 2)
            workflow_id = parts[1] if len(parts) > 1 else None
            feedback    = parts[2] if len(parts) > 2 else ""
            return phone, workflow_id, "approved", feedback
        elif upper.startswith("REJECTED"):
            parts = body.split(None, 2)
            workflow_id = parts[1] if len(parts) > 1 else None
            feedback    = parts[2] if len(parts) > 2 else ""
            return phone, workflow_id, "rejected", feedback
        return phone, None, None, ""

    def get_approver_email_from_phone(self, phone: str) -> Optional[str]:
        """Reverse-lookup email from phone number using approver_map."""
        phone_clean = re.sub(r"[\s\-()]", "", phone).lstrip("+")
        for email, p in self.approver_map.items():
            if re.sub(r"[\s\-()]", "", str(p)).lstrip("+") == phone_clean:
                return email
        return None
