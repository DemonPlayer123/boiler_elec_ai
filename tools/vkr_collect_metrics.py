import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def as_list(obj):
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("items", "records", "data", "results", "electrical_receivers", "equipment", "requirements"):
            if isinstance(obj.get(key), list):
                return obj[key]
        return list(obj.values())
    return []


def first_existing(out_dir: Path, names):
    for name in names:
        p = out_dir / name
        if p.exists():
            return p
    return None


def norm(v):
    return str(v or "").strip().lower().replace("ё", "е")


def candidate_identity(c):
    if not isinstance(c, dict):
        return ""
    return "|".join([
        norm(c.get("vendor") or c.get("price_vendor")),
        norm(c.get("series")),
        norm(c.get("model")),
    ])


def has_price(c):
    if not isinstance(c, dict):
        return False
    if c.get("price_found") is True:
        return True
    if c.get("price_rub") not in (None, "", 0):
        return True
    if c.get("price_value") not in (None, ""):
        return True
    return False


def source_kind(c):
    if not isinstance(c, dict):
        return ""
    return norm(
        c.get("price_source_type")
        or c.get("price_source_name")
        or c.get("price_source_domain")
    )


def is_official_source(c):
    s = source_kind(c)
    return any(x in s for x in ["official", "официаль", "keaz", "chint", "dek.ru", "official_site"])


def extract_result(api_obj):
    if not isinstance(api_obj, dict):
        return {}
    if isinstance(api_obj.get("result"), dict):
        return api_obj["result"]
    return api_obj


def fetch_api(tag, base_url, timeout=30):
    encoded = urllib.parse.quote(tag)
    url = f"{base_url.rstrip('/')}/api/tag/{encoded}"
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            raw = r.read().decode("utf-8")
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return json.loads(raw), elapsed_ms, None
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return None, elapsed_ms, str(e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--tags", required=True, help="Comma-separated tags, e.g. К6,К5,ГГ.1")
    ap.add_argument("--base_url", default="http://127.0.0.1:8000")
    ap.add_argument("--use_api", action="store_true", help="Fetch API responses before metrics")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    api_dir = out_dir / "api"
    api_dir.mkdir(parents=True, exist_ok=True)

    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    if args.use_api:
        for tag in tags:
            obj, elapsed_ms, err = fetch_api(tag, args.base_url)
            if obj is not None:
                (api_dir / f"{tag}.json").write_text(
                    json.dumps(obj, ensure_ascii=False, indent=2),
                    encoding="utf-8"
                )
                print(f"[API] {tag}: OK, {elapsed_ms} ms")
            else:
                print(f"[API] {tag}: ERROR, {elapsed_ms} ms, {err}")

    artifact_candidates = {
        "equipment_registry": [
            "equipment_registry.json",
            "registry.json",
            "schemes_registry.json",
            "equipment.json",
        ],
        "passports_parsed": [
            "passports_parsed.json",
            "passport_registry.json",
            "passports.json",
        ],
        "entity_links": [
            "entity_links.json",
            "links.json",
            "entity_resolution.json",
        ],
        "items_final": [
            "items_final.json",
            "final_items.json",
            "items.json",
        ],
        "requirements": [
            "requirements.json",
            "requirements_by_tag.json",
        ],
        "candidates": [
            "candidates.json",
            "shortlist.json",
            "catalog_with_prices_grouped.json",
            "selection_results.json",
        ],
    }

    artifacts = {}
    artifact_counts = {}

    for name, variants in artifact_candidates.items():
        p = first_existing(out_dir, variants)
        if p:
            obj = read_json(p)
            artifacts[name] = str(p)
            artifact_counts[f"{name}_count"] = len(as_list(obj))
        else:
            artifacts[name] = None
            artifact_counts[f"{name}_count"] = None

    by_tag = []
    api_success = 0
    total_response_ms = 0.0
    response_count = 0

    for tag in tags:
        api_file = api_dir / f"{tag}.json"
        api_obj = read_json(api_file) if api_file.exists() else None
        result = extract_result(api_obj)

        ok = bool(isinstance(api_obj, dict) and api_obj.get("ok", True) is not False and result)
        if ok:
            api_success += 1

        candidate = result.get("candidate") if isinstance(result, dict) else {}
        options = result.get("candidate_options") if isinstance(result, dict) else []
        if not isinstance(options, list):
            options = []

        requirement_full = result.get("requirement_full") if isinstance(result, dict) else {}
        normative_hits = result.get("normative_hits") if isinstance(result, dict) else []
        if not isinstance(normative_hits, list):
            normative_hits = []

        explanation_parts = []
        for key in ("llm_readable_explanation", "readable_explanation", "llm_explanation", "why_this_candidate"):
            v = result.get(key) if isinstance(result, dict) else None
            if isinstance(v, str):
                explanation_parts.append(v)

        review_block = result.get("review_block") if isinstance(result, dict) else {}
        if isinstance(review_block, dict):
            for key in ("review_summary", "manual_caution"):
                v = review_block.get(key)
                if isinstance(v, str):
                    explanation_parts.append(v)

        llm_text = "\n".join(explanation_parts)

        top1 = options[0] if options else {}
        candidate_top1_same = bool(
            candidate and top1 and candidate_identity(candidate) == candidate_identity(top1)
        )

        price_found_options = sum(1 for c in options if has_price(c))
        official_price_options = sum(1 for c in options if has_price(c) and is_official_source(c))
        seller_price_options = sum(
            1 for c in options
            if has_price(c) and not is_official_source(c)
        )

        verdict = result.get("verdict") if isinstance(result, dict) else None
        confidence = result.get("confidence") if isinstance(result, dict) else None

        row = {
            "tag": tag,
            "api_ok": ok,
            "has_requirement_full": bool(requirement_full),
            "has_candidate": bool(candidate),
            "candidate_vendor": candidate.get("vendor") if isinstance(candidate, dict) else "",
            "candidate_series": candidate.get("series") if isinstance(candidate, dict) else "",
            "candidate_model": candidate.get("model") if isinstance(candidate, dict) else "",
            "candidate_price_found": has_price(candidate),
            "candidate_price_rub": candidate.get("price_rub") if isinstance(candidate, dict) else "",
            "candidate_price_article": candidate.get("price_article") if isinstance(candidate, dict) else "",
            "candidate_price_source_type": candidate.get("price_source_type") if isinstance(candidate, dict) else "",
            "candidate_options_count": len(options),
            "candidate_top1_same": candidate_top1_same,
            "candidate_options_price_found_count": price_found_options,
            "candidate_options_official_price_count": official_price_options,
            "candidate_options_seller_price_count": seller_price_options,
            "candidate_options_without_price_count": max(len(options) - price_found_options, 0),
            "normative_hits_count": len(normative_hits),
            "has_llm_text": bool(llm_text.strip()),
            "verdict": verdict,
            "confidence": confidence,
            "supported_with_conditions": verdict == "supported_with_conditions",
            "manual_review_mentions": "manual_review" in json.dumps(result, ensure_ascii=False).lower(),
        }

        by_tag.append(row)

    n = len(by_tag) or 1

    summary = {
        "run_id": out_dir.name,
        "out_dir": str(out_dir),
        "tags_checked": len(by_tag),
        "api_success_count": api_success,
        "api_success_rate": round(api_success / n, 4),
        "artifact_files": artifacts,
        "artifact_counts": artifact_counts,
        "tags_with_requirement_full": sum(1 for r in by_tag if r["has_requirement_full"]),
        "tags_with_candidate": sum(1 for r in by_tag if r["has_candidate"]),
        "tags_with_candidate_options": sum(1 for r in by_tag if r["candidate_options_count"] > 0),
        "tags_candidate_top1_same": sum(1 for r in by_tag if r["candidate_top1_same"]),
        "tags_with_normative_hits": sum(1 for r in by_tag if r["normative_hits_count"] > 0),
        "tags_with_llm_text": sum(1 for r in by_tag if r["has_llm_text"]),
        "tags_supported_with_conditions": sum(1 for r in by_tag if r["supported_with_conditions"]),
        "tags_manual_review_mentions": sum(1 for r in by_tag if r["manual_review_mentions"]),
        "top1_price_found_count": sum(1 for r in by_tag if r["candidate_price_found"]),
        "top1_price_found_rate": round(sum(1 for r in by_tag if r["candidate_price_found"]) / n, 4),
        "candidate_options_total": sum(r["candidate_options_count"] for r in by_tag),
        "candidate_options_price_found_total": sum(r["candidate_options_price_found_count"] for r in by_tag),
        "candidate_options_without_price_total": sum(r["candidate_options_without_price_count"] for r in by_tag),
        "candidate_options_official_price_total": sum(r["candidate_options_official_price_count"] for r in by_tag),
        "candidate_options_seller_price_total": sum(r["candidate_options_seller_price_count"] for r in by_tag),
    }

    total_options = summary["candidate_options_total"] or 1
    summary["candidate_options_price_found_rate"] = round(
        summary["candidate_options_price_found_total"] / total_options, 4
    )
    summary["candidate_options_without_price_rate"] = round(
        summary["candidate_options_without_price_total"] / total_options, 4
    )

    (out_dir / "vkr_metrics_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    (out_dir / "vkr_metrics_by_tag.json").write_text(
        json.dumps(by_tag, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

    with (out_dir / "vkr_metrics_summary.csv").open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["metric", "value"])
        for k, v in summary.items():
            if isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            w.writerow([k, v])

    if by_tag:
        with (out_dir / "vkr_metrics_by_tag.csv").open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(by_tag[0].keys()), delimiter=";")
            w.writeheader()
            w.writerows(by_tag)

    print("\nSaved:")
    print(out_dir / "vkr_metrics_summary.json")
    print(out_dir / "vkr_metrics_by_tag.json")
    print(out_dir / "vkr_metrics_summary.csv")
    print(out_dir / "vkr_metrics_by_tag.csv")


if __name__ == "__main__":
    main()
