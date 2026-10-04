import logging
import time

from flask import Flask, Response, g, jsonify, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import __version__
from .api import bp as api_bp
from .api import touch_activity
from .auth import MIN_SECRET_CHARS, InvalidToken, RateLimiter, bearer_token, verify_token
from .config import Settings
from .tracing import Tracer

log = logging.getLogger("agentteam.auth")

# Reachable without a token: liveness checks, the login call itself, and "is auth on?".
PUBLIC_ENDPOINTS = {"health", "api.login", "api.auth_status"}
# Requests that do not count as "someone is using the dashboard" (see _note_use).
IDLE_NEUTRAL_ENDPOINTS = {"api.idle", "health", "api.login", "api.auth_status"}
CORS_ALLOWED_HEADERS = "Authorization, Content-Type, Last-Event-ID"
CORS_ALLOWED_METHODS = "GET, POST, OPTIONS"


def auth_enabled(app: Flask) -> bool:
    return bool(app.config.get("API_PASSWORD"))


def _validate_auth_config(app: Flask) -> None:
    password, secret = app.config.get("API_PASSWORD"), app.config.get("JWT_SECRET")
    if app.config.get("AUTH_REQUIRED") and not password:
        raise RuntimeError("AUTH_REQUIRED is set but API_PASSWORD is not: refusing to start open")
    if password and (not secret or len(secret) < MIN_SECRET_CHARS):
        raise RuntimeError(
            f"JWT_SECRET must be set to a random string of at least {MIN_SECRET_CHARS} characters "
            'when API_PASSWORD is set. Generate one: python -c "import secrets; '
            'print(secrets.token_urlsafe(48))"'
        )
    if not password:
        log.warning(
            "API_PASSWORD is not set: the API is OPEN. Fine for local use, never deploy it."
        )


def create_app(config: dict | None = None) -> Flask:
    app = Flask(__name__)
    settings = Settings.from_env()
    app.config["DATABASE_PATH"] = settings.database_path
    # Hashed into each run's config hash; includes per-agent overrides when there are any.
    app.config["LLM_MODEL"] = settings.model_signature
    app.config["DELIVERY_ENABLED"] = settings.delivery_enabled
    app.config["API_PASSWORD"] = settings.api_password
    app.config["JWT_SECRET"] = settings.jwt_secret
    app.config["JWT_TTL_S"] = settings.jwt_ttl_s
    app.config["AUTH_REQUIRED"] = settings.auth_required
    app.config["CORS_ORIGINS"] = settings.cors_origins
    if config:
        app.config.update(config)
    _validate_auth_config(app)
    if settings.trust_proxy:
        # Believe one hop of X-Forwarded-For/Proto/Host, so rate limits see real client addresses.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    app.extensions["tracer"] = Tracer(app.config["DATABASE_PATH"])
    app.extensions["limiters"] = {
        "login": RateLimiter(limit=5, window_s=15 * 60),  # failed attempts per client address
        "decide": RateLimiter(limit=20, window_s=60),  # approve/reject: they push and open PRs
        "create": RateLimiter(limit=20, window_s=60),  # new runs spend money
    }

    def cors_origin() -> str | None:
        origin = request.headers.get("Origin", "").rstrip("/")
        return origin if origin and origin in app.config["CORS_ORIGINS"] else None

    last_touch = [0.0]

    def _note_use() -> None:
        """An authenticated person is using the dashboard: reset the idle clock (at most every
        30 s). The idle probe itself must not count, or the server could never look idle."""
        if request.endpoint in IDLE_NEUTRAL_ENDPOINTS:
            return
        now = time.monotonic()
        if now - last_touch[0] >= 30:
            last_touch[0] = now
            touch_activity(app.config["DATABASE_PATH"])

    @app.before_request
    def _preflight_and_auth():
        origin = cors_origin()
        if request.method == "OPTIONS" and origin:
            return Response(status=204)  # CORS headers are added in after_request
        if request.method == "OPTIONS":
            return None
        if not auth_enabled(app):
            _note_use()
            return None
        if request.endpoint is None or request.endpoint in PUBLIC_ENDPOINTS:
            return None
        token = bearer_token(request.headers.get("Authorization"))
        try:
            if token is None:
                raise InvalidToken("missing")
            g.claims = verify_token(app.config["JWT_SECRET"], token)
        except InvalidToken:
            resp = jsonify(error="authentication required")
            resp.status_code = 401
            resp.headers["WWW-Authenticate"] = 'Bearer realm="agentteam"'
            return resp
        _note_use()
        return None

    @app.after_request
    def _cors(resp: Response) -> Response:
        origin = cors_origin()
        if origin:
            # Bearer tokens, no cookies: no Allow-Credentials, so a stolen page cannot ride on
            # ambient browser credentials.
            resp.headers["Access-Control-Allow-Origin"] = origin
            resp.headers["Vary"] = "Origin"
            resp.headers["Access-Control-Allow-Headers"] = CORS_ALLOWED_HEADERS
            resp.headers["Access-Control-Allow-Methods"] = CORS_ALLOWED_METHODS
            resp.headers["Access-Control-Max-Age"] = "600"
        return resp

    app.register_blueprint(api_bp)

    # /health for proxies and load balancers, /api/health for the dashboard.
    @app.get("/health")
    @app.get("/api/health")
    def health():
        return jsonify(status="ok", version=__version__)

    return app
