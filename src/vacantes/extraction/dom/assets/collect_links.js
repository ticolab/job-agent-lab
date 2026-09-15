([basePath, careerOrigin, minDepth = 1, suppressSelector = null]) => {
    // Recursively walk the DOM to collect <a> elements from the top document,
    // every open shadow root reachable under it, and every same-origin
    // iframe/frame contentDocument. Traversal is best-effort:
    //   - Closed shadow roots are invisible to JS by design; anchors inside
    //     them cannot be reached. Live sites we integrate use `mode: 'open'`
    //     (or declarative shadow DOM with `shadowrootmode="open"`), so this
    //     is not a practical limitation for the current corpus.
    //   - Cross-origin (and sandboxed / opaque-origin) frames throw on
    //     contentDocument access; we catch and skip. Cross-origin anchors
    //     would need a driver-side frame walk, which the runtime's page
    //     handle does not expose — that gap is tracked separately.
    // ``minDepth`` (SYS-3, defaulted so any pre-SYS-3 caller keeps exact
    // current behaviour) is a *floor*, not an exact match, applied inside
    // the id-in-path branch only. A depth of N keeps every anchor whose
    // path has ≥N segments after the prefix — the C9 shape (Databricks,
    // Avionyx/iCIMS) has chrome links at depth 1 sharing the prefix with
    // real postings at depth 2, and setting ``minDepth=2`` drops the
    // chrome without touching the postings. The id-in-query bucket and
    // the Lever id-in-path-preference fallback are structurally untouched:
    // a depth-emptied path bucket falls back to the query bucket exactly
    // like an empty bucket does today. Double-slash segments (empty
    // components after ``split('/')``) inflate ``depth`` accordingly; no
    // board in the current corpus emits them, so this is documented rather
    // than special-cased.
    // ``suppressSelector`` (SYS-14, defaulted so any pre-SYS-14 caller keeps
    // exact current behaviour) drops an anchor when
    // ``anchor.closest(suppressSelector)`` is non-null. It closes the C16
    // shape: a board section whose anchors share the origin, prefix, depth,
    // and URL shape of the real postings, leaving no URL-layer
    // discriminator — Ulteig's UKG "Featured opportunities" block, marked
    // by the platform-owned ``[data-automation="featured-opportunities"]``.
    // Three properties of the gate are load-bearing and each has a test:
    //   - It runs AFTER the visibility gate and BEFORE bucketing. So it is
    //     orthogonal to visibility (a perfectly visible anchor inside the
    //     container is dropped — that is the point), and a path bucket
    //     fully drained by suppression falls back to the id-in-query
    //     bucket exactly like a naturally empty or depth-drained one.
    //   - ``closest()`` does not cross shadow or frame boundaries. That
    //     matches this matcher's per-document scan model: every scanned
    //     document applies the gate independently, so an anchor inside a
    //     shadow root is NOT suppressed by a matching container wrapping
    //     its host in the light DOM. Intended, not an oversight.
    //   - An invalid selector is a loud error, never a silent no-op. See
    //     the validation probe below for why ``closest()``'s own throw is
    //     not sufficient.
    const anchors = [];
    const seenRoots = new WeakSet();
    const collectFrom = (root) => {
        if (!root || seenRoots.has(root)) return;
        seenRoots.add(root);
        let elements;
        try { elements = root.querySelectorAll('*'); }
        catch (e) { return; }
        for (const el of elements) {
            if (el.tagName === 'A') anchors.push(el);
            if (el.shadowRoot) collectFrom(el.shadowRoot);
            if (el.tagName === 'IFRAME' || el.tagName === 'FRAME') {
                let doc;
                try { doc = el.contentDocument; }
                catch (e) { continue; }
                if (doc) collectFrom(doc);
            }
        }
    };
    collectFrom(document);

    // Visibility gate: drop anchors the user cannot see. `checkVisibility`
    // with `checkVisibilityCSS: true` is the standards-track answer
    // (display:none, visibility:hidden, content-visibility:hidden). Fallback
    // for older engines uses `offsetParent`, which is null for display:none
    // and detached subtrees; `position: fixed` elements legitimately have
    // null offsetParent while being visible, so we treat those as visible.
    // Opacity is intentionally NOT checked: opacity-0 anchors are still
    // interactable and are used as legitimate overlay hit targets on some
    // boards. CSS-hidden is not the same as off-screen; we do not attempt
    // viewport-clipping detection here.
    const isVisible = (el) => {
        if (typeof el.checkVisibility === 'function') {
            return el.checkVisibility({ checkVisibilityCSS: true });
        }
        if (el.offsetParent !== null) return true;
        try {
            return getComputedStyle(el).position === 'fixed';
        } catch (e) {
            return false;
        }
    };

    // Validate the suppression selector once, up front. ``closest()``
    // throws SyntaxError on an invalid selector, but only when it actually
    // runs — on a document where every anchor was already dropped by the
    // visibility or origin gate (or a document with no anchors at all, e.g.
    // a same-origin frame that contributes nothing) the gate would never
    // execute and a typo'd selector would silently behave like "no
    // suppression". That is the exact failure mode a config-level knob must
    // not have, so probe the selector against a node that always exists and
    // rethrow with the offending value named. Mirrors the SYS-12
    // ``_INJECT_CSS_JS`` zero-rule guard: config typos fail the run.
    //
    // Scope note: this catches *unparseable* selectors, not everything a
    // human would call a typo. Per the CSS Syntax spec an unclosed block is
    // auto-closed at EOF, so ``[data-automation`` parses as the valid
    // attribute-presence selector ``[data-automation]`` and suppresses
    // accordingly rather than erroring. ``div:::bad``, ``a[``, ``>>>`` and
    // the empty string are genuinely invalid and do throw. Both behaviours
    // are pinned in ``tests/snapshots/test_matcher_rules.py``.
    if (suppressSelector !== null && suppressSelector !== undefined) {
        try {
            document.documentElement.matches(suppressSelector);
        } catch (e) {
            throw new Error(
                'collect_links: suppress_ancestor_selector is not a valid CSS ' +
                'selector: ' + suppressSelector
            );
        }
    }

    const deeperPathUrls = new Set();
    const prefixWithQueryUrls = new Set();
    for (const a of anchors) {
        const href = a.href || '';
        if (!href) continue;
        if (!isVisible(a)) continue;

        // Container suppression (SYS-14). After visibility, before
        // bucketing — see the header comment for why that placement is
        // load-bearing.
        if (suppressSelector !== null && suppressSelector !== undefined) {
            if (a.closest(suppressSelector)) continue;
        }

        let linkUrl;
        try { linkUrl = new URL(href); }
        catch (e) { continue; }

        if (linkUrl.origin !== careerOrigin) continue;

        // Fragment normalization: two anchors that differ only by `#section`
        // point at the same job posting. Collapse them by stripping the hash
        // before bucketing, so the returned list is fragment-deduplicated.
        linkUrl.hash = '';

        const linkPath = linkUrl.pathname.replace(/\/$/, '');
        // The link must live under the job-link prefix, either as a
        // deeper path segment (id in the path, e.g. /jobs/123-eng) or
        // as the prefix itself carrying a query string (id in the
        // query, e.g. /careers/requirements/?pId=180).
        if (linkPath.startsWith(basePath + '/')) {
            // Depth = number of segments after ``<basePath>/``. Trailing
            // slash was already stripped above, so ``<prefix>/a`` and
            // ``<prefix>/a/`` both count as depth 1. ``basePath="/"`` never
            // reaches this branch — the ``startsWith("//")`` test is
            // false for any real path — so the check is unaffected by
            // that pre-existing quirk.
            const remainder = linkPath.slice(basePath.length + 1);
            const depth = remainder.split('/').length;
            if (depth >= minDepth) {
                deeperPathUrls.add(linkUrl.href);
            }
        } else if (linkPath === basePath && linkUrl.search.length > 0) {
            prefixWithQueryUrls.add(linkUrl.href);
        }
    }
    // Prefer id-in-path matches when both shapes appear on the same page.
    // On Lever-style boards the id-in-query shape on a listing-root URL is
    // a filter facet, not a real job link; falling back to the query bucket
    // only when the path bucket is empty preserves query-shape boards
    // (Akurey, 10Pearls) where no id-in-path matches exist. A path bucket
    // fully drained by the ``minDepth`` floor takes the same fallback path
    // — indistinguishable from a naturally empty one.
    const chosen = deeperPathUrls.size > 0 ? deeperPathUrls : prefixWithQueryUrls;
    return Array.from(chosen);
}
