"""Search and RAG synthesis worker management."""
from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cereal_killer.ui.app import CerealKillerApp

from textual.widgets import Input, Markdown

from mentor.engine.search_orchestrator import tiered_search
from mentor.kb.query import RAGSnippet

from ..base import resolve_dashboard

SEARCH_SOURCE_FILTER_RE = re.compile(r"\bonly\s+(ippsec|gtfobs|lolbas|hacktricks|payloads)\b", re.IGNORECASE)


class SearchWorkerManager:
    """Manages search and Gibson RAG workers."""

    def __init__(self, app: "CerealKillerApp") -> None:
        self._app = app

    _dashboard = resolve_dashboard

    def _worker_name(self, method_name: str) -> str:
        return f"{self.__class__.__name__}.{method_name}"

    def extract_source_filters(self, query: str) -> list[str] | None:
        match = SEARCH_SOURCE_FILTER_RE.search(query)
        if not match:
            return None
        return [match.group(1).strip().lower()]

    async def run_search(self, query: str) -> None:
        """Run a search against local knowledge."""
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_search")
        self._app._register_worker(worker_name, asyncio.current_task())
        self._app._analysis_busy(True)
        dashboard.set_active_tool("Search")
        self._app.notify(f"Searching local Gibson memory for '{query}'...", title="Search Status", severity="information")
        dashboard.append_system(f"Searching local Gibson memory for '{query}'...", style="bold cyan")
        try:
            await self._app._with_worker_cancellation(self._search_body(query))
        except Exception as exc:
            dashboard.append_system(f"Search error: {exc}", style="red")
        finally:
            dashboard.set_active_tool("Idle")
            self._app._analysis_busy(False)
            self._app._unregister_worker(worker_name)

    async def _search_body(self, query: str) -> None:
        source_filters = self.extract_source_filters(query)
        search_result = await tiered_search(
            query=query,
            settings=self._app.kb.settings,
            history_commands=[],
            target_machine=None,
            allow_web=False,
            force_web=False,
            top_k=12,
            source_filters=source_filters,
        )
        chunks = search_result.vector_snippets
        top_scores = ", ".join(f"{score:.3f}" for score in search_result.top_similarity_scores) or "none"
        dashboard = self._dashboard()
        if dashboard:
            dashboard.append_system(f"RAG top-3 similarity: {top_scores}", style="bold cyan")
            dashboard.append_system(f"Gibson found {len(chunks)} relevant matches in local memory.", style="bold green")
            self._app.notify(f"Gibson found {len(chunks)} relevant matches in local memory.", title="Search Status", severity="information")

            if chunks:
                response = await self._app.engine.synthesize_search_results(query, chunks)
                self._app._update_llm_cache_metrics(response.backend_meta)
                labeled_answer = f"## [SEARCH RESULT]\n\n{response.answer.strip()}"
                await self._app._consume_llm_response(labeled_answer, response.reasoning_content or response.thought)
                dashboard.show_gibson_summary(f"# GIBSON SUMMARY\n\n{response.answer.strip()}")
            else:
                fallback = (
                    f"My local memory for '{query}' is a void. "
                    "Checking the IppSec and HackTricks datasets again... "
                    "or perhaps you should learn to type."
                )
                dashboard.append_assistant(fallback)
                self._app._append_chat("assistant", fallback)

            # Populate Gibson tab with raw snippets
            self._app._gibson_snippets = []
            for snippet in chunks:
                row = {"title": snippet.title, "content": snippet.content, "source": snippet.source, "machine": snippet.machine, "url": snippet.url}
                row["visual_image_url"] = self._extract_visual_candidate_url([row])
                self._app._gibson_snippets.append(row)
            remote_candidate = self._extract_visual_candidate_url(self._app._gibson_snippets)
            dashboard.set_remote_image_candidate(remote_candidate)
            if remote_candidate:
                dashboard.append_system("Relevant diagram found. Use [VIEW IMAGE] in the Media Drawer.", style="bold cyan")
            dashboard.set_gibson_results(self._app._gibson_snippets)
            dashboard.set_active_view("gibson")

    def _extract_visual_candidate_url(self, snippets: list[dict]) -> str | None:
        return self._app._extract_visual_candidate_url(snippets)

    async def run_gibson_search(self, query: str) -> None:
        """Direct search from the Gibson tab."""
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_gibson_search")
        self._app._register_worker(worker_name, asyncio.current_task())
        dashboard.set_gibson_loading(True)
        dashboard.set_active_tool("Gibson")
        self._app._analysis_busy(True)
        try:
            await self._app._with_worker_cancellation(self._gibson_search_body(query))
        except Exception as exc:
            try:
                dashboard.query_one("#gibson_viewer", Markdown).update(f"_Search error: {exc}_")
            except Exception:
                pass
        finally:
            dashboard.set_active_tool("Idle")
            dashboard.set_gibson_loading(False)
            self._app._analysis_busy(False)
            self._app._unregister_worker(worker_name)

    async def _gibson_search_body(self, query: str) -> None:
        source_filters = self.extract_source_filters(query)
        search_result = await tiered_search(
            query=query,
            settings=self._app.kb.settings,
            history_commands=[],
            target_machine=None,
            allow_web=False,
            force_web=False,
            top_k=15,
            source_filters=source_filters,
        )
        top_scores = ", ".join(f"{score:.3f}" for score in search_result.top_similarity_scores) or "none"
        dashboard = self._dashboard()
        dashboard.append_system(f"RAG top-3 similarity: {top_scores}", style="bold cyan")
        dashboard.append_system(f"Gibson found {len(search_result.vector_snippets)} relevant matches in local memory.", style="bold green")
        self._app._gibson_snippets = []
        for snippet in search_result.vector_snippets:
            row = {"title": snippet.title, "content": snippet.content, "source": snippet.source, "machine": snippet.machine, "url": snippet.url}
            row["visual_image_url"] = self._extract_visual_candidate_url([row])
            self._app._gibson_snippets.append(row)
        remote_candidate = self._extract_visual_candidate_url(self._app._gibson_snippets)
        dashboard.set_remote_image_candidate(remote_candidate)
        if remote_candidate:
            dashboard.append_system("Relevant diagram found. Use [VIEW IMAGE] in the Media Drawer.", style="bold cyan")
        dashboard.set_gibson_results(self._app._gibson_snippets)

        if self._app._gibson_snippets:
            rag_snippets = [RAGSnippet(source=s.get("source", ""), machine=s.get("machine", ""), title=s.get("title", ""), url=s.get("url", ""), content=s.get("content", ""), score=0.0) for s in self._app._gibson_snippets]
            response = await self._app.engine.synthesize_search_results(query, rag_snippets)
            self._app._update_llm_cache_metrics(response.backend_meta)
            summary = f"# GIBSON SUMMARY\n\n{response.answer.strip()}"
            dashboard.show_gibson_summary(summary)

    async def run_gibson_synthesize(self) -> None:
        """LLM-synthesize all current Gibson snippets."""
        dashboard = self._dashboard()
        worker_name = self._worker_name("run_gibson_synthesize")
        self._app._register_worker(worker_name, asyncio.current_task())
        self._app._analysis_busy(True)
        dashboard.set_active_tool("Synthesize")
        dashboard.set_gibson_loading(True)
        try:
            await self._app._with_worker_cancellation(self._gibson_synthesize_body())
        except Exception as exc:
            try:
                dashboard.query_one("#gibson_viewer", Markdown).update(f"_Synthesis error: {exc}_")
            except Exception:
                pass
        finally:
            dashboard.set_active_tool("Idle")
            self._app._analysis_busy(False)
            dashboard.set_gibson_loading(False)
            self._app._unregister_worker(worker_name)

    async def _gibson_synthesize_body(self) -> None:
        dashboard = self._dashboard()
        query = dashboard.query_one("#gibson_search_input", Input).value or "summarize"
        snippets = [RAGSnippet(source=s.get("source", ""), machine=s.get("machine", ""), title=s.get("title", ""), url=s.get("url", ""), content=s.get("content", ""), score=0.0) for s in self._app._gibson_snippets]
        response = await self._app.engine.synthesize_search_results(query, snippets)
        self._app._update_llm_cache_metrics(response.backend_meta)
        cheat_sheet = f"# MASTER CHEAT SHEET\n\n{response.answer.strip()}"
        dashboard.query_one("#gibson_viewer", Markdown).update(cheat_sheet)

    # Public aliases for delegation from app.py
    run_search_worker = run_search
    search_body = _search_body
    run_gibson_search_worker = run_gibson_search
    gibson_search_body = _gibson_search_body
    run_gibson_synthesize_worker = run_gibson_synthesize
    gibson_synthesize_body = _gibson_synthesize_body

