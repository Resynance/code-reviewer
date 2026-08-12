"""Tests for core/assessment_engine.py selection helpers."""

from assessment_engine import AssessmentEngine


def test_compliance_selector_includes_non_entry_source_files():
    engine = AssessmentEngine()
    blobs = (
        [{"path": "README.md", "size": 10}]
        + [{"path": f"src/module_{i}.py", "size": 10} for i in range(30)]
        + [
            {"path": "node_modules/pkg/index.js", "size": 10},
            {"path": "package-lock.json", "size": 10},
            {"path": ".env", "size": 10},
        ]
    )

    assessment_paths = [item["path"] for item in engine._select_files(blobs)]
    compliance_paths = [item["path"] for item in engine._select_compliance_files(blobs)]

    assert "src/module_25.py" not in assessment_paths
    assert "src/module_25.py" in compliance_paths
    assert "README.md" in compliance_paths
    assert "node_modules/pkg/index.js" not in compliance_paths
    assert "package-lock.json" not in compliance_paths
    assert ".env" not in compliance_paths
