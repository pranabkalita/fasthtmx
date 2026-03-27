"""initial schema

Revision ID: 0001_initial
Revises: 
Create Date: 2026-03-27 00:00:00

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("two_factor_enabled", sa.Boolean(), nullable=False),
        sa.Column("two_factor_secret", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("target", sa.String(length=100), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"], unique=False)

    op.create_table(
        "email_verification_tokens",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_email_verification_tokens_token_hash",
        "email_verification_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "login_attempts",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"], unique=False)
    op.create_index("ix_login_attempts_attempted_at", "login_attempts", ["attempted_at"], unique=False)

    op.create_table(
        "password_reset_tokens",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)

    op.create_table(
        "sessions",
        sa.Column("id", mysql.CHAR(length=36), nullable=False),
        sa.Column("user_id", mysql.CHAR(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sessions_token_hash", "sessions", ["token_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sessions_token_hash", table_name="sessions")
    op.drop_table("sessions")

    op.drop_index("ix_password_reset_tokens_token_hash", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")

    op.drop_index("ix_login_attempts_attempted_at", table_name="login_attempts")
    op.drop_index("ix_login_attempts_email", table_name="login_attempts")
    op.drop_table("login_attempts")

    op.drop_index("ix_email_verification_tokens_token_hash", table_name="email_verification_tokens")
    op.drop_table("email_verification_tokens")

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
