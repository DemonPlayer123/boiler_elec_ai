from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer


@dataclass
class QdrantHit:
    row: dict
    score: float


class NormativeQdrantStore:
    def __init__(
        self,
        client: QdrantClient,
        collection_name: str,
        model: SentenceTransformer,
    ):
        self.client = client
        self.collection_name = collection_name
        self.model = model

    @classmethod
    def load(
        cls,
        qdrant_path: str | Path,
        collection_name: str,
        model_name: str = "models/Frida",
    ) -> "NormativeQdrantStore":
        client = QdrantClient(path=str(qdrant_path))
        model = SentenceTransformer(str(model_name).strip())
        return cls(client=client, collection_name=collection_name, model=model)

    def search(self, query: str, top_k: int = 10) -> list[QdrantHit]:
        vector = self.model.encode(
            [f"search_query: {query}"],
            normalize_embeddings=True
        )[0].tolist()

        result = self.client.query_points(
            collection_name=self.collection_name,
            query=vector,
            limit=top_k,
            with_payload=True,
        )

        # В разных версиях клиента результат может лежать либо в .points, либо уже быть списком
        points = getattr(result, "points", result)

        out: list[QdrantHit] = []
        for h in points:
            payload = dict(getattr(h, "payload", None) or {})
            score = float(getattr(h, "score", 0.0))
            out.append(QdrantHit(row=payload, score=score))
        return out