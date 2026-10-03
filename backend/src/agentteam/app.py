from flask import Flask, jsonify

from . import __version__


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    if config:
        app.config.update(config)

    # /health for proxies and load balancers, /api/health for the dashboard.
    @app.get("/health")
    @app.get("/api/health")
    def health():
        return jsonify(status="ok", version=__version__)

    return app
