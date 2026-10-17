"""add dynasty ranking history

Revision ID: c3e8a1f05d27
Revises: 492f86eec5e4
Create Date: 2026-10-16 22:45:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c3e8a1f05d27'
down_revision: str | Sequence[str] | None = '492f86eec5e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('dynasty_ranking_history',
    sa.Column('as_of', sa.Date(), nullable=False),
    sa.Column('source', sa.String(), nullable=False),
    sa.Column('player_id', sa.String(), nullable=False),
    sa.Column('overall_rank', sa.Integer(), nullable=False),
    sa.Column('position_rank', sa.Integer(), nullable=True),
    sa.Column('tier', sa.Integer(), nullable=True),
    sa.Column('value', sa.Float(), nullable=True),
    sa.PrimaryKeyConstraint('as_of', 'source', 'player_id')
    )
    with op.batch_alter_table('dynasty_ranking_history', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_dynasty_ranking_history_player_id'), ['player_id'], unique=False
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('dynasty_ranking_history', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dynasty_ranking_history_player_id'))
    op.drop_table('dynasty_ranking_history')
