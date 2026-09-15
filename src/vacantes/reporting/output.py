"""Console + on-disk output for one strategy-produced result dict.

``save_result`` writes the result dict as pretty-printed JSON under
``<output_dir>/<slug>_<timestamp>.json``. ``print_summary`` renders the
same result as a human-readable block for the CLI. Both were previously
inlined in ``main.py``; they live here so the CLI is only argparse-plus-
orchestration and the render shape is independently testable.

The dict shape is authored by
:func:`vacantes.extraction.base.build_report` — this module only
consumes it. API strategies (Greenhouse and future siblings) emit
``None`` for ``metadata.agent_steps`` / ``metadata.agent_completed`` /
``metadata.agent_had_errors`` because there is no LLM agent in the
loop; :func:`print_summary` renders each ``None`` as ``n/a`` rather
than the literal ``None`` string.

SYS-9 additive keys consumed here: ``metadata.expected_jobs`` (the
human-counted target, ``None`` if never counted) and
``metadata.verdict`` (one of ``"match"``/``"under"``/``"over"``/
``"unverified"``). :func:`print_summary` renders both through the
``Verdict:`` line built by :func:`_verdict_line`, and the CLI's
``--strict`` gate reads ``metadata.verdict`` verbatim to decide the
exit code — this module never mutates either value.

SYS-13 additive key consumed here: ``metadata.states_visited``, an
optional integer emitted only by the agent-less multi-state
``DomStrategy._extract_prefiltered`` path (see :func:`build_report`
for the serialisation contract). :func:`print_summary` renders a
``States:`` line right after the verdict when the key is present and
omits the line entirely otherwise, keeping every pre-SYS-13 report's
console rendering byte-identical.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from vacantes.catalog import slugify


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


def _verdict_line(meta: dict[str, Any]) -> str:
    """Render the SYS-9 verdict as a single ``Verdict:`` line.

    Format is fixed by the SYS-9 plan:

    - ``Verdict:  match`` (double space after label; no delta).
    - ``Verdict:  under — found N, expected M (-K)`` where ``K = M - N``,
      rendered with an em-dash separator and a signed delta.
    - ``Verdict:  over — found N, expected M (+K)`` where ``K = N - M``,
      likewise signed.
    - ``Verdict:  unverified`` (the ``expected_jobs`` was ``None``).

    When ``meta["agent_had_errors"] is True`` — and *only* then, so
    ``None`` (API strategies) and ``False`` never trigger — the
    suffix ``" (agent reported step errors)"`` is appended. This is a
    warning annotation, not a verdict override: a ``match`` run with
    agent step errors is still worth landing but merits a run-log
    look before commit, and the DoD in ``integrate-company/SKILL.md``
    reflects that.

    Uses ``format(delta, "+d")`` so both signs render explicitly
    (``+3`` / ``-3``), keeping the delta unambiguous at a glance.
    """
    verdict: str = meta["verdict"]
    expected: int | None = meta["expected_jobs"]
    found: int = meta["total_jobs_found"]

    if verdict == "match":
        line = "Verdict:  match"
    elif verdict == "unverified":
        line = "Verdict:  unverified"
    else:
        # ``under`` / ``over``. ``expected`` is guaranteed non-None
        # here because ``compute_verdict`` only produces those two
        # verdicts when ``expected is not None``.
        assert expected is not None, "under/over verdict requires expected_jobs"
        delta = found - expected  # positive for over, negative for under
        line = f"Verdict:  {verdict} — found {found}, expected {expected} ({delta:+d})"

    if meta.get("agent_had_errors") is True:
        line = f"{line} (agent reported step errors)"

    return line


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
    print(f"  {_verdict_line(meta)}")
    # SYS-13 additive line: rendered only when the report came from the
    # agent-less multi-state path (build_report omits the key from
    # metadata otherwise, so every agent + Greenhouse report's console
    # rendering stays byte-identical to pre-SYS-13).
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
