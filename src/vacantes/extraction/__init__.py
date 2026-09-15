"""Extraction strategies for the job-agent lab.

Five strategies are registered here at import time:

- :class:`~vacantes.extraction.dom.strategy.DomStrategy` — the
  browser-use agent + deterministic in-page matcher pipeline (default).
- :class:`~vacantes.extraction.ats.greenhouse.GreenhouseStrategy` —
  the Greenhouse job-boards API adapter.
- :class:`~vacantes.extraction.ats.phenom.PhenomStrategy` — the
  Phenom People ``refineSearch`` API adapter (SYS-15). Its motivating
  board, BCG, is runtime-clean on the DOM path but has no freezable
  DOM snapshot; a recorded API payload is its regression artifact
  instead, which is how C12 closes for BCG.
- :class:`~vacantes.extraction.ats.talentbrew.TalentbrewStrategy`
  — the Talentbrew (Radancy) results-API adapter (SYS-17). Motivating
  board Citi shares BCG's C12 shape; Talentbrew's cleaner
  URL-addressable facet contract closes it here through a recorded
  API payload at ``tests/fixtures/api/talentbrew/<handle>.json``.
- :class:`~vacantes.extraction.ats.coveo.CoveoStrategy` — the
  Coveo ``/rest/search/v2`` API adapter (SYS-18). Motivating board UST
  is a C1 case rather than C12: its React SPA renders **zero** job
  anchors at any viewport, so the postings are unreachable by any
  matcher extension and exist only as ``clickUri`` values in the
  search XHR. This adapter is therefore the only path to them, not
  merely a better regression artifact.
- :class:`~vacantes.extraction.ats.bamboohr.BambooHrStrategy` —
  the BambooHR ``/careers/list`` adapter. Unlike its siblings this one
  is not motivated by an unreachable DOM: BambooHR's rendered board is
  perfectly matcher-friendly (two corpus entries collect from it on
  ``strategy="dom"``). It exists because the rendered board offers no
  way to *narrow* by region — no location filter, and ``/careers/<id>``
  anchors carrying no location — so the DOM path can only ever return
  the whole board. The JSON listing carries structured location, which
  is the only region discriminator BambooHR provides.

Registration happens in this file (rather than each strategy module
mutating :data:`~vacantes.extraction.base.STRATEGIES` on import)
so the strategy modules stay importable in isolation and there is one
place to audit the registry.
"""

from __future__ import annotations

from vacantes.extraction.ats.bamboohr import BambooHrStrategy
from vacantes.extraction.ats.coveo import CoveoStrategy
from vacantes.extraction.ats.greenhouse import GreenhouseStrategy
from vacantes.extraction.ats.peopleforce import PeopleForceStrategy
from vacantes.extraction.ats.phenom import PhenomStrategy
from vacantes.extraction.ats.talentbrew import TalentbrewStrategy
from vacantes.extraction.base import STRATEGIES
from vacantes.extraction.dom.strategy import DomStrategy

STRATEGIES[DomStrategy.name] = DomStrategy()
STRATEGIES[GreenhouseStrategy.name] = GreenhouseStrategy()
STRATEGIES[PhenomStrategy.name] = PhenomStrategy()
STRATEGIES[TalentbrewStrategy.name] = TalentbrewStrategy()
STRATEGIES[CoveoStrategy.name] = CoveoStrategy()
STRATEGIES[PeopleForceStrategy.name] = PeopleForceStrategy()
STRATEGIES[BambooHrStrategy.name] = BambooHrStrategy()
