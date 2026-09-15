"""One-shot recorder: dry-run FIND_NEXT_CONTROL_JS descriptors on paginated fixtures.

Emits the exact ``{found, signal, text}`` dict per state file so a
discovery-sweep test can pin known-good behaviour as expected values —
used once to record the HEAD descriptors baked into
``TestPaginatedFixtureSweep`` in
``tests/snapshots/test_pagination_discovery.py`` ahead of SYS-11's
signal changes. Kept for the next discovery-asset change that needs the
same recording step; run manually, not part of any test suite or CI
gate.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from vacantes.extraction.dom import FIND_NEXT_CONTROL_JS

REPO = Path(__file__).parent.parent
FIX = REPO / "tests" / "fixtures" / "snapshots"

FIXTURES = ["techwarely", "nearshore_business_solutions", "apm_terminals"]


def inject_base(html: str, base: str) -> str:
    tag = f'<base href="{base}">'
    lower = html.lower()
    head_open = lower.find("<head")
    if head_open == -1:
        return f"<head>{tag}</head>{html}"
    close = html.find(">", head_open)
    if close == -1:
        return f"<head>{tag}</head>{html}"
    return html[: close + 1] + tag + html[close + 1 :]


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--password-store=basic", "--use-mock-keychain"])
        ctx = b.new_context(java_script_enabled=False)
        page = ctx.new_page()
        for slug in FIXTURES:
            fdir = FIX / slug
            meta = json.loads((fdir / "metadata.json").read_text())
            print(f"--- {slug} ---")
            html = (fdir / "page.html").read_text(encoding="utf-8")
            page.set_content(
                inject_base(html, meta["job_board_url"]),
                wait_until="domcontentloaded",
            )
            desc = page.evaluate(FIND_NEXT_CONTROL_JS, [True])
            print(f"  page.html: {desc}")
            for entry in meta.get("pages", []):
                phtml = (fdir / entry["file"]).read_text(encoding="utf-8")
                page.set_content(
                    inject_base(phtml, entry["url"]),
                    wait_until="domcontentloaded",
                )
                desc = page.evaluate(FIND_NEXT_CONTROL_JS, [True])
                print(f"  {entry['file']}: {desc}")
        b.close()


if __name__ == "__main__":
    main()
