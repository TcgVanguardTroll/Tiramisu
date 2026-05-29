"""type_config.py — Type-aware chunk params, decay config, metadata extraction."""

import re

# ---------------------------------------------------------------------------
# Chunk parameters per document type
# ---------------------------------------------------------------------------
CHUNK_PARAMS = {
    "1:1":          {"size": 400,  "overlap": 60,  "split": "section"},
    "ops-review":   {"size": 600,  "overlap": 100, "split": "section"},
    "planning":     {"size": 600,  "overlap": 100, "split": "section"},
    "standup":      {"size": None, "overlap": 0,   "split": "none", "max_size": 1500},
    "project-sync": {"size": 1000, "overlap": 150, "split": "section"},
    "design-doc":   {"size": 1200, "overlap": 200, "split": "section"},
}
DEFAULT_CHUNK = {"size": 512, "overlap": 80, "split": "section"}

# Startup validation: clamp overlap < size
for _t, _p in CHUNK_PARAMS.items():
    if _p["size"] is not None and _p["overlap"] >= _p["size"]:
        _p["overlap"] = _p["size"] - 1

# ---------------------------------------------------------------------------
# Decay config (used by reranker)
# ---------------------------------------------------------------------------
DECAY_CONFIG = {
    "standup":      (5,   "exponential"),
    "1:1":          (75,  "gaussian"),
    "ops-review":   (45,  "linear"),
    "planning":     (120, "gaussian"),
    "project-sync": (60,  "linear"),
    "design-doc":   (540, "gaussian"),
}
DEFAULT_DECAY = (90, "linear")

# ---------------------------------------------------------------------------
# Type hints for query parsing
# ---------------------------------------------------------------------------
TYPE_HINTS = {
    "standup": ["standup", "stand-up", "daily"],
    "1:1": ["1:1", "one on one", "1-on-1", "one-on-one"],
    "ops-review": ["ops review", "ops-review", "operational review"],
    "planning": ["planning", "roadmap meeting", "strategy"],
    "project-sync": ["project sync", "sync", "project update"],
    "design-doc": ["design doc", "design document", "architecture doc"],
}

TOPIC_MODEL = {
    "1:1": "multi", "ops-review": "multi", "planning": "multi",
    "standup": "single", "project-sync": "single", "design-doc": "single",
}

# ---------------------------------------------------------------------------
# Metadata extraction regexes
# ---------------------------------------------------------------------------
ACTION_RE = re.compile(r"^[\s]*[-*]\s*\[[ x]\]\s*(.+)", re.MULTILINE)
DECISION_RE = re.compile(
    r"(?:decided\s+to|agreed\s+to|decision\s*:)\s*([^.;\n]+)", re.IGNORECASE
)
WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
BLOCKER_RE = re.compile(r"(?:blocker|blocked\s+by|blocking)[:.]?\s*([^.;\n]+)", re.IGNORECASE)


def extract_type_metadata(text: str, doc_type: str, fm: dict = None) -> dict:
    """Extract type-specific metadata from chunk text."""
    fm = fm or {}
    meta = {}

    actions = ACTION_RE.findall(text)
    if actions:
        meta["action_items"] = "; ".join(a.strip() for a in actions[:5])
    decisions = DECISION_RE.findall(text)
    if decisions:
        meta["decisions"] = "; ".join(d.strip() for d in decisions[:5])
    refs = WIKILINK_RE.findall(text)
    if refs:
        meta["references"] = ",".join(refs[:10])
    meta["topic_model"] = TOPIC_MODEL.get(doc_type, "single")

    if doc_type == "standup":
        blockers = BLOCKER_RE.findall(text)
        if blockers:
            meta["blockers"] = "; ".join(b.strip() for b in blockers[:5])
    elif doc_type == "design-doc":
        if fm.get("status"):
            meta["doc_status"] = fm["status"]
    elif doc_type == "ops-review":
        if fm.get("review_period"):
            meta["review_period"] = fm["review_period"]

    return meta


def _extract_tables(text: str) -> tuple:
    """Split text into (list_of_table_blocks, remaining_text)."""
    lines = text.split("\n")
    tables, current_table, non_table = [], [], []
    in_table = False
    for line in lines:
        if "|" in line and (in_table or "---" in line):
            in_table = True
            current_table.append(line)
        else:
            if in_table:
                tables.append("\n".join(current_table))
                current_table = []
                in_table = False
            non_table.append(line)
    if current_table:
        tables.append("\n".join(current_table))
    return tables, "\n".join(non_table)
