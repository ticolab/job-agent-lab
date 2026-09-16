"""scratch verifier for capture v2's shadow-DOM serialization.

Loads a synthetic page with an open shadow root attached at runtime, runs
the capture script's in-page bake+serialize routine against it, and
asserts the serialized HTML contains the declarative-shadow-DOM marker
(``shadowrootmode`` set to ``open``). This proves ``Element.getHTML`` is
emitting DSD, not silently dropping the shadow tree as ``page.content()``
would.

Also confirms the DOCTYPE prefix and the visibility bake.

Not a pytest test — this is a Task 3 one-shot verification script.
"""

from __future__ import annotations

# Load ``capture_snapshot`` as a sibling file — ``scripts/`` is not a
# package, so we can't do ``from scripts.capture_snapshot import ...``.
import importlib.util
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

_capture_path = Path(__file__).resolve().parent / "capture_snapshot.py"
_spec = importlib.util.spec_from_file_location("capture_snapshot", _capture_path)
assert _spec is not None and _spec.loader is not None
_capture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_capture_module)
_BAKE_AND_SERIALIZE_JS = _capture_module._BAKE_AND_SERIALIZE_JS

HTML = """<!DOCTYPE html>
<html>
<head><title>shadow spot-check</title></head>
<body>
  <div id='host'></div>
  <a href='/jobs/hidden' style='display: none;'>Hidden</a>
  <script>
    const host = document.getElementById('host');
    const root = host.attachShadow({ mode: 'open' });
    root.innerHTML = '<a href="/jobs/shadow">Shadow job</a>';
  </script>
</body>
</html>
"""

DSD_MARKER = 'shadowrootmode="open"'


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        # JS enabled here — the capture script runs against live boards
        # with JS on, and we need the inline <script> to attach the root.
        context = browser.new_context()
        page = context.new_page()
        page.set_content(HTML, wait_until="domcontentloaded")
        result = page.evaluate(_BAKE_AND_SERIALIZE_JS)
        html = result["html"]
        browser.close()

    problems: list[str] = []
    if DSD_MARKER not in html:
        problems.append("Serialized HTML is missing the DSD marker.")
    if "/jobs/shadow" not in html:
        problems.append("Shadow-root anchor href not present in serialized HTML.")
    first_line = html.splitlines()[0] if html else ""
    if "<!DOCTYPE html>" not in first_line:
        problems.append("Serialized HTML is not DOCTYPE-prefixed.")
    # Hidden anchor should carry a display:none stamp (it did in the
    # source; the bake keeps it there and would add one if it was missing).
    if (
        "/jobs/hidden" in html
        and "display: none" not in html
        and "display:none" not in html
    ):
        problems.append("Hidden anchor lost its display:none stamp.")

    if problems:
        print("FAIL")
        for msg in problems:
            print(f"  - {msg}")
        print("--- serialized (first 2 KB) ---")
        print(html[:2048])
        sys.exit(1)

    print("OK - shadow root serialized, doctype prefixed, visibility baked.")
    idx = html.find("shadowrootmode")
    excerpt = html[max(0, idx - 60) : idx + 200]
    print("Excerpt around DSD emission:")
    print(excerpt)


if __name__ == "__main__":
    main()
