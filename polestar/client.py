"""Minimal Polestar Data Portal M2M client: cached token + telemetry GETs."""
import os
import time

import httpx
from dotenv import load_dotenv

DOMAINS = (
    "battery", "odometer", "location", "availability",
    "exterior", "health", "parkingClimatization", "preCleaning",
)
SCOPES = " ".join(f"pdp-telemetry/{d}" for d in DOMAINS)
PATHS = {"parkingClimatization": "parking-climatization", "preCleaning": "pre-cleaning"}


class PolestarClient:
    def __init__(self, base_url=None, client_id=None, secret=None, http=None):
        load_dotenv()
        self.base = (base_url or os.environ["M2M_ENDPOINT"]).rstrip("/").removesuffix("/token")
        self.client_id = client_id or os.environ["APP_ID"]
        self.secret = secret or os.environ["SECRET"]
        self.account_id = os.environ.get("ACCOUNT_ID", self.client_id)
        self.http = http or httpx.Client(timeout=30)
        self._token = None
        self._expires = 0.0

    def _auth(self):
        if self._token and time.time() < self._expires - 60:
            return self._token
        r = self.http.post(
            f"{self.base}/token",
            json={"clientId": self.client_id, "clientSecret": self.secret, "scope": SCOPES},
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["accessToken"]
        self._expires = time.time() + body["expiresIn"]
        return self._token

    def _get(self, path, retries=3):
        for attempt in range(retries):
            r = self.http.get(
                f"{self.base}{path}",
                headers={"Authorization": f"Bearer {self._auth()}", "x-client-id": self.account_id},
            )
            if r.status_code in (502, 503) and attempt < retries - 1:
                time.sleep(2**attempt)
                continue
            r.raise_for_status()
            return r.json()

    def vehicles(self):
        return self._get("/v1/vehicles")

    def telemetry(self, vin, domain):
        return self._get(f"/v1/vehicles/{vin}/telemetry/{PATHS.get(domain, domain)}")
