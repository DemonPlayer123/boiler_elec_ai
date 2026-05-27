import json
from pathlib import Path


INPUT_FILE = Path("data/output/runs/25-05/api_K6.json")
OUTPUT_FILE = Path("data/output/runs/25-05/api_K6_short.json")


def main() -> None:
    with INPUT_FILE.open("r", encoding="utf-8") as f:
        data = json.load(f)

    result = data["result"]
    candidate = result["candidate"]
    first_option = result["candidate_options"][0]

    short_data = {
        "ok": data["ok"],
        "tag": data["tag"],
        "candidate": {
            "vendor": candidate.get("vendor"),
            "series": candidate.get("series"),
            "model": candidate.get("model"),
            "device_class": candidate.get("device_class"),
            "rated_current_a": candidate.get("rated_current_a"),
            "current_range_a": candidate.get("current_range_a"),
            "poles": candidate.get("poles"),
            "trip_curve": candidate.get("trip_curve"),
            "breaking_capacity_ka": candidate.get("breaking_capacity_ka"),
            "price_rub": candidate.get("price_rub"),
            "price_article": candidate.get("price_article"),
        },
        "verdict": result.get("verdict"),
        "confidence": result.get("confidence"),
        "candidate_options[0]": {
            "rank": first_option.get("rank"),
            "vendor": first_option.get("vendor"),
            "series": first_option.get("series"),
            "model": first_option.get("model"),
            "device_class": first_option.get("device_class"),
            "rated_current_a": first_option.get("rated_current_a"),
            "poles": first_option.get("poles"),
            "trip_curve": first_option.get("trip_curve"),
            "breaking_capacity_ka": first_option.get("breaking_capacity_ka"),
            "price_rub": first_option.get("price_rub"),
            "price_article": first_option.get("price_article"),
            "verdict": first_option.get("verdict"),
            "confidence": first_option.get("confidence"),
        },
    }

    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        json.dump(short_data, f, ensure_ascii=False, indent=2)

    print(f"Готово: {OUTPUT_FILE.resolve()}")


if __name__ == "__main__":
    main()