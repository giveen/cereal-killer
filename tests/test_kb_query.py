import unittest

from mentor.kb.query import RAGSnippet, retrieve_reference_material


class QueryPrioritizationTests(unittest.TestCase):
    def test_prioritizes_target_machine_results(self) -> None:
        snippets = [
            RAGSnippet(source="ippsec", machine="monitor", title="m", url="u", content="c", score=0.01),
            RAGSnippet(source="ippsec", machine="HackTheBox - Cap", title="c", url="u", content="c", score=0.20),
            RAGSnippet(source="ippsec", machine="cap", title="c2", url="u", content="c", score=0.15),
        ]

        # Keep this helper test local to expected behavior by monkey-patching
        # query path with pre-cooked snippets.
        import mentor.kb.query as q

        def fake_query_single_index(settings, index_name, query, limit):  # type: ignore[no-untyped-def]
            return snippets

        original = q._query_single_index
        q._query_single_index = fake_query_single_index  # type: ignore[assignment]
        try:
            result = retrieve_reference_material(
                settings=type("S", (), {"redis_index": "ippsec", "redis_url": "redis://localhost:6379"})(),
                command_or_prompt="command injection",
                context_commands=[],
                top_k=2,
                target_machine="cap",
            )
        finally:
            q._query_single_index = original  # type: ignore[assignment]

        self.assertEqual(len(result), 2)
        self.assertTrue(all(item.machine.lower() == "cap" for item in result))


if __name__ == "__main__":
    unittest.main()
