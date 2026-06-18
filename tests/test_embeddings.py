"""Tests for core/embeddings.py — the OpenAI client is faked (no network)."""

import types

import embeddings


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
