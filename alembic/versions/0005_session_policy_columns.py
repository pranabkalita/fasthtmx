"""session policy columns

Revision ID: 0005_session_policy_columns
Revises: 0004_security_hardening_columns
Create Date: 2026-04-01 00:30:00
"""

from datetime import datetime, timedelta, UTC

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0005_session_policy_columns"
down_revision = "0004_security_hardening_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_columns = {col["name"] for col in inspector.get_columns("sessions")}

    if "last_seen_at" not in existing_columns:
        op.add_column("sessions", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    if "absolute_expires_at" not in existing_columns:
        op.add_column("sessions", sa.Column("absolute_expires_at", sa.DateTime(timezone=True), nullable=True))
    if "step_up_verified_at" not in existing_columns:
        op.add_column("sessions", sa.Column("step_up_verified_at", sa.DateTime(timezone=True), nullable=True))
    if "remember_me" not in existing_columns:
        op.add_column("sessions", sa.Column("remember_me", sa.Boolean(), nullable=False, server_default=sa.false()))

    now = datetime.now(UTC)
    fallback_absolute = now + timedelta(days=7)
    op.execute(sa.text("UPDATE sessions SET last_seen_at = created_at WHERE last_seen_at IS NULL"))
    op.execute(
        sa.text("UPDATE sessions SET absolute_expires_at = :fallback WHERE absolute_expires_at IS NULL").bindparams(
            fallback=fallback_absolute
        )
    )
    op.execute(sa.text("UPDATE sessions SET step_up_verified_at = created_at WHERE step_up_verified_at IS NULL"))

    dt_tz = sa.DateTime(timezone=True)
    op.alter_column("sessions", "last_seen_at", existing_type=dt_tz, nullable=False)
    op.alter_column("sessions", "absolute_expires_at", existing_type=dt_tz, nullable=False)
    op.alter_column("sessions", "step_up_verified_at", existing_type=dt_tz, nullable=False)
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("sessions")}
    if "ix_sessions_last_seen_at" not in existing_indexes:
        op.create_index("ix_sessions_last_seen_at", "sessions", ["last_seen_at"], unique=False)
    if "ix_sessions_absolute_expires_at" not in existing_indexes:
        op.create_index("ix_sessions_absolute_expires_at", "sessions", ["absolute_expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sessions_absolute_expires_at", table_name="sessions")
    op.drop_index("ix_sessions_last_seen_at", table_name="sessions")
    op.drop_column("sessions", "remember_me")
    op.drop_column("sessions", "step_up_verified_at")
    op.drop_column("sessions", "absolute_expires_at")
    op.drop_column("sessions", "last_seen_at")
