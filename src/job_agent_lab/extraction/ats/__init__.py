"""Applicant-tracking-system API adapters.

Each ATS with a well-behaved public JSON API lives here as a sibling
module: :mod:`greenhouse` today; Lever / Ashby / Workable are natural
future additions when a target board runs on them and the company's
region-filtered subset is not straightforwardly extractable through the
DOM strategy.

The API strategies share the :class:`~job_agent_lab.extraction.base.ExtractionStrategy`
protocol with the DOM strategy: they take a
:class:`~job_agent_lab.domain.company.Company` and a
:class:`~job_agent_lab.extraction.base.RunContext`, and produce the same
report shape via :func:`~job_agent_lab.extraction.base.build_report`.
Region filtering uses
:meth:`~job_agent_lab.domain.region.TargetRegion.matches` on the ATS's
structured location field rather than the DOM strategy's UI filter.
"""
