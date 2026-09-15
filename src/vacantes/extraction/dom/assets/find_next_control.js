([dryRun = false, overrideSelector = null]) => {
    // Locate the "next page" pagination affordance on the current DOM
    // state and — unless ``dryRun`` — stamp it with the ``data-jal-next``
    // marker attribute so Python-side code can click it via a trusted
    // driver-side selector (browser-use's Element.click / Playwright's
    // page.click). Marker-bridge instead of an in-JS click is deliberate:
    // page.evaluate clicks are untrusted and known to fail on SPAs that
    // gate their pagination pipeline on real user gestures
    // (INTEGRATION_BLOCKERS.md, C7 / BCG).
    //
    // ``overrideSelector`` (SYS-12) is the explicit-CSS bypass path:
    // when set to a non-null string, the standard signal cascade below
    // is skipped entirely and the first ``document.querySelector``
    // match is stamped instead — no visibility gate, no enabled gate,
    // no tag check. It is the caller's responsibility (via the
    // ``RuntimeHooks.next_control_selector`` schema-level validation)
    // to choose a selector that resolves to a clickable control. This
    // fallback exists for boards where the six-signal cascade below
    // cannot legitimately identify the pager: an ARIA-mislabelled
    // control, a nav framed as a non-standard widget, or a signal-5
    // false-positive that outranks the true Next. When
    // ``overrideSelector`` is provided but ``querySelector`` returns
    // no match, the return is ``{found: false}`` — identical to a
    // signal cascade that found nothing — so the walker's no-control
    // termination branch fires.
    //
    // Signal priority, highest first (consulted only when
    // ``overrideSelector`` is null):
    //   1) a[rel~="next"]                  — the standards-track marker
    //   2) a, button with aria-label passing the next-allowlist check
    //   3) a, button with trimmed text passing the next-allowlist check
    //   4) numeric successor: a pure-integer non-anchor "current page"
    //      indicator (e.g. ``<span>1</span>``) followed by a visible
    //      a/button in the *same parent container* whose trimmed text
    //      is exactly N+1.
    //   5) a[class~="next"], button[class~="next"] — CSS token match on
    //      the ``next`` class name; never a substring match. Motivating
    //      shape: Movate's SJB icon-only pager
    //      (``<a class="next page-numbers"><i class="fa fa-angle-right">``)
    //      where trimmed text is empty, no rel / no aria-label, and the
    //      current-page indicator lives in a sibling ``<li>`` outside
    //      signal 4's same-parent scope. Signal 5's class-attribute
    //      promiscuity (carousels and wizards commonly use
    //      ``class="next"`` for their forward affordance) is bounded
    //      *only* by its position below every semantic signal above —
    //      the load-bearing mitigation. **Do not reorder.**
    //   6) a, button with trimmed text or aria-label matching
    //      /^(load|show)\s*more/i (prefix-anchored — ``Load More Jobs``
    //      matches). Motivating shape: Svitla's listing paginates via a
    //      persistent ``Load More`` button that appends the next chunk
    //      into the same DOM state (cumulative append; monotonic +12
    //      per click; button removed at API exhaustion). No walker-loop
    //      change is required: cumulative-append terminates via the
    //      walker's existing zero-new-links rule. The persistent button
    //      is re-discovered and re-stamped each state, which is correct.
    //
    // Next-allowlist (signals 2 and 3): lowercase the raw label/text,
    // split on ``/[^a-z]+/``, drop empties (digits and punctuation
    // vanish — ``"Next »"`` → ``[next]``, ``"View next 25 jobs"`` →
    // ``[view, next, jobs]``). Match iff the tokens include ``next`` and
    // every other token is in the allowlist {view, go, to, the, page,
    // pages, result, results, job, jobs, posting, postings, listing,
    // listings, opening, openings}. This preserves every recorded win
    // (``View next page`` — BCG's aria-label; bare ``Next`` —
    // Techwarely/Nearshore text) while rejecting Svitla's marketing
    // carousels (``Next office``/``Next review``) that would otherwise
    // false-positive on signal 2 *above* the Load-More affordance. The
    // signal-3 semantics widen from exact ``/^next$/i`` to the same
    // allowlist so multi-word shapes like ``Go to next page`` can win
    // legitimately; ``Nextcloud`` (single token ≠ ``next``) still
    // rejects.
    //
    // Every candidate must additionally be:
    //   - an <a> or <button> — de-anchored ``<span>`` last-page pagers
    //     (per Techwarely's Prev/1 spans) are never candidates
    //   - visible per the same gate the matcher asset applies
    //   - not [disabled] and not [aria-disabled="true"]
    //
    // Scope: light DOM of the top document only. The matcher asset walks
    // shadow roots and same-origin frames for anchor collection; pagers
    // are near-universally in the light DOM and every extra surface
    // enlarges the false-positive budget. Shadow / frame pager discovery
    // is deferred until a board demands it.
    //
    // Return shape: ``{found: bool, signal: string|null, text: string|null}``.
    // ``signal`` names the rule that fired (``"rel-next"``,
    // ``"aria-label"``, ``"text-next"``, ``"numeric-successor"``,
    // ``"class-next"``, ``"load-more"``, ``"selector-override"``);
    // ``text`` is the winning element's trimmed textContent (or the
    // matched aria-label / load-more source string for the branches
    // that read those) for diagnostic logs.

    const MARKER = 'data-jal-next';

    // Clear any stale marker from a previous pass. Doing this
    // unconditionally — even on the "found nothing" path — means the
    // driver never sees a marker attached to a stale winner from an
    // earlier state, so ``page.click('[data-jal-next]')`` is always
    // pointing at the current pass's decision.
    for (const stale of document.querySelectorAll('[' + MARKER + ']')) {
        stale.removeAttribute(MARKER);
    }

    // SYS-12 override branch: when an explicit CSS selector is
    // provided, the standard signal cascade below is skipped
    // entirely. First ``querySelector`` match wins, no filtering. A
    // null return propagates as ``found: false`` so the walker
    // terminates the same way it does on a signal-cascade miss.
    if (overrideSelector != null) {
        const overrideEl = document.querySelector(overrideSelector);
        if (!overrideEl) {
            return { found: false, signal: null, text: null };
        }
        if (!dryRun) overrideEl.setAttribute(MARKER, '');
        const overrideText = (overrideEl.textContent || '').trim();
        return { found: true, signal: 'selector-override', text: overrideText };
    }

    // Visibility gate: duplicated from the matcher asset because these
    // two assets ship independently and the discovery pass must not
    // depend on the matcher's helpers being in scope. Kept intentionally
    // small and symmetric — same standards-track check with the same
    // offsetParent / position:fixed fallback.
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

    const isEnabled = (el) => {
        if (el.hasAttribute('disabled')) return false;
        const ad = el.getAttribute('aria-disabled');
        if (ad != null && ad.toLowerCase() === 'true') return false;
        return true;
    };

    const isCandidate = (el) =>
        !!el
        && (el.tagName === 'A' || el.tagName === 'BUTTON')
        && isVisible(el)
        && isEnabled(el);

    const stamp = (el, signal, textSource) => {
        if (!dryRun) el.setAttribute(MARKER, '');
        const raw = textSource != null ? textSource : (el.textContent || '');
        return { found: true, signal, text: raw.trim() };
    };

    // Shared next-allowlist helper for signals 2 and 3. See header
    // comment for the tokenization rules and evidence citations.
    // Signal 2 strictly narrows (was ``/next/i`` substring — ``Next
    // office`` / ``Next review`` used to false-positive here above the
    // Load-More affordance on Svitla). Signal 3 widens from exact
    // ``/^next$/i`` — ``Go to next page`` and other in-allowlist
    // multi-word shapes are legitimate wins.
    const NEXT_ALLOWED_TOKENS = new Set([
        'view', 'go', 'to', 'the',
        'page', 'pages',
        'result', 'results',
        'job', 'jobs',
        'posting', 'postings',
        'listing', 'listings',
        'opening', 'openings',
    ]);
    const matchesNextAllowlist = (raw) => {
        if (raw == null) return false;
        const tokens = raw.toLowerCase().split(/[^a-z]+/).filter(Boolean);
        if (tokens.length === 0) return false;
        let sawNext = false;
        for (const tok of tokens) {
            if (tok === 'next') { sawNext = true; continue; }
            if (!NEXT_ALLOWED_TOKENS.has(tok)) return false;
        }
        return sawNext;
    };

    // Signal 1: rel=next. Only anchors carry ``rel``; the query is scoped
    // accordingly. Space-separated token match via ``[rel~="next"]``.
    for (const el of document.querySelectorAll('a[rel~="next"]')) {
        if (isCandidate(el)) return stamp(el, 'rel-next', null);
    }

    // Collect the live pool of interactive controls once. Signals 2, 3,
    // 5 and 6 all filter this same pool; scanning it more than once
    // would be wasteful.
    const controls = Array.from(document.querySelectorAll('a, button'))
        .filter(isCandidate);

    // Signal 2: aria-label under the next-allowlist. ``View next page``
    // (BCG) passes; ``View previous page`` fails (no ``next`` token);
    // ``Next office`` / ``Next review`` (Svitla marketing carousels)
    // fail (``office`` / ``review`` outside the allowlist), so they no
    // longer win above the real Load-More affordance below.
    for (const el of controls) {
        const label = el.getAttribute('aria-label') || '';
        if (label && matchesNextAllowlist(label)) {
            return stamp(el, 'aria-label', label);
        }
    }

    // Signal 3: trimmed textContent under the same next-allowlist.
    // Bare ``Next`` (Techwarely, Nearshore) passes as a single-token
    // match; multi-word shapes like ``Go to next page`` now pass
    // legitimately; ``Nextcloud`` (single token ≠ ``next``) still
    // rejects.
    for (const el of controls) {
        const text = (el.textContent || '').trim();
        if (matchesNextAllowlist(text)) return stamp(el, 'text-next', text);
    }

    // Signal 4: numeric successor of a pure-integer current-page indicator.
    // Real-world shape: ``<span>1</span><a>2</a><a>3</a>``, with the
    // current page rendered as a de-anchored span/strong/em and successor
    // pages as anchors. Candidates for the indicator are elements that:
    //   - are not <a>/<button> themselves (those would already have been
    //     caught by signals 1-3 if they were the current page)
    //   - are leaf-ish: no element children (``<div>Page <span>2</span>
    //     of 5</div>`` would otherwise match with textContent "Page 2 of
    //     5"; we only want the tight leaf carrying the integer)
    //   - have trimmed text matching /^\d+$/
    // The successor must live in the same parent container so unrelated
    // integer labels elsewhere on the page (footer years, price tags)
    // cannot be paired with a random N+1 anchor across the DOM.
    const intOnly = /^\d+$/;
    for (const cur of document.querySelectorAll('*')) {
        if (cur.tagName === 'A' || cur.tagName === 'BUTTON') continue;
        if (cur.children.length > 0) continue;
        const curText = (cur.textContent || '').trim();
        if (!intOnly.test(curText)) continue;

        const parent = cur.parentElement;
        if (!parent) continue;
        const n = parseInt(curText, 10);

        for (const el of parent.querySelectorAll('a, button')) {
            if (!isCandidate(el)) continue;
            const text = (el.textContent || '').trim();
            if (intOnly.test(text) && parseInt(text, 10) === n + 1) {
                return stamp(el, 'numeric-successor', text);
            }
        }
    }

    // Signal 5: elements carrying a ``next`` class token. CSS token
    // operator (``a[class~="next"]``), never a substring — so a class
    // like ``next-of-kin`` or ``anextbutton`` does not falsely satisfy
    // this. Reuses the already-filtered ``controls`` pool via
    // ``classList.contains('next')``, which is equivalent to the
    // ``[class~="next"]`` selector. See the header comment for the
    // Movate SJB shape that motivates this signal and the load-bearing
    // position-below-semantic-signals mitigation for its class-attribute
    // promiscuity risk (carousels / wizards use ``class="next"`` too).
    for (const el of controls) {
        if (el.classList.contains('next')) {
            return stamp(el, 'class-next', null);
        }
    }

    // Signal 6: Load-More / Show-More affordance. Prefix-anchored on the
    // trimmed text *or* aria-label so ``Load More Jobs``, ``Show More``,
    // ``load more`` (lowercase, no whitespace tolerated between ``load``
    // and ``more`` beyond ``/\s*/``) all match. Cumulative-append boards
    // terminate via the walker's existing zero-new-links rule; the
    // persistent button gets re-discovered and re-stamped each state,
    // which is correct — the marker moves in place, the walker keeps
    // clicking, and the union of URL sets grows monotonically until the
    // API is exhausted (Svitla evidence).
    const loadMoreRe = /^(load|show)\s*more/i;
    for (const el of controls) {
        const text = (el.textContent || '').trim();
        if (loadMoreRe.test(text)) return stamp(el, 'load-more', text);
        const label = el.getAttribute('aria-label') || '';
        if (label && loadMoreRe.test(label)) {
            return stamp(el, 'load-more', label);
        }
    }

    return { found: false, signal: null, text: null };
}
