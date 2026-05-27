from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from sentence_transformers import SentenceTransformer


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _build_embedding_text(row: dict) -> str:
    parts = [
        str(row.get("doc_title") or ""),
        str(row.get("section_hint") or ""),
        str(row.get("text") or ""),
    ]
    body = "\n".join(x for x in parts if x).strip()
    return f"search_document: {body}"


def _chunk_point_id(i: int) -> int:
    return i + 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True, help="Путь к normative_corpus.json")
    ap.add_argument("--qdrant_path", required=True, help="Локальная папка Qdrant store")
    ap.add_argument("--collection", default="normative_chunks", help="Имя коллекции")
    ap.add_argument(
        "--model_name",
        default="models/Frida",
        help="Embedding model name or local path",
    )
    ap.add_argument("--batch_size", type=int, default=32)
    args = ap.parse_args()

    corpus = _load_json(Path(args.corpus))
    texts = [_build_embedding_text(row) for row in corpus]

    model = SentenceTransformer(str(args.model_name).strip())

    if not texts:
        raise ValueError("Корпус нормативки пустой, нечего индексировать.")

    first_batch_texts = texts[: min(len(texts), args.batch_size)]
    first_vectors = model.encode(
        first_batch_texts,
        batch_size=args.batch_size,
        show_progress_bar=False,
        normalize_embeddings=True,
    )

    first_vec = first_vectors[0]
    dim = len(first_vec)

    print(f"first_batch_size: {len(first_batch_texts)}")
    print(f"first_vector_dim: {dim}")

    client = QdrantClient(path=args.qdrant_path)

    client.recreate_collection(
        collection_name=args.collection,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )

    points: list[PointStruct] = []
    for start in range(0, len(corpus), args.batch_size):
        batch_rows = corpus[start : start + args.batch_size]
        batch_texts = texts[start : start + args.batch_size]

        vectors = model.encode(
            batch_texts,
            batch_size=args.batch_size,
            show_progress_bar=False,
            normalize_embeddings=True,
        )

        current_dim = len(vectors[0])
        if current_dim != dim:
            raise ValueError(
                f"Несовпадение размерности эмбеддингов: collection_dim={dim}, batch_dim={current_dim}, "
                f"batch_start={start}"
            )

        for idx, (row, vec) in enumerate(zip(batch_rows, vectors), start=start):
            payload = {
                "chunk_id": row.get("chunk_id"),
                "doc_title": row.get("doc_title"),
                "source_file": row.get("source_file"),
                "section_hint": row.get("section_hint"),
                "text": row.get("text"),
            }
            points.append(
                PointStruct(
                    id=_chunk_point_id(idx),
                    vector=vec.tolist(),
                    payload=payload,
                )
            )

        client.upsert(collection_name=args.collection, points=points)
        points.clear()

        print(f"uploaded: {min(start + args.batch_size, len(corpus))}/{len(corpus)}")

    print(f"qdrant_collection: {args.collection}")
    print(f"qdrant_points: {len(corpus)}")
    print(f"embedding_dim: {dim}")


if __name__ == "__main__":
    main()