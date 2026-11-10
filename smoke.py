"""Smoke test: prints status and field names only (no VIN / location values)."""
import httpx

from polestar.client import DOMAINS, PolestarClient

c = PolestarClient()
try:
    vs = c.vehicles()
except httpx.HTTPStatusError as e:
    raise SystemExit(f"vehicles: HTTP {e.response.status_code} {e.response.text[:200]}")
print("vehicles response keys:", list(vs) if isinstance(vs, dict) else type(vs))
vins = vs.get("vins") or vs.get("data") or [] if isinstance(vs, dict) else vs
print("vehicle count:", len(vins))
for vin in vins[:1]:
    vin = vin if isinstance(vin, str) else vin.get("vin")
    for d in DOMAINS:
        try:
            data = c.telemetry(vin, d)["data"]
            print(d, "OK fields:", sorted(data))
        except httpx.HTTPStatusError as e:
            print(d, "HTTP", e.response.status_code)
