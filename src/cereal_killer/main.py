from __future__ import annotations

from cereal_killer.config import get_settings
from cereal_killer.engine import LLMEngine
from cereal_killer.knowledge_base import KnowledgeBase
from cereal_killer.ui import CerealKillerApp


def main() -> None:
    settings = get_settings()
    app = CerealKillerApp(engine=LLMEngine(settings), kb=KnowledgeBase(settings))
    app.run()


if __name__ == "__main__":
    main()
