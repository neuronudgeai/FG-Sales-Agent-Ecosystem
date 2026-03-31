#!/bin/bash
# ============================================================================
# entrypoint_agents.sh — Agent Engine container entrypoint
# Validates required env vars then runs the requested CLI command
# ============================================================================
set -e

# Patch DB paths so the ecosystem uses /data (the shared volume)
# These sed replacements override the hardcoded /home/claude paths at runtime
export FG_DB_PATH="${FG_DB_PATH:-/data/fg_workflows.db}"
export FG_KNOWLEDGE_DB_PATH="${FG_KNOWLEDGE_DB_PATH:-/data/fg_knowledge.db}"
export FG_LOG_PATH="${FG_LOG_PATH:-/data/fg_agents.log}"

# ── API key check ─────────────────────────────────────────────────────────────
if [ -z "$ANTHROPIC_API_KEY" ]; then
    echo ""
    echo "ERROR: ANTHROPIC_API_KEY is not set."
    echo "  1. Copy .env.example to .env"
    echo "  2. Add your key: ANTHROPIC_API_KEY=sk-ant-..."
    echo "  3. Re-run: docker compose run --rm agents <command>"
    echo ""
    exit 1
fi

# ── Environment banner ────────────────────────────────────────────────────────
echo ""
echo "=================================================="
echo " FG Sales Agent Engine"
echo " ENV:   ${FG_ENV:-production}"
echo " DATA:  ${FG_DB_PATH}"
echo " CMD:   $*"
echo "=================================================="
echo ""

exec python claude_code_agent_ecosystem.py "$@"
