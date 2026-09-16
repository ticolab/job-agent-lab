"""for the ``--expand-selector`` capture flag.

Loads a synthetic accordion page whose anchors mount only when a
``button[aria-expanded="false"]`` header is clicked (mirroring the C4
Deel-style header shape), then exercises the capture script's expand
loop and bake+serialize routine against it. Asserts three properties:

  1. **Baseline (no expansion).** The serialized HTML must contain
     none of the accordion-mounted anchor hrefs — capture without
     ``--expand-selector`` sees an empty listing region.
  2. **With expansion.** After :func:`expand_all` runs (via the
     capture script's ``_PlaywrightPageDriver`` adapter, mirroring the
     unified expansion path), the serialized HTML must contain
     every expected anchor href, and the click counter must match the
     number of accordion headers (proving each header is clicked
     exactly once).
  3. **Suite-style replay.** Running the production matcher
     (:data:`EXTRACT_JOB_LINKS_JS`) against the expanded HTML must
     return the expected URL set — proving a fixture captured with
     ``--expand-selector`` would pass the snapshot regression harness.

Not a pytest test — this is a Task 2 one-shot verification script,
following the ``verify_capture_v2.py`` / ``verify_matcher_v2.py``
precedent (loaded as a sibling file rather than a package member so
the same importlib bootstrap works from any working directory).
"""

from __future__ import annotations

import asyncio

# Load ``capture_snapshot`` as a sibling file — ``scripts/`` is not a
# package, so we can't do ``from scripts.capture_snapshot import ...``.
import importlib.util
import sys
from pathlib import Path

from playwright.async_api import async_playwright

from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS
from vacantes.extraction.dom.collector import EXPAND_MAX_ROUNDS, expand_all

_capture_path = Path(__file__).resolve().parent / "capture_snapshot.py"
_spec = importlib.util.spec_from_file_location("capture_snapshot", _capture_path)
assert _spec is not None and _spec.loader is not None
_capture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_capture_module)
_BAKE_AND_SERIALIZE_JS = _capture_module._BAKE_AND_SERIALIZE_JS
_PlaywrightPageDriver = _capture_module._PlaywrightPageDriver

# Synthetic accordion. Two collapsed headers, each mounting its child
# anchors only on click. Absolute anchor hrefs so the matcher's origin
# check works without depending on the ``set_content`` base URI (which
# would otherwise be ``about:blank``). The DOM shape mirrors Deel's
# real board: a ``button[aria-expanded="false"]`` labelled with a role
# count is the trigger, and the anchors live in a sibling container
# that starts empty.
HTML = """<!DOCTYPE html>
<html>
<head><title> expand-selector verify</title></head>
<body>
  <button aria-expanded="false" id="hdr1">Engineering — 3 open roles</button>
  <div id="body1"></div>
  <button aria-expanded="false" id="hdr2">Design — 2 open roles</button>
  <div id="body2"></div>
  <script>
    const mount = (btnId, bodyId, hrefs) => {
      const btn = document.getElementById(btnId);
      const body = document.getElementById(bodyId);
      btn.addEventListener('click', () => {
        if (btn.getAttribute('aria-expanded') === 'true') return;
        btn.setAttribute('aria-expanded', 'true');
        for (const href of hrefs) {
          const a = document.createElement('a');
          a.href = href;
          a.textContent = href;
          body.appendChild(a);
        }
      });
    };
    mount('hdr1', 'body1', [
      'https://example.com/jobs/eng-1',
      'https://example.com/jobs/eng-2',
      'https://example.com/jobs/eng-3',
    ]);
    mount('hdr2', 'body2', [
      'https://example.com/jobs/dsn-1',
      'https://example.com/jobs/dsn-2',
    ]);
  </script>
</body>
</html>
"""

SELECTOR: str = "button[aria-expanded='false']"

EXPECTED_HREFS: frozenset[str] = frozenset(
    {
        "https://example.com/jobs/eng-1",
        "https://example.com/jobs/eng-2",
        "https://example.com/jobs/eng-3",
        "https://example.com/jobs/dsn-1",
        "https://example.com/jobs/dsn-2",
    }
)
# Two accordion headers, each expanded exactly once.
EXPECTED_CLICKS: int = 2


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            # (1) Baseline: bake without expansion.
            page = await browser.new_page()
            await page.set_content(HTML, wait_until="domcontentloaded")
            baseline_result = await page.evaluate(_BAKE_AND_SERIALIZE_JS)
            baseline_html: str = baseline_result["html"]
            baseline_anchor_count: int = int(baseline_result["anchorCount"])

            # (2) With expansion: run the capture-side loop, then bake.
            # the bounded loop now lives on the collector and
            # is driven via ``_PlaywrightPageDriver`` — this verify
            # script exercises the same code path the capture script
            # uses at runtime, so a regression in either surface
            # trips this replay.
            page2 = await browser.new_page()
            await page2.set_content(HTML, wait_until="domcontentloaded")
            driver = _PlaywrightPageDriver(page2)
            clicks = await expand_all(driver, SELECTOR)
            expanded_result = await page2.evaluate(_BAKE_AND_SERIALIZE_JS)
            expanded_html: str = expanded_result["html"]
            expanded_anchor_count: int = int(expanded_result["anchorCount"])

            # (3) Suite-style replay: load the expanded HTML into a fresh
            # page and run the production matcher against it, mirroring
            # what the snapshot regression harness does per fixture.
            page3 = await browser.new_page()
            await page3.set_content(expanded_html, wait_until="domcontentloaded")
            replay_urls = await page3.evaluate(
                EXTRACT_JOB_LINKS_JS,
                ["/jobs", "https://example.com", 1],
            )
        finally:
            await browser.close()

    problems: list[str] = []

    # (1) Baseline must contain zero anchor elements (the accordion-
    # mounted anchors only exist after a click). URL literals inside the
    # inline <script> block are inert — the check is on <a> elements,
    # not on raw substrings, using the bake's own anchor tally.
    if baseline_anchor_count != 0:
        problems.append(
            f"Baseline (no expansion) has {baseline_anchor_count} anchor "
            f"element(s); expected 0."
        )
    baseline_leaks = [h for h in EXPECTED_HREFS if f'href="{h}"' in baseline_html]
    if baseline_leaks:
        problems.append(
            "Baseline (no expansion) has "
            f"{len(baseline_leaks)} <a href> element(s) for accordion-"
            f"mounted anchors: {baseline_leaks}"
        )

    # (2) Expanded HTML must contain a matching <a href> element for
    # every expected anchor, the anchor tally must equal the expected
    # set size, and the click counter must match the accordion-header
    # count (proving each header was clicked exactly once).
    missing = [h for h in EXPECTED_HREFS if f'href="{h}"' not in expanded_html]
    if missing:
        problems.append(
            f"Expanded HTML missing <a href> for {len(missing)} anchor(s): {missing}"
        )
    if expanded_anchor_count != len(EXPECTED_HREFS):
        problems.append(
            f"Expanded HTML has {expanded_anchor_count} anchor element(s); "
            f"expected {len(EXPECTED_HREFS)}."
        )
    if clicks != EXPECTED_CLICKS:
        problems.append(
            f"expand_all returned {clicks} clicks; "
            f"expected {EXPECTED_CLICKS} (one per accordion header)."
        )

    # (3) Suite-style replay must return exactly the expected URL set.
    replay_set = set(replay_urls or [])
    if replay_set != EXPECTED_HREFS:
        problems.append(
            "Matcher replay URL set mismatch:\n"
            f"  expected: {sorted(EXPECTED_HREFS)}\n"
            f"  got:      {sorted(replay_set)}"
        )

    if problems:
        print("FAIL")
        for msg in problems:
            print(f"  - {msg}")
        sys.exit(1)

    print(
        f"OK - baseline shows 0 accordion anchors, "
        f"expand_all mounted {len(EXPECTED_HREFS)} anchor(s) "
        f"in {clicks} click(s), matcher replay returned "
        f"{len(replay_set)} URL(s) matching the expected set. "
        f"(EXPAND_MAX_ROUNDS={EXPAND_MAX_ROUNDS})"
    )


if __name__ == "__main__":
    asyncio.run(main())
