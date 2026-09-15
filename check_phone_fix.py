import asyncio
from app.engine.validators import DataValidator

async def run():
    # Bare 10-digit Indian mobile, no country -> must become +91...
    res, err, meta = DataValidator._validate_phone("6350063338", country_code=None)
    print("bare10:", res, err, meta)
    assert res == "+916350063338", res
    # Explicit +91 still works
    res2, _, _ = DataValidator._validate_phone("+91 6350063338", country_code=None)
    print("explicit:", res2)
    # US number untouched
    res3, _, _ = DataValidator._validate_phone("+1 555-123-4567", country_code="US")
    print("us:", res3)
    print("PHONE_FIX_OK")

asyncio.run(run())
