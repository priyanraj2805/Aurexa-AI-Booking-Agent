import asyncio
from app.db.database import AsyncSessionLocal
from app.engine.booking_directive.service import get_by_id
from app.core.whatsapp_directive_renderer import render

async def test():
    async with AsyncSessionLocal() as db:
        dir = await get_by_id(db, '91611b53-e06a-4e14-9c30-bd2c60e2caab')
        if dir:
            print(f'Directive: id={dir.id} type={dir.type} status={dir.status} title={dir.title}')
            print(f'Options count: {len(dir.options) if dir.options else 0}')
            result = render(dir)
            print(f'Render result type: {result.get("type")}')
            print(f'Render result: {result}')
        else:
            print('Directive not found')
asyncio.run(test())