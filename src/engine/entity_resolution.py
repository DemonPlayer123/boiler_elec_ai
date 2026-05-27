from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from typing import Any


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def _registry_base(reg: dict) -> str:
    return _norm_text(reg.get("group_base") or reg.get("base_name") or reg.get("tag"))


def _passport_base(item: dict) -> str:
    return _norm_text(item.get("tag") or item.get("display_name") or item.get("model"))


def _compact_text(value: Any) -> str:
    s = _norm_text(value)
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
    return "".join(out)


def _registry_group_prefix(tag: str) -> str:
    tag = _norm_text(tag)
    if "." in tag:
        return tag.split(".", 1)[0]
    out = []
    for ch in tag:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return "".join(out)


def _passport_search_blob(item: dict) -> str:
    parts = [
        item.get("tag"),
        item.get("display_name"),
        item.get("model"),
        item.get("file_name"),
        item.get("source_file"),
    ]
    return " ".join(str(x or "") for x in parts)


def _contains_modelish_overlap(reg_text: str, pass_text: str) -> bool:
    a = _compact_text(reg_text)
    b = _compact_text(pass_text)
    if not a or not b:
        return False
    if len(a) >= 5 and a in b:
        return True
    if len(b) >= 5 and b in a:
        return True
    return False


def _is_hvac_group_file_match(reg_tag: str, reg_name: str, passport_item: dict) -> bool:
    tag = _norm_text(reg_tag)
    name = _norm_text(reg_name)

    blob = _norm_text(_passport_search_blob(passport_item))
    compact_blob = _compact_text(blob)
    compact_name = _compact_text(name)

    if tag.startswith("а"):
        if "а1-а4" in blob or "вс-2245" in blob or "вс2245" in compact_blob:
            return True
        if ("греерс" in name or "вс2245" in compact_name) and (
            "греерс" in blob or "вс-2245" in blob or "вс2245" in compact_blob
        ):
            return True

    if tag.startswith("п"):
        if "п1-п4" in blob:
            return True
        if "vo-500-4e-03-b" in blob or "vo5004e03b" in compact_blob:
            return True
        if ("nevatom" in name or "vo5004e03b" in compact_name) and (
            "nevatom" in blob or "vo-500-4e-03-b" in blob or "vo5004e03b" in compact_blob
        ):
            return True

    if tag == "в1":
        if "slim 4c" in blob or "slim4c" in compact_blob:
            return True
        if ("slim 4c" in name or "slim4c" in compact_name) and (
            "slim 4c" in blob or "slim4c" in compact_blob
        ):
            return True
        if "вытяж" in name and ("slim" in blob or "рнк-100" in blob or "рнк100" in compact_blob):
            return True

    return False


def _pick_best_from_tag_matches(reg: dict, matches: list[dict]) -> tuple[dict | None, float, str]:
    if not matches:
        return None, 0.0, "unmatched"

    reg_kind = _norm_text(reg.get("equip_class"))
    reg_name = _norm_text(reg.get("base_name"))
    reg_base = _registry_base(reg)

    best = None
    best_score = -1.0

    for p in matches:
        score = 1.0
        p_kind = _norm_text(p.get("ep_kind"))
        p_blob = _norm_text(_passport_search_blob(p))

        if reg_kind and p_kind and reg_kind == p_kind:
            score += 0.05
        if _contains_modelish_overlap(reg_name, p_blob):
            score += 0.05
        if _contains_modelish_overlap(reg_base, p_blob):
            score += 0.03

        if score > best_score:
            best = p
            best_score = score

    return best, min(best_score, 1.0), "tag_exact"


def _is_pump_rule_match(reg_tag: str, reg_name: str, passport_item: dict) -> bool:
    tag = _norm_text(reg_tag)
    if not tag.startswith("к"):
        return False

    p_tag = _norm_text(passport_item.get("tag"))
    blob = _norm_text(_passport_search_blob(passport_item))
    compact_blob = _compact_text(blob)
    compact_name = _compact_text(reg_name)

    if p_tag and p_tag == tag:
        return True

    if tag in blob:
        return True

    if _contains_modelish_overlap(reg_name, blob):
        return True

    if compact_name and compact_name in compact_blob and len(compact_name) >= 5:
        return True

    return False


def resolve_entities(registry: list[dict], passport_items: list[dict]) -> list[dict]:
    links: list[dict] = []

    pass_by_tag: dict[str, list[dict]] = defaultdict(list)
    for p in passport_items:
        tag = _norm_text(p.get("tag"))
        if tag:
            pass_by_tag[tag].append(p)

    for r in registry:
        reg_tag = _norm_text(r.get("tag"))
        reg_kind = _norm_text(r.get("equip_class"))
        reg_base = _registry_base(r)
        reg_name = _norm_text(r.get("base_name"))
        reg_group = _registry_group_prefix(reg_tag)

        best_match = None
        best_score = 0.0
        reason = "unmatched"

        # 1. exact tag
        if reg_tag and reg_tag in pass_by_tag:
            best_match, best_score, reason = _pick_best_from_tag_matches(r, pass_by_tag[reg_tag])

        # 1.5 HVAC grouped passport
        if best_match is None and reg_kind == "hvac":
            for p in passport_items:
                if _is_hvac_group_file_match(reg_tag, reg_name, p):
                    best_match = p
                    best_score = 0.95
                    reason = "hvac_group_file"
                    break

        # 1.6 pump rule-first
        if best_match is None and reg_kind == "pump":
            for p in passport_items:
                if _is_pump_rule_match(reg_tag, reg_name, p):
                    best_match = p
                    best_score = 0.92
                    reason = "pump_tag_model_file"
                    break

        # 2. fuzzy fallback
        if best_match is None:
            for p in passport_items:
                p_kind = _norm_text(p.get("ep_kind"))
                p_base = _passport_base(p)
                p_name = _norm_text(p.get("display_name"))
                p_model = _norm_text(p.get("model"))
                p_blob = _norm_text(_passport_search_blob(p))

                score = 0.0

                if reg_kind and p_kind and reg_kind == p_kind:
                    score += 0.30

                score += 0.30 * max(
                    _similarity(reg_base, p_base),
                    _similarity(reg_base, p_name),
                    _similarity(reg_name, p_name),
                    _similarity(reg_name, p_model),
                )

                if _contains_modelish_overlap(reg_name, p_blob):
                    score += 0.35
                if _contains_modelish_overlap(reg_base, p_blob):
                    score += 0.25

                if reg_group and reg_group in p_blob:
                    score += 0.10

                important_tokens = ["baltur", "tbg", "vo", "вс", "cdm", "td", "chl", "llts"]
                reg_text_join = f"{reg_name} {reg_base}"
                for tok in important_tokens:
                    if tok in _norm_text(reg_text_join) and tok in p_blob:
                        score += 0.08

                if score > best_score:
                    best_score = score
                    best_match = p
                    reason = "fuzzy_base_model_group"

        link = {
            "registry_tag": r.get("tag"),
            "registry_base_name": r.get("base_name"),
            "registry_kind": r.get("equip_class"),
            "matched": best_match is not None and best_score >= 0.38,
            "match_confidence": round(best_score, 3),
            "link_reason": reason,
            "passport_tag": None,
            "passport_model": None,
            "passport_display_name": None,
        }

        if best_match is not None and best_score >= 0.38:
            link["passport_tag"] = best_match.get("tag")
            link["passport_model"] = best_match.get("model")
            link["passport_display_name"] = best_match.get("display_name")

        links.append(link)

    return links