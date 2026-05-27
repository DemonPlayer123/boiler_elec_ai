from __future__ import annotations

from typing import Any


def _norm(s: str) -> str:
    return (s or "").lower().replace("ё", "е")


def _pick_best_evidence(hits: list[dict], max_items: int = 3) -> list[dict]:
    out = []

    def weak(h: dict) -> bool:
        txt = ((h.get("section_hint") or "") + " " + (h.get("text") or "")[:1400]).lower()

        bad = [
            "термины и определения",
            "рисунок",
            "таблица",
            "приложение а",
            "приложение в",
            "приложение д",
            "библиография",
            "испытание импульсным током",
            "испытательная цепь",
            "электротехническая библиотека",
            "окончание таблицы",
            "продолжение таблицы",
        ]
        return any(x in txt for x in bad)

    def strong(h: dict) -> bool:
        txt = ((h.get("section_hint") or "") + " " + (h.get("text") or "")[:1400]).lower()
        good = [
            "защита от тока перегрузки",
            "защита от тока короткого замыкания",
            "дополнительную защиту посредством устройства дифференциального тока",
            "номинальным отключающим дифференциальным током до 30 ма",
            "пускатель",
            "контактор",
            "электродвигател",
            "автоматические выключатели с комбинированными расцепителями",
            "устройства, обеспечивающие защиту",
        ]
        return any(x in txt for x in good)

    filtered_strong = [h for h in hits if not weak(h) and strong(h)]
    filtered_soft = [h for h in hits if not weak(h)]

    source = filtered_strong if filtered_strong else (filtered_soft if filtered_soft else hits)

    for h in source[:max_items]:
        text = (h.get("text") or "").strip()
        out.append(
            {
                "doc_title": h.get("doc_title"),
                "source_file": h.get("source_file"),
                "chunk_id": h.get("chunk_id"),
                "section_hint": h.get("section_hint"),
                "score": h.get("score"),
                "excerpt": text[:700],
            }
        )
    return out


def _make_summary(req_ref: dict, candidate: dict, hits: list[dict]) -> tuple[str, list[str], float]:
    req_cls = str(req_ref.get("device_class") or "").upper()

    evidence = _pick_best_evidence(hits, max_items=3)
    all_text = " ".join((h.get("excerpt") or h.get("text") or "")[:1200] for h in evidence)
    t = _norm(all_text)

    bullets: list[str] = []
    confidence = 0.5

    if req_cls == "RCBO":
        if "30 ма" in t or "30 мa" in t or "удт" in t or "дифференциаль" in t:
            bullets.append("Найдены нормативные основания для дополнительной защиты посредством УДТ/АВДТ с током отключения до 30 мА.")
            bullets.append("Выбранный кандидат относится к RCBO и соответствует логике дополнительной защиты конечной цепи.")
            confidence = 0.9
            verdict = "supported"
        else:
            bullets.append("Найдены общие нормативные материалы по RCBO/АВДТ, но прямое подтверждение именно для 30 мА выражено слабо.")
            confidence = 0.65 if hits else 0.2
            verdict = "supported_with_conditions" if hits else "weak_evidence"
        return verdict, bullets, confidence

    if req_cls == "MPCB":
        if "пускател" in t or "электродвигател" in t or "контактор" in t:
            bullets.append("Найдены нормативные фрагменты по пускателям и защите электродвигателей от перегрузки и короткого замыкания.")
            bullets.append("Выбранный кандидат относится к моторной ветке защиты и согласуется с нормативной логикой для двигательной нагрузки.")
            bullets.append("Требуется инженерная проверка координации аппарата с конкретной схемой и пусковыми условиями.")
            confidence = 0.88
            verdict = "supported_with_conditions"
        else:
            bullets.append("Найдены лишь косвенные нормативные материалы по автоматическим выключателям без сильного моторного обоснования.")
            confidence = 0.5 if hits else 0.2
            verdict = "weak_evidence" if hits else "no_evidence_found"
        return verdict, bullets, confidence
    
    if req_cls == "MCCB":
        if "аппаратом защиты" in t or "автоматические выключатели с комбинированными расцепителями" in t or "токи уставок автоматических выключателей" in t:
            bullets.append("Найдены нормативные основания по выбору аппарата защиты для линии с учетом расчетного тока и отключающей способности.")
            bullets.append("Выбранный кандидат относится к классу MCCB и соответствует логике силовой защиты трехфазной нагрузки.")
            bullets.append("Требуется инженерная проверка уставок, ожидаемого тока КЗ и координации с вышестоящей защитой.")
            confidence = 0.8
            verdict = "supported_with_conditions"
        else:
            bullets.append("Найдены только общие нормативные материалы по аппаратам защиты без сильного прямого обоснования именно для MCCB.")
            confidence = 0.55 if hits else 0.2
            verdict = "supported_with_conditions" if hits else "no_evidence_found"
        return verdict, bullets, confidence

    if req_cls == "MCB":
        if "сверхток" in t or "перегрузк" in t or "короткого замыкания" in t:
            bullets.append("Найдены нормативные основания по защите линии от перегрузки и короткого замыкания.")
            bullets.append("Выбранный автоматический выключатель соответствует общей логике защиты линии по току.")
            if str(req_ref.get("trip_curve") or "").upper() == "D":
                bullets.append("Для нагрузки с повышенными пусковыми токами требуется отдельная инженерная проверка корректности применения характеристики D.")
                verdict = "supported_with_conditions"
                confidence = 0.78
            else:
                verdict = "supported"
                confidence = 0.82
        else:
            bullets.append("Найдены общие материалы по выключателям, но прямое нормативное основание по защите линии выражено слабо.")
            verdict = "supported_with_conditions" if hits else "no_evidence_found"
            confidence = 0.55 if hits else 0.2
        return verdict, bullets, confidence

    bullets.append("Найдены только общие нормативные материалы.")
    return ("supported_with_conditions" if hits else "no_evidence_found"), bullets, (0.5 if hits else 0.2)

def _make_readable_explanation(row: dict, verdict: str, bullets: list[str], confidence: float) -> str:
    req_ref = row.get("requirement_ref") or {}
    candidate = row.get("candidate") or {}
    req_full = row.get("requirement_full") or {}

    tag = row.get("tag") or ""
    display_name = req_full.get("display_name") or tag
    device_class = req_ref.get("device_class") or candidate.get("device_class") or "UNKNOWN"
    model = candidate.get("model") or "не определён"
    series = candidate.get("series") or ""
    vendor = candidate.get("vendor") or ""
    current = candidate.get("rated_current_a")
    poles = candidate.get("poles")
    trip_curve = candidate.get("trip_curve") or req_ref.get("trip_curve")
    breaking = candidate.get("breaking_capacity_ka")

    candidate_text = " ".join(str(x) for x in [vendor, series, model] if x).strip()

    base = (
        f"Для тега {tag}"
        f"{' (' + str(display_name) + ')' if display_name and display_name != tag else ''} "
        f"лучшим кандидатом выбран аппарат {candidate_text}."
    )

    tech_parts = []
    if device_class:
        tech_parts.append(f"Класс аппарата: {device_class}")
    if current is not None:
        tech_parts.append(f"Номинальный ток: {current} А")
    if poles is not None:
        tech_parts.append(f"Число полюсов: {poles}")
    if trip_curve:
        tech_parts.append(f"Характеристика: {trip_curve}")
    if breaking is not None:
        tech_parts.append(f"Отключающая способность: {breaking} кА")

    verdict_map = {
        "supported": "Нормативная база в целом подтверждает применимость решения.",
        "supported_with_conditions": "Нормативная база в целом поддерживает решение, но требуется инженерная проверка условий применения.",
        "weak_evidence": "Найдены только частичные нормативные основания, решение требует дополнительной проверки.",
        "no_evidence_found": "Достаточные нормативные основания автоматически не найдены.",
    }
    verdict_text = verdict_map.get(verdict, f"Статус проверки: {verdict}.")

    why_this = str(row.get("why_this_candidate") or "").strip()

    # Короткая сравнительная ремарка по альтернативам
    comparison_text = ""
    opts = row.get("candidate_options") or []
    alternative_notes = []
    if isinstance(opts, list):
        for opt in opts:
            if int(opt.get("rank") or 0) <= 1:
                continue
            model_alt = str(opt.get("model") or "").strip()
            why_not_best = str(opt.get("why_not_best") or "").strip()
            if model_alt and why_not_best:
                alternative_notes.append(f"{model_alt} — {why_not_best}")
            if len(alternative_notes) >= 2:
                break

    if alternative_notes:
        comparison_text = " Альтернативы: " + " | ".join(alternative_notes) + "."

    bullets_text = " ".join(bullets) if bullets else ""
    conf_text = f"Оценка уверенности: {round(confidence * 100, 1)}%."

    details_text = ""
    if tech_parts:
        details_text = " " + ". ".join(tech_parts) + "."

    why_text = f" {why_this}" if why_this else ""

    return f"{base}{details_text} {verdict_text}{why_text} {bullets_text}{comparison_text} {conf_text}".strip()

def _build_engineering_checks(row: dict) -> list[dict]:
    req_ref = row.get("requirement_ref") or {}
    req_full = row.get("requirement_full") or {}
    candidate = row.get("candidate") or {}
    evidence = row.get("evidence_top") or []

    checks: list[dict] = []

    device_class = str(req_ref.get("device_class") or "").upper()
    trip_curve = str(req_ref.get("trip_curve") or candidate.get("trip_curve") or "").upper()
    selection_current = req_full.get("selection_current_a")
    est_current = req_full.get("estimated_current_a")
    rated_current = candidate.get("rated_current_a")
    poles_req = req_ref.get("poles")
    poles_cand = candidate.get("poles")
    breaking_req = req_ref.get("breaking_capacity_ka")
    breaking_cand = candidate.get("breaking_capacity_ka")
    rcd_ma = candidate.get("rcd_ma")

    def ev(i: int) -> dict | None:
        return evidence[i] if i < len(evidence) else None

    if device_class == "MCB":
        checks.append({
            "title": "Проверка номинального тока",
            "status": "ok" if (rated_current is not None and selection_current is not None and rated_current >= selection_current) else "manual_review",
            "details": f"Сравнить номинал автомата ({rated_current} А) с расчётным током выбора ({selection_current} А).",
            "reference": ev(0),
        })
        checks.append({
            "title": "Проверка отключающей способности",
            "status": "ok" if (breaking_cand is not None and breaking_req is not None and breaking_cand >= breaking_req) else "manual_review",
            "details": f"Проверить, что отключающая способность аппарата ({breaking_cand} кА) не ниже требуемой ({breaking_req} кА).",
            "reference": ev(0),
        })
        if trip_curve == "D":
            checks.append({
                "title": "Проверка пусковых токов",
                "status": "manual_review",
                "details": "Для характеристики D необходимо проверить соответствие пусковых токов нагрузки и отсутствие ложных срабатываний при пуске.",
                "reference": ev(1) or ev(0),
            })
        checks.append({
            "title": "Проверка числа полюсов",
            "status": "ok" if (poles_req is not None and poles_cand == poles_req) else "manual_review",
            "details": f"Требуется {poles_req}P, у кандидата {poles_cand}P.",
            "reference": ev(0),
        })

    elif device_class == "MPCB":
        checks.append({
            "title": "Проверка диапазона уставки",
            "status": "manual_review",
            "details": f"Проверить, что рабочий ток двигателя ({est_current} А) попадает в диапазон уставки аппарата {candidate.get('model')}.",
            "reference": ev(0),
        })
        checks.append({
            "title": "Проверка защиты от короткого замыкания",
            "status": "manual_review",
            "details": "Уточнить, требуется ли отдельная защита от КЗ в составе схемы или она обеспечивается выбранной комбинацией аппаратов.",
            "reference": ev(1) or ev(0),
        })
        checks.append({
            "title": "Проверка координации с пускателем/контактором",
            "status": "manual_review",
            "details": "Проверить координацию аппарата защиты с пускателем, контактором и режимом пуска двигателя.",
            "reference": ev(1) or ev(0),
        })
        checks.append({
            "title": "Проверка числа полюсов",
            "status": "ok" if (poles_req is not None and poles_cand == poles_req) else "manual_review",
            "details": f"Требуется {poles_req}P, у кандидата {poles_cand}P.",
            "reference": ev(0),
        })
        
    elif device_class == "MCCB":
        checks.append({
            "title": "Проверка номинального тока",
            "status": "ok" if (rated_current is not None and selection_current is not None and rated_current >= selection_current) else "manual_review",
            "details": f"Сравнить номинал MCCB ({rated_current} А) с расчётным током выбора ({selection_current} А).",
            "reference": ev(0),
        })
        checks.append({
            "title": "Проверка отключающей способности",
            "status": "ok" if (breaking_cand is not None and breaking_req is not None and breaking_cand >= breaking_req) else "manual_review",
            "details": f"Проверить, что отключающая способность MCCB ({breaking_cand} кА) не ниже требуемой ({breaking_req} кА).",
            "reference": ev(1) or ev(0),
        })
        checks.append({
            "title": "Проверка координации с вышестоящей защитой",
            "status": "manual_review",
            "details": "Проверить селективность и координацию MCCB с вышестоящим аппаратом защиты и ожидаемым током короткого замыкания в точке установки.",
            "reference": ev(1) or ev(0),
        })
        checks.append({
            "title": "Проверка числа полюсов",
            "status": "ok" if (poles_req is not None and poles_cand == poles_req) else "manual_review",
            "details": f"Требуется {poles_req}P, у кандидата {poles_cand}P.",
            "reference": ev(0),
        })

    elif device_class == "RCBO":
        checks.append({
            "title": "Проверка дополнительной защиты УДТ",
            "status": "ok" if rcd_ma == 30 else "manual_review",
            "details": f"Проверить соответствие тока утечки требованиям конечной цепи. У кандидата: {rcd_ma} мА.",
            "reference": ev(0),
        })
        checks.append({
            "title": "Проверка номинального тока линии",
            "status": "ok" if (rated_current is not None and selection_current is not None and rated_current >= selection_current) else "manual_review",
            "details": f"Сравнить номинал RCBO ({rated_current} А) с расчётным током выбора ({selection_current} А).",
            "reference": ev(0),
        })
        checks.append({
            "title": "Проверка отключающей способности",
            "status": "ok" if (breaking_cand is not None and breaking_req is not None and breaking_cand >= breaking_req) else "manual_review",
            "details": f"Проверить, что отключающая способность RCBO ({breaking_cand} кА) не ниже требуемой ({breaking_req} кА).",
            "reference": ev(1) or ev(0),
        })

    return checks

def _build_why_this_candidate(row: dict) -> str:
    req_ref = row.get("requirement_ref") or {}
    req_full = row.get("requirement_full") or {}
    candidate = row.get("candidate") or {}

    tag = row.get("tag") or ""
    device_class = str(req_ref.get("device_class") or "").upper()
    selection_current = req_full.get("selection_current_a")
    required_nominal = req_ref.get("suggested_nominal_a")
    rated_current = candidate.get("rated_current_a")
    trip_curve = candidate.get("trip_curve") or req_ref.get("trip_curve")
    required_curve = req_ref.get("trip_curve")
    breaking = candidate.get("breaking_capacity_ka")
    required_breaking = req_ref.get("breaking_capacity_ka")
    poles = candidate.get("poles")
    required_poles = req_ref.get("poles")
    model = candidate.get("model") or "не определён"

    parts = [f"Для тега {tag} выбран кандидат {model}."]

    if device_class:
        parts.append(f"Он соответствует требуемому классу аппарата {device_class}.")

    if required_nominal is not None and rated_current is not None:
        parts.append(
            f"Номинал кандидата {rated_current} А покрывает требуемый уровень {required_nominal} А."
        )
    elif rated_current is not None and selection_current is not None:
        parts.append(
            f"Номинал кандидата {rated_current} А сопоставлен с расчётным током выбора {selection_current} А."
        )

    if required_poles is not None and poles is not None:
        parts.append(f"Число полюсов кандидата ({poles}) соответствует требованию ({required_poles}).")

    if required_curve:
        parts.append(f"Требуемая характеристика расцепителя: {required_curve}. У кандидата: {trip_curve}.")
    elif trip_curve:
        parts.append(f"У кандидата используется характеристика {trip_curve}.")

    if breaking is not None and required_breaking is not None:
        parts.append(
            f"Отключающая способность кандидата {breaking} кА покрывает требуемые {required_breaking} кА."
        )

    if device_class == "MPCB":
        parts.append("Кандидат выбран как наиболее близкий к логике моторной защиты и дальнейшей координации с пусковой аппаратурой.")
    elif device_class == "MCCB":
        parts.append("Кандидат выбран как ближайший силовой автомат без лишнего переразмера по току и с достаточной отключающей способностью для данной нагрузки.")
    elif device_class == "RCBO":
        parts.append("Кандидат выбран как аппарат, одновременно обеспечивающий защиту от сверхтока и дифференциальную защиту линии.")
    elif device_class == "MCB":
        parts.append("Кандидат выбран как ближайший по номиналу и параметрам линии без лишнего технического переразмера.")

    return " ".join(parts)

def _build_normative_refs(row: dict) -> list[dict]:
    refs = []
    seen = set()

    for item in (row.get("evidence_top") or []):
        doc_title = str(item.get("doc_title") or "").strip()
        section_hint = str(item.get("section_hint") or "").strip()
        source_file = str(item.get("source_file") or "").strip()
        chunk_id = str(item.get("chunk_id") or "").strip()

        key = (doc_title, section_hint)
        if not doc_title or key in seen:
            continue
        seen.add(key)

        refs.append(
            {
                "doc_title": doc_title,
                "section_hint": section_hint,
                "source_file": source_file,
                "chunk_id": chunk_id,
            }
        )

    return refs


def _build_candidate_gap(best_row: dict, opt: dict) -> str:
    best = best_row.get("candidate") or {}
    req_ref = best_row.get("requirement_ref") or {}

    best_current = best.get("rated_current_a")
    opt_current = opt.get("rated_current_a")
    req_current = req_ref.get("suggested_nominal_a")

    best_poles = best.get("poles")
    opt_poles = opt.get("poles")
    req_poles = req_ref.get("poles")

    best_break = best.get("breaking_capacity_ka")
    opt_break = opt.get("breaking_capacity_ka")
    req_break = req_ref.get("breaking_capacity_ka")

    best_conf = float(best_row.get("confidence") or 0.0)
    opt_conf = float(opt.get("confidence") or 0.0)

    reasons = []

    # 1. Номинал ближе к требуемому
    if req_current is not None and best_current is not None and opt_current is not None:
        best_gap = abs(float(best_current) - float(req_current))
        opt_gap = abs(float(opt_current) - float(req_current))
        if opt_gap > best_gap:
            reasons.append("хуже по близости номинала к требуемому току")

    # 2. Полюса
    if req_poles is not None and best_poles is not None and opt_poles is not None:
        best_ok = int(best_poles == req_poles)
        opt_ok = int(opt_poles == req_poles)
        if opt_ok < best_ok:
            reasons.append("хуже по числу полюсов")

    # 3. Отключающая способность
    if req_break is not None and best_break is not None and opt_break is not None:
        best_margin = max(float(best_break) - float(req_break), 0.0)
        opt_margin = max(float(opt_break) - float(req_break), 0.0)

        best_ok = float(best_break) >= float(req_break)
        opt_ok = float(opt_break) >= float(req_break)

        if best_ok and not opt_ok:
            reasons.append("не покрывает требуемую отключающую способность")
        elif best_ok and opt_ok and opt_margin > best_margin:
            reasons.append("избыточен по отключающей способности относительно лучшего кандидата")

    # 4. Нормативная уверенность
    if opt_conf < best_conf - 0.05:
        reasons.append("ниже нормативная уверенность по результатам review")

    if not reasons:
        return "альтернатива допустима, но уступает лучшему кандидату по суммарному техническому и нормативному ранжированию"
    return "; ".join(reasons)

def finalize_normative_row(row: dict) -> dict:
    req_ref = row.get("requirement_ref") or {}
    candidate = row.get("candidate") or {}
    hits = row.get("normative_hits") or []

    verdict, bullets, confidence = _make_summary(req_ref, candidate, hits)
    readable_explanation = _make_readable_explanation(row, verdict, bullets, confidence)
    evidence_top = _pick_best_evidence(hits, max_items=3)
    row_with_evidence = {**row, "evidence_top": evidence_top}

    return {
        **row,
        "verdict": verdict,
        "summary_bullets": bullets,
        "readable_explanation": readable_explanation,
        "evidence_top": evidence_top,
        "confidence": round(confidence, 3),
        "engineering_checks": _build_engineering_checks(row_with_evidence),
        "why_this_candidate": _build_why_this_candidate(row),
        "normative_refs": _build_normative_refs(row_with_evidence),
    }