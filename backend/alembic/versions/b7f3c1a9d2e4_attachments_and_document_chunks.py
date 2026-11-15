"""attachments and document_chunks

Revision ID: b7f3c1a9d2e4
Revises: 44d9a68ebf00
Create Date: 2026-11-13 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'b7f3c1a9d2e4'
down_revision: Union[str, None] = '44d9a68ebf00'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'attachments',
        sa.Column('id', mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column('user_id', mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column('conversation_id', mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column('original_name', sa.String(length=255), nullable=False),
        sa.Column('storage_key', sa.String(length=500), nullable=False),
        sa.Column('mime_type', sa.String(length=150), nullable=True),
        sa.Column('extension', sa.String(length=20), nullable=False),
        sa.Column('size_bytes', mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column('sha256', sa.CHAR(length=64), nullable=True),
        sa.Column('parse_status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            mysql.DATETIME(fsp=3),
            server_default=sa.text('CURRENT_TIMESTAMP(3)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['conversation_id'],
            ['conversations.id'],
            name=op.f('fk_attachments_conversation_id_conversations'),
            ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.id'],
            name=op.f('fk_attachments_user_id_users'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_attachments')),
    )
    op.create_index(
        'ix_attachments_conversation_id', 'attachments', ['conversation_id'], unique=False
    )
    op.create_index(
        'ix_attachments_user_created', 'attachments', ['user_id', 'created_at'], unique=False
    )

    op.create_table(
        'document_chunks',
        sa.Column('id', mysql.BIGINT(unsigned=True), autoincrement=True, nullable=False),
        sa.Column('attachment_id', mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column('chunk_index', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('section_title', sa.String(length=255), nullable=True),
        sa.Column('content', mysql.MEDIUMTEXT(), nullable=False),
        sa.Column('char_count', mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column('metadata_json', sa.JSON(), nullable=True),
        sa.Column(
            'created_at',
            mysql.DATETIME(fsp=3),
            server_default=sa.text('CURRENT_TIMESTAMP(3)'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ['attachment_id'],
            ['attachments.id'],
            name=op.f('fk_document_chunks_attachment_id_attachments'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_document_chunks')),
        sa.UniqueConstraint(
            'attachment_id',
            'chunk_index',
            name='uq_document_chunks_attachment_chunk_index',
        ),
    )


def downgrade() -> None:
    op.drop_table('document_chunks')
    op.drop_index('ix_attachments_user_created', table_name='attachments')
    op.drop_index('ix_attachments_conversation_id', table_name='attachments')
    op.drop_table('attachments')
