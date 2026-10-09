"""Embedding backends.

`HashEmbedder` is a deterministic, dependency-free TF-IDF feature-hashing embedder so the platform (and
its tests) run offline. `SentenceTransformerEmbedder` gives semantic embeddings when the optional
`embeddings` extra is installed. Both return L2-normalised vectors for cosine search in Qdrant.
"""

from __future__ import annotations

import json
import math
import re
import zlib
from collections import Counter
from pathlib import Path
from typing import Protocol

from opspilot.config import Settings

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    "a an the and or of to in on for with is are was were be been it this that these those i my me we our you your "
    "do does did can could how what when where which who why not no from at by as if so but about into than then "
    "there their they them its get got should would will have has had".split()
)
_SYNONYMS = {
    "wifi": "wireless",
    "wi": "wireless",
    "fi": "wireless",
    "lockout": "locked",
    "unlock": "locked",
    "licence": "license",
    "licences": "license",
    "passcode": "password",
    "passwd": "password",
    "laptop": "laptop",
    "notebook": "laptop",
    "macbook": "laptop",
    "phishing": "phish",
    "authenticator": "mfa",
    "2fa": "mfa",
    "otp": "mfa",
    "sharepoint": "sharepoint",
    "onedrive": "onedrive",
    "reimage": "reset",
    "reissue": "reset",
    "stolen": "lost",
    "missing": "lost",
}


def _stem(tok: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if tok.endswith(suffix) and len(tok) - len(suffix) >= 3:
            return tok[: -len(suffix)]
    return tok


def tokenize(text: str) -> list[str]:
    toks = [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]
    return [_SYNONYMS.get(_stem(t), _stem(t)) for t in toks]


class Embedder(Protocol):
    dim: int

    def fit(self, texts: list[str]) -> None: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    def __init__(self, dim: int = 2048, idf_path: Path | None = None):
        self.dim = dim
        self.idf: dict[str, float] = {}
        self.default_idf = 1.0
        self.idf_path = idf_path
        if idf_path and idf_path.exists():
            data = json.loads(idf_path.read_text())
            self.idf, self.default_idf = data["idf"], data["default"]

    def fit(self, texts: list[str]) -> None:
        df: Counter = Counter()
        for t in texts:
            df.update(set(self._features(t)))
        n = max(len(texts), 1)
        self.idf = {f: math.log((n + 1) / (c + 1)) + 1.0 for f, c in df.items()}
        self.default_idf = math.log(n + 1) + 1.0
        if self.idf_path:
            self.idf_path.parent.mkdir(parents=True, exist_ok=True)
            self.idf_path.write_text(json.dumps({"idf": self.idf, "default": self.default_idf}))

    @staticmethod
    def _features(text: str) -> list[str]:
        toks = tokenize(text)
        return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:], strict=False)]

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for feat, tf in Counter(self._features(text)).items():
            h = zlib.crc32(feat.encode())
            sign = 1.0 if (h >> 31) & 1 else -1.0
            weight = (1 + math.log(tf)) * self.idf.get(feat, self.default_idf)
            if "_" in feat:  # bigrams carry less weight than the words themselves
                weight *= 0.6
            v[h % self.dim] += sign * weight
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class SentenceTransformerEmbedder:
    def __init__(self, model_name: str):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("Install the 'embeddings' extra: pip install '.[embeddings]'") from exc
        self.model = SentenceTransformer(model_name)
        self.dim = int(self.model.get_sentence_embedding_dimension())

    def fit(self, texts: list[str]) -> None:  # nothing to fit
        return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def build_embedder(settings: Settings) -> Embedder:
    if settings.embedding_backend == "sentence-transformers":
        return SentenceTransformerEmbedder(settings.embedding_model)
    idf_path = settings.data_dir / "hash_idf.json" if settings.env != "test" else None
    return HashEmbedder(settings.hash_dim, idf_path)
