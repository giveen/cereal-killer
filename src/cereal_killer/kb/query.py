"""Compatibility bridge for retrieval helpers.

Primary implementation lives in mentor.kb.query; this module exists so
callers can import via cereal_killer.kb.query.
"""

from mentor.kb.query import *  # noqa: F401,F403

# Mirror explicit compatibility aliases expected by app-layer callers.
from mentor.kb.query import (  # noqa: F401
	RAG_NOT_EMPTY_SIMILARITY_THRESHOLD,
	has_confident_match,
	top_similarity_scores,
)
