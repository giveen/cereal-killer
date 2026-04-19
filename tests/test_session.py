import unittest

from cereal_killer.config import Settings
from mentor.engine.session import ThinkingSessionStore


class SessionStoreTests(unittest.TestCase):
    def test_reasoning_payload_uses_qwen3_parser_and_preserve_flag(self) -> None:
        settings = Settings(reasoning_parser="qwen3", max_model_len=262144)
        store = ThinkingSessionStore(settings)
        payload = store.reasoning_payload()
        self.assertEqual(payload["reasoning-parser"], "qwen3")
        self.assertEqual(payload["reasoning_parser"], "qwen3")
        self.assertTrue(payload["chat_template_kwargs"]["preserve_thinking"])
        self.assertTrue(payload["metadata"]["preserve_thinking"])
        self.assertEqual(payload["metadata"]["max_model_len"], 262144)


if __name__ == "__main__":
    unittest.main()
