"""Compatibility bridge for retrieval helpers.

Primary implementation lives in mentor.kb.query; this module exists so
callers can import via cereal_killer.kb.query.
"""

from mentor.kb.query import *  # noqa: F401,F403
