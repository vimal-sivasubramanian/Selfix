"""
Selfix — language-agnostic autonomous code improvement pipeline.

Quick start:

    import selfix
    from selfix.signals import ManualSignal
    from selfix.validator.builtin import ShellCommandValidator
    from selfix.config import SelfixConfig

    result = selfix.run_sync(SelfixConfig(
        repo_path="/path/to/repo",
        signal=ManualSignal(description="Fix the O(n²) sort in utils/sort.py"),
        validator=ShellCommandValidator("pytest tests/ -x -q"),
    ))
    print(result.status, result.diff)
"""
from __future__ import annotations

import asyncio
import logging
import uuid

from selfix.config import SelfixConfig
from selfix.graph.nodes.report import get_last_result
from selfix.graph.orchestrator import build_graph
from selfix.result import SelfixResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

__version__ = "0.1.0"
__all__ = ["run", "run_sync", "SelfixConfig", "SelfixResult"]


async def run(config: SelfixConfig) -> SelfixResult:
    """Run the Selfix pipeline asynchronously."""
    graph = build_graph(checkpoint_dir=config.checkpoint_dir)
    thread_id = config.signal.id

    await asyncio.to_thread(
        graph.invoke,
        {"config": config},
        {"configurable": {"thread_id": thread_id}},
    )

    result = get_last_result()
    if result is None:
        # Should not happen — report_node always sets it
        raise RuntimeError("Pipeline completed but no result was produced.")
    return result


def run_sync(config: SelfixConfig) -> SelfixResult:
    """Synchronous wrapper around run() for callers without an event loop."""
    graph = build_graph(checkpoint_dir=config.checkpoint_dir)
    thread_id = config.signal.id

    graph.invoke(
        {"config": config},
        {"configurable": {"thread_id": thread_id}},
    )

    result = get_last_result()
    if result is None:
        raise RuntimeError("Pipeline completed but no result was produced.")
    return result
