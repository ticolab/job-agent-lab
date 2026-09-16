"""scratch verifier for the v2 matcher JS.

Loads a synthetic page that exercises the four behavioural changes:
  - Recursive collector across open (declarative) shadow DOM, including
    a nested shadow root.
  - A same-origin ``srcdoc`` iframe whose anchors must be collected.
  - A CSS-hidden anchor (``display: none``) that must be dropped.
  - A fragment-only duplicate (``/jobs/123`` vs ``/jobs/123#apply``) that
    must collapse to one.

Runs the production ``EXTRACT_JOB_LINKS_JS`` under the same conditions the
snapshot harness uses (JS disabled at the context level, ``set_content``
with ``wait_until='domcontentloaded'``), then asserts the exact expected
set.

Not a pytest test — this is a one-shot verification script and is not part
of the corpus (synthetic ≠ corpus).
"""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from vacantes.extraction.dom import EXTRACT_JOB_LINKS_JS

BASE_URL = "https://example.test/careers"
ORIGIN = "https://example.test"
PATH_PREFIX = "/jobs"

# The page:
#   /jobs/light         — visible anchor in the light DOM
#   /jobs/light#apply   — fragment dupe of /jobs/light, must collapse
#   /jobs/hidden        — display:none anchor, must be dropped
#   /jobs/shadow-outer  — anchor inside an open declarative shadow root
#   /jobs/shadow-inner  — anchor inside a nested open declarative shadow root
#   /jobs/frame         — anchor inside a same-origin srcdoc iframe
#   https://other.test/jobs/x — cross-origin, must be dropped
HTML = """<!DOCTYPE html>
<html>
<head><title>scratch</title></head>
<body>
  <a href="/jobs/light">Light DOM job</a>
  <a href="/jobs/light#apply">Light DOM job (fragment dupe)</a>
  <a href="/jobs/hidden" style="display: none;">Hidden job</a>
  <a href="https://other.test/jobs/x">Cross-origin job</a>

  <div id="shadow-host">
    <template shadowrootmode="open">
      <a href="/jobs/shadow-outer">Shadow DOM job</a>
      <div id="nested-host">
        <template shadowrootmode="open">
          <a href="/jobs/shadow-inner">Nested shadow DOM job</a>
        </template>
      </div>
    </template>
  </div>

  <iframe srcdoc='<a href="/jobs/frame">Same-origin frame job</a>'></iframe>
</body>
</html>
"""

EXPECTED = {
    f"{ORIGIN}/jobs/light",
    f"{ORIGIN}/jobs/shadow-outer",
    f"{ORIGIN}/jobs/shadow-inner",
    f"{ORIGIN}/jobs/frame",
}


def _inject_base(html: str, base_url: str) -> str:
    tag = f'<base href="{base_url}">'
    lower = html.lower()
    head_open = lower.find("<head")
    close = html.find(">", head_open)
    return html[: close + 1] + tag + html[close + 1 :]


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(java_script_enabled=False)
        page = context.new_page()
        page.set_content(_inject_base(HTML, BASE_URL), wait_until="domcontentloaded")
        urls = page.evaluate(EXTRACT_JOB_LINKS_JS, [PATH_PREFIX, ORIGIN])
        browser.close()

    got = set(urls)
    print(f"Expected ({len(EXPECTED)}): {sorted(EXPECTED)}")
    print(f"Got      ({len(got)}): {sorted(got)}")
    missing = EXPECTED - got
    extra = got - EXPECTED
    if missing or extra:
        if missing:
            print(f"MISSING: {sorted(missing)}")
        if extra:
            print(f"EXTRA:   {sorted(extra)}")
        raise SystemExit(1)
    print("OK")


if __name__ == "__main__":
    main()
