"""Applicant-tracking-system API adapters.

Each ATS with a well-behaved public JSON API lives here as a sibling
module: :mod:`greenhouse` today; Lever / Ashby / Workable are natural
future additions when a target board runs on them and the company's
region-filtered subset is not straightforwardly extractable through the
DOM strategy.

The API strategies share the :class:`~vacantes.extraction.base.ExtractionStrategy`
protocol with the DOM strategy: they take a
:class:`~vacantes.domain.company.Company` and a
:class:`~vacantes.extraction.base.RunContext`, and produce the same
report shape via :func:`~vacantes.extraction.base.build_report`.
Region filtering uses
:meth:`~vacantes.domain.region.TargetRegion.matches` on the ATS's
structured location field rather than the DOM strategy's UI filter.
"""
