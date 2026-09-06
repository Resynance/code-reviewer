"""Tests for core/embeddings.py — the OpenAI client is faked (no network)."""

import sys
import types

import embeddings


def fake_openai(monkeypatch):
    created = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            created.append(self)
            self.embeddings = types.SimpleNamespace(
                create=lambda model, input: types.SimpleNamespace(data=[])
            )

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setattr(embeddings, "_client", None)
    monkeypatch.setattr(embeddings, "_client_config", None)
    return created


def test_embed_returns_vectors(monkeypatch):
    captured = {}

    def fake_create(model, input):
        captured["model"] = model
        captured["input"] = input
        data = [types.SimpleNamespace(embedding=[0.1, 0.2, 0.3]) for _ in input]
        return types.SimpleNamespace(data=data)

    fake_client = types.SimpleNamespace(
        embeddings=types.SimpleNamespace(create=lambda model, input: fake_create(model, input))
    )
    monkeypatch.setattr(embeddings, "_get_client", lambda: fake_client)

    vecs = embeddings.embed(["a", "b"])
    assert vecs == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert captured["input"] == ["a", "b"]


def test_model_resolves_from_config(cfg, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "env/embed")
    assert embeddings.get_model() == "env/embed"
    cfg.save_config({"embedding_model": "cfg/embed"})
    assert embeddings.get_model() == "cfg/embed"


def test_dim_from_env(monkeypatch):
    assert embeddings.get_dim() == embeddings.DEFAULT_DIM
    monkeypatch.setenv("EMBEDDING_DIM", "768")
    assert embeddings.get_dim() == 768


def test_client_uses_configured_llm_endpoint_and_key(cfg, monkeypatch):
    created = fake_openai(monkeypatch)
    cfg.save_config({"llm_base_url": "http://localhost:11434/v1/", "llm_api_key": "cfg-key"})

    embeddings._get_client()

    assert created[0].kwargs["base_url"] == "http://localhost:11434/v1"
    assert created[0].kwargs["api_key"] == "cfg-key"


def test_embedding_endpoint_env_overrides_configured_llm(cfg, monkeypatch):
    created = fake_openai(monkeypatch)
    cfg.save_config({"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "cfg-key"})
    monkeypatch.setenv("EMBEDDINGS_BASE_URL", "https://embeddings.example/v1/")
    monkeypatch.setenv("EMBEDDINGS_API_KEY", "embed-key")

    embeddings._get_client()

    assert created[0].kwargs["base_url"] == "https://embeddings.example/v1"
    assert created[0].kwargs["api_key"] == "embed-key"


def test_client_refreshes_when_configured_llm_settings_change(cfg, monkeypatch):
    created = fake_openai(monkeypatch)
    cfg.save_config({"llm_base_url": "http://localhost:11434/v1", "llm_api_key": "old-key"})
    first = embeddings._get_client()

    cfg.save_config({"llm_api_key": "new-key"})
    second = embeddings._get_client()

    assert second is not first
    assert len(created) == 2
    assert created[1].kwargs["base_url"] == "http://localhost:11434/v1"
    assert created[1].kwargs["api_key"] == "new-key"
