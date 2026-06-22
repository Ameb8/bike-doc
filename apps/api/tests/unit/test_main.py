"""FastAPI app factory configuration tests."""

from fastapi.middleware.cors import CORSMiddleware

from bike_doc_api.core.config import Settings
from bike_doc_api.main import create_app


def test_create_app_uses_baseline_settings() -> None:
    settings = Settings(
        app_name="Configured API",
        environment="test",
        debug=True,
        cors_origins=["http://localhost:3000"],
        log_level="INFO",
        log_format="console",
    )

    app = create_app(settings)

    assert app.title == "Configured API"
    assert app.debug is True
    assert any(middleware.cls is CORSMiddleware for middleware in app.user_middleware)
