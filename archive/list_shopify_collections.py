from __future__ import annotations

import os
import json
import requests
from dotenv import load_dotenv

load_dotenv()

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
    raise ValueError("Faltan SHOPIFY_STORE_DOMAIN, SHOPIFY_CLIENT_ID o SHOPIFY_CLIENT_SECRET en .env")

GRAPHQL_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2026-04/graphql.json"


def get_shopify_access_token() -> str:
    response = requests.post(
        f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token",
        headers={"Content-Type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
        timeout=60,
    )

    print("Token response status:", response.status_code)
    response.raise_for_status()

    data = response.json()
    print("Token scope:", data.get("scope"))

    token = data.get("access_token")
    if not token:
        raise RuntimeError(json.dumps(data, indent=2, ensure_ascii=False))

    return token


def main():
    token = get_shopify_access_token()

    query = """
    query {
      collections(first: 100) {
        nodes {
          id
          title
          handle
        }
      }
    }
    """

    response = requests.post(
        GRAPHQL_URL,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
        json={"query": query},
        timeout=60,
    )

    print("GraphQL response status:", response.status_code)
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()