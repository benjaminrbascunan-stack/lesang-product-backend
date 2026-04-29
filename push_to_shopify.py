from __future__ import annotations

import os
import re
import json
import html
from io import BytesIO
from pathlib import Path
from datetime import datetime, UTC

import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

register_heif_opener()

ENV_PATH = Path(__file__).resolve().parent / ".env"
BASE_DIR = Path(__file__).resolve().parent

CREDENTIALS_PATH = BASE_DIR / "credentials.json"
TOKEN_PATH = BASE_DIR / "token.json"

load_dotenv(dotenv_path=ENV_PATH, override=True)

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def clean_env(value: str | None) -> str:
    return (value or "").strip().strip('"').strip("'")


def clean_shop_domain(domain: str) -> str:
    domain = clean_env(domain)
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.rstrip("/")
    return domain


SUPABASE_URL = clean_env(os.getenv("SUPABASE_URL"))
SUPABASE_KEY = clean_env(os.getenv("SUPABASE_KEY"))

SHOPIFY_STORE_DOMAIN = clean_shop_domain(os.getenv("SHOPIFY_STORE_DOMAIN"))
SHOPIFY_CLIENT_ID = clean_env(os.getenv("SHOPIFY_CLIENT_ID"))
SHOPIFY_CLIENT_SECRET = clean_env(os.getenv("SHOPIFY_CLIENT_SECRET"))

SHOPIFY_LOCATION_NUMERIC_ID = "96183910707"
PRODUCT_IMAGES_BUCKET = "product-images"
SHOPIFY_API_VERSION = "2026-04"

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

if not SHOPIFY_STORE_DOMAIN:
    raise ValueError("Falta SHOPIFY_STORE_DOMAIN en .env")

if not SHOPIFY_CLIENT_ID or not SHOPIFY_CLIENT_SECRET:
    raise ValueError("Faltan SHOPIFY_CLIENT_ID o SHOPIFY_CLIENT_SECRET en .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

SHOPIFY_GRAPHQL_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
SHOPIFY_REST_BASE_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/{SHOPIFY_API_VERSION}"
SHOPIFY_TOKEN_URL = f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token"


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


def safe_filename(name: str) -> str:
    name = (name or "image.jpg").lower().strip()
    name = re.sub(r"\.[a-z0-9]+$", "", name)
    name = re.sub(r"[^a-z0-9\-_]+", "-", name)
    name = re.sub(r"-+", "-", name).strip("-")

    if not name:
        name = "image"

    return f"{name}.jpg"


def debug_env():
    print_section("DEBUG ENV")
    print(f"ENV PATH: {ENV_PATH}")
    print(f"TOKEN PATH: {TOKEN_PATH}")
    print(f"SHOPIFY_STORE_DOMAIN: {SHOPIFY_STORE_DOMAIN}")
    print(f"SHOPIFY_TOKEN_URL: {SHOPIFY_TOKEN_URL}")
    print(f"SHOPIFY_GRAPHQL_URL: {SHOPIFY_GRAPHQL_URL}")
    print(f"SHOPIFY_CLIENT_ID largo: {len(SHOPIFY_CLIENT_ID)}")
    print(f"SHOPIFY_CLIENT_SECRET largo: {len(SHOPIFY_CLIENT_SECRET)}")


def get_drive_service():
    print("Conectando con Google Drive...")
    token_json = os.getenv("GOOGLE_TOKEN_JSON")

    # ── MODO NUBE (Railway) ────────────────────────────────────────────────────
    if token_json:
        print("Usando autenticación desde variables de entorno (nube)...")
        creds = Credentials.from_authorized_user_info(
            json.loads(token_json),
            DRIVE_SCOPES,
        )
        if creds.expired and creds.refresh_token:
            print("Refrescando token...")
            creds.refresh(Request())
        print("✔ Google Drive conectado (nube)")
        return build("drive", "v3", credentials=creds)

    # ── MODO LOCAL ─────────────────────────────────────────────────────────────
    print("Usando autenticación local (token.json)...")
    if not TOKEN_PATH.exists():
        raise FileNotFoundError(
            f"No existe token.json en: {TOKEN_PATH}\n"
            "En local necesitás el archivo token.json. "
            "En la nube configurá la variable GOOGLE_TOKEN_JSON."
        )
    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), DRIVE_SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_PATH, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
    if not creds.valid:
        raise RuntimeError("token.json no es válido. Volvé a autenticar Google Drive.")
    print("✔ Google Drive conectado (local)")
    return build("drive", "v3", credentials=creds)


drive_service = None


def get_shopify_access_token() -> str:
    print_section("GENERANDO TOKEN SHOPIFY")

    response = requests.post(
        SHOPIFY_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET,
        },
        timeout=60,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Error generando token Shopify HTTP {response.status_code}\n"
            f"URL: {SHOPIFY_TOKEN_URL}\n"
            f"Respuesta: {response.text[:1500]}"
        )

    data = response.json()
    access_token = clean_env(data.get("access_token"))

    if not access_token:
        raise RuntimeError(
            f"Shopify no devolvió access_token:\n{json.dumps(data, ensure_ascii=False, indent=2)}"
        )

    print("✔ Token generado correctamente")
    print(f"Token inicio: {access_token[:8]}...")
    print(f"Expira en: {data.get('expires_in')} segundos")
    print(f"Scopes: {data.get('scope')}")

    return access_token


def shopify_graphql(query: str, variables: dict, access_token: str) -> dict:
    token = clean_env(access_token)

    response = requests.post(
        SHOPIFY_GRAPHQL_URL,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
        json={"query": query, "variables": variables},
        timeout=90,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Shopify GraphQL HTTP {response.status_code}\n"
            f"URL: {SHOPIFY_GRAPHQL_URL}\n"
            f"Token largo: {len(token)}\n"
            f"Token inicio: {token[:8]}...\n"
            f"Respuesta: {response.text[:1500]}"
        )

    data = response.json()

    if data.get("errors"):
        raise RuntimeError(json.dumps(data["errors"], ensure_ascii=False, indent=2))

    return data


def shopify_rest(method: str, path: str, payload: dict, access_token: str) -> dict:
    token = clean_env(access_token)
    url = f"{SHOPIFY_REST_BASE_URL}{path}"

    response = requests.request(
        method=method,
        url=url,
        headers={
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": token,
        },
        json=payload,
        timeout=90,
    )

    if response.status_code >= 400:
        raise RuntimeError(
            f"Shopify REST HTTP {response.status_code}\n"
            f"URL: {url}\n"
            f"Respuesta: {response.text[:1500]}"
        )

    if not response.text:
        return {}

    return response.json()


def test_shopify_auth(access_token: str):
    print_section("TEST SHOPIFY AUTH")

    data = shopify_graphql(
        query="""
        query {
          shop {
            name
            myshopifyDomain
          }
        }
        """,
        variables={},
        access_token=access_token,
    )

    shop = data.get("data", {}).get("shop", {})

    print("✔ Shopify autenticó correctamente")
    print(f"Tienda: {shop.get('name')}")
    print(f"Dominio: {shop.get('myshopifyDomain')}")


def fetch_items_to_push():
    response = (
        supabase.table("items")
        .select("*")
        .eq("status", "ready_for_review")
        .eq("shopify_status", "draft")
        .is_("shopify_product_gid", "null")
        .execute()
    )

    return response.data or []


def update_item_success(item_id: str, product: dict):
    payload = {
        "status": "pushed_to_shopify",
        "shopify_product_gid": product.get("id"),
        "shopify_handle": product.get("handle"),
        "shopify_status": "created",
        "shopify_pushed_at": now_iso(),
        "shopify_error": None,
    }

    supabase.table("items").update(payload).eq("id", item_id).execute()


def mark_item_as_existing(item_id: str, product: dict):
    payload = {
        "status": "pushed_to_shopify",
        "shopify_product_gid": product.get("id"),
        "shopify_handle": product.get("handle"),
        "shopify_status": "created",
        "shopify_pushed_at": now_iso(),
        "shopify_error": "Producto ya existía en Shopify. Marcado como existente por SKU.",
    }

    supabase.table("items").update(payload).eq("id", item_id).execute()


def update_item_error(item_id: str, error_message: str):
    payload = {
        "shopify_error": error_message,
        "shopify_pushed_at": now_iso(),
    }

    supabase.table("items").update(payload).eq("id", item_id).execute()


def download_drive_image(image: dict) -> bytes:
    file_id = image.get("id")
    filename = image.get("name") or file_id

    if not file_id:
        raise ValueError("Imagen sin id de Drive")

    print(f"  descargando Drive API: {filename}")

    request = drive_service.files().get_media(fileId=file_id)
    buffer = BytesIO()
    downloader = MediaIoBaseDownload(buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    content = buffer.getvalue()

    if not content:
        raise RuntimeError(f"Drive API devolvió archivo vacío para {filename} ({file_id})")

    if b"<html" in content[:300].lower():
        raise RuntimeError(
            f"Drive API devolvió HTML en vez de imagen para {filename} ({file_id})"
        )

    return content


def convert_to_real_jpg(image_bytes: bytes) -> bytes:
    img = Image.open(BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)

    if img.mode != "RGB":
        img = img.convert("RGB")

    img.thumbnail((4096, 4096))

    output = BytesIO()
    img.save(
        output,
        format="JPEG",
        quality=88,
        optimize=True,
        progressive=False,
    )

    return output.getvalue()


def upload_product_image_to_supabase(item_id: str, image: dict, position: int) -> str:
    original_name = image.get("name") or f"image-{position}.jpg"
    filename = safe_filename(original_name)
    storage_path = f"{item_id}/{position:02d}-{filename}"

    try:
        existing = supabase.storage.from_(PRODUCT_IMAGES_BUCKET).download(storage_path)

        if existing:
            public_url = supabase.storage.from_(PRODUCT_IMAGES_BUCKET).get_public_url(storage_path)
            print(f"  imagen ya existe en Supabase: {storage_path}")
            return public_url

    except Exception:
        pass

    raw_bytes = download_drive_image(image)

    print("  convirtiendo a JPG real")
    jpg_bytes = convert_to_real_jpg(raw_bytes)

    print(f"  subiendo a Supabase: {storage_path}")
    supabase.storage.from_(PRODUCT_IMAGES_BUCKET).upload(
        storage_path,
        jpg_bytes,
        {
            "content-type": "image/jpeg",
            "upsert": "true",
        },
    )

    public_url = supabase.storage.from_(PRODUCT_IMAGES_BUCKET).get_public_url(storage_path)
    return public_url


def build_shopify_media_from_images(item: dict) -> list[dict]:
    media = []
    images = item.get("image_urls") or []

    if not isinstance(images, list):
        return media

    title = item.get("title") or "Producto Lé Sang"
    item_id = item["id"]

    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            continue

        try:
            public_url = upload_product_image_to_supabase(item_id, image, index)
        except Exception as e:
            raise RuntimeError(
                f"Error preparando imagen {image.get('name')} para Shopify: {e}"
            )

        media.append(
            {
                "mediaContentType": "IMAGE",
                "originalSource": public_url,
                "alt": image.get("name") or title,
            }
        )

    return media


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
    media = build_shopify_media_from_images(item)

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
        raise RuntimeError(json.dumps(user_errors, ensure_ascii=False, indent=2))

    product = result.get("product")

    if not product:
        raise RuntimeError(json.dumps(data, ensure_ascii=False, indent=2))

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
        raise RuntimeError(json.dumps(user_errors, ensure_ascii=False, indent=2))

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


def main():
    debug_env()

    global drive_service
    drive_service = get_drive_service()

    access_token = get_shopify_access_token()
    test_shopify_auth(access_token)

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