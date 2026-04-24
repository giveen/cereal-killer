import unittest

from mentor.kb.query import RAGSnippet, _canonical_machine, _summarize_snippet, retrieve_reference_material


class QueryPrioritizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prioritizes_target_machine_results(self) -> None:
        snippets = [
            RAGSnippet(source="ippsec", machine="monitor", title="m", url="u", content="c", score=0.01),
            RAGSnippet(source="ippsec", machine="HackTheBox - Cap", title="c", url="u", content="c", score=0.20),
            RAGSnippet(source="ippsec", machine="cap", title="c2", url="u", content="c", score=0.15),
        ]

        # Keep this helper test local to expected behavior by monkey-patching
        # query path with pre-cooked snippets.
        import mentor.kb.query as q

        async def fake_query_single_index(settings, index_name, query, limit, machine_filter=None, precomputed_vector=None, **kwargs):  # type: ignore[no-untyped-def]
            return snippets

        original = q._query_single_index
        q._query_single_index = fake_query_single_index  # type: ignore[assignment]
        try:
            result = await retrieve_reference_material(
                settings=type("S", (), {"redis_index": "ippsec", "redis_url": "redis://localhost:6379"})(),
                command_or_prompt="command injection",
                context_commands=[],
                top_k=2,
                target_machine="cap",
            )
        finally:
            q._query_single_index = original  # type: ignore[assignment]

        self.assertEqual(len(result), 2)
        self.assertTrue(all(_canonical_machine(item.machine) == "cap" for item in result))

    def test_summary_preprocessor_outputs_bullets(self) -> None:
        summary = _summarize_snippet(
            "machine: Cap\nphase: user\nline: Command injection works\ntag: linux easy\n"
            "video_id: abc\ntimestamp_seconds: 120\nhttps://example.com"
        )
        self.assertIn("- phase: user", summary)
        self.assertIn("- line: Command injection works", summary)


if __name__ == "__main__":
    unittest.main()
