from __future__ import annotations

import os.path
from datetime import datetime

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from supabase import create_client, Client

# =========================
# CONFIG
# =========================

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_ID = "1yfADUnnIXsCTqRI7wcZ6ctao60VHXu-R"

SUPABASE_URL = "https://qjnjpixlxeemhruttltk.supabase.co"
SUPABASE_KEY = "sb_publishable_qWix2Xz9Wp1Zv6VKdkkf1w__nGR1bj0"

GROUP_GAP_SECONDS = 300

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================
# AUTH GOOGLE DRIVE
# =========================

def get_drive_service():
    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("drive", "v3", credentials=creds)


# =========================
# GET IMAGES FROM DRIVE
# =========================

def get_images(service):
    query = f"'{FOLDER_ID}' in parents and mimeType contains 'image/' and trashed = false"

    results = (
        service.files()
        .list(
            q=query,
            pageSize=100,
            fields="files(id, name, createdTime)",
            orderBy="createdTime"
        )
        .execute()
    )

    return results.get("files", [])


# =========================
# GROUP IMAGES BY TIME
# =========================

def group_images(files):
    groups = []
    current_group = []
    previous_time = None

    for file in files:
        current_time = datetime.fromisoformat(file["createdTime"].replace("Z", "+00:00"))

        if previous_time:
            diff = (current_time - previous_time).total_seconds()
            if diff > GROUP_GAP_SECONDS:
                groups.append(current_group)
                current_group = []

        current_group.append(file)
        previous_time = current_time

    if current_group:
        groups.append(current_group)

    return groups


# =========================
# BUILD IMAGE DATA
# =========================

def build_image_data(group):
    image_data = []

    for file in group:
        image_data.append({
            "id": file["id"],
            "name": file["name"],
            "url": f"https://drive.google.com/uc?id={file['id']}"
        })

    return image_data


def build_group_signature(image_data):
    """
    Firma única del grupo para detectar duplicados.
    Usamos los IDs de Drive ordenados y unidos en un string.
    """
    ids = sorted([img["id"] for img in image_data])
    return "|".join(ids)


# =========================
# CHECK DUPLICATES
# =========================

def item_exists(image_data):
    """
    Revisa si ya existe un item con exactamente las mismas imágenes.
    Como image_urls es jsonb, traemos los items y comparamos en Python.
    """
    signature_to_find = build_group_signature(image_data)

    response = supabase.table("items").select("id,image_urls").execute()
    items = response.data or []

    for item in items:
        existing_images = item.get("image_urls") or []

        if not isinstance(existing_images, list):
            continue

        existing_ids = []
        for img in existing_images:
            if isinstance(img, dict) and "id" in img:
                existing_ids.append(img["id"])

        existing_signature = "|".join(sorted(existing_ids))

        if existing_signature == signature_to_find:
            return True, item["id"]

    return False, None


# =========================
# CREATE ITEM IN SUPABASE
# =========================

def create_item(group, index):
    image_data = build_image_data(group)

    exists, existing_item_id = item_exists(image_data)

    if exists:
        print(f"Grupo {index+1}: duplicado detectado, ya existe item {existing_item_id}")
        return None

    data = {
        "title": f"Nuevo producto detectado #{index+1}",
        "brand": "Pendiente",
        "category": "Pendiente",
        "status": "pending",
        "image_urls": image_data,
        "drive_folder_id": FOLDER_ID,
        "ai_description": None,
        "notes": None,
        "shopify_status": "draft"
    }

    response = supabase.table("items").insert(data).execute()
    return response.data


# =========================
# MAIN
# =========================

def main():
    service = get_drive_service()
    files = get_images(service)

    if not files:
        print("No hay imágenes.")
        return

    print("IMÁGENES ENCONTRADAS:\n")
    for f in files:
        print(f"{f['name']} | {f['createdTime']}")

    groups = group_images(files)

    print("\n" + "=" * 50)
    print("GRUPOS DETECTADOS:\n")

    for i, group in enumerate(groups):
        print(f"Grupo {i+1}:")
        for file in group:
            print(f" - {file['name']}")
        print()

    print("=" * 50)
    print("CREANDO ITEMS EN SUPABASE:\n")

    for i, group in enumerate(groups):
        created = create_item(group, i)

        if created is not None:
            print(f"Item creado grupo {i+1}: {created}")
        print()


if __name__ == "__main__":
    main()