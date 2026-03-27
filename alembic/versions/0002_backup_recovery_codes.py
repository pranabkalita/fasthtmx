"""add backup recovery codes

Revision ID: 0002_backup_recovery_codes
Revises: 0001_initial
Create Date: 2026-03-27 00:30:00

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0002_backup_recovery_codes"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backup_recovery_codes",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_backup_recovery_codes_user_id", "backup_recovery_codes", ["user_id"], unique=False)
    op.create_index("ix_backup_recovery_codes_code_hash", "backup_recovery_codes", ["code_hash"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_backup_recovery_codes_code_hash", table_name="backup_recovery_codes")
    op.drop_index("ix_backup_recovery_codes_user_id", table_name="backup_recovery_codes")
    op.drop_table("backup_recovery_codes")
