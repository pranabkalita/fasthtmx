"""security hardening columns

Revision ID: 0004_security_hardening_columns
Revises: 0003_deferred_email_jobs
Create Date: 2026-04-01 00:00:00
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0004_security_hardening_columns"
down_revision = "0003_deferred_email_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("two_factor_secret_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "two_factor_secret_encrypted")
