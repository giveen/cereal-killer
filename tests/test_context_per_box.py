from __future__ import annotations

import unittest

from cereal_killer.config import Settings
from cereal_killer.context_per_box import BoxContext, ContextPerBox, _serialize_context


class _FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def get(self, key: str):
        return self.data.get(key)

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True

    def delete(self, key: str) -> int:
        existed = key in self.data
        self.data.pop(key, None)
        return 1 if existed else 0


class ContextPerBoxTests(unittest.TestCase):
    def test_set_active_machine_loads_existing_context_from_redis(self) -> None:
        settings = Settings(redis_url="redis://localhost:59999")
        store = ContextPerBox(settings)
        fake = _FakeRedis()
        store._redis_client = fake

        fake.data["cereal_killer:context:lame"] = _serialize_context(
            BoxContext(history_context=["nmap -sV 10.10.10.3"], chat_transcript=[{"role": "assistant", "text": "hint"}], pathetic_meter=4)
        )

        store.set_active_machine("lame")

        self.assertEqual(store.active_machine, "lame")
        self.assertEqual(store.get_active_history(), ["nmap -sV 10.10.10.3"])
        self.assertEqual(store.get_active_transcript()[0]["text"], "hint")
        self.assertEqual(store.get_active_pathetic_meter(), 4)

    def test_set_active_history_replaces_existing_list(self) -> None:
        settings = Settings(redis_url="redis://localhost:59999")
        store = ContextPerBox(settings)
        store.set_active_machine("knife")
        store.merge_history(["a", "b"])

        store.set_active_history(["x", "y"])

        self.assertEqual(store.get_active_history(), ["x", "y"])

    def test_set_active_transcript_replaces_existing_entries(self) -> None:
        settings = Settings(redis_url="redis://localhost:59999")
        store = ContextPerBox(settings)
        store.set_active_machine("forest")
        store.get_active_transcript().append({"role": "assistant", "text": "old"})

        store.set_active_transcript([{"role": "assistant", "text": "new"}])

        self.assertEqual(len(store.get_active_transcript()), 1)
        self.assertEqual(store.get_active_transcript()[0]["text"], "new")


if __name__ == "__main__":
    unittest.main()
