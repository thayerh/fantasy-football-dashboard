"""switch to dynasty

Rankings become multi-source, and the league stops starting kickers and
defenses.

Three things happen here, and they are one migration because they are one
decision. The league moved to a 12-team superflex PPR dynasty format, in which
`rulebook/02-rosters-and-lineups.md` starts no K and no DEF, and every redraft
ranking and ADP in the database is priced for a competition that no longer
exists.

  1. `dynasty_ranking` replaces `player_ranking` and `player_adp`. Those two
     were single-source snapshots — one FantasyPros feed each — and the new
     board reads four sources at once, so the source becomes half the key.
  2. The redraft rankings and ADP are dropped rather than migrated. There is
     no mapping from a redraft rank to a dynasty one; a 30-year-old QB is a
     different asset under each, which is the whole point of the move.
  3. Kickers and defenses leave the player pool. `ingest.FANTASY_POSITIONS` no
     longer admits them, so a later `ffd sync-players` will not bring them
     back, but the rows already stored have to go now.

The draft board is deliberately left alone. Its picks belong to the finished
redraft league and are still a true record of it; they stop being visible the
moment FFD_SLEEPER_LEAGUE_ID points at the dynasty league, since every draft
query filters on it.

Revision ID: a7c1e9d40b52
Revises: 782862f3c894
Create Date: 2026-09-08 19:05:12.884301

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

import app.db  # noqa: F401  (custom column types used by some migrations)

# revision identifiers, used by Alembic.
revision: str = 'a7c1e9d40b52'
down_revision: str | Sequence[str] | None = '782862f3c894'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The pool filter `app.ingest.sync_players` now applies, restated as SQL.
# Spelled out rather than imported: a migration has to keep describing what it
# actually did, and would start lying the day that constant changes again.
#
# It reads `fantasy_positions`, not `position`, for the same reason the ingest
# does — Sleeper files a fullback as position FB with fantasy_positions ["RB"],
# and he is a real flex body. Matching on `position` would delete all 35 of
# them. On this database it removes 111 kickers, 32 defenses and one punter.
_UNPLAYABLE = """
    NOT EXISTS (
        SELECT 1 FROM json_each(player.fantasy_positions)
        WHERE value IN ('QB', 'RB', 'WR', 'TE')
    )
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'dynasty_ranking',
        sa.Column('source', sa.String(), nullable=False),
        sa.Column('player_id', sa.String(), nullable=False),
        sa.Column('overall_rank', sa.Integer(), nullable=False),
        sa.Column('position_rank', sa.Integer(), nullable=True),
        sa.Column('pos_rank', sa.String(), nullable=True),
        sa.Column('tier', sa.Integer(), nullable=True),
        sa.Column('value', sa.Float(), nullable=True),
        sa.Column('age', sa.Float(), nullable=True),
        sa.Column('rank_min', sa.Integer(), nullable=True),
        sa.Column('rank_max', sa.Integer(), nullable=True),
        sa.Column('rank_ave', sa.Float(), nullable=True),
        sa.Column('rank_std', sa.Float(), nullable=True),
        sa.Column('source_name', sa.String(), nullable=True),
        sa.Column('fetched_at', app.db.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('source', 'player_id'),
    )
    with op.batch_alter_table('dynasty_ranking', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_dynasty_ranking_overall_rank'), ['overall_rank'], unique=False
        )

    # The redraft rankings and ADP, and the tables that held them. Their
    # writers — the FantasyPros API and cheat-sheet syncs — go with them.
    op.drop_table('player_ranking')
    op.drop_table('player_adp')

    # Kickers, defenses and the one punter.
    op.execute(sa.text(f"DELETE FROM player WHERE {_UNPLAYABLE}"))
    # The Vegas projections are kept — they are season points, not a redraft
    # ranking — but rows for the players just removed would join to nothing.
    op.execute(
        sa.text(
            "DELETE FROM player_projection "
            "WHERE player_id NOT IN (SELECT player_id FROM player)"
        )
    )


def downgrade() -> None:
    """Downgrade schema.

    Schema only. The redraft rankings and ADP are not recoverable here — they
    came from feeds, and `ffd sync-players` refills the pool — so this restores
    the shape and leaves the tables empty rather than pretending otherwise.
    """
    op.create_table(
        'player_adp',
        sa.Column('player_id', sa.String(), nullable=False),
        sa.Column('adp', sa.Float(), nullable=True),
        sa.Column('adp_rank', sa.Integer(), nullable=True),
        sa.Column('adp_min', sa.Integer(), nullable=True),
        sa.Column('adp_max', sa.Integer(), nullable=True),
        sa.Column('adp_std', sa.Float(), nullable=True),
        sa.Column('scoring', sa.String(), nullable=True),
        sa.Column('sources', sa.Integer(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('fp_player_id', sa.String(), nullable=True),
        sa.Column('fp_name', sa.String(), nullable=True),
        sa.Column('published_at', app.db.UtcDateTime(), nullable=True),
        sa.Column('fetched_at', app.db.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('player_id'),
    )
    with op.batch_alter_table('player_adp', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_player_adp_adp'), ['adp'], unique=False)

    op.create_table(
        'player_ranking',
        sa.Column('player_id', sa.String(), nullable=False),
        sa.Column('ecr_overall', sa.Integer(), nullable=True),
        sa.Column('ecr_position', sa.Integer(), nullable=True),
        sa.Column('pos_rank', sa.String(), nullable=True),
        sa.Column('tier', sa.Integer(), nullable=True),
        sa.Column('bye_week', sa.Integer(), nullable=True),
        sa.Column('scoring', sa.String(), nullable=True),
        sa.Column('ranking_type', sa.String(), nullable=True),
        sa.Column('experts', sa.Integer(), nullable=True),
        sa.Column('rank_min', sa.Integer(), nullable=True),
        sa.Column('rank_max', sa.Integer(), nullable=True),
        sa.Column('rank_ave', sa.Float(), nullable=True),
        sa.Column('rank_std', sa.Float(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('fp_player_id', sa.String(), nullable=True),
        sa.Column('fp_name', sa.String(), nullable=True),
        sa.Column('published_at', app.db.UtcDateTime(), nullable=True),
        sa.Column('fetched_at', app.db.UtcDateTime(), nullable=False),
        sa.PrimaryKeyConstraint('player_id'),
    )
    with op.batch_alter_table('player_ranking', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_player_ranking_ecr_overall'), ['ecr_overall'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_player_ranking_ecr_position'), ['ecr_position'], unique=False
        )

    with op.batch_alter_table('dynasty_ranking', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_dynasty_ranking_overall_rank'))
    op.drop_table('dynasty_ranking')
