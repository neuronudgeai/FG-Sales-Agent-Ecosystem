"""
chat_with_ecosystem.py — Share the FG ecosystem code with Claude and chat about it.

Usage:
    python chat_with_ecosystem.py

The script uploads your key source files to Claude via the Files API (one-time,
cached for 24 hours), then opens an interactive chat session where Claude can
answer questions about, explain, or help you modify the code.

Press Ctrl+C or type 'exit' / 'quit' to end the session.
"""

import os
import sys
import anthropic

# ── Files to share with Claude ────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SOURCE_FILES = [
    ("claude_code_agent_ecosystem.py", "Agent definitions, skills catalog, stage-gate workflow"),
    ("dashboard_server.py",            "Flask dashboard server, REST API, WebSocket events"),
]

SYSTEM_PROMPT = """\
You are an expert software architect reviewing the First Genesis Multi-Agent Ecosystem.
The attached source files are the complete codebase. You have full context on:
  • 5 autonomous agents (PM, BA, QA, Vendor, Manager)
  • Email approval gates and stage-gate workflows
  • 144-skill AGENT_SKILLS_CATALOG
  • Flask dashboard server with real-time WebSocket updates
  • Cost guardrails and token optimization

Answer questions, explain design decisions, suggest improvements, and help write
new code — always referencing specific file names and line numbers where relevant.
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def upload_files(client: anthropic.Anthropic) -> list[dict]:
    """Upload source files via the Files API and return content blocks."""
    blocks = []
    for filename, description in SOURCE_FILES:
        path = os.path.join(SCRIPT_DIR, filename)
        if not os.path.exists(path):
            print(f"  ⚠  Skipping {filename} (not found)")
            continue
        size_kb = os.path.getsize(path) // 1024
        print(f"  ↑  Uploading {filename} ({size_kb} KB) …", end=" ", flush=True)
        with open(path, "rb") as f:
            uploaded = client.beta.files.upload(
                file=(filename, f, "text/plain"),
            )
        print(f"✓  {uploaded.id}")
        blocks.append({
            "type": "document",
            "source": {"type": "file", "file_id": uploaded.id},
            "title": filename,
            "context": description,
        })
    return blocks


def chat(client: anthropic.Anthropic, file_blocks: list[dict]) -> None:
    """Run an interactive multi-turn chat with the uploaded files in context."""
    history: list[dict] = []

    print("\n" + "─" * 60)
    print("  FG Ecosystem Chat  •  type 'exit' to quit")
    print("─" * 60)
    print("Claude has the full source code loaded. Ask anything!\n")

    while True:
        # ── Get user input ──────────────────────────────────────────────────
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "bye"}:
            print("Goodbye!")
            break

        # First turn: attach the file blocks so Claude has the code
        if not history:
            content = file_blocks + [{"type": "text", "text": user_input}]
        else:
            content = user_input

        history.append({"role": "user", "content": content})

        # ── Stream the response ─────────────────────────────────────────────
        print("\nClaude: ", end="", flush=True)
        full_text = ""

        with client.beta.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=history,
            betas=["files-api-2025-04-14"],
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
                full_text += text

        print("\n")  # blank line after response

        # Append assistant turn (text only — keeps context lean)
        history.append({"role": "assistant", "content": full_text})


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: ANTHROPIC_API_KEY environment variable not set.")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    print("\n🚀  First Genesis Ecosystem Chat")
    print("=" * 60)
    print("Uploading source files to Claude Files API …\n")

    file_blocks = upload_files(client)

    if not file_blocks:
        print("No files uploaded. Check that source files exist in this directory.")
        sys.exit(1)

    print(f"\n✅  {len(file_blocks)} file(s) ready.")
    chat(client, file_blocks)


if __name__ == "__main__":
    main()
