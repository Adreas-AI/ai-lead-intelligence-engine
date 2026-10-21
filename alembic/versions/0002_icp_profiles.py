"""ICP profiles: leads, scores and imports belong to a profile

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Leads created before profiles existed were all agri-food B2B leads.
LEGACY_PROFILE = "agrifood_b2b"


def upgrade() -> None:
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("profile", sa.String(length=64), nullable=False, server_default=LEGACY_PROFILE)
        )
        batch_op.create_index(batch_op.f("ix_companies_profile"), ["profile"], unique=False)
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.alter_column("profile", server_default=None)

    with op.batch_alter_table("lead_scores", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("profile", sa.String(length=64), nullable=False, server_default=LEGACY_PROFILE)
        )
    with op.batch_alter_table("lead_scores", schema=None) as batch_op:
        batch_op.alter_column("profile", server_default=None)

    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("profile", sa.String(length=64), nullable=True))

    with op.batch_alter_table("enrichments", schema=None) as batch_op:
        batch_op.alter_column("b2c_signals", new_column_name="negative_signals")


def downgrade() -> None:
    with op.batch_alter_table("enrichments", schema=None) as batch_op:
        batch_op.alter_column("negative_signals", new_column_name="b2c_signals")
    with op.batch_alter_table("import_batches", schema=None) as batch_op:
        batch_op.drop_column("profile")
    with op.batch_alter_table("lead_scores", schema=None) as batch_op:
        batch_op.drop_column("profile")
    with op.batch_alter_table("companies", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_companies_profile"))
        batch_op.drop_column("profile")
