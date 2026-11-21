"""Record the reasoning effort a pipeline run and each attempt used.

Adds a nullable ``reasoning_effort`` column to ``pipeline_runs`` and
``job_attempts``, constrained to the efforts the LiteLLM caller accepts.
Existing rows keep null: they ran with ``temperature=0`` and no reasoning
effort, which is exactly what null records.

Downgrade drops both columns, losing which runs and attempts used a reasoning
effort; the model name on each row survives.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e1d4b9a2f6"
down_revision: str | Sequence[str] | None = "a5c3e9d17b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EFFORTS = "reasoning_effort IN ('minimal', 'low', 'medium', 'high')"


def upgrade() -> None:
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.add_column(sa.Column("reasoning_effort", sa.String(), nullable=True))
        batch.create_check_constraint("ck_pipeline_runs_reasoning_effort", _EFFORTS)
    with op.batch_alter_table("job_attempts") as batch:
        batch.add_column(sa.Column("reasoning_effort", sa.String(), nullable=True))
        batch.create_check_constraint("ck_job_attempts_reasoning_effort", _EFFORTS)


def downgrade() -> None:
    with op.batch_alter_table("job_attempts") as batch:
        batch.drop_constraint("ck_job_attempts_reasoning_effort", type_="check")
        batch.drop_column("reasoning_effort")
    with op.batch_alter_table("pipeline_runs") as batch:
        batch.drop_constraint("ck_pipeline_runs_reasoning_effort", type_="check")
        batch.drop_column("reasoning_effort")
