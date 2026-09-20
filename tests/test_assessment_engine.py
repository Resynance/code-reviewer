"""Tests for assessment repo-content selection."""

from assessment_engine import AssessmentEngine


def _blob(path, size=10):
    return {"path": path, "type": "blob", "size": size}


def test_select_files_excludes_secret_bearing_config_files():
    engine = AssessmentEngine()
    selected = engine._select_files([
        _blob("README.md"),
        _blob("package.json"),
        _blob("docker-compose.yml"),
        _blob("docker-compose.override.yaml"),
        _blob(".env.example"),
        _blob("config/prod.env"),
        _blob(".github/workflows/deploy.yml"),
        _blob("src/main.py"),
    ])

    selected_paths = {item["path"] for item in selected}
    assert {"README.md", "package.json", "src/main.py"}.issubset(selected_paths)
    assert "docker-compose.yml" not in selected_paths
    assert "docker-compose.override.yaml" not in selected_paths
    assert ".env.example" not in selected_paths
    assert "config/prod.env" not in selected_paths
    assert ".github/workflows/deploy.yml" not in selected_paths
