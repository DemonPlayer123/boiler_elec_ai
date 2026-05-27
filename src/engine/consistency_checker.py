from __future__ import annotations

from typing import Any


def _norm_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def _phase_type(u_v: Any, phases: Any) -> str:
    u = _safe_float(u_v)
    try:
        ph = int(phases) if phases is not None else None
    except Exception:
        ph = None

    if ph == 1:
        return "single_phase"
    if ph == 3:
        return "three_phase"
    if u in (220.0, 230.0):
        return "single_phase"
    if u in (380.0, 400.0):
        return "three_phase"
    return "unknown"


def _build_class_map(classification_report: list[dict]) -> dict[str, dict]:
    out = {}
    for row in classification_report or []:
        tag = str(row.get("tag") or "").strip()
        if tag:
            out[tag] = row
    return out


def _build_link_map(entity_links: list[dict]) -> dict[str, dict]:
    out = {}
    for row in entity_links or []:
        tag = str(row.get("registry_tag") or "").strip()
        if tag:
            out[tag] = row
    return out


def check_consistency(
    items: list[dict],
    entity_links: list[dict],
    classification_report: list[dict],
) -> list[dict]:
    """
    Базовый consistency checker без вмешательства в расчётный контур.
    Проверяет:
    - отсутствие связи с паспортом;
    - отсутствие критичных полей;
    - противоречие фазности и напряжения;
    - подозрительный тип нагрузки;
    - пустую мощность для ключевых категорий;
    """
    issues: list[dict] = []

    class_map = _build_class_map(classification_report)
    link_map = _build_link_map(entity_links)

    for it in items:
        tag = str(it.get("tag") or "").strip()
        ep_kind = _norm_text(it.get("ep_kind"))
        display_name = it.get("display_name")
        p_kw = _safe_float(it.get("p_kw"))
        u_v = _safe_float(it.get("u_v"))
        phases = it.get("phases")

        cls = class_map.get(tag, {})
        link = link_map.get(tag, {})

        # 1. Нет матча с паспортом для "паспортно-ожидаемых" сущностей
        if ep_kind in {"pump", "fan", "burner"}:
            matched = bool(link.get("matched"))
            if not matched:
                issues.append(
                    {
                        "severity": "medium",
                        "issue_type": "missing_passport_link",
                        "tag": tag,
                        "ep_kind": ep_kind,
                        "description": f"Для {tag} не найдено уверенное сопоставление с паспортом.",
                        "suggested_action": "Проверить тег, модель и соответствующий паспорт/техлист.",
                    }
                )

        # 2. Нет мощности там, где она ожидается
        if ep_kind in {"pump", "fan", "burner", "hvac", "cabinet", "heating"} and p_kw is None:
            issues.append(
                {
                    "severity": "high" if ep_kind in {"pump", "fan", "burner"} else "medium",
                    "issue_type": "missing_power",
                    "tag": tag,
                    "ep_kind": ep_kind,
                    "description": f"У объекта {tag} отсутствует значение p_kw.",
                    "suggested_action": "Заполнить мощность из паспорта, схемы или user_inputs.",
                }
            )

        # 3. Нет напряжения / фазности
        if ep_kind in {"pump", "fan", "burner", "hvac", "cabinet", "heating"}:
            if u_v is None:
                issues.append(
                    {
                        "severity": "medium",
                        "issue_type": "missing_voltage",
                        "tag": tag,
                        "ep_kind": ep_kind,
                        "description": f"У объекта {tag} отсутствует значение u_v.",
                        "suggested_action": "Уточнить напряжение питания.",
                    }
                )

            if phases is None:
                issues.append(
                    {
                        "severity": "low",
                        "issue_type": "missing_phases",
                        "tag": tag,
                        "ep_kind": ep_kind,
                        "description": f"У объекта {tag} отсутствует поле phases.",
                        "suggested_action": "Уточнить число фаз.",
                    }
                )

        # 4. Противоречие фазности и напряжения
        pt = _phase_type(u_v, phases)
        if u_v in (220.0, 230.0) and phases == 3:
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "phase_voltage_conflict",
                    "tag": tag,
                    "ep_kind": ep_kind,
                    "description": f"У объекта {tag} указано 3 фазы при напряжении {u_v:.0f} В.",
                    "suggested_action": "Проверить корректность phases/u_v.",
                }
            )
        if u_v in (380.0, 400.0) and phases == 1:
            issues.append(
                {
                    "severity": "high",
                    "issue_type": "phase_voltage_conflict",
                    "tag": tag,
                    "ep_kind": ep_kind,
                    "description": f"У объекта {tag} указана 1 фаза при напряжении {u_v:.0f} В.",
                    "suggested_action": "Проверить корректность phases/u_v.",
                }
            )

        # 5. Подозрительное сочетание класса и типа нагрузки
        load_type = _norm_text(cls.get("load_type"))
        if ep_kind in {"pump", "fan", "burner"} and load_type not in {"motor", "unknown"}:
            issues.append(
                {
                    "severity": "medium",
                    "issue_type": "unexpected_load_type",
                    "tag": tag,
                    "ep_kind": ep_kind,
                    "description": f"Для {tag} определён нетипичный load_type={load_type}.",
                    "suggested_action": "Проверить classification_report и исходные данные.",
                }
            )

        # 1. Нет матча с паспортом для "паспортно-ожидаемых" сущностей
        # Мягкий fallback:
        # если объект уже несет явные паспортные признаки (model/source_file/i_nom),
        # не считаем отсутствие entity_link критичной проблемой.
        if ep_kind in {"pump", "fan", "burner"}:
            matched = bool(link.get("matched"))

            has_passport_evidence = any(
                [
                    bool(it.get("model")),
                    bool(it.get("source_file")),
                    _safe_float(it.get("i_nom_a")) is not None,
                    _safe_float(it.get("i_a")) is not None,
                ]
            )

            if not matched and not has_passport_evidence:
                issues.append(
                    {
                        "severity": "medium",
                        "issue_type": "missing_passport_link",
                        "tag": tag,
                        "ep_kind": ep_kind,
                        "description": f"Для {tag} не найдено уверенное сопоставление с паспортом.",
                        "suggested_action": "Проверить тег, модель и соответствующий паспорт/техлист.",
                    }
                )
        
        # 7. Cabinet без имени
        if ep_kind == "cabinet" and not display_name:
            issues.append(
                {
                    "severity": "low",
                    "issue_type": "missing_display_name",
                    "tag": tag,
                    "ep_kind": ep_kind,
                    "description": f"У шкафа {tag} отсутствует display_name.",
                    "suggested_action": "Подставить понятное отображаемое имя.",
                }
            )

    return issues