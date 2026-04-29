from __future__ import annotations

import os
import json
import base64
from datetime import datetime
from io import BytesIO

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, Client

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

load_dotenv()

# =========================
# CONFIG
# =========================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_ID = "1yfADUnnIXsCTqRI7wcZ6ctao60VHXu-R"
GROUP_GAP_SECONDS = 300

if not OPENAI_API_KEY:
    raise ValueError("Falta OPENAI_API_KEY en .env")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY en .env")

client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

LE_SANG_TEXT = """Prenda de diseñador cuidadosamente seleccionada por Lé Sang. Cada pieza ha sido revisada en detalle para asegurar su calidad, autenticidad y condición, priorizando siempre aquellas que mantienen relevancia en diseño y uso actual.

Debido a la naturaleza única de este tipo de piezas, el stock es limitado y no se repone. Una vez vendida, no vuelve a estar disponible. Cada producto es una oportunidad puntual dentro de una selección en constante cambio."""


# =========================
# GOOGLE DRIVE AUTH
# =========================

def get_drive_service():
    print("Conectando con Google Drive...")

    creds = None

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refrescando token de Google...")
            creds.refresh(Request())
        else:
            print("Abriendo autenticación de Google...")
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json",
                SCOPES,
            )
            creds = flow.run_local_server(port=0)

        with open("token.json", "w") as token:
            token.write(creds.to_json())

    print("Google Drive conectado.\n")
    return build("drive", "v3", credentials=creds)


# =========================
# DRIVE
# =========================

def get_images(service):
    print("Buscando imágenes en la carpeta de Drive...")

    query = f"'{FOLDER_ID}' in parents and mimeType contains 'image/' and trashed = false"

    results = (
        service.files()
        .list(
            q=query,
            pageSize=100,
            fields="files(id, name, mimeType, createdTime)",
            orderBy="createdTime"
        )
        .execute()
    )

    files = results.get("files", [])
    print(f"Se encontraron {len(files)} imágenes.\n")
    return files


def group_images(files):
    print("Agrupando imágenes por tiempo...")

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

    print(f"Se detectaron {len(groups)} grupos.\n")
    return groups


# =========================
# DOWNLOAD
# =========================

def download_drive_file_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    file_buffer = BytesIO()
    downloader = MediaIoBaseDownload(file_buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return file_buffer.getvalue()


def guess_mime_type(filename: str) -> str:
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"


def build_openai_image_inputs(service, group):
    print("Preparando imágenes para OpenAI...\n")

    content = []

    for file in group:
        print(f"Usando imagen: {file['name']}")
        file_bytes = download_drive_file_bytes(service, file["id"])
        base64_image = base64.b64encode(file_bytes).decode("utf-8")

        content.append({
            "type": "input_image",
            "image_url": f"data:{guess_mime_type(file['name'])};base64,{base64_image}",
        })

    print("\nImágenes preparadas.\n")
    return content


def build_image_data(group):
    images = []

    for file in group:
        images.append({
            "id": file["id"],
            "name": file["name"],
            "url": f"https://drive.google.com/uc?id={file['id']}"
        })

    return images


# =========================
# DUPLICATES
# =========================

def build_group_signature(image_data):
    ids = sorted([img["id"] for img in image_data])
    return "|".join(ids)


def find_existing_item_by_images(image_data):
    """
    Busca un item existente comparando los IDs de image_urls.
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
            return item["id"]

    return None


# =========================
# CATEGORY MAPPING
# =========================

def map_shopify_category(category: str) -> str:
    c = category.strip().lower()

    if c in {
        "hoodie", "sweatshirt", "crewneck", "t-shirt", "shirt", "polo",
        "knit", "jacket", "coat", "blazer", "vest", "top"
    }:
        return "SUPERIOR"

    if c in {"pants", "jeans", "shorts", "skirt", "trousers"}:
        return "INFERIOR"

    if c in {"shoes", "sneakers", "boots", "loafers", "sandals"}:
        return "ZAPATOS"

    if c in {"bag", "belt", "hat", "scarf", "wallet", "jewelry", "accessory", "sunglasses"}:
        return "ACCESORIOS"

    if c in {"jersey", "track jacket", "sportswear", "activewear", "track pants"}:
        return "DEPORTIVO"

    return "ACCESORIOS"


# =========================
# FORCE SPANISH
# =========================

def force_spanish(text: str) -> str:
    response = client.responses.create(
        model="gpt-5.4-mini",
        input=f"Reescribe este texto en español neutro para e-commerce de moda. Devuelve solo el texto final:\n\n{text}"
    )
    return response.output_text.strip()


# =========================
# IA
# =========================

def analyze_group_with_ai(service, group):
    image_inputs = build_openai_image_inputs(service, group)

    print("Analizando con IA...\n")

    response = client.responses.create(
        model="gpt-5.4-mini",
        instructions=(
            "Analiza prendas de vestir con precisión. No inventes datos. "
            "La descripción y las notas deben estar en español. "
            "La categoría específica puede usar términos de moda en inglés como Hoodie, Crewneck, Loafers, Jacket, Pants. "
            "La talla debe devolverse como campo propio llamado size. "
            "Si no puedes determinar algo con seguridad, escribe 'Pendiente'."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Devuelve JSON con los campos: title, brand, category, size, ai_description, notes. "
                            "brand: solo si es visible o altamente probable. "
                            "category: específica y simple, por ejemplo Hoodie, Crewneck, Pants, Jacket, Loafers, Shirt, Shoes, Bag. "
                            "size: talla visible en etiqueta o prenda; si no se ve, escribe 'Pendiente'. "
                            "ai_description: siempre en español, útil para catálogo. "
                            "notes: siempre en español, observaciones adicionales, dudas, desgaste o detalles que valga la pena mencionar."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "catalog_item",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "brand": {"type": "string"},
                        "category": {"type": "string"},
                        "size": {"type": "string"},
                        "ai_description": {"type": "string"},
                        "notes": {"type": "string"}
                    },
                    "required": ["title", "brand", "category", "size", "ai_description", "notes"],
                    "additionalProperties": False
                }
            }
        }
    )

    data = json.loads(response.output_text)

    category = data.get("category", "Accessory").strip() or "Accessory"
    brand = data.get("brand", "Pendiente").strip() or "Pendiente"
    size = data.get("size", "Pendiente").strip() or "Pendiente"

    data["title"] = f"{category} {brand}"
    data["shopify_category"] = map_shopify_category(category)

    description = data.get("ai_description", "").strip()
    if any(word in description.lower() for word in [" the ", " with ", " and ", " made ", "front", "label", "visible"]):
        print("Reescribiendo descripción al español...")
        description = force_spanish(description)

    notes = data.get("notes", "").strip()
    if any(word in notes.lower() for word in [" the ", " with ", " and ", " made ", "visible", "label", "wear"]):
        print("Reescribiendo notas al español...")
        notes = force_spanish(notes)

    data["size"] = size
    data["ai_description"] = f"{description}\n\n{LE_SANG_TEXT}"
    data["notes"] = notes

    print("Análisis IA completado.\n")
    return data


# =========================
# SUPABASE SAVE
# =========================

def build_item_payload(group, ai_result):
    image_data = build_image_data(group)

    payload = {
        "title": ai_result["title"],
        "brand": ai_result["brand"],
        "category": ai_result["category"],
        "size": ai_result["size"],
        "status": "ready_for_review",
        "drive_folder_id": FOLDER_ID,
        "ai_description": ai_result["ai_description"],
        "notes": ai_result["notes"],
        "shopify_status": "draft",
        "shopify_category": ai_result["shopify_category"],
        "image_urls": image_data,
    }

    return payload


def save_item_to_supabase(group, ai_result):
    image_data = build_image_data(group)
    existing_item_id = find_existing_item_by_images(image_data)
    payload = build_item_payload(group, ai_result)

    if existing_item_id:
        print(f"Item existente detectado: {existing_item_id}")
        print("Actualizando item en Supabase...\n")

        response = (
            supabase.table("items")
            .update(payload)
            .eq("id", existing_item_id)
            .execute()
        )

        return "updated", response.data

    print("No existe item previo para este grupo.")
    print("Creando item nuevo en Supabase...\n")

    response = supabase.table("items").insert(payload).execute()
    return "created", response.data


# =========================
# MAIN
# =========================

def main():
    service = get_drive_service()
    files = get_images(service)

    if not files:
        print("No se encontraron imágenes.")
        return

    print("Imágenes encontradas:\n")
    for file in files:
        print(f"- {file['name']} | {file['createdTime']}")
    print()

    groups = group_images(files)

    if not groups:
        print("No hay grupos.")
        return

    print("Grupos detectados:\n")
    for i, group in enumerate(groups, start=1):
        print(f"Grupo {i}:")
        for file in group:
            print(f"  - {file['name']}")
        print()

    first_group = groups[0]

    print("Usando el primer grupo detectado:\n")
    for file in first_group:
        print(f"- {file['name']} | {file['createdTime']}")
    print()

    ai_result = analyze_group_with_ai(service, first_group)

    print("RESULTADO IA:\n")
    print(json.dumps(ai_result, indent=2, ensure_ascii=False))
    print()

    action, saved_data = save_item_to_supabase(first_group, ai_result)

    print(f"ACCIÓN EN SUPABASE: {action.upper()}\n")
    print(json.dumps(saved_data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()