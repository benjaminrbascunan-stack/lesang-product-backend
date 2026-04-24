from __future__ import annotations

import os
import json
import html
from datetime import datetime, UTC

import requests
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

SHOPIFY_LOCATION_NUMERIC_ID = "96183910707"

SHOPIFY_COLLECTIONS = {
    "SUPERIOR": "gid://shopify/Collection/471425515827",
    "INFERIOR": "gid://shopify/Collection/471425581363",
    "ZAPATOS": "gid://shopify/Collection/471425614131",
    "ACCESORIOS": "gid://shopify/Collection/471425679667",
    "NOVEDADES": "gid://shopify/Collection/471425745203",
    "DEPORTIVO": "gid://shopify/Collection/498900992307",
}

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY en .env")

if not SHOPIFY_STORE_DOMAIN or not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
    raise ValueError("Faltan variables Shopify en .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2026-04/graphql.json"
SHOPIFY_REST_BASE_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2026-04"

PRODUCT_CREATE_MUTATION = """
mutation productCreate($input: ProductCreateInput!, $media: [CreateMediaInput!]) {
  productCreate(product: $input, media: $media) {
    product {
      id
      title
      handle
      status
      variants(first: 1) {
        nodes {
          id
          inventoryItem {
            id
          }
        }
      }
    }
    userErrors {
      field
      message
    }
  }
}
"""

COLLECTION_ADD_PRODUCTS_MUTATION = """
mutation collectionAddProducts($id: ID!, $productIds: [ID!]!) {
  collectionAddProducts(id: $id, productIds: $productIds) {
    collection {
      id
      title
    }
    userErrors {
      field
      message
    }
  }
}
"""

FIND_PRODUCT_BY_SKU_QUERY = """
query findProductBySku($query: String!) {
  productVariants(first: 1, query: $query) {
    nodes {
      sku
      product {
        id
        title
        handle
        status
      }
    }
  }
}
"""


def print_section(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def gid_to_numeric_id(gid: str) -> str:
    return gid.split("/")[-1]


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

    response.raise_for_status()
    data = response.json()

    print("Shopify token scope:", data.get("scope"))

    token = data.get("access_token")
    if not token:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))

    return token


def shopify_graphql(query: str, variables: dict, access_token: str) -> dict:
    response = requests.post(
        SHOPIFY_GRAPHQL_URL,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        },
        json={"query": query, "variables": variables},
        timeout=90,
    )

    response.raise_for_status()
    data = response.json()

    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False))

    return data


def shopify_rest(method: str, path: str, payload: dict, access_token: str) -> dict:
    response = requests.request(
        method=method,
        url=f"{SHOPIFY_REST_BASE_URL}{path}",
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        },
        json=payload,
        timeout=90,
    )

    if response.status_code >= 400:
        raise RuntimeError(response.text)

    if not response.text:
        return {}

    return response.json()


def fetch_items_to_push():
    """
    Validación principal:
    Solo trae items que todavía NO tienen shopify_product_gid.
    """
    response = (
        supabase.table("items")
        .select("*")
        .eq("status", "ready_for_review")
        .eq("shopify_status", "draft")
        .is_("shopify_product_gid", "null")
        .execute()
    )
    return response.data or []


def description_to_html(text: str) -> str:
    clean = (text or "").strip()

    if not clean:
        return ""

    paragraphs = [p.strip() for p in clean.split("\n\n") if p.strip()]

    html_paragraphs = []
    for paragraph in paragraphs:
        escaped = html.escape(paragraph).replace("\n", "<br>")
        html_paragraphs.append(f"<p>{escaped}</p>")

    return "\n".join(html_paragraphs)


def build_tags(item: dict) -> list[str]:
    tags = ["Lé Sang"]

    if item.get("shopify_category"):
        tags.append(str(item["shopify_category"]))

    if item.get("size"):
        tags.append(f"size:{item['size']}")

    if item.get("category"):
        tags.append(f"category:{item['category']}")

    if item.get("brand"):
        tags.append(f"brand:{item['brand']}")

    tags.append(f"supabase_id:{item['id']}")

    return tags


def drive_image_url(file_id: str) -> str:
    return f"https://drive.google.com/uc?export=view&id={file_id}"


def build_shopify_media(item: dict) -> list[dict]:
    media = []
    images = item.get("image_urls") or []

    if not isinstance(images, list):
        return media

    title = item.get("title") or "Producto Lé Sang"

    for image in images:
        if not isinstance(image, dict):
            continue

        file_id = image.get("id")
        name = image.get("name") or title

        if not file_id:
            continue

        media.append({
            "mediaContentType": "IMAGE",
            "originalSource": drive_image_url(file_id),
            "alt": name,
        })

    return media


def build_shopify_input(item: dict) -> dict:
    return {
        "title": item.get("title") or "Producto Lé Sang",
        "descriptionHtml": description_to_html(item.get("ai_description") or ""),
        "vendor": item.get("brand") or "Lé Sang",
        "productType": item.get("category") or "Accessory",
        "status": "DRAFT",
        "tags": build_tags(item),
    }


def find_existing_shopify_product_by_sku(sku: str, access_token: str) -> dict | None:
    data = shopify_graphql(
        query=FIND_PRODUCT_BY_SKU_QUERY,
        variables={"query": f"sku:{sku}"},
        access_token=access_token,
    )

    nodes = data.get("data", {}).get("productVariants", {}).get("nodes", [])

    if not nodes:
        return None

    return nodes[0].get("product")


def create_shopify_product(item: dict, access_token: str) -> dict:
    media = build_shopify_media(item)

    print(f"  Imágenes a subir: {len(media)}")

    data = shopify_graphql(
        query=PRODUCT_CREATE_MUTATION,
        variables={
            "input": build_shopify_input(item),
            "media": media,
        },
        access_token=access_token,
    )

    result = data.get("data", {}).get("productCreate", {})
    user_errors = result.get("userErrors", [])

    if user_errors:
        raise RuntimeError(json.dumps(user_errors, ensure_ascii=False))

    product = result.get("product")

    if not product:
        raise RuntimeError(json.dumps(data, ensure_ascii=False))

    return product


def get_first_variant_and_inventory_item(product: dict):
    variants = product.get("variants", {}).get("nodes", [])

    if not variants:
        raise RuntimeError("Shopify no devolvió variante para el producto.")

    variant = variants[0]
    inventory_item = variant.get("inventoryItem")

    if not inventory_item or not inventory_item.get("id"):
        raise RuntimeError("Shopify no devolvió inventoryItem para la variante.")

    return variant, inventory_item


def update_inventory_item_sku_and_tracking(
    inventory_item_gid: str,
    sku: str,
    access_token: str,
):
    inventory_item_id = gid_to_numeric_id(inventory_item_gid)

    payload = {
        "inventory_item": {
            "id": int(inventory_item_id),
            "sku": sku,
            "tracked": True,
        }
    }

    shopify_rest(
        method="PUT",
        path=f"/inventory_items/{inventory_item_id}.json",
        payload=payload,
        access_token=access_token,
    )

    print(f"  SKU asignado: {sku}")
    print("  Inventario rastreado: sí")


def set_inventory_quantity_to_one(
    inventory_item_gid: str,
    access_token: str,
):
    inventory_item_id = gid_to_numeric_id(inventory_item_gid)

    payload = {
        "location_id": int(SHOPIFY_LOCATION_NUMERIC_ID),
        "inventory_item_id": int(inventory_item_id),
        "available": 1,
    }

    shopify_rest(
        method="POST",
        path="/inventory_levels/set.json",
        payload=payload,
        access_token=access_token,
    )

    print("  Inventario disponible asignado: 1")


def configure_inventory_for_product(item: dict, product: dict, access_token: str):
    _, inventory_item = get_first_variant_and_inventory_item(product)

    inventory_item_gid = inventory_item["id"]
    sku = item["id"]

    update_inventory_item_sku_and_tracking(
        inventory_item_gid=inventory_item_gid,
        sku=sku,
        access_token=access_token,
    )

    set_inventory_quantity_to_one(
        inventory_item_gid=inventory_item_gid,
        access_token=access_token,
    )


def add_product_to_collection(product_gid: str, collection_gid: str, access_token: str):
    data = shopify_graphql(
        query=COLLECTION_ADD_PRODUCTS_MUTATION,
        variables={
            "id": collection_gid,
            "productIds": [product_gid],
        },
        access_token=access_token,
    )

    result = data.get("data", {}).get("collectionAddProducts", {})
    user_errors = result.get("userErrors", [])

    if user_errors:
        raise RuntimeError(json.dumps(user_errors, ensure_ascii=False))

    return result.get("collection")


def add_product_to_collections(item: dict, product_gid: str, access_token: str):
    collections_to_add = []

    category = (item.get("shopify_category") or "").strip().upper()

    if category in SHOPIFY_COLLECTIONS:
        collections_to_add.append(category)

    collections_to_add.append("NOVEDADES")
    collections_to_add = list(dict.fromkeys(collections_to_add))

    for collection_key in collections_to_add:
        collection_gid = SHOPIFY_COLLECTIONS.get(collection_key)

        if not collection_gid:
            continue

        add_product_to_collection(
            product_gid=product_gid,
            collection_gid=collection_gid,
            access_token=access_token,
        )

        print(f"  Añadido a colección: {collection_key}")


def update_item_success(item_id: str, product: dict):
    payload = {
        "status": "pushed_to_shopify",
        "shopify_product_gid": product.get("id"),
        "shopify_handle": product.get("handle"),
        "shopify_status": "created",
        "shopify_pushed_at": now_iso(),
        "shopify_error": None,
    }

    (
        supabase.table("items")
        .update(payload)
        .eq("id", item_id)
        .execute()
    )


def mark_item_as_existing(item_id: str, product: dict):
    payload = {
        "status": "pushed_to_shopify",
        "shopify_product_gid": product.get("id"),
        "shopify_handle": product.get("handle"),
        "shopify_status": "created",
        "shopify_pushed_at": now_iso(),
        "shopify_error": "Producto ya existía en Shopify. Marcado como existente por SKU.",
    }

    (
        supabase.table("items")
        .update(payload)
        .eq("id", item_id)
        .execute()
    )


def update_item_error(item_id: str, error_message: str):
    payload = {
        "shopify_error": error_message,
        "shopify_pushed_at": now_iso(),
    }

    (
        supabase.table("items")
        .update(payload)
        .eq("id", item_id)
        .execute()
    )


def main():
    print_section("GENERANDO TOKEN DE SHOPIFY")
    access_token = get_shopify_access_token()
    print("✔ Token obtenido correctamente.")

    print_section("BUSCANDO ITEMS NUEVOS PARA SHOPIFY")
    items = fetch_items_to_push()

    if not items:
        print("No hay items nuevos pendientes para Shopify.")
        return

    print(f"Se encontraron {len(items)} items nuevos.\n")

    for item in items:
        print(f"- {item.get('title')} | {item.get('id')}")

    print_section("CREANDO PRODUCTOS DRAFT EN SHOPIFY")

    success_count = 0
    skipped_existing_count = 0
    error_count = 0

    for item in items:
        item_id = item["id"]
        title = item.get("title", "Sin título")

        print(f"\nProcesando: {title} | {item_id}")

        try:
            existing_product = find_existing_shopify_product_by_sku(
                sku=item_id,
                access_token=access_token,
            )

            if existing_product:
                print("  Producto ya existe en Shopify por SKU.")
                print(f"  gid: {existing_product.get('id')}")
                print(f"  handle: {existing_product.get('handle')}")

                mark_item_as_existing(item_id, existing_product)
                skipped_existing_count += 1
                continue

            product = create_shopify_product(item, access_token)

            print("✔ Creado en Shopify")
            print(f"  gid: {product.get('id')}")
            print(f"  handle: {product.get('handle')}")
            print(f"  status: {product.get('status')}")

            configure_inventory_for_product(item, product, access_token)
            add_product_to_collections(item, product["id"], access_token)

            update_item_success(item_id, product)

            success_count += 1

        except Exception as e:
            error_message = str(e)
            update_item_error(item_id, error_message)

            print("✘ Error")
            print(f"  error: {error_message}")

            error_count += 1

    print_section("RESUMEN FINAL")
    print(f"Exitosos nuevos: {success_count}")
    print(f"Omitidos por existir: {skipped_existing_count}")
    print(f"Con error: {error_count}")


if __name__ == "__main__":
    main()