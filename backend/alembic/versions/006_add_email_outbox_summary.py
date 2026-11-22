"""add email outbox and lead summary

Revision ID: 006
Revises: 005
Create Date: 2026-10-23

Phase 6: Email outbox for transactional emails + lead summary field
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade():
    """Add email_outbox table and leads.summary column"""

    # 1. Add summary column to leads table
    op.add_column(
        "leads",
        sa.Column("summary", sa.Text(), nullable=True)
    )

    # 2. Create email_outbox table
    op.create_table(
        "email_outbox",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("to_email", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=False),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("related_type", sa.String(), nullable=True),
        sa.Column("related_id", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint("id")
    )

    # 3. Create indexes for performance
    op.create_index("ix_email_outbox_id", "email_outbox", ["id"], unique=False)
    op.create_index("ix_email_outbox_to_email", "email_outbox", ["to_email"], unique=False)
    op.create_index("ix_email_outbox_status", "email_outbox", ["status"], unique=False)
    op.create_index("ix_email_outbox_next_attempt_at", "email_outbox", ["next_attempt_at"], unique=False)
    op.create_index("ix_email_outbox_dedupe_key", "email_outbox", ["dedupe_key"], unique=True)
    op.create_index("ix_email_outbox_related_type", "email_outbox", ["related_type"], unique=False)


def downgrade():
    """Remove email_outbox table and leads.summary column"""

    # Drop indexes
    op.drop_index("ix_email_outbox_related_type", table_name="email_outbox")
    op.drop_index("ix_email_outbox_dedupe_key", table_name="email_outbox")
    op.drop_index("ix_email_outbox_next_attempt_at", table_name="email_outbox")
    op.drop_index("ix_email_outbox_status", table_name="email_outbox")
    op.drop_index("ix_email_outbox_to_email", table_name="email_outbox")
    op.drop_index("ix_email_outbox_id", table_name="email_outbox")

    # Drop table
    op.drop_table("email_outbox")

    # Drop column
    op.drop_column("leads", "summary")
