from __future__ import annotations

import os
import sys
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def approve_item(item_id: str):
    response = (
        supabase.table("items")
        .update({
            "status": "approved"
        })
        .eq("id", item_id)
        .execute()
    )

    if response.data:
        print("\n✔ Item aprobado correctamente:\n")
        print(response.data[0])
    else:
        print("No se encontró el item o hubo un error.")


def main():
    if len(sys.argv) < 2:
        print("Uso:")
        print("python3 approve_item.py <ITEM_ID>")
        return

    item_id = sys.argv[1]
    approve_item(item_id)


if __name__ == "__main__":
    main()