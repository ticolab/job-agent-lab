"""Record the one-shot eligibility contract on attempts and decisions.

Adds the eligibility prompt hash to attempts, and the prompt hash plus
conflicting evidence to decisions. Existing rows keep null values: they were
decided under an earlier contract and are never backfilled with a hash or a
conflicting-evidence list they did not have. Decisions gain composite
attempt/job and content/job foreign keys so a decision can never cite another
job's attempt or content.

Downgrade drops the new columns, losing one-shot prompt hashes and conflicting
evidence; supporting evidence, reasons, and contract versions survive.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a5c3e9d17b40"
down_revision: str | Sequence[str] | None = "e7a9b2c4d601"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The original job_decisions foreign keys are unnamed in SQLite; batch mode
# reflects them under this convention so they can be dropped by name.
_FK_CONVENTION = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def upgrade() -> None:
    with op.batch_alter_table("job_attempts") as batch:
        batch.add_column(
            sa.Column("eligibility_prompt_hash", sa.String(), nullable=True)
        )
        batch.create_unique_constraint("uq_job_attempts_id_job", ["id", "job_id"])
    with op.batch_alter_table("job_contents") as batch:
        batch.create_unique_constraint("uq_job_contents_id_job", ["id", "job_id"])
    with op.batch_alter_table(
        "job_decisions", naming_convention=_FK_CONVENTION
    ) as batch:
        batch.add_column(
            sa.Column("conflicting_evidence_json", sa.Text(), nullable=True)
        )
        batch.add_column(sa.Column("prompt_hash", sa.String(), nullable=True))
        batch.create_check_constraint(
            "ck_job_decisions_contract_pair",
            "(prompt_hash IS NULL) = (conflicting_evidence_json IS NULL)",
        )
        batch.drop_constraint(
            "fk_job_decisions_attempt_id_job_attempts", type_="foreignkey"
        )
        batch.drop_constraint(
            "fk_job_decisions_content_id_job_contents", type_="foreignkey"
        )
        batch.create_foreign_key(
            "fk_job_decisions_attempt_job",
            "job_attempts",
            ["attempt_id", "job_id"],
            ["id", "job_id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_job_decisions_content_job",
            "job_contents",
            ["content_id", "job_id"],
            ["id", "job_id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("job_decisions") as batch:
        batch.drop_constraint("fk_job_decisions_content_job", type_="foreignkey")
        batch.drop_constraint("fk_job_decisions_attempt_job", type_="foreignkey")
        batch.create_foreign_key(
            "fk_job_decisions_attempt_id_job_attempts",
            "job_attempts",
            ["attempt_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_foreign_key(
            "fk_job_decisions_content_id_job_contents",
            "job_contents",
            ["content_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.drop_constraint("ck_job_decisions_contract_pair", type_="check")
        batch.drop_column("prompt_hash")
        batch.drop_column("conflicting_evidence_json")
    with op.batch_alter_table("job_contents") as batch:
        batch.drop_constraint("uq_job_contents_id_job", type_="unique")
    with op.batch_alter_table("job_attempts") as batch:
        batch.drop_constraint("uq_job_attempts_id_job", type_="unique")
        batch.drop_column("eligibility_prompt_hash")
