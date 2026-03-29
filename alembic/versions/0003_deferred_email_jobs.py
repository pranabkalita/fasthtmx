"""add deferred email jobs

Revision ID: 0003_deferred_email_jobs
Revises: 0002_backup_recovery_codes
Create Date: 2026-03-29 00:00:00

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0003_deferred_email_jobs"
down_revision = "0002_backup_recovery_codes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deferred_email_jobs",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=True),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("recipients_json", sa.Text(), nullable=False),
        sa.Column("template_name", sa.String(length=100), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False),
        sa.Column("metadata_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deferred_email_jobs_template_name", "deferred_email_jobs", ["template_name"], unique=False)
    op.create_index("ix_deferred_email_jobs_status", "deferred_email_jobs", ["status"], unique=False)
    op.create_index("ix_deferred_email_jobs_available_at", "deferred_email_jobs", ["available_at"], unique=False)
    op.create_index("ix_deferred_email_jobs_created_at", "deferred_email_jobs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_deferred_email_jobs_created_at", table_name="deferred_email_jobs")
    op.drop_index("ix_deferred_email_jobs_available_at", table_name="deferred_email_jobs")
    op.drop_index("ix_deferred_email_jobs_status", table_name="deferred_email_jobs")
    op.drop_index("ix_deferred_email_jobs_template_name", table_name="deferred_email_jobs")
    op.drop_table("deferred_email_jobs")
