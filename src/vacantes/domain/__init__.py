"""Pure-domain types for the job-agent lab.

The domain layer owns the value objects that describe *what* the system
operates on (companies, link rules, extraction results). It intentionally
imports nothing from ``extraction``, ``navigation``, ``reporting``, or any
browser/HTTP library — those layers depend on the domain, never the other
way around. See ``ARCHITECTURE_PROPOSAL.md`` §4.1 for the dependency rule.
"""
