from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.pipeline.run_pipeline import run_pipeline  # оставляем совместимость


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project_code", default="25-05")
    ap.add_argument("--schemes_dir", required=True)
    ap.add_argument("--passports_dir", required=True)
    ap.add_argument("--template_xlsx", required=True)
    ap.add_argument("--out_dir", required=True)
    ap.add_argument("--norms_dir", required=False)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = run_pipeline(
        schemes_dir=Path(args.schemes_dir),
        passports_dir=Path(args.passports_dir),
        template_xlsx=Path(args.template_xlsx),
        out_dir=out_dir,
        norms_dir=Path(args.norms_dir) if args.norms_dir else None,
        project_code=args.project_code,
    )

    (out_dir / "run_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    print(f"OK: {out_dir}")


if __name__ == "__main__":
    main()
