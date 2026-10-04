"""Versioned prompts. The config hash is stored on every run so scorecards can be
compared across prompt or model changes.

Prompt variants let an old or experimental prompt be re-run for an A/B comparison without
touching git: put the text in prompts/variants/<agent>.<variant>.md and select it with the
environment variable PROMPT_VARIANTS="review=v1" (comma-separated agent=variant pairs).
The config hash covers whichever text is active, so runs stay attributable.
"""

import hashlib
import os
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
VARIANT_DIR = PROMPT_DIR / "variants"


def active_variants() -> dict[str, str]:
    out: dict[str, str] = {}
    for pair in os.environ.get("PROMPT_VARIANTS", "").split(","):
        if pair.strip():
            agent, _, variant = pair.partition("=")
            out[agent.strip()] = variant.strip()
    return out


def prompt_path(name: str) -> Path:
    variant = active_variants().get(name)
    if variant:
        path = VARIANT_DIR / f"{name}.{variant}.md"
        if not path.exists():
            raise ValueError(f"no prompt variant {name!r}={variant!r} (expected {path.name})")
        return path
    return PROMPT_DIR / f"{name}.md"


def load_prompt(name: str) -> str:
    return prompt_path(name).read_text(encoding="utf-8")


def config_hash(model: str) -> str:
    h = hashlib.sha256(model.encode())
    for path in sorted(PROMPT_DIR.glob("*.md")):
        h.update(path.name.encode())
        h.update(prompt_path(path.stem).read_bytes().replace(b"\r\n", b"\n"))
    return h.hexdigest()[:12]
