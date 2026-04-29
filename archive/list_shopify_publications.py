# list_shopify_publications.py

from __future__ import annotations

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

GRAPHQL_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2026-04/graphql.json"


def get_token():
    r = requests.post(
        f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    print("Scope:", data.get("scope"))
    return data["access_token"]


query = """
query {
  publications(first: 20) {
    nodes {
      id
      name
    }
  }
}
"""

token = get_token()

r = requests.post(
    GRAPHQL_URL,
    headers={
        "Content-Type": "application/json",
        "X-Shopify-Access-Token": token,
    },
    json={"query": query},
    timeout=60,
)

print(json.dumps(r.json(), indent=2, ensure_ascii=False))