import fitz  # PyMuPDF
from typing import List, Dict
import hashlib
import logging

logger = logging.getLogger(__name__)


async def process_pdf(uploaded_file: bytes, filename: str) -> List[dict]:
    """Extract text chunks from PDF"""
    try:
        doc = fitz.open(stream=uploaded_file, filetype="pdf")
        chunks = []
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            
            # Chunk by ~2000 chars (~500 tokens)
            for chunk in chunk_text(text, max_chars=2000):
                chunks.append({
                    'content': chunk,
                    'page_number': page_num + 1,
                    'source_type': 'pdf'
                })
        
        doc.close()
        return chunks
    except Exception as e:
        logger.error(f"PDF processing failed: {e}")
        raise


def chunk_text(text: str, max_chars: int = 2000) -> List[str]:
    """Split text into chunks by paragraphs"""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    chunks, current = [], ""
    
    for p in paragraphs:
        if len(current) + len(p) > max_chars:
            if current:
                chunks.append(current)
            current = p
        else:
            current += "\n\n" + p
    
    if current:
        chunks.append(current)
    
    return chunks


def compute_content_hash(content: bytes) -> str:
    """Compute SHA256 hash of content"""
    return hashlib.sha256(content).hexdigest()