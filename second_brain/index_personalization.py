"""index_personalization.py — Profile-aware indexing: relevance tagging, chunk sizing, enhanced metadata."""

import re

# ---------------------------------------------------------------------------
# Relevance tagging
# ---------------------------------------------------------------------------

def _build_keyword_index(profile: dict) -> list[dict]:
    """Build a list of {name, priority, keywords, systems} from active_projects."""
    projects = []
    for proj in profile.get("active_projects", []):
        kws = [k.lower() for k in proj.get("keywords", [])]
        systems = [s.lower() for s in proj.get("systems", [])]
        projects.append({
            "name": proj["name"],
            "priority": proj.get("priority", "low"),
            "keywords": kws,
            "systems": systems,
            "partners": [p.lower() for p in proj.get("partners", [])],
        })
    return projects


def tag_relevance(text: str, profile: dict) -> tuple[str, float]:
    """Return (project_name, relevance_score) for a chunk. '' and 0.0 if unrelated."""
    projects = _build_keyword_index(profile)
    text_lower = text.lower()
    words = set(re.findall(r"[a-z][a-z0-9_-]+", text_lower))
    word_count = max(len(words), 1)

    best_name, best_score = "", 0.0
    for proj in projects:
        hits = 0
        all_terms = proj["keywords"] + proj["systems"] + proj["partners"]
        for term in all_terms:
            # Multi-word terms: check substring. Single-word: check word set.
            if " " in term:
                if term in text_lower:
                    hits += 1
            elif term in words:
                hits += 1
        if hits == 0:
            continue
        score = min(1.0, hits / max(len(all_terms) * 0.3, 1))
        if score > best_score:
            best_score = score
            best_name = proj["name"]
    return best_name, round(best_score, 3)


# ---------------------------------------------------------------------------
# Priority-based chunk size adjustment
# ---------------------------------------------------------------------------

PRIORITY_SCALE = {"high": 0.65, "medium": 0.85, "low": 1.0}


def adjusted_chunk_params(params: dict, priority: str) -> dict:
    """Return chunk params with size scaled down for high-priority projects."""
    if params.get("size") is None:
        return params
    scale = PRIORITY_SCALE.get(priority, 1.0)
    if scale >= 1.0:
        return params
    new_size = max(200, int(params["size"] * scale))
    new_overlap = max(20, int(params["overlap"] * scale))
    if new_overlap >= new_size:
        new_overlap = new_size - 1
    return {**params, "size": new_size, "overlap": new_overlap}


# ---------------------------------------------------------------------------
# Enhanced metadata for relevant docs
# ---------------------------------------------------------------------------

SYSTEM_RE = re.compile(r"\b([A-Z][a-zA-Z]+(?:Service|Registry|Responder|Precompute|Ranker|Aggregator|API|Path))\b")
DECISION_RE = re.compile(r"(?:decided|agreed|decision|approved|confirmed)\s+(?:to\s+)?([^.;\n]{5,80})", re.IGNORECASE)
ACTION_RE = re.compile(r"(?:TODO|action item|follow[- ]?up|next step)[s]?\s*[:]\s*([^.;\n]{5,80})", re.IGNORECASE)


def extract_enhanced_metadata(text: str, profile: dict) -> dict:
    """Extract richer metadata for chunks matching user's projects."""
    meta = {}

    # Mentioned systems from profile
    known_systems = set()
    for proj in profile.get("active_projects", []):
        for s in proj.get("systems", []):
            known_systems.add(s.lower())

    found_systems = []
    for sys_name in known_systems:
        if sys_name in text.lower():
            found_systems.append(sys_name)
    # Also catch CamelCase service names
    for m in SYSTEM_RE.finditer(text):
        s = m.group(1)
        if s.lower() not in [f.lower() for f in found_systems]:
            found_systems.append(s)
    if found_systems:
        meta["mentioned_systems"] = ",".join(found_systems[:10])

    # Partner teams
    known_partners = set()
    for proj in profile.get("active_projects", []):
        for p in proj.get("partners", []):
            known_partners.add(p.lower())
    found_partners = [p for p in known_partners if p in text.lower()]
    if found_partners:
        meta["partner_teams"] = ",".join(found_partners)

    # Decisions and action items (supplement type_config extraction)
    decisions = DECISION_RE.findall(text)
    if decisions:
        meta["decisions_enhanced"] = "; ".join(d.strip() for d in decisions[:5])
    actions = ACTION_RE.findall(text)
    if actions:
        meta["action_items_enhanced"] = "; ".join(a.strip() for a in actions[:5])

    return meta
