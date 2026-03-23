"""
fg_integrations/notification_router.py
────────────────────────────────────────
Unified notification router for First Genesis stage gate approvals.

StageGateManager calls this instead of EmailGateway directly.
The router fans out approval requests to every channel the approver
prefers (configured in APPROVER_CHANNELS env var) and returns True if
at least one channel succeeded.

Per-approver channel preferences (APPROVER_CHANNELS env var, JSON):
{
  "tjohnson@firstgenesis.com":  ["slack", "email"],
  "k.phipps@firstgenesis.com":  ["telegram", "email"],
  "emaiteu@firstgenesis.com":   ["whatsapp", "email"],
  "pwatty@firstgenesis.com":    ["slack", "email"]
}

If an approver has no entry, all configured channels are tried.
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

logger = logging.getLogger(__name__)


class NotificationRouter:
    """
    Fan-out notification router.

    Drop-in replacement for EmailGateway — exposes the same
    send_approval_request() method signature so StageGateManager
    needs no logic changes.
    """

    def __init__(
        self,
        email_gw=None,
        slack_gw=None,
        telegram_gw=None,
        whatsapp_gw=None,
        hubspot_sync=None,
    ):
        self._gateways = {}
        if email_gw and getattr(email_gw, "enabled", True):
            self._gateways["email"] = email_gw
        if slack_gw and getattr(slack_gw, "enabled", False):
            self._gateways["slack"] = slack_gw
        if telegram_gw and getattr(telegram_gw, "enabled", False):
            self._gateways["telegram"] = telegram_gw
        if whatsapp_gw and getattr(whatsapp_gw, "enabled", False):
            self._gateways["whatsapp"] = whatsapp_gw

        self.hubspot = hubspot_sync  # CRM sync — not a notification channel

        self._approver_prefs: dict = json.loads(
            os.environ.get("APPROVER_CHANNELS", "{}")
        )

        active = list(self._gateways.keys())
        logger.info(f"NotificationRouter: active channels = {active}")

    # ── Public API (same signature as EmailGateway) ───────────────────────────

    def send_approval_request(
        self,
        workflow_id: str,
        stage_gate,            # StageGate dataclass
        agent_name: str,
        project_name: str,
        content_summary: str,
        content_detail: str,
    ) -> bool:
        """
        Send approval notification via all channels preferred by the approver.
        Falls back to all configured channels if no preference is set.
        Returns True if at least one channel succeeded.
        """
        approver_email = getattr(stage_gate, "approver_email", "")
        stage_name     = getattr(stage_gate, "name", stage_gate)
        stage_name_str = stage_name.value if hasattr(stage_name, "value") else str(stage_name)

        preferred = self._approver_prefs.get(approver_email)
        channels_to_use = (
            [ch for ch in preferred if ch in self._gateways]
            if preferred
            else list(self._gateways.keys())
        )

        if not channels_to_use:
            logger.warning(f"NotificationRouter: no channels available for {approver_email}")
            return False

        logger.info(
            f"NotificationRouter: routing {workflow_id} via {channels_to_use} to {approver_email}"
        )

        results = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {}
            for ch in channels_to_use:
                gw = self._gateways[ch]
                if ch == "email":
                    fut = pool.submit(
                        gw.send_approval_request,
                        workflow_id, stage_gate, agent_name, project_name,
                        content_summary, content_detail,
                    )
                else:
                    # Slack / Telegram / WhatsApp share a common signature
                    fut = pool.submit(
                        gw.send_approval_request,
                        workflow_id, stage_name_str, agent_name, project_name,
                        content_summary, approver_email,
                    )
                futures[fut] = ch

            for fut in as_completed(futures):
                ch = futures[fut]
                try:
                    results[ch] = fut.result()
                except Exception as exc:
                    logger.warning(f"NotificationRouter: {ch} raised {exc}")
                    results[ch] = False

        succeeded = [ch for ch, ok in results.items() if ok]
        failed    = [ch for ch, ok in results.items() if not ok]
        if succeeded:
            logger.info(f"NotificationRouter: sent via {succeeded}")
        if failed:
            logger.warning(f"NotificationRouter: failed on {failed}")

        return bool(succeeded)

    # ── HubSpot helpers (called from AutonomousAgentWithEmailGates) ───────────

    def notify_hubspot_on_gate_approval(
        self,
        workflow_id: str,
        project_name: str,
        agent_name: str,
        stage_gate: str,
        decision: str,
        content_summary: str = "",
    ) -> None:
        """Sync a stage gate approval to HubSpot as a deal activity."""
        if self.hubspot:
            self.hubspot.sync_project_milestone(
                workflow_id, project_name, agent_name, stage_gate, decision, content_summary
            )

    def notify_hubspot_on_completion(
        self,
        workflow_id: str,
        project_name: str,
        agent_name: str,
        status: str = "completed",
    ) -> None:
        """Sync workflow completion to HubSpot — advances deal stage."""
        if self.hubspot:
            self.hubspot.on_workflow_completed(workflow_id, project_name, agent_name)

    # ── Introspection ─────────────────────────────────────────────────────────

    def active_channels(self) -> list:
        return list(self._gateways.keys())

    def get_gateway(self, channel: str):
        return self._gateways.get(channel)
