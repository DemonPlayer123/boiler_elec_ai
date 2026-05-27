from __future__ import annotations

from typing import Any

from pathlib import Path

from src.domain.taxonomy import load_taxonomy


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _detect_phase_type(u_v: Any, phases: Any) -> str:
    try:
        u = float(u_v) if u_v is not None else None
    except Exception:
        u = None

    try:
        ph = int(phases) if phases is not None else None
    except Exception:
        ph = None

    if ph == 1:
        return "single_phase"
    if ph == 3:
        return "three_phase"

    if u in (220, 230):
        return "single_phase"
    if u in (380, 400):
        return "three_phase"

    return "unknown"


def _detect_load_type(ep_kind: str) -> str:
    if ep_kind in {"pump", "fan", "burner"}:
        return "motor"
    if ep_kind in {"heating", "lighting"}:
        return "resistive"
    if ep_kind in {"cabinet", "hvac"}:
        return "mixed"
    return "unknown"


def _taxonomy_class(name: str, taxonomy: dict) -> tuple[str, list[str]]:
    reasons = []
    for cls_name, cls_cfg in (taxonomy or {}).items():
        keywords = [str(x).lower() for x in cls_cfg.get("keywords", [])]
        for kw in keywords:
            if kw and kw in name:
                reasons.append(f"keyword:{kw}")
                return cls_name, reasons
    return "unknown", reasons


def classify_items(registry: list[dict], passport_items: list[dict]) -> list[dict]:
    taxonomy_path = Path(__file__).resolve().parents[1] / "domain" / "taxonomy.yaml"
    taxonomy = load_taxonomy(taxonomy_path)
    report: list[dict] = []

    pass_by_tag = {}
    for p in passport_items:
        tag = _norm_text(p.get("tag"))
        if tag:
            pass_by_tag[tag] = p

    for r in registry:
        tag = r.get("tag")
        name = _norm_text(r.get("base_name"))
        reg_kind = _norm_text(r.get("equip_class"))

        p = pass_by_tag.get(_norm_text(tag), {})

        u_v = p.get("u_v")
        phases = p.get("phases")
        display_name = p.get("display_name") or r.get("base_name") or tag or ""
        model = p.get("model") or ""

        tax_class, tax_reasons = _taxonomy_class(
            f"{display_name} {model} {name}", taxonomy
        )

        final_class = reg_kind or tax_class
        load_type = _detect_load_type(final_class)
        phase_type = _detect_phase_type(u_v, phases)

        has_vfd = any(
            x in _norm_text(f"{display_name} {model}")
            for x in ["чрп", "vfd", "преобразователь частоты", "частотн"]
        )

        safety_critical = final_class in {"pump", "burner", "cabinet"}
        duty = r.get("duty")
        dry_reserve = bool(r.get("dry_reserve"))

        reasons = []
        if reg_kind:
            reasons.append(f"registry_kind:{reg_kind}")
        reasons.extend(tax_reasons)
        if has_vfd:
            reasons.append("detected_vfd")
        if phase_type != "unknown":
            reasons.append(f"phase_type:{phase_type}")
        if load_type != "unknown":
            reasons.append(f"load_type:{load_type}")

        report.append(
            {
                "tag": tag,
                "display_name": display_name,
                "model": model,
                "equipment_class": final_class,
                "load_type": load_type,
                "phase_type": phase_type,
                "has_vfd": has_vfd,
                "safety_critical": safety_critical,
                "duty": duty,
                "dry_reserve": dry_reserve,
                "classification_reasons": reasons,
            }
        )
    
    # ===== FALLBACK: позиции, которых нет в registry, но они есть в passport_items/items =====
    seen_tags = {str(x.get("tag") or "").strip() for x in report if x.get("tag")}

    for p in passport_items:
        tag = str(p.get("tag") or "").strip()
        if not tag or tag in seen_tags:
            continue

        display_name = p.get("display_name") or tag or ""
        model = p.get("model") or ""
        u_v = p.get("u_v")
        phases = p.get("phases")

        item_class = _norm_text(p.get("equipment_class") or p.get("ep_kind"))
        tax_class, tax_reasons = _taxonomy_class(
            f"{display_name} {model} {tag}", taxonomy
        )

        final_class = item_class or tax_class
        load_type = _detect_load_type(final_class)
        phase_type = _detect_phase_type(u_v, phases)

        has_vfd = any(
            x in _norm_text(f"{display_name} {model}")
            for x in ["чрп", "vfd", "преобразователь частоты", "частотн"]
        )

        safety_critical = final_class in {"pump", "burner", "cabinet"}

        reasons = []
        if item_class:
            reasons.append(f"item_kind:{item_class}")
        reasons.extend(tax_reasons)
        if has_vfd:
            reasons.append("detected_vfd")
        if phase_type != "unknown":
            reasons.append(f"phase_type:{phase_type}")
        if load_type != "unknown":
            reasons.append(f"load_type:{load_type}")

        report.append(
            {
                "tag": tag,
                "display_name": display_name,
                "model": model,
                "equipment_class": final_class,
                "load_type": load_type,
                "phase_type": phase_type,
                "has_vfd": has_vfd,
                "safety_critical": safety_critical,
                "duty": p.get("duty"),
                "dry_reserve": bool(p.get("dry_reserve")),
                "classification_reasons": reasons,
            }
        )

    return report