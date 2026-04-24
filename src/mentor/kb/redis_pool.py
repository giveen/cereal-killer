"""Centralized Redis connection pool for the mentor package.

Provides both sync and async connection pools to avoid
creating new Redis connections on every query.
"""
from __future__ import annotations

import threading
from typing import Any

_SYNC_POOL: Any | None = None
_ASYNC_POOL: Any | None = None
_pool_lock = threading.Lock()

# Default configuration
DEFAULT_MAX_CONNECTIONS = 10
DEFAULT_SOCKET_TIMEOUT = 5.0
DEFAULT_SOCKET_CONNECT_TIMEOUT = 5.0
DEFAULT_DECODE_RESPONSES = True


def get_sync_pool(
    redis_url: str,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    decode_responses: bool = DEFAULT_DECODE_RESPONSES,
) -> Any:
    """Get or create a synchronous Redis connection pool.

    Returns a shared pool singleton. Subsequent calls with the same
    redis_url return the same pool instance.

    Args:
        redis_url: Redis connection URL
        max_connections: Maximum number of connections in the pool
        decode_responses: Whether to decode responses to strings

    Returns:
        redis.ConnectionPool instance
    """
    global _SYNC_POOL

    pool_key = (redis_url, decode_responses)

    with _pool_lock:
        if _SYNC_POOL is not None and _SYNC_POOL._url_key == pool_key:
            return _SYNC_POOL.connection_pool

        try:
            from redis import ConnectionPool

            _SYNC_POOL = type(
                '_PoolWrapper', (),
                {
                    'connection_pool': ConnectionPool.from_url(
                        redis_url,
                        max_connections=max_connections,
                        decode_responses=decode_responses,
                        socket_timeout=DEFAULT_SOCKET_TIMEOUT,
                        socket_connect_timeout=DEFAULT_SOCKET_CONNECT_TIMEOUT,
                    ),
                    '_url_key': pool_key,
                }
            )()
            return _SYNC_POOL.connection_pool
        except ImportError:
            return None


def get_sync_client(
    redis_url: str,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    decode_responses: bool = DEFAULT_DECODE_RESPONSES,
) -> Any:
    """Get a synchronous Redis client using the shared connection pool.

    Args:
        redis_url: Redis connection URL
        max_connections: Maximum number of connections in the pool
        decode_responses: Whether to decode responses to strings

    Returns:
        redis.Redis client instance using the shared pool, or None if redis is unavailable
    """
    try:
        from redis import Redis
    except ImportError:
        return None

    pool = get_sync_pool(redis_url, max_connections, decode_responses)
    if pool is None:
        return Redis.from_url(redis_url, decode_responses=decode_responses)

    return Redis(connection_pool=pool)


def get_async_pool(
    redis_url: str,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    decode_responses: bool = DEFAULT_DECODE_RESPONSES,
) -> Any:
    """Get or create an asynchronous Redis connection pool.

    Returns a shared pool singleton. Subsequent calls with the same
    redis_url return the same pool instance.

    Args:
        redis_url: Redis connection URL
        max_connections: Maximum number of connections in the pool
        decode_responses: Whether to decode responses to strings

    Returns:
        redis.asyncio.ConnectionPool instance
    """
    global _ASYNC_POOL

    pool_key = (redis_url, decode_responses)

    with _pool_lock:
        if _ASYNC_POOL is not None and _ASYNC_POOL._url_key == pool_key:
            return _ASYNC_POOL.connection_pool

        try:
            from redis.asyncio import ConnectionPool

            _ASYNC_POOL = type(
                '_PoolWrapper', (),
                {
                    'connection_pool': ConnectionPool.from_url(
                        redis_url,
                        max_connections=max_connections,
                        decode_responses=decode_responses,
                        socket_timeout=DEFAULT_SOCKET_TIMEOUT,
                        socket_connect_timeout=DEFAULT_SOCKET_CONNECT_TIMEOUT,
                    ),
                    '_url_key': pool_key,
                }
            )()
            return _ASYNC_POOL.connection_pool
        except ImportError:
            return None


def get_async_client(
    redis_url: str,
    max_connections: int = DEFAULT_MAX_CONNECTIONS,
    decode_responses: bool = DEFAULT_DECODE_RESPONSES,
) -> Any:
    """Get an asynchronous Redis client using the shared connection pool.

    Args:
        redis_url: Redis connection URL
        max_connections: Maximum number of connections in the pool
        decode_responses: Whether to decode responses to strings

    Returns:
        redis.asyncio.Redis client instance using the shared pool, or None if redis is unavailable
    """
    try:
        from redis.asyncio import Redis
    except ImportError:
        return None

    pool = get_async_pool(redis_url, max_connections, decode_responses)
    if pool is None:
        return Redis.from_url(redis_url, decode_responses=decode_responses)

    return Redis(connection_pool=pool)


def reset_pools() -> None:
    """Reset all connection pools. Useful for testing.

    WARNING: Calling this while connections are in use may cause
    'Connection pool closed' errors. Only call during shutdown or testing.
    """
    global _SYNC_POOL, _ASYNC_POOL
    with _pool_lock:
        if _SYNC_POOL is not None and hasattr(_SYNC_POOL, 'connection_pool'):
            try:
                _SYNC_POOL.connection_pool.disconnect()
            except Exception:
                pass
        if _ASYNC_POOL is not None and hasattr(_ASYNC_POOL, 'connection_pool'):
            try:
                # async pool disconnect is async, but we can't await here
                # so we just null it out
                pass
            except Exception:
                pass
        _SYNC_POOL = None
        _ASYNC_POOL = None
