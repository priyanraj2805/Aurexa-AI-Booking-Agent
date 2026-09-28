import uuid

import sqlalchemy as sa
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.sql import func

from app.db.base import Base


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id = Column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    doc_id = Column(UUID(as_uuid=True), nullable=True)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    page_number = Column(Integer, nullable=True)
    source_type = Column(String(20), nullable=False)  # pdf | docx | xlsx | csv | txt | md | website
    source_url = Column(Text, nullable=True)
    chunk_metadata = Column("metadata", JSONB, default={})
    tsvector_col = Column(TSVECTOR, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("idx_chunks_fts", sa.text("tsvector_col"), postgresql_using="gin"),
        Index("idx_chunks_org", "org_id"),
        Index("idx_chunks_source", "source_type"),
    )