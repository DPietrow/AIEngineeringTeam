"""Versioned prompts. The config hash is stored on every run so scorecards can be
compared across prompt or model changes."""

import hashlib
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str) -> str:
    return (PROMPT_DIR / f"{name}.md").read_text(encoding="utf-8")


def config_hash(model: str) -> str:
    h = hashlib.sha256(model.encode())
    for path in sorted(PROMPT_DIR.glob("*.md")):
        h.update(path.name.encode())
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]
