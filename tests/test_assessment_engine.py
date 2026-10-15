"""Tests for core/assessment_engine.py parsing."""

import types

from assessment_engine import AssessmentEngine


def _response_with_arguments(arguments: str):
    fn = types.SimpleNamespace(name="submit_assessment", arguments=arguments)
    message = types.SimpleNamespace(tool_calls=[types.SimpleNamespace(function=fn)])
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def test_extract_tool_input_recovers_unescaped_control_characters():
    raw_arguments = (
        '{"summary":"Line one\nLine two",'
        '"purpose":"Assess parser resilience",'
        '"tech_stack":["Python"],'
        '"key_components":[],'
        '"vulnerabilities":[]}'
    )

    payload = AssessmentEngine()._extract_tool_input(_response_with_arguments(raw_arguments))

    assert payload["summary"] == "Line one\nLine two"
    assert payload["purpose"] == "Assess parser resilience"
