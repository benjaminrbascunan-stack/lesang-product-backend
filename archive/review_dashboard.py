from __future__ import annotations

import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY en .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def print_section(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def fetch_rows(table_or_view: str, limit: int = 50):
    response = (
        supabase.table(table_or_view)
        .select("*")
        .limit(limit)
        .execute()
    )
    return response.data or []


def show_ready_review():
    rows = fetch_rows("items_ready_review", limit=100)

    print_section("ITEMS LISTOS PARA REVISIÓN")

    if not rows:
        print("No hay items listos para revisión.")
        return

    print(f"Total: {len(rows)}\n")

    for row in rows:
        print(f"- {row['title']}")
        print(f"  id: {row['id']}")
        print(f"  marca: {row.get('brand', 'N/A')}")
        print(f"  categoría: {row.get('category', 'N/A')}")
        print(f"  talla: {row.get('size', 'N/A')}")
        print(f"  categoría Shopify: {row.get('shopify_category', 'N/A')}")
        print(f"  estado: {row.get('status', 'N/A')}")
        print(f"  shopify_status: {row.get('shopify_status', 'N/A')}")
        print(f"  creado: {row.get('created_at', 'N/A')}")
        print()


def show_ready_shopify():
    rows = fetch_rows("items_ready_for_shopify", limit=100)

    print_section("ITEMS LISTOS PARA SHOPIFY")

    if not rows:
        print("No hay items listos para Shopify.")
        return

    print(f"Total: {len(rows)}\n")

    for row in rows:
        print(f"- {row['title']}")
        print(f"  id: {row['id']}")
        print(f"  marca: {row.get('brand', 'N/A')}")
        print(f"  categoría: {row.get('category', 'N/A')}")
        print(f"  talla: {row.get('size', 'N/A')}")
        print(f"  categoría Shopify: {row.get('shopify_category', 'N/A')}")
        print(f"  creado: {row.get('created_at', 'N/A')}")
        print()


def show_manual_review():
    rows = fetch_rows("ingest_manual_review", limit=100)

    print_section("GRUPOS CON REVISIÓN MANUAL PENDIENTE")

    if not rows:
        print("No hay grupos pendientes de revisión manual.")
        return

    print(f"Total: {len(rows)}\n")

    for row in rows:
        print(f"- cache_id: {row['id']}")
        print(f"  status: {row.get('status', 'N/A')}")
        print(f"  confidence: {row.get('confidence', 'N/A')}")
        print(f"  reason: {row.get('reason', 'N/A')}")
        print(f"  image_ids: {row.get('image_ids', [])}")
        print(f"  resolved_groups: {row.get('resolved_groups', [])}")
        print(f"  unassigned_image_ids: {row.get('unassigned_image_ids', [])}")
        print(f"  updated_at: {row.get('updated_at', 'N/A')}")
        print()


def show_summary():
    ready_review = fetch_rows("items_ready_review", limit=200)
    ready_shopify = fetch_rows("items_ready_for_shopify", limit=200)
    manual_review = fetch_rows("ingest_manual_review", limit=200)

    print_section("RESUMEN GENERAL")
    print(f"Items listos para revisión: {len(ready_review)}")
    print(f"Items listos para Shopify: {len(ready_shopify)}")
    print(f"Grupos con revisión manual pendiente: {len(manual_review)}")


def main():
    show_summary()
    show_ready_review()
    show_ready_shopify()
    show_manual_review()


if __name__ == "__main__":
    main()