from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.engine.normative_retriever import (
    load_normative_corpus,
    retrieve_normative_chunks,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="Путь к normative_corpus.json")
    ap.add_argument("--query", required=True, help="Поисковый запрос")
    ap.add_argument("--top_k", type=int, default=5)
    ap.add_argument("--out_json", default="", help="Куда сохранить результаты")
    args = ap.parse_args()

    corpus = load_normative_corpus(Path(args.corpus))
    hits = retrieve_normative_chunks(args.query, corpus, top_k=args.top_k)

    print(f"hits: {len(hits)}")
    for i, h in enumerate(hits, start=1):
        print(f"\n[{i}] {h['doc_title']} :: {h['section_hint']}")
        print(f"score={h['score']}")
        print(h["text"][:700])

    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps(hits, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nsaved: {args.out_json}")


if __name__ == "__main__":
    main()