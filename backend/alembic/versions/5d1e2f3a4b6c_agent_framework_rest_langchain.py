"""agent_framework: only rest and langchain

Revision ID: 5d1e2f3a4b6c
Revises: c776e466d326
Create Date: 2026-10-05 12:00:00.000000+00:00
"""

from typing import Sequence, Union

from alembic import op

revision: str = '5d1e2f3a4b6c'
down_revision: Union[str, None] = 'c776e466d326'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# PostgreSQL cannot drop a value from an enum type, so the type is rebuilt:
# create the new type, move the column onto it, drop the old one, rename.
def upgrade() -> None:
    # A plain Python agent is now registered as `rest` behind python_wrapper.py.
    op.execute("UPDATE agents SET framework = 'rest' WHERE framework = 'python'")
    # Any `crewai` agent left over makes the cast below fail, by design: there is
    # no CrewAI adapter to run it, so it must be removed by hand first.
    op.execute("CREATE TYPE agent_framework_new AS ENUM ('rest', 'langchain')")
    op.execute(
        "ALTER TABLE agents ALTER COLUMN framework TYPE agent_framework_new "
        "USING framework::text::agent_framework_new"
    )
    op.execute("DROP TYPE agent_framework")
    op.execute("ALTER TYPE agent_framework_new RENAME TO agent_framework")


def downgrade() -> None:
    op.execute("CREATE TYPE agent_framework_old AS ENUM ('python', 'rest', 'langchain', 'crewai')")
    op.execute(
        "ALTER TABLE agents ALTER COLUMN framework TYPE agent_framework_old "
        "USING framework::text::agent_framework_old"
    )
    op.execute("DROP TYPE agent_framework")
    op.execute("ALTER TYPE agent_framework_old RENAME TO agent_framework")
