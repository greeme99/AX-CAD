"""Built-in help: keyword search over kb.json (offline, no index; ~30 entries)."""

import functools
import json
from pathlib import Path
from typing import Any

KB_PATH = Path(__file__).with_name("kb.json")


@functools.cache
def entries() -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads(KB_PATH.read_text(encoding="utf-8"))
    return data


def search(text: str, page: str, limit: int = 3) -> list[dict[str, Any]]:
    """Entries whose keywords occur in the text, best first; entries for this page win ties."""
    t = text.lower()
    scored = []
    for e in entries():
        hits = float(sum(1 for k in e["keywords"] if k.lower() in t))
        if not hits:
            continue
        here = page in e["pages"]
        if not here and "*" not in e["pages"]:
            hits -= 0.5  # another page's help still answers, ranked lower
        scored.append((-hits, not here, e["id"], e))
    scored.sort(key=lambda s: s[:3])
    return [{"id": e["id"], "q": e["q"], "a": e["a"]} for *_, e in scored[:limit]]


def for_page(page: str, limit: int = 4) -> list[dict[str, Any]]:
    """Suggested topics for a page (shown when the panel opens or nothing matched)."""
    here = [e for e in entries() if page in e["pages"]]
    common = [e for e in entries() if "*" in e["pages"] and e not in here]
    return [{"id": e["id"], "q": e["q"], "a": e["a"]} for e in [*here, *common][:limit]]
