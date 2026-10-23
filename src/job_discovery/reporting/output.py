"""Console + on-disk output for one strategy-produced result dict.

``save_result`` writes the result dict as pretty-printed JSON under
``<output_dir>/<slug>_<timestamp>.json``. ``print_summary`` renders the
same result as a human-readable block for the CLI. Both were previously
inlined in ``main.py``; they live here so the CLI is only argparse-plus-
orchestration and the render shape is independently testable.

The dict shape is authored by
:func:`job_discovery.extraction.base.build_report` — this module only
consumes it. API strategies (Greenhouse and future siblings) emit
``None`` for ``metadata.agent_steps`` / ``metadata.agent_completed`` /
``metadata.agent_had_errors`` because there is no LLM agent in the
loop; :func:`print_summary` renders each ``None`` as ``n/a`` rather
than the literal ``None`` string.

The states-related additive key consumed here is
``metadata.states_visited``, an
optional integer emitted only by the agent-less multi-state
``DomStrategy._extract_prefiltered`` path (see :func:`build_report`
for the serialisation contract). :func:`print_summary` renders a
``States:`` line when the key is present and omits the line entirely
otherwise, keeping every single-state report's
console rendering byte-identical.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from job_discovery.catalog import slugify


def save_result(result: dict[str, Any], output_dir: Path) -> Path:
    """Save extraction result to a JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    slug = slugify(result["company"])
    filepath = output_dir / f"{slug}_{timestamp}.json"
    filepath.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return filepath


def _fmt(value: Any) -> str:
    """Render a metadata field, mapping ``None`` to ``n/a``.

    Used for the agent-run fields (steps / completed / errors / model)
    that API strategies emit as ``None``. Non-``None`` values fall
    through to ``str()`` so booleans render as ``True`` / ``False``
    exactly like they used to.
    """
    return "n/a" if value is None else str(value)


def print_summary(result: dict[str, Any]) -> None:
    """Print a human-readable extraction summary to console."""
    meta = result["metadata"]
    company = result["company"]
    jobs_count = meta["total_jobs_found"]
    elapsed = meta["extraction_time_seconds"]
    error = meta["error"]

    print(f"\n{'=' * 60}")
    print(f"  Company:  {company}")
    print(f"  URL:      {result['url']}")
    print(f"  Strategy: {meta['strategy']}")
    print(f"  Jobs:     {jobs_count}")
    print(f"  Time:     {elapsed:.1f}s")
    print(f"  Steps:    {_fmt(meta['agent_steps'])}")
    print(f"  Done:     {_fmt(meta['agent_completed'])}")
    print(f"  Errors:   {_fmt(meta['agent_had_errors'])}")
    # additive line: rendered only when the report came from the
    # agent-less multi-state path (build_report omits the key from
    # metadata otherwise, so every agent + Greenhouse report's console
    # rendering stays byte-identical to single-state).
    if "states_visited" in meta:
        print(f"  States:   {meta['states_visited']}")

    if error:
        print(f"  Error:    {error}")

    if result["jobs"]:
        print(f"\n  {'Job URLs':}")
        print(f"  {'-' * 40}")
        for url in result["jobs"]:
            print(f"    - {url}")

    print(f"{'=' * 60}")
