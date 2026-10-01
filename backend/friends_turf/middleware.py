"""
Performance monitoring middleware for Friends Turf.
Active only in DEBUG mode — zero overhead in production.
"""
import logging
import time
from django.conf import settings
from django.db import connection, reset_queries

logger = logging.getLogger(__name__)


class QueryCountMiddleware:
    """
    Logs per-request database query counts and flags slow or excessive queries.
    Enable by adding 'friends_turf.middleware.QueryCountMiddleware' to MIDDLEWARE
    and setting DEBUG=True.

    Environment variables:
        QUERY_COUNT_THRESHOLD: Warn if query count exceeds this (default: 20)
        SLOW_QUERY_MS: Flag individual queries slower than this (default: 100ms)
    """

    def __init__(self, get_response):
        self.get_response = get_response
        import os
        self.query_threshold = int(os.getenv("QUERY_COUNT_THRESHOLD", "20"))
        self.slow_query_ms = float(os.getenv("SLOW_QUERY_MS", "100"))

    def __call__(self, request):
        if not settings.DEBUG:
            return self.get_response(request)

        # Reset Django's query log for this request
        reset_queries()
        start = time.monotonic()

        response = self.get_response(request)

        duration_ms = (time.monotonic() - start) * 1000
        query_count = len(connection.queries)
        path = request.path

        # Skip static/media/health endpoints
        if any(path.startswith(p) for p in ("/static/", "/media/", "/health", "/api/health")):
            return response

        # Log query count summary
        if query_count > self.query_threshold:
            logger.warning(
                "[WARN]  HIGH QUERY COUNT: %s %s → %d queries in %.1fms",
                request.method, path, query_count, duration_ms,
            )
        elif query_count > 0:
            logger.debug(
                "[STATS] %s %s → %d queries in %.1fms",
                request.method, path, query_count, duration_ms,
            )

        # Flag individual slow queries
        for q in connection.queries:
            query_time_ms = float(q.get("time", 0)) * 1000
            if query_time_ms > self.slow_query_ms:
                logger.warning(
                    "[SLOW] SLOW QUERY (%.1fms): %s",
                    query_time_ms, q["sql"][:500],
                )

        return response
