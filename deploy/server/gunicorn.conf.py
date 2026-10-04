"""Gunicorn settings for the API behind Caddy.

One process with many threads, deliberately:
- Each live event stream (SSE) holds a thread for as long as the run is going, so the worker
  needs plenty of threads (the `gthread` class).
- The login lockout and rate limits live in process memory. One process means the limits are
  exact; with N processes an attacker would get N times as many guesses.
"""

bind = "127.0.0.1:8000"
worker_class = "gthread"
workers = 1
threads = 32
# Seconds without the worker's heartbeat before it is killed. Streams do not trip this (the
# heartbeat comes from the main thread, not from requests).
timeout = 120
graceful_timeout = 30
keepalive = 75
accesslog = "-"
errorlog = "-"
# Behind Caddy only. TRUST_PROXY=1 in the environment makes the app read X-Forwarded-For.
forwarded_allow_ips = "127.0.0.1"
