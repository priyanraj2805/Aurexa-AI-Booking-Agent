import asyncio
from sqlalchemy import text
from app.db.database import AsyncSessionLocal

async def check():
    async with AsyncSessionLocal() as db:
        # Check org_configs columns
        result = await db.execute(text("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'org_configs' 
            AND column_name IN ('website_url', 'crawl_status', 'uploaded_docs', 'crawl_started_at', 'crawl_completed_at', 'total_pages_crawled')
        """)
        cols = [r[0] for r in result.fetchall()]
        print('org_configs new columns:', cols)
        
        # Check document_chunks table
        result = await db.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'document_chunks')"))
        print('document_chunks table exists:', result.scalar())
        
        if result.scalar():
            result = await db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'document_chunks'"))
            cols = [r[0] for r in result.fetchall()]
            print('document_chunks columns:', cols)

asyncio.run(check())