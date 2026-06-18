"""
embeddings.py — text embeddings via an OpenAI-compatible API.

Defaults to OpenRouter (reusing OPENROUTER_API_KEY) so embeddings align with the
review model. Everything is overridable:

  EMBEDDINGS_BASE_URL  default https://openrouter.ai/api/v1
  EMBEDDINGS_API_KEY   default falls back to OPENROUTER_API_KEY
  EMBEDDING_MODEL      configurable in Settings (config_store), else env, else default
  EMBEDDING_DIM        vector dimension; MUST match the pgvector column (default 1536)

If OpenRouter doesn't serve the chosen embeddings model, point EMBEDDINGS_BASE_URL
+ EMBEDDINGS_API_KEY at a provider that does (e.g. OpenAI).
"""

import os

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/text-embedding-3-small"
DEFAULT_DIM = 1536

_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        base = os.getenv("EMBEDDINGS_BASE_URL") or DEFAULT_BASE_URL
        key = os.getenv("EMBEDDINGS_API_KEY") or os.getenv("OPENROUTER_API_KEY")
        _client = OpenAI(
            base_url=base,
            api_key=key,
            default_headers={
                "HTTP-Referer": os.getenv("OPENROUTER_APP_URL", "http://localhost:1500"),
                "X-Title": "ReviewBot",
            },
        )
    return _client


def get_model() -> str:
    # Resolve through config_store so the embedding model is UI-configurable
    # alongside the review model/provider.
    import config_store

    return config_store.get_embedding_model()


def get_dim() -> int:
    return int(os.getenv("EMBEDDING_DIM") or DEFAULT_DIM)


def embed(texts) -> list:
    """Return one embedding vector (list[float]) per input text."""
    resp = _get_client().embeddings.create(model=get_model(), input=list(texts))
    return [item.embedding for item in resp.data]
