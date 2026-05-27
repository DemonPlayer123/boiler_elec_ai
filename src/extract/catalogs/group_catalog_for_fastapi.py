from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_option(row: dict, rank: int) -> dict:
    cand = dict(row.get("candidate") or {})

    option = {
        **cand,
        "rank": rank,
        "selector_score": row.get("selector_score"),
        "selector_reasons": row.get("selector_reasons") or [],
        # price fields
        "price_found": row.get("price_found", cand.get("price_found")),
        "price_match_type": row.get("price_match_type", cand.get("price_match_type")),
        "price_vendor": row.get("price_vendor", cand.get("price_vendor")),
        "price_article": row.get("price_article", cand.get("price_article")),
        "price_title": row.get("price_title", cand.get("price_title")),
        "price_rub": row.get("price_rub", cand.get("price_rub")),
        "price_currency": row.get("price_currency", cand.get("price_currency")),
        "price_source_domain": row.get("price_source_domain", cand.get("price_source_domain")),
        "price_source_type": row.get("price_source_type", cand.get("price_source_type")),
        "price_product_url": row.get("price_product_url", cand.get("price_product_url")),
        "price_match_key": row.get("price_match_key", cand.get("price_match_key")),
        "price_designation": row.get("price_designation", cand.get("price_designation")),
    }

    return option


def _build_best_candidate(row: dict) -> dict:
    cand = dict(row.get("candidate") or {})
    cand.update(
        {
            "price_found": row.get("price_found", cand.get("price_found")),
            "price_match_type": row.get("price_match_type", cand.get("price_match_type")),
            "price_vendor": row.get("price_vendor", cand.get("price_vendor")),
            "price_article": row.get("price_article", cand.get("price_article")),
            "price_title": row.get("price_title", cand.get("price_title")),
            "price_rub": row.get("price_rub", cand.get("price_rub")),
            "price_currency": row.get("price_currency", cand.get("price_currency")),
            "price_source_domain": row.get("price_source_domain", cand.get("price_source_domain")),
            "price_source_type": row.get("price_source_type", cand.get("price_source_type")),
            "price_product_url": row.get("price_product_url", cand.get("price_product_url")),
            "price_match_key": row.get("price_match_key", cand.get("price_match_key")),
            "price_designation": row.get("price_designation", cand.get("price_designation")),
        }
    )
    return cand


def group_catalog_rows(rows: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}

    for row in rows:
        tag = str(row.get("tag") or "").strip()
        if not tag:
            continue
        grouped.setdefault(tag, []).append(row)

    out: list[dict] = []

    for tag, tag_rows in grouped.items():
        sorted_rows = sorted(
            tag_rows,
            key=lambda x: _as_float(x.get("selector_score"), default=-1e9),
            reverse=True,
        )

        best_row = sorted_rows[0]
        best_candidate = _build_best_candidate(best_row)

        candidate_options = []
        for idx, row in enumerate(sorted_rows, start=1):
            candidate_options.append(_build_option(row, rank=idx))

        payload = {
            "tag": tag,
            "requirement_ref": best_row.get("requirement_ref"),
            "candidate": best_candidate,
            "candidate_options": candidate_options,
            "selector_score": best_row.get("selector_score"),
            "selector_reasons": best_row.get("selector_reasons") or [],
        }

        out.append(payload)

    out.sort(key=lambda x: str(x.get("tag") or ""))
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Путь к плоскому catalog_with_prices.json")
    parser.add_argument("--out", required=True, help="Куда сохранить grouped JSON")
    args = parser.parse_args()

    rows = _load_json(Path(args.input))
    if not isinstance(rows, list):
        raise ValueError("Input JSON must be a list of rows.")

    grouped = group_catalog_rows(rows)
    _save_json(Path(args.out), grouped)

    print(f"input rows: {len(rows)}")
    print(f"grouped tags: {len(grouped)}")


if __name__ == "__main__":
    main()