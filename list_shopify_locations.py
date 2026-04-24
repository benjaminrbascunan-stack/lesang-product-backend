# list_shopify_locations.py

from __future__ import annotations

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

def get_token():
    r = requests.post(
        f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
    )
    r.raise_for_status()
    data = r.json()
    print("Scope:", data.get("scope"))
    return data["access_token"]

token = get_token()

query = """
query {
  locations(first: 20) {
    nodes {
      id
      name
      isActive
    }
  }
}
"""

r = requests.post(
    f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2026-04/graphql.json",
    headers={
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": token,
    },
    json={"query": query},
)

print(json.dumps(r.json(), indent=2, ensure_ascii=False))