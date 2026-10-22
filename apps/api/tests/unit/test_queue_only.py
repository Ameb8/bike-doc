"""Guard the production cutover against accidental executor reintroduction."""

import ast
from pathlib import Path

import pytest

from bike_doc_api.core.config import Settings

REPO = Path(__file__).resolve().parents[4]
SELECTORS = ("diagnostic_execution", "profile_inference_execution")


def test_no_production_in_process_executor_or_selector() -> None:
    forbidden = {
        "BackgroundTasks",
        "execute_diagnostic_turn_background",
        "execute_profile_inference_background",
        *SELECTORS,
    }
    for path in (REPO / "apps/api/src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        imports = {node.name for node in ast.walk(tree) if isinstance(node, ast.alias)}
        definitions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        arguments = {node.arg for node in ast.walk(tree) if isinstance(node, ast.arg)}
        keywords = {
            node.arg for node in ast.walk(tree) if isinstance(node, ast.keyword)
        }
        assert not (names | imports | definitions | arguments | keywords) & forbidden, (
            path
        )
    assert not (REPO / "apps/api/src/bike_doc_api/adk/background.py").exists()
    for relative in (
        "compose.yaml",
        "compose.production.yaml",
        ".env.example",
        "scripts/prepare-production.py",
    ):
        content = (REPO / relative).read_text()
        for selector in SELECTORS:
            assert selector.upper() not in content, relative


@pytest.mark.parametrize("value", ["legacy", "durable_queue", "both"])
def test_obsolete_environment_cannot_select_execution(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    for selector in SELECTORS:
        monkeypatch.setenv("BIKE_DOC_API_" + selector.upper(), value)
    settings = Settings()
    assert not set(SELECTORS) & Settings.model_fields.keys()
    assert not any(hasattr(settings, selector) for selector in SELECTORS)
