#!/bin/bash
# ============================================================================
# entrypoint_dashboard.sh — Dashboard Server container entrypoint
# ============================================================================
set -e

export FG_DB_PATH="${FG_DB_PATH:-/data/fg_workflows.db}"
export FG_KNOWLEDGE_DB_PATH="${FG_KNOWLEDGE_DB_PATH:-/data/fg_knowledge.db}"

echo ""
echo "=================================================="
echo " FG Sales Agent Dashboard"
echo " URL:  http://0.0.0.0:${PORT:-5000}/dashboard"
echo " API:  http://0.0.0.0:${PORT:-5000}/api/dashboard"
echo " ENV:  ${FG_ENV:-production}"
echo " DB:   ${DATABASE_URL}"
echo "=================================================="
echo ""

exec python dashboard_server.py
