"""Matcher URL-set regression tool: HEAD's ``collect_links.js`` vs working tree.

Complements the snapshot regression suite, which asserts unfiltered *count*
equality per fixture. Count equality is a weaker check than URL-set
equality: a matcher change that drops one visibility-hidden anchor and
adds one shadow-DOM anchor would preserve the count while silently
shifting the set. This script runs both the committed and working-tree
matcher JS against every snapshot fixture and asserts per-fixture set
equality after fragment-strip normalisation of the committed side
(fragment collapse is the one intentional non-identity SYS-2 introduced;
future matcher changes that deliberately alter URL shape will need this
predicate adjusted).

For each snapshot fixture under ``tests/fixtures/snapshots/``:

  1. Loads the committed matcher JS via ``git show HEAD:...``.
  2. Loads the working-tree matcher JS.
  3. Runs both against the fixture's ``page.html`` inside a real Chromium
     page with a ``<base href>`` injected exactly as the snapshot suite
     does.
  4. Asserts ``new_set == {strip_fragment(url) for url in old_set}``.

Also runs a 3× wall-clock re-measurement of each JS version against the
full corpus in a warm browser process, reporting medians. Median rather
than mean because playwright cold-page churn produces long-tailed noise.

Run:
    uv run python scripts/verify_urlset_diff.py

Exit code is 0 when every fixture matches under the predicate, 1 otherwise.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from playwright.sync_api import Page, sync_playwright

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "tests" / "fixtures" / "snapshots"
JS_ASSET = "src/vacantes/extraction/dom/assets/collect_links.js"


def _load_old_js() -> str:
    return subprocess.check_output(
        ["git", "show", f"HEAD:{JS_ASSET}"], cwd=REPO_ROOT, text=True
    )


def _load_new_js() -> str:
    return (REPO_ROOT / JS_ASSET).read_text(encoding="utf-8")


def _strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def _derive_prefix(sample_job_url: str) -> str:
    from posixpath import dirname

    return dirname(urlparse(sample_job_url).path.rstrip("/"))


def _inject_base_href(html: str, base_url: str) -> str:
    tag = f'<base href="{base_url}">'
    lower = html.lower()
    head_open = lower.find("<head")
    if head_open == -1:
        return f"<head>{tag}</head>{html}"
    close_angle = html.find(">", head_open)
    if close_angle == -1:
        return f"<head>{tag}</head>{html}"
    return html[: close_angle + 1] + tag + html[close_angle + 1 :]


def _discover() -> list[Path]:
    return sorted(
        p
        for p in FIXTURES_DIR.iterdir()
        if p.is_dir()
        and (p / "page.html").is_file()
        and (p / "metadata.json").is_file()
    )


def _load_fixture(snap_dir: Path) -> dict:
    metadata = json.loads((snap_dir / "metadata.json").read_text(encoding="utf-8"))
    html = (snap_dir / "page.html").read_text(encoding="utf-8")
    job_board_url = metadata["job_board_url"]
    parsed = urlparse(job_board_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    override = metadata.get("path_prefix")
    prefix = (
        override if override is not None else _derive_prefix(metadata["sample_job_url"])
    )
    return {
        "slug": snap_dir.name,
        "html": html,
        "base_url": job_board_url,
        "prefix": prefix,
        "origin": origin,
    }


def _run(page: Page, js: str, fixture: dict) -> set[str]:
    page.set_content(
        _inject_base_href(fixture["html"], fixture["base_url"]),
        wait_until="domcontentloaded",
    )
    result = page.evaluate(js, [fixture["prefix"], fixture["origin"]])
    return set(result or [])


def urlset_diff(
    fixtures: list[dict], old_js: str, new_js: str
) -> tuple[int, list[str]]:
    """Return (passes, failures) after per-fixture set equality check."""
    passes = 0
    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(java_script_enabled=False)
        page = ctx.new_page()
        for fx in fixtures:
            old_set = _run(page, old_js, fx)
            new_set = _run(page, new_js, fx)
            normalised_old = {_strip_fragment(u) for u in old_set}
            if new_set == normalised_old:
                passes += 1
            else:
                only_new = new_set - normalised_old
                only_old = normalised_old - new_set
                failures.append(
                    f"  {fx['slug']}: +{len(only_new)} new-only, "
                    f"-{len(only_old)} old-only"
                )
                if only_new:
                    failures.append(f"    new-only: {sorted(only_new)[:3]}")
                if only_old:
                    failures.append(f"    old-only: {sorted(only_old)[:3]}")
        browser.close()
    return passes, failures


def _measure_once(fixtures: list[dict], js: str) -> float:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(java_script_enabled=False)
        page = ctx.new_page()
        # Warm the page process with one dummy set_content before timing.
        page.set_content("<html><head></head><body></body></html>")
        t0 = time.perf_counter()
        for fx in fixtures:
            _run(page, js, fx)
        elapsed = time.perf_counter() - t0
        browser.close()
    return elapsed


def timing(fixtures: list[dict], old_js: str, new_js: str, runs: int = 3) -> None:
    old_times = [_measure_once(fixtures, old_js) for _ in range(runs)]
    new_times = [_measure_once(fixtures, new_js) for _ in range(runs)]
    print()
    print("=== Wall-clock (all 37 fixtures, warm browser, JS disabled) ===")
    print(f"OLD JS runs (s): {[f'{t:.2f}' for t in old_times]}")
    print(f"NEW JS runs (s): {[f'{t:.2f}' for t in new_times]}")
    print(f"OLD median: {statistics.median(old_times):.2f}s")
    print(f"NEW median: {statistics.median(new_times):.2f}s")


def main() -> int:
    old_js = _load_old_js()
    new_js = _load_new_js()
    snaps = _discover()
    fixtures = [_load_fixture(p) for p in snaps]
    print(f"Discovered {len(fixtures)} v1 fixtures under {FIXTURES_DIR}")

    passes, failures = urlset_diff(fixtures, old_js, new_js)
    print()
    print("=== URL-set diff: new_set == {strip_fragment(u) for u in old_set} ===")
    print(f"Passes: {passes}/{len(fixtures)}")
    if failures:
        print("Failures:")
        for line in failures:
            print(line)
    else:
        print("No divergences.")

    timing(fixtures, old_js, new_js)
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
