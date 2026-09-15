import asyncio
from app.db.database import AsyncSessionLocal
from app.engine.booking_directive.service import get_by_id

async def test():
    async with AsyncSessionLocal() as db:
        dir = await get_by_id(db, '91611b53-e06a-4e14-9c30-bd2c60e2caab')
        if dir:
            print(f'Directive: id={dir.id}')
            print(f'  type attr: {getattr(dir, "type", "NOT_FOUND")}')
            print(f'  type type: {type(getattr(dir, "type", None))}')
            print(f'  directive_type attr: {getattr(dir, "directive_type", "NOT_FOUND")}')
            print(f'  options: {dir.options}')
            print(f'  options type: {type(dir.options)}')
            
            # Simulate renderer logic
            dtype = getattr(dir, "type", None) or getattr(dir, "directive_type", None) or dir.get("type") if isinstance(dir, dict) else None
            print(f'  dtype: {dtype}')
            print(f'  dtype in tuple: {dtype in ("SELECT_DATE", "SELECT_TIME", "SELECT_SLOT")}')
            
            options = getattr(dir, "options", None)
            norm = []
            for o in options or []:
                if isinstance(o, str):
                    norm.append({"id": o, "label": o})
                elif isinstance(o, dict):
                    norm.append({"id": str(o.get("id") or o.get("value") or o.get("label") or ""), "label": str(o.get("label") or o.get("id") or "")})
            print(f'  norm: {norm}')
            print(f'  norm empty: {not norm}')
        else:
            print('Directive not found')
asyncio.run(test())