"""Split pipeline stages.

Downgrade loses the waiting-state distinction by mapping ready_for_extraction
jobs to immediately due extraction retries, and maps ready_for_extraction
attempt outcomes to retry and missing_checkpoint errors to internal. Run stage
and trigger metadata are dropped; upgrading again backfills combined and derives
triggers from attempts rather than recovering the original run identity.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "e7a9b2c4d601"
down_revision: str | Sequence[str] | None = "c9eded2a94c2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATUSES = "'new', 'in_progress', 'retry', 'completed_eligible', 'completed_ineligible', 'unknown', 'unavailable', 'failed', 'merged'"
OLD_OUTCOMES = "'completed_eligible', 'completed_ineligible', 'unknown', 'unavailable', 'retry', 'failed', 'merged', 'interrupted', 'skipped'"
OLD_ERRORS = "'not_found', 'closed', 'blocked', 'timeout', 'transport', 'server_error', 'empty_content', 'off_site_redirect', 'malformed', 'model_call', 'validation_failed', 'ungrounded_evidence', 'internal', 'interrupted'"


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_status", type_="check")
        batch.create_check_constraint(
            "ck_jobs_status", f"status IN ({OLD_STATUSES}, 'ready_for_extraction')"
        )
        batch.create_check_constraint(
            "ck_jobs_ready_released",
            "status != 'ready_for_extraction' OR (next_phase = 'extraction' AND current_attempt_id IS NULL)",
        )
    with op.batch_alter_table("job_attempts") as batch:
        batch.drop_constraint("ck_job_attempts_outcome", type_="check")
        batch.drop_constraint("ck_job_attempts_error_category", type_="check")
        batch.create_check_constraint(
            "ck_job_attempts_outcome",
            f"outcome IN ({OLD_OUTCOMES}, 'ready_for_extraction')",
        )
        batch.create_check_constraint(
            "ck_job_attempts_error_category",
            f"error_category IN ({OLD_ERRORS}, 'missing_checkpoint')",
        )
    op.add_column("pipeline_runs", sa.Column("stage", sa.String(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("trigger", sa.String(), nullable=True))
    op.execute(
        "UPDATE pipeline_runs SET stage = 'combined', trigger = CASE WHEN EXISTS "
        "(SELECT 1 FROM job_attempts WHERE job_attempts.run_id = pipeline_runs.id "
        "AND job_attempts.trigger = 'reprocess') THEN 'reprocess' ELSE 'scheduled' END"
    )
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.alter_column("stage", existing_type=sa.String(), nullable=False)
        batch.alter_column("trigger", existing_type=sa.String(), nullable=False)
        batch.create_check_constraint(
            "ck_pipeline_runs_stage",
            "stage IN ('combined', 'eligibility', 'extraction')",
        )
        batch.create_check_constraint(
            "ck_pipeline_runs_trigger", "trigger IN ('scheduled', 'reprocess')"
        )


def downgrade() -> None:
    moment = datetime.now(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")
    op.get_bind().execute(
        sa.text(
            "UPDATE jobs SET status = 'retry', next_phase = 'extraction', "
            "next_attempt_after = :moment WHERE status = 'ready_for_extraction'"
        ),
        {"moment": moment},
    )
    op.execute(
        "UPDATE job_attempts SET outcome = 'retry' WHERE outcome = 'ready_for_extraction'"
    )
    op.execute(
        "UPDATE job_attempts SET error_category = 'internal' WHERE error_category = 'missing_checkpoint'"
    )
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_ready_released", type_="check")
        batch.drop_constraint("ck_jobs_status", type_="check")
        batch.create_check_constraint("ck_jobs_status", f"status IN ({OLD_STATUSES})")
    with op.batch_alter_table("job_attempts") as batch:
        batch.drop_constraint("ck_job_attempts_outcome", type_="check")
        batch.drop_constraint("ck_job_attempts_error_category", type_="check")
        batch.create_check_constraint(
            "ck_job_attempts_outcome", f"outcome IN ({OLD_OUTCOMES})"
        )
        batch.create_check_constraint(
            "ck_job_attempts_error_category", f"error_category IN ({OLD_ERRORS})"
        )
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_constraint("ck_pipeline_runs_stage", type_="check")
        batch.drop_constraint("ck_pipeline_runs_trigger", type_="check")
        batch.drop_column("stage")
        batch.drop_column("trigger")
