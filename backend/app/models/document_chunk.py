from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from sqlalchemy.orm import relationship
from sqlalchemy import ForeignKey
from sqlalchemy.sql import func
from app.db.base import Base
from sqlalchemy.dialects.postgresql import UUID, JSONB, TSVECTOR
from sqlalchemy import Column, String, Text, DateTime, Integer, ForeignKey
from sqlalchemy.orm import relationship
import uuid


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
    created_at = Column(DateTime, server_default=func.now())