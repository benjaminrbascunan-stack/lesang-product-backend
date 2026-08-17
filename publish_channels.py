from __future__ import annotations

import os
import json
import requests
from pathlib import Path
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

def clean_env(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")

def clean_shop_domain(domain: str) -> str:
    domain = clean_env(domain)
    domain = domain.replace("https://", "").replace("http://", "")
    return domain.rstrip("/")

SHOPIFY_STORE_DOMAIN  = clean_shop_domain(os.getenv("SHOPIFY_STORE_DOMAIN"))
SHOPIFY_CLIENT_ID     = clean_env(os.getenv("SHOPIFY_CLIENT_ID"))
SHOPIFY_CLIENT_SECRET = clean_env(os.getenv("SHOPIFY_CLIENT_SECRET"))
SHOPIFY_API_VERSION   = "2026-04"

SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
SHOPIFY_TOKEN_URL   = f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token"

# IDs de canales de venta de la tienda
PUBLICATION_ONLINE_STORE = "gid://shopify/Publication/221867442483"
PUBLICATION_POS          = "gid://shopify/Publication/221867475251"
PUBLICATIONS             = [PUBLICATION_ONLINE_STORE, PUBLICATION_POS]

def print_section(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)

def get_shopify_access_token() -> str:
    print_section("GENERANDO TOKEN SHOPIFY")
    response = requests.post(
        SHOPIFY_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type":    "client_credentials",
            "client_id":     SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Error generando token HTTP {response.status_code}: {response.text[:500]}")
    token = clean_env(response.json().get("access_token"))
    if not token:
        raise RuntimeError(f"No se obtuvo access_token: {response.json()}")
    print("✔ Token generado correctamente")
    return token

def shopify_graphql(query: str, variables: dict, token: str) -> dict:
    response = requests.post(
        SHOPIFY_GRAPHQL_URL,
        headers={
            "Content-Type":             "application/json",
            "X-Shopify-Access-Token":   token,
        },
        json={"query": query, "variables": variables},
        timeout=90,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Shopify GraphQL HTTP {response.status_code}: {response.text[:500]}")
    data = response.json()
    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))
    return data

# ── Queries / Mutations ───────────────────────────────────────────────────────

FETCH_DRAFT_PRODUCTS = """
query fetchDrafts($cursor: String) {
  products(first: 50, after: $cursor, query: "status:draft") {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      title
      status
    }
  }
}
"""

PUBLISH_PRODUCT_MUTATION = """
mutation publishProduct($id: ID!, $input: [PublicationInput!]!) {
  publishablePublish(id: $id, input: $input) {
    publishable {
      ... on Product {
        id
        title
        status
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

ACTIVATE_PRODUCT_MUTATION = """
mutation activateProduct($input: ProductUpdateInput!) {
  productUpdate(product: $input) {
    product {
      id
      title
      status
    }
    userErrors {
      field
      message
    }
  }
}
"""

def activate_product(product: dict, token: str) -> bool:
    data = shopify_graphql(
        query=ACTIVATE_PRODUCT_MUTATION,
        variables={
            "input": {
                "id":     product["id"],
                "status": "ACTIVE",
            },
        },
        token=token,
    )

    result      = data.get("data", {}).get("productUpdate", {})
    user_errors = result.get("userErrors", [])

    if user_errors:
        print(f"  ✘ Error activando: {json.dumps(user_errors, ensure_ascii=False)}")
        return False

    print(f"  ✔ Activado: {product['title']}")
    return True


def fetch_all_draft_products(token: str) -> list[dict]:
    print_section("BUSCANDO PRODUCTOS EN DRAFT")
    products = []
    cursor   = None

    while True:
        data = shopify_graphql(
            query=FETCH_DRAFT_PRODUCTS,
            variables={"cursor": cursor},
            token=token,
        )
        page     = data["data"]["products"]
        nodes    = page["nodes"]
        products.extend(nodes)

        print(f"  Cargados: {len(products)} productos...")

        if page["pageInfo"]["hasNextPage"]:
            cursor = page["pageInfo"]["endCursor"]
        else:
            break

    print(f"\n✔ Total drafts encontrados: {len(products)}")
    return products

def publish_product_to_channels(product: dict, token: str) -> bool:
    publication_input = [{"publicationId": pub_id} for pub_id in PUBLICATIONS]

    data = shopify_graphql(
        query=PUBLISH_PRODUCT_MUTATION,
        variables={
            "id":    product["id"],
            "input": publication_input,
        },
        token=token,
    )

    result      = data.get("data", {}).get("publishablePublish", {})
    user_errors = result.get("userErrors", [])

    if user_errors:
        print(f"  ✘ Error: {json.dumps(user_errors, ensure_ascii=False)}")
        return False

    print(f"  ✔ Publicado: {product['title']}")
    return True

def main():
    print_section("ACTIVAR PRODUCTOS — ONLINE STORE + POS + ACTIVE")

    token    = get_shopify_access_token()
    products = fetch_all_draft_products(token)

    if not products:
        print("No hay productos en draft para activar.")
        return

    print_section("PUBLICANDO EN CANALES + ACTIVANDO")

    success = 0
    errors  = 0
    total   = len(products)

    for i, product in enumerate(products, start=1):
        print(f"\nPROGRESS: {i} / {total}")
        print(f"Procesando: {product['title']}")

        ok_publish  = publish_product_to_channels(product, token)
        ok_activate = activate_product(product, token) if ok_publish else False

        if ok_publish and ok_activate:
            success += 1
        else:
            errors += 1

    print_section("RESUMEN FINAL")
    print(f"Publicados exitosamente: {success}")
    print(f"Con error:               {errors}")
    print(f"Total procesados:        {total}")

if __name__ == "__main__":
    main()
    