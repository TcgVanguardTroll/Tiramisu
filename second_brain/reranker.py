"""reranker.py — Temporal decay + personalized reranking."""

import math
import numpy as np
from datetime import datetime

from type_config import DECAY_CONFIG, DEFAULT_DECAY

ALPHA = 0.75  # semantic weight; (1-ALPHA) = recency weight
PROFILE_WEIGHT = 0.15  # weight for profile similarity boost


def decay_weight(age_days: float, half_life: float, fn: str) -> float:
    if fn == "exponential":
        return math.exp(-0.693 * age_days / half_life)
    elif fn == "gaussian":
        return math.exp(-0.5 * (age_days / half_life) ** 2)
    else:  # linear
        return max(0.05, 1 - age_days / (2 * half_life))


def rerank_with_decay(items: list, query_is_temporal: bool) -> list:
    """Rerank items using additive blend of semantic distance + age penalty."""
    if not query_is_temporal:
        return items
    now = datetime.now()
    for item in items:
        meta = item["metadata"]
        date_str = meta.get("date", "")
        if not date_str:
            item["final_score"] = item["distance"]
            continue
        try:
            doc_date = datetime.strptime(date_str, "%Y-%m-%d")
            age_days = (now - doc_date).days
        except ValueError:
            item["final_score"] = item["distance"]
            continue
        hl, fn = DECAY_CONFIG.get(meta.get("type", ""), DEFAULT_DECAY)
        weight = decay_weight(age_days, hl, fn)
        age_penalty = 1.0 - weight
        item["final_score"] = ALPHA * item["distance"] + (1 - ALPHA) * age_penalty
    items.sort(key=lambda x: x.get("final_score", x["distance"]))
    return items


def personalized_rerank(items, profile_embedding, profile, model, weight=PROFILE_WEIGHT):
    """Rerank items using profile vector similarity + metadata boosts."""
    if not items or profile_embedding is None:
        return items

    prof_norm = profile_embedding / (np.linalg.norm(profile_embedding) + 1e-9)

    # Active project names and team members for metadata boosting
    project_names = {p["name"].lower() for p in profile.get("active_projects", [])}
    team_members = {t.lower() for t in profile.get("team", [])}

    for item in items:
        # Use stored embedding from ChromaDB if available, else skip vector rerank
        emb = item.get("embedding")
        if emb is not None:
            emb = np.asarray(emb)
            emb_norm = emb / (np.linalg.norm(emb) + 1e-9)
            profile_sim = float(np.dot(emb_norm, prof_norm))
        else:
            profile_sim = 0.0

        base = item.get("final_score", item["distance"])
        # Blend: subtract weighted similarity (higher sim = lower score = better)
        score = base - weight * profile_sim

        # Metadata boosts
        meta = item["metadata"]
        source = meta.get("source", "").lower()
        tags = meta.get("tags", "").lower()
        participants = {p.strip() for p in meta.get("participants", "").split(",") if p.strip()}

        if any(pn in source or pn in tags for pn in project_names):
            score *= 0.9
        if participants & team_members:
            score *= 0.85

        item["final_score"] = score

    items.sort(key=lambda x: x.get("final_score", x["distance"]))
    return items
