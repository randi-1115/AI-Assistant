"""tool_runs

Revision ID: c1d2e3f4a5b6
Revises: b7f3c1a9d2e4
Create Date: 2026-11-21 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'c1d2e3f4a5b6'
down_revision: Union[str, None] = 'b7f3c1a9d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tool_runs',
        sa.Column('id', mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column('message_id', mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column('tool_name', sa.String(length=100), nullable=False),
        sa.Column('input_json', mysql.JSON(), nullable=True),
        sa.Column('output_summary', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('error_code', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('duration_ms', mysql.INTEGER(unsigned=True), nullable=True),
        sa.Column(
            'created_at',
            mysql.DATETIME(fsp=3),
            server_default=sa.text('CURRENT_TIMESTAMP(3)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['message_id'],
            ['messages.id'],
            name=op.f('fk_tool_runs_message_id_messages'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_tool_runs')),
    )
    op.create_index(
        'ix_tool_runs_message_created',
        'tool_runs',
        ['message_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_tool_runs_message_created', table_name='tool_runs')
    op.drop_table('tool_runs')
