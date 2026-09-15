import asyncio
from app.db.database import AsyncSessionLocal
from sqlalchemy import text as st
import hashlib
async def run():
    async with AsyncSessionLocal() as db:
        r=await db.execute(st("SELECT id FROM chat_sessions WHERE channel_user_id='144367272632357' ORDER BY updated_at DESC LIMIT 1"))
        row=r.fetchone()
        sid=str(row[0])
        print(f"sid={sid}")
        r2=await db.execute(st("SELECT session_id, channel, code_hash, expires_at, created_at FROM otp_records WHERE session_id=:sid"), {"sid": sid})
        for x in r2.fetchall():
            print(f"otp chan={x[1]} hash={x[2]} expires={x[3]} created={x[4]}")
            # try to check against recent codes
            from app.core.security import verify_password
            import inspect
            print(inspect.getsource(verify_password)[:500] if 'verify_password' in dir() else "no verify")
            for code in ["368968","369968","484276","851120","851120"]:
                # try bcrypt verify if hash is bcrypt
                try:
                    import bcrypt
                    # code_hash is hashed code
                    if x[2].startswith("$2"):
                        ok=bcrypt.checkpw(code.encode(), x[2].encode())
                        print(f"  bcrypt {code} -> {ok}")
                except Exception as e:
                    print(f" bcrypt err {e}")
                try:
                    from passlib.context import CryptContext
                    pwd_context=CryptContext(schemes=["bcrypt"], deprecated="auto")
                    ok=pwd_context.verify(code, x[2])
                    print(f"  passlib {code} -> {ok}")
                except Exception as e:
                    print(f" passlib err {e}")

asyncio.run(run())
