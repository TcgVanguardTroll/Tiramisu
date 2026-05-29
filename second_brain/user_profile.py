"""profile.py — User profile loading, embedding, and caching."""

import hashlib
import json
from pathlib import Path

import yaml

DEFAULT_PROFILE_PATH = Path(__file__).parent / "profile.yaml"
_CACHE_FILE = ".profile_cache.json"

_profile_cache = {"hash": None, "profile": None, "embedding": None}


def load_profile(path: str = None):
    """Load user profile from YAML. Returns None if file missing."""
    p = Path(path) if path else DEFAULT_PROFILE_PATH
    if not p.exists():
        return None
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    if _profile_cache["hash"] == h and _profile_cache["profile"]:
        return _profile_cache["profile"]
    profile = yaml.safe_load(p.read_text())
    _profile_cache["hash"] = h
    _profile_cache["profile"] = profile
    return profile


def profile_text(profile: dict) -> str:
    """Build a single text string from profile for embedding."""
    parts = [profile.get("role", "")]
    for proj in profile.get("active_projects", []):
        parts.append(proj.get("name", ""))
        parts.extend(proj.get("keywords", []))
    parts.extend(profile.get("interests", []))
    return ", ".join(p for p in parts if p)


def get_profile_embedding(profile: dict, model):
    """Return cached profile embedding, recompute only when profile changes."""
    h = _profile_cache.get("hash")
    if _profile_cache["embedding"] is not None and h:
        return _profile_cache["embedding"]
    text = profile_text(profile)
    emb = model.encode([text], show_progress_bar=False)[0]
    _profile_cache["embedding"] = emb
    return emb


def expand_query(raw_query: str, profile: dict) -> str:
    """Prepend user context to query for better retrieval."""
    parts = [profile.get("role", "")]
    for proj in profile.get("active_projects", [])[:2]:
        parts.extend(proj.get("keywords", []))
    context = ", ".join(p for p in parts if p)
    return f"{context}: {raw_query}" if context else raw_query


def format_profile(profile: dict) -> str:
    """Pretty-print profile for CLI display."""
    lines = [f"Name: {profile.get('name', '?')}",
             f"Role: {profile.get('role', '?')}",
             f"Team: {', '.join(profile.get('team', []))}"]
    for proj in profile.get("active_projects", []):
        kw = ", ".join(proj.get("keywords", []))
        lines.append(f"  Project: {proj['name']} ({proj.get('priority','?')}) — {kw}")
    lines.append(f"Interests: {', '.join(profile.get('interests', []))}")
    prefs = profile.get("query_preferences", {})
    lines.append(f"Detail level: {prefs.get('preferred_detail', 'concise')}")
    return "\n".join(lines)
