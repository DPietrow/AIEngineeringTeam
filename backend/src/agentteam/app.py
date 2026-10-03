from flask import Flask, jsonify

from . import __version__


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    if config:
        app.config.update(config)

    @app.get("/health")
    def health():
        return jsonify(status="ok", version=__version__)

    return app
