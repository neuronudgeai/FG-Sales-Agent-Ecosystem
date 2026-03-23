"""
fg_integrations/hubspot_sync.py
─────────────────────────────────
HubSpot CRM sync for First Genesis project milestones.

This is NOT an approval channel — it syncs deliverable status and project
milestones as HubSpot Deals and Activity Notes.

Trigger points:
  1. Any stage gate is approved → log activity on the deal
  2. WorkflowStatus.COMPLETED → update deal stage to "closed_won" or advance pipeline

Required env vars:
  HUBSPOT_ACCESS_TOKEN   Private App token (pat-...)
  HUBSPOT_PIPELINE_ID    The HubSpot pipeline ID for FG projects
  HUBSPOT_PORTAL_ID      HubSpot portal/account ID
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_HUBSPOT_BASE = "https://api.hubapi.com"


class HubSpotSync:
    """Sync First Genesis project milestones to HubSpot CRM."""

    def __init__(self):
        self.token       = os.environ.get("HUBSPOT_ACCESS_TOKEN", "")
        self.pipeline_id = os.environ.get("HUBSPOT_PIPELINE_ID", "")
        self.portal_id   = os.environ.get("HUBSPOT_PORTAL_ID", "")
        self.enabled     = bool(self.token)
        self._headers    = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type":  "application/json",
        }
        # In-memory deal cache: project_name → deal_id
        self._deal_cache: dict = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def sync_project_milestone(
        self,
        workflow_id: str,
        project_name: str,
        agent_name: str,
        stage_gate: str,
        decision: str,
        content_summary: str = "",
    ) -> bool:
        """
        Called after any stage gate decision. Upserts a Deal for the project
        and logs an Activity Note with the milestone details.

        Returns True if sync succeeded.
        """
        if not self.enabled:
            return False
        try:
            deal_id = self.get_or_create_deal(project_name)
            if not deal_id:
                return False
            self.log_activity(
                deal_id=deal_id,
                title=f"[{agent_name}] {stage_gate} — {decision.upper()}",
                body=(
                    f"Workflow: {workflow_id}\n"
                    f"Gate: {stage_gate}\n"
                    f"Decision: {decision}\n"
                    f"Timestamp: {datetime.utcnow().isoformat()}Z\n\n"
                    + (f"Summary:\n{content_summary[:500]}" if content_summary else "")
                ),
            )
            logger.info(f"HubSpotSync: milestone logged for '{project_name}' ({stage_gate})")
            return True
        except Exception as exc:
            logger.warning(f"HubSpotSync: sync_project_milestone failed — {exc}")
            return False

    def on_workflow_completed(self, workflow_id: str, project_name: str, agent_name: str) -> bool:
        """
        Called when a workflow reaches COMPLETED status.
        Advances the deal's stage to 'Delivered' in the pipeline.
        """
        if not self.enabled:
            return False
        try:
            deal_id = self.get_or_create_deal(project_name)
            if not deal_id:
                return False
            self._update_deal_stage(deal_id, "CLOSED_WON")
            self.log_activity(
                deal_id=deal_id,
                title=f"[{agent_name}] Workflow COMPLETED",
                body=f"Workflow {workflow_id} completed successfully. All gates passed.",
            )
            logger.info(f"HubSpotSync: deal advanced to COMPLETED for '{project_name}'")
            return True
        except Exception as exc:
            logger.warning(f"HubSpotSync: on_workflow_completed failed — {exc}")
            return False

    # ── Deal management ───────────────────────────────────────────────────────

    def get_or_create_deal(self, project_name: str) -> Optional[str]:
        """Return the HubSpot deal_id for project_name, creating it if needed."""
        if project_name in self._deal_cache:
            return self._deal_cache[project_name]

        # Search for existing deal
        deal_id = self._search_deal(project_name)
        if not deal_id:
            deal_id = self._create_deal(project_name)
        if deal_id:
            self._deal_cache[project_name] = deal_id
        return deal_id

    def _search_deal(self, project_name: str) -> Optional[str]:
        url = f"{_HUBSPOT_BASE}/crm/v3/objects/deals/search"
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {"propertyName": "dealname", "operator": "EQ", "value": project_name}
                    ]
                }
            ],
            "properties": ["dealname", "dealstage"],
            "limit": 1,
        }
        try:
            resp = requests.post(url, json=payload, headers=self._headers, timeout=10)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            return results[0]["id"] if results else None
        except Exception as exc:
            logger.debug(f"HubSpotSync: deal search failed — {exc}")
            return None

    def _create_deal(self, project_name: str) -> Optional[str]:
        url = f"{_HUBSPOT_BASE}/crm/v3/objects/deals"
        payload = {
            "properties": {
                "dealname":   project_name,
                "dealstage":  "appointmentscheduled",  # first stage
                "pipeline":   self.pipeline_id,
                "closedate":  "",
                "description": f"First Genesis agent-managed project: {project_name}",
            }
        }
        try:
            resp = requests.post(url, json=payload, headers=self._headers, timeout=10)
            resp.raise_for_status()
            deal_id = resp.json().get("id")
            logger.info(f"HubSpotSync: created deal {deal_id} for '{project_name}'")
            return deal_id
        except Exception as exc:
            logger.warning(f"HubSpotSync: deal creation failed — {exc}")
            return None

    def _update_deal_stage(self, deal_id: str, stage: str) -> None:
        url = f"{_HUBSPOT_BASE}/crm/v3/objects/deals/{deal_id}"
        try:
            requests.patch(
                url,
                json={"properties": {"dealstage": stage.lower()}},
                headers=self._headers,
                timeout=10,
            ).raise_for_status()
        except Exception as exc:
            logger.debug(f"HubSpotSync: deal stage update failed — {exc}")

    # ── Activity / Note ───────────────────────────────────────────────────────

    def log_activity(self, deal_id: str, title: str, body: str) -> None:
        """Create an engagement Note on a HubSpot deal."""
        url = f"{_HUBSPOT_BASE}/crm/v3/objects/notes"
        payload = {
            "properties": {
                "hs_note_body":      f"**{title}**\n\n{body}",
                "hs_timestamp":      str(int(datetime.utcnow().timestamp() * 1000)),
            },
            "associations": [
                {
                    "to":   {"id": deal_id},
                    "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}],
                }
            ],
        }
        try:
            requests.post(url, json=payload, headers=self._headers, timeout=10).raise_for_status()
            logger.debug(f"HubSpotSync: note logged on deal {deal_id}")
        except Exception as exc:
            logger.debug(f"HubSpotSync: note creation failed — {exc}")
