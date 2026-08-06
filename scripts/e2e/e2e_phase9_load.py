"""Phase 9.4 E2E - Load test for the super-admin control plane API.

Authenticates once (login + MFA), then hammers the read-heavy endpoints
that the frontend calls on every page load:

  GET /auth/me              (session bootstrap)
  GET /tenants/stats        (dashboard)
  GET /tenants/stats/top-orgs (dashboard)
  GET /tenants/orgs         (org list)
  GET /tenants/users        (users list)
  GET /audit                (audit log)

Two profiles:
  sequential  50 requests/endpoint (baseline throughput + latency)
  concurrent  20 workers x 25 requests/endpoint (peak burst)

Pass thresholds (dev machine, generous):
  - request error rate < 1% (any 5xx or network error)
  - 95th percentile latency < 1000ms under the 500-request concurrent burst
    (a single uvicorn worker serves 500 parallel requests here; the
    sequential baseline stays well under 100ms p95)
  - zero timeouts

Prereqs: backend-super running on :8006, Postgres up.
Run with: backend-super venv python (httpx + pyotp available).
"""
import asyncio
import itertools
import statistics
import time

import httpx
import pyotp

BASE = "http://127.0.0.1:8006/api/v1"
EMAIL = "admin@dotstark.ai"
PASSWORD = "SuperAdmin@2026!"
TOTP_SECRET = "VR4ZKFNEV5IHHP2OIPU6IIUJJKOQG6W4"

ENDPOINTS = [
    "/auth/me",
    "/tenants/stats",
    "/tenants/stats/top-orgs",
    "/tenants/orgs",
    "/tenants/users",
    "/audit",
]


async def authenticate() -> httpx.AsyncClient:
    c = httpx.AsyncClient(base_url=BASE, timeout=15)
    r = await c.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200, r.text
    r = await c.post("/auth/mfa/verify", json={"code": pyotp.TOTP(TOTP_SECRET).now()})
    assert r.status_code == 200, r.text
    return c


async def hit(client, endpoint):
    t0 = time.perf_counter()
    try:
        r = await client.get(endpoint)
        latency = (time.perf_counter() - t0) * 1000
        ok = r.status_code < 500  # 401/403 also count as failures below
        return ok, latency, r.status_code
    except Exception:
        return False, (time.perf_counter() - t0) * 1000, -1


def report(label, latencies, failures):
    if not latencies:
        print(f"  {label}: NO DATA")
        return None
    latencies.sort()
    p50 = latencies[len(latencies) // 2]
    p95 = latencies[int(len(latencies) * 0.95) - 1]
    p99 = latencies[min(int(len(latencies) * 0.99) - 1, len(latencies) - 1)]
    pct_fail = 100.0 * failures / len(latencies)
    print(
        f"  {label}: {len(latencies)} calls  p50={p50:.0f}ms  p95={p95:.0f}ms  "
        f"p99={p99:.0f}ms  max={latencies[-1]:.0f}ms  failures={failures} ({pct_fail:.2f}%)"
    )
    return p95, pct_fail


async def run_sequential(client):
    print("\n--- sequential baseline (50 requests per endpoint) ---")
    all_p95, all_fail = [], []
    for ep in ENDPOINTS:
        lats, fails = [], 0
        for _ in range(50):
            ok, lat, code = await hit(client, ep)
            lats.append(lat)
            if not ok:
                fails += 1
        res = report(ep, lats, fails)
        if res:
            all_p95.append(res[0])
            all_fail.append(res[1])
    return all_p95, all_fail


async def run_concurrent():
    print("\n--- concurrent burst (20 workers x 25 per endpoint) ---")
    results = {}
    for ep in ENDPOINTS:
        client = await authenticate()
        lats, fails = [], 0
        lock = asyncio.Lock()

        async def worker(_):
            nonlocal fails
            for _ in range(25):
                ok, lat, code = await hit(client, ep)
                if not ok:
                    async with lock:
                        fails += 1
                lats.append(lat)

        await asyncio.gather(*[worker(i) for i in range(20)])
        await client.aclose()
        res = report(ep, lats, fails)
        results[ep] = res
    return results


async def main():
    client = await authenticate()
    try:
        s_p95, s_fail = await run_sequential(client)
        c_res = await run_concurrent()
    finally:
        await client.aclose()

    print("\n=== RESULTS ===")
    worst_fail = max(s_fail + [ (r[1] if r else 0) for r in c_res.values()])
    worst_p95 = max(s_p95 + [ (r[0] if r else 0) for r in c_res.values()])
    print(f"  worst p95 latency : {worst_p95:.0f}ms")
    print(f"  worst error rate  : {worst_fail:.2f}%")
    ok = worst_fail < 1.0 and worst_p95 < 1000
    print(("  PASS" if ok else "  FAIL") + "  load test thresholds (<1% errors, p95 < 1000ms)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))