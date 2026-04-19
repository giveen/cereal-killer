import unittest

from cereal_killer.knowledge_base import transform_dataset


class KnowledgeBaseTransformTests(unittest.TestCase):
    def test_transform_uses_line_video_timestamp_fields(self) -> None:
        docs = transform_dataset(
            [
                {
                    "machine": "HackTheBox - Cap",
                    "videoId": "abc123",
                    "timestamp": {"minutes": 1, "seconds": 30},
                    "line": "command injection in /ip",
                    "tag": "web",
                }
            ]
        )

        self.assertEqual(len(docs), 1)
        doc = docs[0]
        self.assertEqual(doc["machine"], "Cap")
        self.assertIn("line: command injection in /ip", doc["content"])
        self.assertIn("video_id: abc123", doc["content"])
        self.assertIn("timestamp_seconds: 90", doc["content"])
        self.assertIn("youtube.com/watch?v=abc123&t=90s", doc["url"])


if __name__ == "__main__":
    unittest.main()
