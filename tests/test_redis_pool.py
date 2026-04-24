"""Tests for the Redis connection pool module."""
import unittest
from unittest.mock import MagicMock, patch


class TestRedisPool(unittest.TestCase):
    """Tests for get_sync_pool(), get_sync_client(), get_async_pool(), get_async_client()."""

    def setUp(self) -> None:
        import mentor.kb.redis_pool as rp
        self.rp = rp
        self.rp.reset_pools()

    def tearDown(self) -> None:
        self.rp.reset_pools()

    def test_get_sync_pool_returns_same_pool_on_repeated_calls(self) -> None:
        """get_sync_pool() returns the same pool instance on repeated calls with the same URL."""
        # Reset to ensure fresh state
        self.rp._SYNC_POOL = None

        pool1 = self.rp.get_sync_pool("redis://localhost:6379")
        pool2 = self.rp.get_sync_pool("redis://localhost:6379")

        self.assertIs(pool1, pool2)
        # Verify it's a real ConnectionPool (redis is installed in this env)
        from redis import ConnectionPool
        self.assertIsInstance(pool1, ConnectionPool)

    def test_get_sync_client_returns_client_using_shared_pool(self) -> None:
        """get_sync_client() returns a Redis client using the shared connection pool."""
        self.rp._SYNC_POOL = None

        client = self.rp.get_sync_client("redis://localhost:6379")

        from redis import Redis
        self.assertIsInstance(client, Redis)
        # The client should use the pooled connection
        self.assertIsNotNone(client.connection_pool)

    def test_get_async_pool_returns_same_pool_on_repeated_calls(self) -> None:
        """get_async_pool() returns the same pool instance on repeated calls with the same URL."""
        self.rp._ASYNC_POOL = None

        pool1 = self.rp.get_async_pool("redis://localhost:6379")
        pool2 = self.rp.get_async_pool("redis://localhost:6379")

        self.assertIs(pool1, pool2)

    def test_get_async_client_returns_client_using_shared_pool(self) -> None:
        """get_async_client() returns a Redis async client using the shared connection pool."""
        self.rp._ASYNC_POOL = None

        client = self.rp.get_async_client("redis://localhost:6379")

        from redis.asyncio import Redis
        self.assertIsInstance(client, Redis)

    def test_reset_pools_clears_all_cached_pools(self) -> None:
        """reset_pools() clears all cached pool instances."""
        # Create pools first
        self.rp.get_sync_pool("redis://localhost:6379")
        self.rp.get_async_pool("redis://localhost:6379")

        self.assertIsNotNone(self.rp._SYNC_POOL)
        self.assertIsNotNone(self.rp._ASYNC_POOL)

        self.rp.reset_pools()

        self.assertIsNone(self.rp._SYNC_POOL)
        self.assertIsNone(self.rp._ASYNC_POOL)

    def test_different_redis_urls_create_different_pools(self) -> None:
        """Different redis_url values create different pool instances."""
        self.rp._SYNC_POOL = None

        pool1 = self.rp.get_sync_pool("redis://localhost:6379")
        # After first call, the pool is cached with the first URL
        # Second call with different URL will NOT match the cached key,
        # but the implementation only stores one global pool.
        # The second call replaces the singleton with the new URL's pool.
        pool2 = self.rp.get_sync_pool("redis://localhost:6380")

        # They should be different pools because the URL changed
        self.assertIsNot(pool1, pool2)

    def test_different_decode_responses_create_different_pools(self) -> None:
        """Different decode_responses values create different pool instances."""
        self.rp._SYNC_POOL = None

        pool1 = self.rp.get_sync_pool("redis://localhost:6379", decode_responses=True)
        pool2 = self.rp.get_sync_pool("redis://localhost:6379", decode_responses=False)

        # Different decode_responses creates a new pool
        self.assertIsNot(pool1, pool2)

    def test_sync_pool_has_correct_configuration(self) -> None:
        """Sync pool is created with correct timeout settings."""
        self.rp._SYNC_POOL = None

        pool = self.rp.get_sync_pool("redis://localhost:6379")

        self.assertIsNotNone(pool)
        # Verify pool configuration
        self.assertEqual(pool.max_connections, 10)

    def test_async_pool_has_correct_configuration(self) -> None:
        """Async pool is created with correct timeout settings."""
        self.rp._ASYNC_POOL = None

        pool = self.rp.get_async_pool("redis://localhost:6379")

        self.assertIsNotNone(pool)
        self.assertEqual(pool.max_connections, 10)

    def test_pool_module_constants(self) -> None:
        """Default constants are correctly set."""
        self.assertEqual(self.rp.DEFAULT_MAX_CONNECTIONS, 10)
        self.assertEqual(self.rp.DEFAULT_SOCKET_TIMEOUT, 5.0)
        self.assertEqual(self.rp.DEFAULT_SOCKET_CONNECT_TIMEOUT, 5.0)
        self.assertEqual(self.rp.DEFAULT_DECODE_RESPONSES, True)


if __name__ == "__main__":
    unittest.main()
