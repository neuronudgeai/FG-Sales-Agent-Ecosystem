"""
fg_knowledge_injector.py
────────────────────────
Reads the knowledge/ folder and the fg_knowledge.db lessons table to build a
context block that is prepended to every agent's system prompt at call time.

This is the bridge between the static knowledge files and live agent calls:

  knowledge/shared/          ← always included
  knowledge/<agent_name>/    ← agent-specific files
  fg_knowledge.db            ← top recent lessons_learned rows for this agent

Usage (internal — called by AutonomousAgentWithEmailGates._governed_call):

    injector = KnowledgeInjector()
    context  = injector.get_context("pm_agent")
    full_sys = f"{context}\\n\\n---\\n\\n{original_system_prompt}"
"""

from __future__ import annotations

import os
import sqlite3
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Default paths — override via constructor args or env vars
_DEFAULT_KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"
_DEFAULT_DB_PATH = os.environ.get(
    "KNOWLEDGE_DB", "/home/claude/fg_knowledge.db"
)

# Agents that have a dedicated sub-folder in knowledge/
_KNOWN_AGENTS = {"pm_agent", "ba_agent", "qa_agent", "vendor_agent"}

# How many DB lessons to inject per call (keeps context window lean)
_MAX_DB_LESSONS = 3


class KnowledgeInjector:
    """
    Builds a context block for a given agent by combining:
      1. Shared knowledge files (frozen facts, company context, format rules)
      2. Agent-specific knowledge files (best practices, templates, patterns)
      3. Top recent lessons_learned rows from the SQLite knowledge DB

    The result is a compact markdown string ready to prepend to a system prompt.
    """

    def __init__(
        self,
        knowledge_dir: Optional[Path] = None,
        db_path: Optional[str] = None,
    ):
        self.knowledge_dir = Path(knowledge_dir or _DEFAULT_KNOWLEDGE_DIR)
        self.db_path = db_path or _DEFAULT_DB_PATH

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_context(self, agent_name: str, max_db_lessons: int = _MAX_DB_LESSONS) -> str:
        """
        Return a markdown context block for the given agent.

        Sections:
          ## SHARED KNOWLEDGE        — frozen facts, company context, format rules
          ## AGENT KNOWLEDGE         — agent-specific files
          ## LESSONS LEARNED (LIVE)  — top N rows from the DB lessons table
        """
        sections: list[str] = []

        shared = self._load_folder("shared")
        if shared:
            sections.append("## SHARED KNOWLEDGE\n\n" + shared)

        agent_kb = self._load_folder(agent_name) if agent_name in _KNOWN_AGENTS else ""
        if agent_kb:
            sections.append(f"## {agent_name.upper()} KNOWLEDGE\n\n" + agent_kb)

        db_lessons = self._load_db_lessons(agent_name, max_db_lessons)
        if db_lessons:
            sections.append("## LESSONS LEARNED (LIVE)\n\n" + db_lessons)

        if not sections:
            return ""

        header = (
            "<!-- KNOWLEDGE CONTEXT — read this before answering -->\n"
            f"<!-- Agent: {agent_name} -->\n\n"
        )
        return header + "\n\n---\n\n".join(sections)

    def add_lesson(
        self,
        agent_name: str,
        title: str,
        content: str,
        category: str = "general",
        workflow_id: str = "manual",
    ) -> None:
        """
        Write a new lesson directly to the DB lessons table.
        This is a lightweight write path — no need to import KnowledgeLibrary.
        """
        import uuid
        from datetime import datetime

        try:
            conn = sqlite3.connect(self.db_path)
            conn.execute(
                """
                INSERT OR IGNORE INTO lessons_learned
                (lesson_id, workflow_id, lesson_title, lesson_content,
                 category, applicable_agents, created_at, usage_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    str(uuid.uuid4()), workflow_id, title, content,
                    category, f'["{agent_name}"]',
                    datetime.utcnow().isoformat(),
                ),
            )
            conn.commit()
            conn.close()
            logger.info(f"KnowledgeInjector: lesson saved for {agent_name} — {title}")
        except Exception as exc:
            logger.warning(f"KnowledgeInjector: could not save lesson — {exc}")

    # ── Private helpers ────────────────────────────────────────────────────────

    def _load_folder(self, subfolder: str) -> str:
        """
        Read all .md and .json files in knowledge/<subfolder>/ and return
        their contents concatenated with file-name headers.
        """
        folder = self.knowledge_dir / subfolder
        if not folder.is_dir():
            return ""

        parts: list[str] = []
        for path in sorted(folder.iterdir()):
            if path.suffix not in (".md", ".json", ".txt"):
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
                if text:
                    parts.append(f"### {path.stem.replace('_', ' ').title()}\n\n{text}")
            except OSError as exc:
                logger.warning(f"KnowledgeInjector: could not read {path} — {exc}")

        return "\n\n".join(parts)

    def _load_db_lessons(self, agent_name: str, limit: int) -> str:
        """
        Pull the most recently used / highest usage_count lessons for this
        agent from fg_knowledge.db. Returns empty string if DB unavailable.
        """
        if not Path(self.db_path).exists():
            return ""

        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute(
                """
                SELECT lesson_title, lesson_content, category, usage_count
                FROM   lessons_learned
                WHERE  applicable_agents LIKE ?
                ORDER  BY usage_count DESC, created_at DESC
                LIMIT  ?
                """,
                (f'%"{agent_name}"%', limit),
            )
            rows = cursor.fetchall()
            conn.close()
        except Exception as exc:
            logger.warning(f"KnowledgeInjector: DB read failed — {exc}")
            return ""

        if not rows:
            return ""

        lines: list[str] = []
        for title, content, category, usage in rows:
            lines.append(
                f"**[{category.upper()}] {title}** (used {usage}×)\n{content}"
            )
        return "\n\n".join(lines)


# ── CLI: inspect what an agent will see ───────────────────────────────────────

if __name__ == "__main__":
    import sys

    agent = sys.argv[1] if len(sys.argv) > 1 else "pm_agent"
    injector = KnowledgeInjector()
    ctx = injector.get_context(agent)

    if ctx:
        print(f"\n{'═'*60}")
        print(f"  Knowledge context for: {agent}")
        print(f"  Characters: {len(ctx):,}  |  Approx tokens: {len(ctx)//4:,}")
        print(f"{'═'*60}\n")
        print(ctx)
    else:
        print(f"No knowledge context found for '{agent}'.")
        print(f"Expected folder: {_DEFAULT_KNOWLEDGE_DIR / agent}")
