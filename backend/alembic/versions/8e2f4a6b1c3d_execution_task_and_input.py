"""executions: task and input

Revision ID: 8e2f4a6b1c3d
Revises: 5d1e2f3a4b6c
Create Date: 2026-10-08 12:00:00.000000+00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '8e2f4a6b1c3d'
down_revision: Union[str, None] = '5d1e2f3a4b6c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The Orchestrator (Prompt 8) sends `task` to every agent and reads `"from": "input"`
# mappings from `input`. Server defaults fill any execution row that already exists.
def upgrade() -> None:
    op.add_column('executions', sa.Column('task', sa.Text(), server_default='', nullable=False))
    op.add_column('executions', sa.Column('input', postgresql.JSONB(astext_type=sa.Text()),
                                          server_default=sa.text("'{}'::jsonb"), nullable=False))


def downgrade() -> None:
    op.drop_column('executions', 'input')
    op.drop_column('executions', 'task')
