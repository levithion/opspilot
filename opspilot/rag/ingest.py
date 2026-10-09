"""CLI / helpers to build the knowledge base index."""

from __future__ import annotations

import argparse
import logging

from opspilot.config import get_settings
from opspilot.rag.embeddings import build_embedder
from opspilot.rag.store import KnowledgeStore


def build_store(settings=None) -> KnowledgeStore:
    settings = settings or get_settings()
    store = KnowledgeStore(settings, build_embedder(settings))
    store.ingest()
    return store


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest the markdown knowledge base into Qdrant")
    parser.add_argument("--kb-dir", default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    if args.kb_dir:
        settings.kb_dir = type(settings.kb_dir)(args.kb_dir)
    store = KnowledgeStore(settings, build_embedder(settings))
    n = store.ingest()
    print(f"Ingested {n} chunks from {settings.kb_dir} into collection '{settings.qdrant_collection}'")
    if not settings.qdrant_url:
        print("Note: no OPSPILOT_QDRANT_URL set, so the index lived in memory and was discarded.")


if __name__ == "__main__":
    main()
