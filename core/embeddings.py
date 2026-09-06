"""
embeddings.py — text embeddings via an OpenAI-compatible API.

Defaults to OpenRouter (reusing OPENROUTER_API_KEY) so embeddings align with the
review model. Everything is overridable:

  EMBEDDINGS_BASE_URL  default falls back to configured LLM base URL
  EMBEDDINGS_API_KEY   default falls back to configured LLM API key
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
_client_config = None


def _resolve_client_config():
    import config_store

    base = (
        os.getenv("EMBEDDINGS_BASE_URL")
        or config_store.get_llm_base_url()
        or DEFAULT_BASE_URL
    ).strip().rstrip("/")
    key = os.getenv("EMBEDDINGS_API_KEY") or config_store.get_llm_api_key() or None
    referer = os.getenv("OPENROUTER_APP_URL", "http://localhost:1500")
    return base, key, referer


def _get_client():
    global _client, _client_config
    client_config = _resolve_client_config()
    if _client is None or _client_config != client_config:
        from openai import OpenAI

        base, key, referer = client_config
        _client = OpenAI(
            base_url=base,
            api_key=key,
            default_headers={
                "HTTP-Referer": referer,
                "X-Title": "ReviewBot",
            },
            # Context retrieval must never hang the whole review: fail fast and
            # let _retrieve_context() fall back to an empty context.
            timeout=30,
            max_retries=0,
        )
        _client_config = client_config
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
