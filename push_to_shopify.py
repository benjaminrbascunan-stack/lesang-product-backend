import os
import json
import requests
from dotenv import load_dotenv
from supabase import create_client, Client
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

load_dotenv()

# ===============================
# CONFIG
# ===============================

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SHOPIFY_STORE_DOMAIN = os.getenv("SHOPIFY_STORE_DOMAIN")
SHOPIFY_CLIENT_ID = os.getenv("SHOPIFY_CLIENT_ID")
SHOPIFY_CLIENT_SECRET = os.getenv("SHOPIFY_CLIENT_SECRET")

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===============================
# GOOGLE DRIVE
# ===============================

def get_drive_creds():
    token_json = os.getenv("GOOGLE_TOKEN_JSON")

    if not token_json:
        raise RuntimeError("Falta GOOGLE_TOKEN_JSON")

    creds = Credentials.from_authorized_user_info(
        json.loads(token_json),
        DRIVE_SCOPES
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return creds

# ===============================
# SHOPIFY TOKEN DINÁMICO
# ===============================

def get_shopify_token():
    print("Generando token Shopify...")

    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/oauth/access_token"

    response = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": SHOPIFY_CLIENT_ID,
            "client_secret": SHOPIFY_CLIENT_SECRET
        }
    )

    data = response.json()

    if "access_token" not in data:
        raise RuntimeError(data)

    print("✔ Token Shopify generado")
    return data["access_token"]

# ===============================
# FETCH ITEMS
# ===============================

def fetch_items():
    res = supabase.table("items") \
        .select("*") \
        .eq("status", "ready_for_review") \
        .execute()

    return res.data

# ===============================
# CREATE PRODUCT
# ===============================

def create_product(item, token):
    print(f"Creando producto: {item['title']}")

    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-04/products.json"

    payload = {
        "product": {
            "title": item.get("title"),
            "body_html": item.get("description", ""),
            "vendor": item.get("brand", "Lé Sang"),
            "product_type": item.get("category", ""),
            "status": "draft",
            "variants": [
                {
                    "price": item.get("price", 0),
                    "inventory_quantity": 1,
                    "inventory_management": "shopify"
                }
            ]
        }
    }

    response = requests.post(
        url,
        headers={
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json"
        },
        json=payload
    )

    data = response.json()

    if "product" not in data:
        raise RuntimeError(data)

    print("✔ Producto creado")
    return data["product"]["id"]

# ===============================
# UPLOAD IMAGES
# ===============================

def upload_images(product_id, image_urls, token):
    if not image_urls:
        print("⚠️ Sin imágenes")
        return

    url = f"https://{SHOPIFY_STORE_DOMAIN}/admin/api/2024-04/products/{product_id}/images.json"

    for img in image_urls:
        print(f"Subiendo imagen: {img}")

        requests.post(
            url,
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json"
            },
            json={
                "image": {"src": img}
            }
        )

# ===============================
# MAIN
# ===============================

def main():
    print("=" * 80)
    print("PUSH TO SHOPIFY — DYNAMIC TOKEN VERSION")
    print("=" * 80)

    get_drive_creds()

    token = get_shopify_token()

    items = fetch_items()

    if not items:
        print("No hay productos")
        return

    print(f"Se encontraron {len(items)} productos")

    for item in items:
        try:
            product_id = create_product(item, token)

            image_urls = item.get("images", [])
            upload_images(product_id, image_urls, token)

        except Exception as e:
            print(f"Error: {e}")

    print("\n✔ Proceso terminado")

if __name__ == "__main__":
    main()