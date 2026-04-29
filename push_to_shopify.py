import os
import json
import requests
from datetime import datetime, UTC
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

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan credenciales de Supabase")

if not SHOPIFY_STORE_DOMAIN:
    raise ValueError("Falta dominio de Shopify")

# ===============================
# SUPABASE
# ===============================

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===============================
# GOOGLE DRIVE AUTH (CLOUD)
# ===============================

def get_drive_creds():
    print("Conectando con Google Drive...")

    token_json = os.getenv("GOOGLE_TOKEN_JSON")

    if not token_json:
        raise RuntimeError("Falta GOOGLE_TOKEN_JSON")

    creds = Credentials.from_authorized_user_info(
        json.loads(token_json),
        DRIVE_SCOPES
    )

    if creds.expired and creds.refresh_token:
        print("Refrescando token...")
        creds.refresh(Request())

    print("✔ Google Drive conectado (nube)")
    return creds

# ===============================
# SHOPIFY TOKEN
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
        raise RuntimeError(f"Error token Shopify: {data}")

    print("✔ Token Shopify OK")
    return data["access_token"]

# ===============================
# FETCH ITEMS
# ===============================

def fetch_items():
    print("Buscando productos en Supabase...")

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
            "status": "draft"
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
    return data["product"]

# ===============================
# MAIN
# ===============================

def main():
    print("=" * 80)
    print("PUSH TO SHOPIFY — CLOUD READY")
    print("=" * 80)

    # Google (solo para validar token)
    get_drive_creds()

    # Shopify
    token = get_shopify_token()

    # Items
    items = fetch_items()

    if not items:
        print("No hay productos para subir")
        return

    print(f"Se encontraron {len(items)} productos")

    for item in items:
        try:
            create_product(item, token)
        except Exception as e:
            print(f"Error: {e}")

    print("\n✔ Proceso terminado")

if __name__ == "__main__":
    main()