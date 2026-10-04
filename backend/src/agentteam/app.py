from flask import Flask, jsonify

from . import __version__
from .api import bp as api_bp
from .config import Settings
from .tracing import Tracer


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    settings = Settings.from_env()
    app.config["DATABASE_PATH"] = settings.database_path
    # Hashed into each run's config hash; includes per-agent overrides when there are any.
    app.config["LLM_MODEL"] = settings.model_signature
    app.config["DELIVERY_ENABLED"] = settings.delivery_enabled
    if config:
        app.config.update(config)

    app.extensions["tracer"] = Tracer(app.config["DATABASE_PATH"])
    app.register_blueprint(api_bp)

    # /health for proxies and load balancers, /api/health for the dashboard.
    @app.get("/health")
    @app.get("/api/health")
    def health():
        return jsonify(status="ok", version=__version__)

    return app
