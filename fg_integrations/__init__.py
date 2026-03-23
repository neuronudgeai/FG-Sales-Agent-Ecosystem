"""
fg_integrations
───────────────
Multi-platform notification and CRM integration layer for the
First Genesis agent ecosystem.

Available gateways (all optional — missing credentials = graceful skip):
  SlackGateway      — send Block Kit approval messages + receive slash commands
  TelegramGateway   — send bot messages + receive /approve /reject commands
  WhatsAppGateway   — send/receive via Twilio or Meta (WHATSAPP_PROVIDER env var)
  HubSpotSync       — CRM milestone sync (not an approval channel)

NotificationRouter wraps all gateways and fans out approval requests based on
per-approver channel preferences (APPROVER_CHANNELS env var).
"""
