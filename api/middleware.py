"""
gateway/middleware.py — Rate limiting en memoria (dict + timestamp).

Simple in-memory rate limiter. Resets on server restart.
For production, use Redis-backed limiting.
"""

import time
from collections import defaultdict
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Per-IP rate limiter.
    max_requests: maximum requests per window.
    window_seconds: time window in seconds.
    """

    def __init__(self, app, max_requests: int = 30, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        # {ip: [timestamp, timestamp, ...]}
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Only rate limit POST /chat
        if request.url.path == "/chat" and request.method == "POST":
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()

            # Clean old entries
            self._requests[client_ip] = [
                t
                for t in self._requests[client_ip]
                if now - t < self.window_seconds
            ]

            if len(self._requests[client_ip]) >= self.max_requests:
                raise HTTPException(
                    status_code=429,
                    detail=f"Rate limit exceeded. Max {self.max_requests} requests per {self.window_seconds}s.",
                )

            self._requests[client_ip].append(now)

        response = await call_next(request)
        return response
