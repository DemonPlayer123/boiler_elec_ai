from __future__ import annotations

import argparse
import json
from pathlib import Path
from collections import defaultdict

from src.extract.schemes.scheme_parser import parse_schemes_to_registry
from src.extract.passports.passport_parser import parse_passports_dir
from src.excel.writer import upsert_items_to_template
from src.engine.calibrate import calibrate_items
from src.extract.pdf_text import extract_text_pymupdf
from src.extract.schemes.scheme_parser import parse_lighting_from_eo_text

from src.engine.entity_resolution import resolve_entities
from src.engine.classifier import classify_items
from src.engine.consistency_checker import check_consistency
from src.engine.requirements_builder import build_requirements
from src.engine.selector import select_catalog_candidates
from src.engine.shortlist import build_shortlist
from src.engine.retriever import retrieve_normative_chunks
from src.engine.rag_pipeline import build_rag_summary
from src.engine.normative_corpus_builder import build_normative_corpus_from_dir
from src.excel.excel_identity import build_excel_identity

from src.engine.ai_entity_review import build_ai_entity_review
from src.engine.ai_classification_review import build_ai_classification_review
from src.engine.ai_consistency_review import build_ai_consistency_review
from src.engine.ai_normative_review import build_ai_normative_review

from src.engine.ai_catalog_review import build_ai_catalog_review
from src.engine.ai_project_summary import build_ai_project_summary

def _load_user_inputs(out_dir: Path) -> dict:
    p = out_dir / "user_inputs.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _load_catalog_normalized(out_dir: Path) -> list[dict]:
    """
    Порядок загрузки:
    1) data/output/.../catalog_normalized.json
    2) data/catalogs/catalog_metadata.json
    """
    candidates = [
        out_dir / "catalog_normalized.json",
        Path(__file__).resolve().parents[2] / "data" / "catalogs" / "catalog_metadata.json",
    ]

    for p in candidates:
        if not p.exists():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            continue

    return []
    
def _load_normative_corpus(out_dir: Path) -> list[dict]:
    p = out_dir / "normative_corpus.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []

def _merge_user_inputs(items: list[dict], user_inputs: dict) -> list[dict]:
    by_tag = {it.get("tag"): it for it in items if it.get("tag")}
    for tag, fields in (user_inputs or {}).items():
        if tag in by_tag and isinstance(fields, dict):
            by_tag[tag].update(fields)
            by_tag[tag]["_force_overwrite"] = True
    return list(by_tag.values())


def _make_name_from_registry(r: dict) -> str:
    base = (r.get("base_name") or "").strip()
    tag = r.get("tag")
    duty = r.get("duty")
    if duty == "work":
        return f"{base} ({tag}) (раб.)"
    if duty == "reserve":
        return f"{base} ({tag}) (рез.)"
    return f"{base} ({tag})"


def _make_pump_display_name(pump_kind: str, tag: str, duty: str | None) -> str:
    if duty == "work":
        return f"{pump_kind} {tag} (раб.)"
    if duty == "reserve":
        return f"{pump_kind} {tag} (рез.)"
    return f"{pump_kind} {tag}"


def _expand_pumps_from_tm(passport_items: list[dict], registry: list[dict]) -> list[dict]:
    pass_by_base = {it["tag"]: it for it in passport_items if it.get("tag")}

    pumps = [r for r in registry if r.get("equip_class") == "pump" and r.get("tag")]
    pumps_by_base = defaultdict(list)

    for r in pumps:
        base = (r.get("group_base") or r["tag"].split(".")[0]).upper()
        pumps_by_base[base].append(r)

    out: list[dict] = []
    used_pass_tags = set()

    for base, regs in pumps_by_base.items():

        regs_sorted = sorted(
            regs,
            key=lambda x: (
                x["tag"].split(".", 1)[0],
                int(x["tag"].split(".", 1)[1]) if "." in x["tag"] else 0,
            ),
        )

        p = pass_by_base.get(base)
        if not p:
            continue

        used_pass_tags.add(base)

        # Сухой резерв вообще НЕ добавляем
        regs_sorted = [r for r in regs_sorted if not r.get("dry_reserve")]

        n = len(regs_sorted)

        for i, r in enumerate(regs_sorted, start=1):
            duty_from_scheme = r.get("duty")

            if duty_from_scheme in ("work", "reserve"):
                duty = duty_from_scheme
            else:
                duty = None if n == 1 else ("work" if i < n else "reserve")

            item = dict(p)
            item["tag"] = r["tag"]
            item["ep_kind"] = "pump"
            item["duty"] = duty

            pump_kind = r.get("pump_kind") or "Насос"
            item["display_name"] = _make_pump_display_name(pump_kind, r["tag"], duty)

            out.append(item)

    for it in passport_items:
        if it.get("tag") in used_pass_tags:
            continue
        out.append(it)

    return out

def _ensure_ep_kind_from_registry(items: list[dict], registry: list[dict]) -> list[dict]:
    """
    Жестко восстанавливает ep_kind и equipment_class по registry и по
    детерминированным теговым правилам для уже собранных items.
    """
    reg_by_tag = {
        str(r.get("tag") or "").strip(): r
        for r in (registry or [])
        if r.get("tag")
    }

    for it in items:
        tag = str(it.get("tag") or "").strip()
        if not tag:
            continue

        r = reg_by_tag.get(tag)
        equip_class = ""
        ep_kind = str(it.get("ep_kind") or "").strip().lower()

        if r:
            equip_class = str(r.get("equip_class") or "").strip().lower()

        tag_u = tag.upper()

        # 1. Насосы
        if not equip_class and tag_u.startswith("К"):
            equip_class = "pump"
        if equip_class == "pump":
            ep_kind = "pump"

        # 2. Горелки
        if not equip_class and tag_u.startswith("ГГ"):
            equip_class = "burner"
        if equip_class == "burner":
            ep_kind = "burner"

        # 3. HVAC
        if not equip_class and (tag_u.startswith("А") or tag_u.startswith("П") or tag_u.startswith("В")):
            equip_class = "hvac"
        if equip_class == "hvac":
            ep_kind = "hvac"

        # 4. Шкафы котлов
        if not equip_class and tag_u.startswith("ШК"):
            equip_class = "cabinet"
        if equip_class == "cabinet" and not ep_kind:
            ep_kind = "cabinet"

        # 5. Фиксированные шкафы и щиты
        if not equip_class and tag in {"ШУК", "ЩУТ", "ЩУГ", "ШСС"}:
            equip_class = "cabinet"
        if tag in {"ШУК", "ЩУТ", "ЩУГ", "ШСС"}:
            ep_kind = "cabinet"

        # 6. Освещение
        if not equip_class and tag.startswith("Гр."):
            equip_class = "lighting"
        if tag.startswith("Гр."):
            ep_kind = "lighting"

        # 7. Электрообогрев
        if not equip_class and tag_u.startswith(("EK", "ЕК")):
            equip_class = "heating"
        if tag_u.startswith(("EK", "ЕК")):
            ep_kind = "heating"

        # 8. ХВО
        if tag == "ХВО":
            equip_class = "water_treatment"
            if not ep_kind:
                ep_kind = "water_treatment"

        if equip_class:
            it["equipment_class"] = equip_class
        if ep_kind:
            it["ep_kind"] = ep_kind

    return items

def _add_non_passport_items_from_registry(items: list[dict], registry: list[dict]) -> list[dict]:
    existing = {it.get("tag") for it in items if it.get("tag")}

    for r in registry:
        tag = r.get("tag")
        if not tag or tag in existing:
            continue
        if r.get("dry_reserve"):
            continue

        if r.get("equip_class") == "hvac":
            items.append(
                {
                    "tag": tag,
                    "ep_kind": "hvac",
                    "display_name": _make_name_from_registry(r),
                    "u_v": None,
                    "p_kw": None,
                    "phases": None,
                    "eta_pct": None,
                    "cos_phi": None,
                }
            )
            existing.add(tag)
            
        if r.get("equip_class") == "burner":
            items.append({
                "tag": tag,
                "ep_kind": "burner",
                "display_name": _make_name_from_registry(r),
                "u_v": None, "p_kw": None, "phases": None,
                "eta_pct": 100.0,
                "cos_phi": None,
            })
            existing.add(tag)

    return items

# ===== ДОБАВИТЬ В run_pipeline.py (на уровень модуля, рядом с другими helper'ами) =====

def _apply_hvac_known_passport_defaults(items: list[dict]) -> list[dict]:
    """
    Подкладывает паспортные значения для ОВ (hvac) по известным моделям.
    Заполняем ТОЛЬКО если поле отсутствует или равно None.
    """
    def set_if_none(d: dict, k: str, v):
        if d.get(k) is None:
            d[k] = v

    for it in items:
        if (it.get("ep_kind") or "").strip() != "hvac":
            continue

        tag = str(it.get("tag") or "").strip()
        name = str(it.get("display_name") or "").lower()

        # ГРЕЕРС ВС-2245 (А1–А4), двигатель АС:
        # U=230, 1 фаза, P=0.26 кВт, Iном=1.2 А
        if tag.startswith("А") and "вс-2245" in name:
            set_if_none(it, "u_v", 230)
            set_if_none(it, "phases", 1)
            set_if_none(it, "p_kw", 0.26)
            set_if_none(it, "i_a", 1.2)

        # Nevatom VO 500-4E-03-B (П1–П4):
        # U=220, 1 фаза, P=0.42 кВт, Iном=1.85 А
        elif tag.startswith("П") and ("vo-500-4" in name or "vo 500-4" in name):
            set_if_none(it, "u_v", 220)
            set_if_none(it, "phases", 1)
            set_if_none(it, "p_kw", 0.42)
            set_if_none(it, "i_a", 1.85)

        # SLIM 4C (В1) — мощность 7.8 Вт
        elif tag == "В1" and "slim" in name:
            set_if_none(it, "u_v", 220)
            set_if_none(it, "phases", 1)
            set_if_none(it, "p_kw", 0.0078)

    return items

def _lighting_is_in_cabinet(user_inputs: dict) -> bool:
    """
    Ожидаем, что user_inputs может содержать спец-раздел:
      {
        "_meta": {
          "lighting_in_cabinet": true
        }
      }
    или (альтернатива)
      {
        "lighting": {"in_cabinet": true}
      }

    Сделал оба варианта на будущее, чтобы не ломать формат.
    """
    if not isinstance(user_inputs, dict):
        return False

    meta = user_inputs.get("_meta")
    if isinstance(meta, dict) and meta.get("lighting_in_cabinet") is True:
        return True

    lighting = user_inputs.get("lighting")
    if isinstance(lighting, dict) and lighting.get("in_cabinet") is True:
        return True

    return False


def _add_lighting_items(items: list[dict], lighting_items: list[dict]) -> list[dict]:
    existing = {it.get("tag") for it in items if it.get("tag")}
    for li in lighting_items:
        tag = li.get("tag")
        if tag and tag not in existing:
            items.append(li)
            existing.add(tag)
    return items

def _build_heating_items_from_user_inputs(user_inputs: dict) -> list[dict]:
    meta = (user_inputs or {}).get("_meta") or {}
    if not meta.get("heating_needed"):
        return []

    heating = meta.get("heating") or {}
    w_per_m = float(heating.get("linear_w_per_m") or 16.0)

    out = []
    # точечные объекты (дренаж, Т96, желоба и т.п.)
    for idx, it in enumerate(heating.get("items") or [], start=1):
        name = str(it.get("name") or f"Электрообогрев {idx}").strip()
        name_l = name.lower()
        if "крыша" in name_l:
            # крыша считается отдельной веткой roof, чтобы не было двойного учета
            continue
        meters = float(it.get("meters") or 0.0)
        if meters <= 0:
            continue
        p_kw = round((meters * w_per_m) / 1000.0, 6)
        out.append({
            "tag": f"EK{idx}",  # универсально: EK1, EK2...
            "ep_kind": "heating",
            "display_name": f"Электрообогрев {name}",
            "u_v": 220,
            "phases": 1,
            "p_kw": p_kw,
            "cos_phi": 1.0,
            "eta_pct": 100.0,
            "i_a": None,
            "source": "user_inputs",
        })

    # крыша
    roof = heating.get("roof")
    if isinstance(roof, dict) and roof.get("roof_len_m"):
        roof_len = float(roof["roof_len_m"])
        mult = float(roof.get("multiplier") or 8.0)
        meters_total = roof_len * mult
        p_kw = round((meters_total * w_per_m) / 1000.0, 6)

        # следующий индекс после уже добавленных EK-позиций
        next_idx = 1 + sum(1 for x in out if str(x.get("tag", "")).startswith("EK"))
        out.append({
            "tag": f"EK{next_idx}",
            "ep_kind": "heating",
            "display_name": "Электрообогрев крыши",
            "u_v": 220,
            "phases": 1,
            "p_kw": p_kw,
            "cos_phi": 1.0,
            "eta_pct": 100.0,
            "i_a": None,
            "source": "user_inputs",
        })

    return out

def _build_cabinet_items_from_user_inputs(user_inputs: dict) -> list[dict]:
    meta = (user_inputs or {}).get("_meta") or {}
    cabs = meta.get("cabinets") or []
    out = []
    for c in cabs:
        tag = str(c.get("tag") or "").strip()
        p_kw = c.get("p_kw")
        if not tag or p_kw is None:
            continue
        out.append({
            "tag": tag,
            "ep_kind": "cabinet",
            "display_name": tag,
            "u_v": 220,
            "phases": 1,
            "p_kw": float(p_kw),
            "cos_phi": 1.0,
            "eta_pct": 100.0,
            "i_a": None,
            "source": "user_inputs",
        })
    return out

def _add_shk_from_burners(items: list[dict], registry: list[dict]) -> list[dict]:
    """
    Шкафы управления котлом (ШК) — обязательны.
    Кол-во ШК = кол-во горелок (equip_class == 'burner') в registry.
    Параметры: P=0.1 кВт, cos=1, eta=100%.
    Теги: ШК1..ШКn
    """
    # считаем горелки по registry
    burners = [r for r in (registry or []) if (r.get("equip_class") or "") == "burner"]
    n = len(burners)

    if n <= 0:
        return items

    existing = {str(it.get("tag") or "").strip() for it in items}
    for i in range(1, n + 1):
        tag = f"ШК{i}"
        if tag in existing:
            continue
        items.append({
            "tag": tag,
            "ep_kind": "cabinet",                 # можно "shk", но cabinet проще для writer
            "display_name": f"Шкаф управления котлом {i}",
            "p_kw": 0.1,
            "u_v": 220,
            "phases": 1,
            "cos_phi": 1.0,
            "eta_pct": 100.0,
            "i_a": None,
            "source": "rule",
        })
        existing.add(tag)

    return items

def run_pipeline(
    *,
    schemes_dir: Path,
    passports_dir: Path,
    template_xlsx: Path,
    out_dir: Path,
    norms_dir: Path | None = None,
    project_code: str = "25-05",
) -> dict:

    out_dir.mkdir(parents=True, exist_ok=True)
    
    normative_corpus = []
    if norms_dir is not None and norms_dir.exists():
        normative_corpus = build_normative_corpus_from_dir(norms_dir)
        (out_dir / "normative_corpus.json").write_text(
            json.dumps(normative_corpus, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    registry = parse_schemes_to_registry(schemes_dir)
    (out_dir / "equipment_registry.json").write_text(
        json.dumps(registry, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    passport_items, passports_parsed = parse_passports_dir(passports_dir)
    (out_dir / "passports_parsed.json").write_text(
        json.dumps(passports_parsed, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    # Для entity linking используем объединенный набор:
    # - passport_items хорошо держит индивидуальные теги (например, ГГ.1..ГГ.4)
    # - passports_parsed нужен для групповых HVAC-паспортов через source_file
    resolver_passport_items = list(passport_items)

    seen_keys = {
        (
            str(x.get("tag") or "").strip(),
            str(x.get("model") or "").strip(),
            str(x.get("source_file") or x.get("file_name") or "").strip(),
        )
        for x in resolver_passport_items
    }

    for x in passports_parsed:
        key = (
            str(x.get("tag") or "").strip(),
            str(x.get("model") or "").strip(),
            str(x.get("source_file") or x.get("file_name") or "").strip(),
        )
        if key not in seen_keys:
            resolver_passport_items.append(x)
            seen_keys.add(key)

    entity_links = resolve_entities(registry, resolver_passport_items)
    (out_dir / "entity_links.json").write_text(
        json.dumps(entity_links, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    ai_entity_review = build_ai_entity_review(registry, entity_links)
    (out_dir / "ai_entity_review.json").write_text(
        json.dumps(ai_entity_review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    classification_report_initial = classify_items(registry, passport_items)
    (out_dir / "classification_report_initial.json").write_text(
        json.dumps(classification_report_initial, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    # 1. Насосы
    items = _expand_pumps_from_tm(passport_items, registry)
    items = _ensure_ep_kind_from_registry(items, registry)

    # 2. ОВ и прочие без паспорта
    items = _add_non_passport_items_from_registry(items, registry)
    
    items = _add_shk_from_burners(items, registry)
    
    # 2.5 ОВ: известные паспортные значения (ГРЕЕРС/VO500/SLIM4C)
    items = _apply_hvac_known_passport_defaults(items)

    # 3. Пользовательские правки
    user_inputs = _load_user_inputs(out_dir)
    items = _merge_user_inputs(items, user_inputs)
    
    cab_items = _build_cabinet_items_from_user_inputs(user_inputs)
    if cab_items:
        existing = {it.get("tag") for it in items if it.get("tag")}
        for ci in cab_items:
            if ci["tag"] not in existing:
                items.append(ci)
                existing.add(ci["tag"])
    
    # 3.5 Освещение из ЭО (если НЕ заведено в шкаф)
    # Ищем ЭО pdf в папке schemes_dir по имени (универсально — по подстроке "ЭО")
    if not _lighting_is_in_cabinet(user_inputs):
        eo_pdf = None
        for p in schemes_dir.glob("*.pdf"):
            if "ЭО" in p.name.upper():
                eo_pdf = p
                break

        if eo_pdf is not None:
            eo_text = extract_text_pymupdf(eo_pdf)
            lighting_items = parse_lighting_from_eo_text(eo_text)
            items = _add_lighting_items(items, lighting_items)
            
    # 3.6 Электрообогрев (из user_inputs)
    heating_items = _build_heating_items_from_user_inputs(user_inputs)
    if heating_items:
        # не дублируем по tag
        existing = {it.get("tag") for it in items if it.get("tag")}
        for hi in heating_items:
            if hi["tag"] not in existing:
                items.append(hi)
                existing.add(hi["tag"])

    # 4. КАЛИБРОВКА (новая, с кэшем и диапазонами 0.8–0.9 / 75–90)
    items = calibrate_items(
        items,
        user_inputs,
        eta_min_pct=75,
        eta_max_pct=90,
        cos_min=0.80,
        cos_max=0.90,
        eta_step_pct=1,
        cos_step=0.01,
        tolerance_pct=3.0,
    )
    
    items = build_excel_identity(items)
    
    items = _ensure_ep_kind_from_registry(items, registry)
    
    classification_report = classify_items(registry, items)
    (out_dir / "classification_report.json").write_text(
        json.dumps(classification_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ai_classification_review = build_ai_classification_review(classification_report)
    (out_dir / "ai_classification_review.json").write_text(
        json.dumps(ai_classification_review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    consistency_report = check_consistency(
        items=items,
        entity_links=entity_links,
        classification_report=classification_report,
    )
    (out_dir / "consistency_report.json").write_text(
        json.dumps(consistency_report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    requirements = build_requirements(
        items=items,
        classification_report=classification_report,
    )
    (out_dir / "requirements.json").write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    ai_consistency_review = build_ai_consistency_review(
        items=items,
        entity_links=entity_links,
        classification_report=classification_report,
        requirements=requirements,
    )
    (out_dir / "ai_consistency_review.json").write_text(
        json.dumps(ai_consistency_review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    catalog_items = _load_catalog_normalized(out_dir)

    candidates = select_catalog_candidates(
        requirements=requirements,
        catalog_items=catalog_items,
    )
    (out_dir / "candidates.json").write_text(
        json.dumps(candidates, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    shortlist = build_shortlist(candidates, top_n=8)
    (out_dir / "shortlist.json").write_text(
        json.dumps(shortlist, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    ai_catalog_review = build_ai_catalog_review(
        requirements=requirements,
        candidates=candidates,
        shortlist=shortlist,
    )
    (out_dir / "ai_catalog_review.json").write_text(
        json.dumps(ai_catalog_review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    normative_corpus = _load_normative_corpus(out_dir)

    retrieved_chunks = retrieve_normative_chunks(
        requirements=requirements,
        shortlist=shortlist,
        normative_corpus=normative_corpus,
        top_k=5,
    )
    (out_dir / "retrieved_chunks.json").write_text(
        json.dumps(retrieved_chunks, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    rag_summary = build_rag_summary(
        requirements=requirements,
        shortlist=shortlist,
        retrieved_chunks=retrieved_chunks,
    )
    (out_dir / "rag_summary.json").write_text(
        json.dumps(rag_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    ai_normative_review = build_ai_normative_review(
        rag_summary=rag_summary,
        requirements=requirements,
    )
    (out_dir / "ai_normative_review.json").write_text(
        json.dumps(ai_normative_review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    ai_project_summary = build_ai_project_summary(
        ai_entity_review=ai_entity_review,
        ai_consistency_review=ai_consistency_review,
        ai_normative_review=ai_normative_review,
        ai_catalog_review=ai_catalog_review,
    )
    (out_dir / "ai_project_summary.json").write_text(
        json.dumps(ai_project_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    out_xlsx = out_dir / f"result_{project_code}.xlsx"
    audit_csv = out_dir / "audit_log.csv"

    log = upsert_items_to_template(
        template_path=template_xlsx,
        out_path=out_xlsx,
        items=items,
        registry=registry,
        overwrite=False,
        only_fill_empty=True,
        end_row=3000,
        audit_csv_path=audit_csv,
        prune_template=False,  # ВАЖНО: отключено
    )

    (out_dir / "write_log.txt").write_text("\n".join(log), encoding="utf-8")

    (out_dir / "items_final.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "out_xlsx": str(out_xlsx),
        "audit_csv": str(audit_csv),
        "registry_count": len(registry),
        "items_count": len(items),
        "entity_links_count": len(entity_links),
        "classification_count": len(classification_report),
        "consistency_issues_count": len(consistency_report),
        "requirements_count": len(requirements),
        "candidates_count": len(candidates),
        "shortlist_count": len(shortlist),
        "retrieved_chunks_count": len(retrieved_chunks),
        "rag_summary_count": len(rag_summary),
        "log_lines": len(log),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--schemes_dir", required=True)
    ap.add_argument("--passports_dir", required=True)
    ap.add_argument("--template_xlsx", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--norms_dir", required=False)
    ap.add_argument("--project_code", default="25-05")
    args = ap.parse_args()

    run_pipeline(
        schemes_dir=Path(args.schemes_dir),
        passports_dir=Path(args.passports_dir),
        template_xlsx=Path(args.template_xlsx),
        out_dir=Path(args.out_dir),
        norms_dir=Path(args.norms_dir) if args.norms_dir else None,
        project_code=args.project_code,
    )


if __name__ == "__main__":
    main()