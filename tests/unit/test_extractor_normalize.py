from __future__ import annotations

from job_extractor.normalize import load_technology_entries, normalize
from job_extractor.schema import TechnologyMention


def mention(
    value: str, status: str = "mentioned", evidence: str | None = None
) -> TechnologyMention:
    return TechnologyMention.model_validate(
        {
            "mention": value,
            "status": status,
            "evidence": [evidence or value],
        }
    )


def test_alias_and_version_normalization() -> None:
    technologies = normalize([mention("Python 3.11"), mention("AWS")])
    assert [item.canonical for item in technologies] == [
        "Python",
        "Amazon Web Services",
    ]


def test_angularjs_and_angular_are_distinct() -> None:
    technologies = normalize([mention("AngularJS"), mention("Angular 17")])
    assert [item.canonical for item in technologies] == ["AngularJS", "Angular"]


def test_plain_sql_is_a_concept_not_a_vendor() -> None:
    technology = normalize([mention("SQL")])[0]
    assert technology.canonical == "SQL"
    assert technology.kind == "concept"


def test_dotnet_family_follows_legacy_alias_rule() -> None:
    technologies = normalize(
        [mention(".NET"), mention(".NET Core"), mention(".NET Framework")]
    )
    assert len(technologies) == 1
    assert technologies[0].canonical == ".NET"
    assert len(technologies[0].mentions) == 3


def test_status_precedence_preserves_all_mentions_and_evidence() -> None:
    technologies = normalize(
        [
            mention("ReactJS", "preferred", "ReactJS is a plus"),
            mention("React", "required", "React is required"),
            mention("React.js", "mentioned", "We build with React.js"),
        ]
    )
    assert len(technologies) == 1
    assert technologies[0].status == "required"
    assert [item.mention for item in technologies[0].mentions] == [
        "ReactJS",
        "React",
        "React.js",
    ]


def test_unmatched_mentions_are_preserved_and_grouped_by_raw_name() -> None:
    technologies = normalize([mention("NewDB"), mention("newdb", "preferred")])
    assert len(technologies) == 1
    assert technologies[0].canonical is None
    assert technologies[0].kind is None
    assert technologies[0].status == "preferred"
    assert [item.mention for item in technologies[0].mentions] == [
        "NewDB",
        "newdb",
    ]


def test_aliases_are_unique_case_insensitively() -> None:
    aliases = [
        alias.casefold()
        for entry in load_technology_entries()
        for alias in entry.aliases
    ]
    assert len(aliases) == len(set(aliases))
