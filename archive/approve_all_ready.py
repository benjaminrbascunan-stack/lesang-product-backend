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


def get_ready_items():
    response = (
        supabase.table("items")
        .select("id,title,status")
        .eq("status", "ready_for_review")
        .execute()
    )
    return response.data or []


def approve_items(item_ids: list[str]):
    if not item_ids:
        print("No hay items para aprobar.")
        return

    # actualiza todos
    (
        supabase.table("items")
        .update({"status": "approved"})
        .in_("id", item_ids)
        .execute()
    )


def get_items_by_ids(item_ids: list[str]):
    if not item_ids:
        return []

    response = (
        supabase.table("items")
        .select("id,title,status")
        .in_("id", item_ids)
        .execute()
    )
    return response.data or []


def main():
    print("Buscando items con status = ready_for_review...\n")

    ready_items = get_ready_items()

    if not ready_items:
        print("No hay items pendientes de aprobación.")
        return

    print(f"Se encontraron {len(ready_items)} items:\n")

    for item in ready_items:
        print(f"- {item['title']} | {item['id']}")

    item_ids = [item["id"] for item in ready_items]

    print("\nAprobando todos los items...\n")
    approve_items(item_ids)

    updated_items = get_items_by_ids(item_ids)

    approved_items = [item for item in updated_items if item.get("status") == "approved"]

    print(f"Se aprobaron {len(approved_items)} items.\n")

    for item in approved_items:
        print(f"✔ {item.get('title', 'Sin título')} | {item.get('id')} | {item.get('status')}")


if __name__ == "__main__":
    main()