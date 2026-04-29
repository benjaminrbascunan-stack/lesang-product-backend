from __future__ import annotations

import os
import re
import json
import base64
import hashlib
from datetime import datetime, UTC
from io import BytesIO

from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client, Client
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

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

# Mantener este mismo scope en todos los scripts para no tener que reautorizar.
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Carpeta madre donde están las carpetas ya ordenadas por producto.
FOLDER_ID = "1yfADUnnIXsCTqRI7wcZ6ctao60VHXu-R"

CACHE_TABLE = "ingest_groups_cache"
IMAGE_CACHE_BUCKET = "ai-image-cache"

PRODUCT_ANALYSIS_MAX_SIZE = 1024
PRODUCT_ANALYSIS_QUALITY = 78

PRODUCT_VALIDATION_MAX_SIZE = 1024
PRODUCT_VALIDATION_QUALITY = 78

# Si una carpeta tiene una mezcla evidente, no crea item.
VALIDATION_CONFIDENCE_MIN = 0.75
FABRIC_MATCH_MIN = 0.70

SKIP_FOLDER_NAMES = {
    "REVISAR",
    "revisar",
    "Review",
    "review",
}

if not OPENAI_API_KEY:
    raise ValueError("Falta OPENAI_API_KEY en .env")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY en .env")

client = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

LE_SANG_TEXT = """Prenda de diseñador cuidadosamente seleccionada por Lé Sang. Cada pieza ha sido revisada en detalle para asegurar su calidad, autenticidad y condición, priorizando siempre aquellas que mantienen relevancia en diseño y uso actual.

Debido a la naturaleza única de este tipo de piezas, el stock es limitado y no se repone. Una vez vendida, no vuelve a estar disponible. Cada producto es una oportunidad puntual dentro de una selección en constante cambio."""

BANNED_MARKETING_TERMS = [
    "descubre",
    "perfecto",
    "perfecta",
    "ideal",
    "añade un toque",
    "guardarropa",
    "sofisticación",
    "elegante",
    "elegantes",
    "moderno",
    "moderna",
    "cualquier ocasión",
    "alta calidad",
    "must have",
    "imprescindible",
    "luce",
    "estilo único",
]


# =========================
# HELPERS
# =========================

def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def print_section(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def natural_sort_key(text: str):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]


def hash_signature(signature: str) -> str:
    return hashlib.md5(signature.encode("utf-8")).hexdigest()


def build_hashed_signature(prefix: str, values: list[str]) -> str:
    raw = prefix + "|" + "|".join(sorted(values))
    return hash_signature(raw)


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
# DRIVE FOLDERS / FILES
# =========================

def list_product_folders(service) -> list[dict]:
    print("Buscando subcarpetas de producto en Drive...")

    query = (
        f"'{FOLDER_ID}' in parents and "
        "mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )

    folders = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=query,
                pageSize=500,
                fields="nextPageToken, files(id, name, mimeType, createdTime)",
                orderBy="name",
                pageToken=page_token,
            )
            .execute()
        )

        folders.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    folders = [folder for folder in folders if folder["name"] not in SKIP_FOLDER_NAMES]
    folders.sort(key=lambda f: natural_sort_key(f["name"]))

    print(f"Se encontraron {len(folders)} carpetas de producto.\n")
    return folders


def list_images_in_folder(service, folder_id: str) -> list[dict]:
    query = f"'{folder_id}' in parents and mimeType contains 'image/' and trashed = false"

    files = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=query,
                pageSize=500,
                fields="nextPageToken, files(id, name, mimeType, createdTime, imageMediaMetadata(time))",
                orderBy="name",
                pageToken=page_token,
            )
            .execute()
        )

        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")

        if not page_token:
            break

    files.sort(key=lambda f: natural_sort_key(f["name"]))
    return files


# =========================
# IMAGE DOWNLOAD / COMPRESSION / SUPABASE CACHE
# =========================

def download_drive_file_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    file_buffer = BytesIO()
    downloader = MediaIoBaseDownload(file_buffer, request)

    done = False
    while not done:
        _, done = downloader.next_chunk()

    return file_buffer.getvalue()


def compress_image_for_ai(file_bytes: bytes, max_size: int = 1024, quality: int = 78) -> bytes:
    image = Image.open(BytesIO(file_bytes))
    image = ImageOps.exif_transpose(image)

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")

    image.thumbnail((max_size, max_size))

    output = BytesIO()
    image.save(
        output,
        format="JPEG",
        quality=quality,
        optimize=True,
        progressive=True,
    )

    return output.getvalue()


def get_cached_compressed_image_from_supabase(service, file: dict, max_size: int, quality: int) -> bytes:
    cache_path = f"{file['id']}_{max_size}_{quality}.jpg"

    try:
        cached = supabase.storage.from_(IMAGE_CACHE_BUCKET).download(cache_path)
        print(f"  usando cache Supabase: {cache_path}")
        return cached
    except Exception:
        pass

    original_bytes = download_drive_file_bytes(service, file["id"])
    compressed_bytes = compress_image_for_ai(
        original_bytes,
        max_size=max_size,
        quality=quality,
    )

    supabase.storage.from_(IMAGE_CACHE_BUCKET).upload(
        cache_path,
        compressed_bytes,
        {
            "content-type": "image/jpeg",
            "upsert": "true",
        },
    )

    print(
        f"  comprimida y subida a Supabase | "
        f"original: {round(len(original_bytes) / 1024)} KB | "
        f"IA: {round(len(compressed_bytes) / 1024)} KB"
    )

    return compressed_bytes


def build_openai_image_inputs(service, images: list[dict], max_size: int, quality: int) -> list[dict]:
    print("Preparando imágenes comprimidas para OpenAI...\n")

    content = []

    for idx, file in enumerate(images):
        print(f"Usando imagen {idx}: {file['name']} | id={file['id']}")

        compressed_bytes = get_cached_compressed_image_from_supabase(
            service=service,
            file=file,
            max_size=max_size,
            quality=quality,
        )

        base64_image = base64.b64encode(compressed_bytes).decode("utf-8")

        content.append({
            "type": "input_image",
            "image_url": f"data:image/jpeg;base64,{base64_image}",
        })

    print("\nImágenes preparadas.\n")
    return content


def build_image_data(images: list[dict]) -> list[dict]:
    return [
        {
            "id": file["id"],
            "name": file["name"],
            "url": f"https://drive.google.com/uc?id={file['id']}",
        }
        for file in images
    ]


# =========================
# CACHE TABLE
# =========================

def build_product_folder_signature(folder: dict, images: list[dict]) -> str:
    values = [folder["id"], folder["name"]] + [img["id"] for img in images]
    return build_hashed_signature("product_folder_ingest_editorial_v1", values)


def get_cache_record(group_signature: str):
    response = (
        supabase.table(CACHE_TABLE)
        .select("*")
        .eq("group_signature", group_signature)
        .limit(1)
        .execute()
    )

    rows = response.data or []
    return rows[0] if rows else None


def upsert_cache_record(
    group_signature: str,
    status: str,
    image_ids: list[str],
    resolved_groups: list[list[str]] | None = None,
    unassigned_image_ids: list[str] | None = None,
    reason: str | None = None,
    confidence: float | None = None,
):
    payload = {
        "group_signature": group_signature,
        "status": status,
        "image_ids": image_ids,
        "resolved_groups": resolved_groups,
        "unassigned_image_ids": unassigned_image_ids,
        "reason": reason,
        "confidence": confidence,
        "updated_at": now_iso(),
    }

    response = (
        supabase.table(CACHE_TABLE)
        .upsert(payload, on_conflict="group_signature")
        .execute()
    )

    return response.data


# =========================
# EXISTING ITEMS
# =========================

def build_signature_from_image_data(image_data: list[dict]) -> str:
    ids = sorted([img["id"] for img in image_data if isinstance(img, dict) and img.get("id")])
    return "|".join(ids)


def find_existing_item_by_images(image_data: list[dict]):
    signature_to_find = build_signature_from_image_data(image_data)

    response = supabase.table("items").select("*").execute()
    items = response.data or []

    for item in items:
        existing_images = item.get("image_urls") or []

        if not isinstance(existing_images, list):
            continue

        existing_signature = build_signature_from_image_data(existing_images)

        if existing_signature == signature_to_find:
            return item

    return None


def item_is_complete(item: dict) -> bool:
    if not item:
        return False

    required_fields = [
        "title",
        "brand",
        "category",
        "size",
        "ai_description",
        "shopify_category",
    ]

    for field in required_fields:
        value = item.get(field)

        if value is None:
            return False

        if isinstance(value, str):
            cleaned = value.strip()
            if cleaned == "":
                return False

    return True


# =========================
# CATEGORY MAPPING
# =========================

def map_shopify_category(category: str) -> str:
    c = category.strip().lower()

    if c in {
        "hoodie", "sweatshirt", "crewneck", "t-shirt", "shirt", "polo",
        "knit", "jacket", "coat", "blazer", "vest", "top", "cardigan",
        "sweater", "zip hoodie", "zip-up hoodie", "denim jacket",
    }:
        return "SUPERIOR"

    if c in {
        "pants", "jeans", "shorts", "skirt", "trousers", "cargo pants",
        "denim pants", "denim", "jean",
    }:
        return "INFERIOR"

    if c in {"shoes", "sneakers", "boots", "loafers", "sandals"}:
        return "ZAPATOS"

    if c in {"bag", "belt", "hat", "scarf", "wallet", "jewelry", "accessory", "sunglasses"}:
        return "ACCESORIOS"

    if c in {"jersey", "track jacket", "sportswear", "activewear", "track pants"}:
        return "DEPORTIVO"

    return "ACCESORIOS"


# =========================
# TEXT HELPERS
# =========================

def force_spanish(text: str) -> str:
    response = client.responses.create(
        model="gpt-4o",
        input=(
            "Reescribe este texto en español neutro, seco y descriptivo para catálogo de moda. "
            "No uses lenguaje comercial. Devuelve solo el texto final:\n\n"
            + text
        ),
    )

    return response.output_text.strip()


def rewrite_editorial_description(text: str) -> str:
    response = client.responses.create(
        model="gpt-4o",
        input=(
            "Reescribe este texto en español para Lé Sang, con tono de archivo/catálogo.\n\n"
            "Reglas estrictas:\n"
            "- No uses lenguaje de venta.\n"
            "- No uses: descubre, perfecto, ideal, elegante, sofisticado, guardarropa, ocasión.\n"
            "- No inventes información.\n"
            "- Frases cortas.\n"
            "- Prioriza tipo de prenda, marca, color/material, detalles visibles y estado.\n"
            "- Máximo 3 frases.\n\n"
            "Texto a reescribir:\n"
            f"{text}"
        ),
    )

    return response.output_text.strip()


def clean_editorial_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return "Descripción pendiente."

    lowered = cleaned.lower()

    should_rewrite = any(term in lowered for term in BANNED_MARKETING_TERMS)
    should_rewrite = should_rewrite or any(
        word in lowered
        for word in [" the ", " with ", " and ", " made ", "front", "label", "visible"]
    )

    if should_rewrite:
        print("Reescribiendo descripción a tono editorial Lé Sang...")
        cleaned = rewrite_editorial_description(cleaned)

    # Normaliza exceso de líneas.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def clean_notes_text(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    lowered = cleaned.lower()
    if any(word in lowered for word in [" the ", " with ", " and ", " made ", "visible", "label", "wear"]):
        print("Reescribiendo notas al español...")
        cleaned = force_spanish(cleaned)

    return cleaned.strip()


# =========================
# AI VALIDATION / ANALYSIS
# =========================

def validate_folder_product_with_ai(service, folder: dict, images: list[dict]) -> dict:
    print("Validando que la carpeta corresponde a un solo producto...\n")

    image_inputs = build_openai_image_inputs(
        service=service,
        images=images,
        max_size=PRODUCT_VALIDATION_MAX_SIZE,
        quality=PRODUCT_VALIDATION_QUALITY,
    )

    image_lines = []
    for index, image in enumerate(images):
        image_lines.append(f"{index}: {image['name']} | id={image['id']}")

    response = client.responses.create(
        model="gpt-4o",
        instructions=(
            "Eres un validador estricto para catálogo de moda. "
            "Recibirás imágenes que vienen de una carpeta ya revisada por una persona, donde cada carpeta debería representar un solo producto. "
            "Tu tarea es validar si TODAS las imágenes pertenecen a una sola prenda. "
            "La carpeta humana es una señal fuerte, así que no rechaces por falta de foto frontal si el set es coherente. "
            "Sí debes rechazar si hay múltiples prendas evidentes, marcas incompatibles, categorías incompatibles o denim claramente distinto. "
            "Si hay frente, espalda, etiqueta, detalles, interior o close-ups de la misma prenda, responde true. "
            "Para denim, revisa tono, lavado, desgaste, costuras, color de hilo, textura y construcción. "
            "Para etiquetas, revisa que el color/tela alrededor de la etiqueta sea compatible con la prenda. "
            "No inventes información."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Carpeta de producto: {folder['name']} | id={folder['id']}\n\n"
                            "Imágenes en la carpeta:\n"
                            + "\n".join(image_lines)
                            + "\n\nValida si esta carpeta representa un único producto."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "folder_product_validation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "is_single_product": {"type": "boolean"},
                        "confidence": {"type": "number"},
                        "same_color_likely": {"type": "boolean"},
                        "same_brand_likely": {"type": "boolean"},
                        "same_category_likely": {"type": "boolean"},
                        "label_color_matches_product": {"type": "boolean"},
                        "fabric_match_confidence": {"type": "number"},
                        "reason": {"type": "string"},
                        "suspected_multiple_products": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "is_single_product",
                        "confidence",
                        "same_color_likely",
                        "same_brand_likely",
                        "same_category_likely",
                        "label_color_matches_product",
                        "fabric_match_confidence",
                        "reason",
                        "suspected_multiple_products",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    result = json.loads(response.output_text)

    print("Resultado validación carpeta:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()

    return result


def analyze_folder_product_with_ai(service, folder: dict, images: list[dict]) -> dict:
    print("Analizando producto de carpeta con IA...\n")

    image_inputs = build_openai_image_inputs(
        service=service,
        images=images,
        max_size=PRODUCT_ANALYSIS_MAX_SIZE,
        quality=PRODUCT_ANALYSIS_QUALITY,
    )

    image_lines = []
    for index, image in enumerate(images):
        image_lines.append(f"{index}: {image['name']} | id={image['id']}")

    response = client.responses.create(
        model="gpt-4o",
        instructions=(
            "Eres parte del equipo editorial de Lé Sang. "
            "Analiza UNA prenda de vestir para un catálogo de moda de diseñador/archivo. "
            "La carpeta fue separada manualmente por producto, así que debes extraer la mayor cantidad de información útil. "
            "No inventes datos: si no se ve, escribe 'Pendiente'. "
            "La descripción y las notas deben estar en español. "
            "La categoría específica puede usar términos de moda en inglés como Hoodie, Crewneck, Loafers, Jacket, Pants, Jeans, Shirt, Bag. "
            "La talla debe devolverse como campo propio llamado size. "
            "Si detectas claramente más de una prenda, escribe ERROR_MULTIPLE_PRODUCTS en todos los campos. "
            "No describas lotes. No combines múltiples marcas. "
            "\n\nReglas editoriales para ai_description: "
            "NO uses lenguaje comercial ni de venta. "
            "No uses palabras como descubre, perfecto, ideal, elegante, sofisticado, guardarropa, ocasión. "
            "Escribe en tono seco, preciso, de archivo/catálogo. "
            "Frases cortas. Máximo 3 frases. "
            "Describe lo visible: tipo de prenda, marca, color/material, corte, detalles, estado. "
            "Ejemplo correcto: 'Pantalones Jean Paul Gaultier en algodón blanco. Presentan aros metálicos en los laterales y corte recto. Buen estado general.' "
            "Ejemplo incorrecto: 'Descubre estos elegantes pantalones, perfectos para añadir un toque de sofisticación a tu guardarropa.'"
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Carpeta: {folder['name']}\n"
                            "Imágenes:\n"
                            + "\n".join(image_lines)
                            + "\n\nDevuelve JSON con: title, brand, category, size, ai_description, notes. "
                            "brand: solo si es visible o altamente probable por etiqueta/detalle. "
                            "category: específica y simple. "
                            "ai_description: descripción editorial, seca y factual para Shopify. "
                            "notes: dudas, condición, detalles visibles, talla, composición o cualquier dato relevante."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "catalog_item_from_folder_editorial",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "brand": {"type": "string"},
                        "category": {"type": "string"},
                        "size": {"type": "string"},
                        "ai_description": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": [
                        "title",
                        "brand",
                        "category",
                        "size",
                        "ai_description",
                        "notes",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    data = json.loads(response.output_text)

    joined = json.dumps(data, ensure_ascii=False).lower()
    if "error_multiple_products" in joined:
        raise RuntimeError("La IA detectó múltiples productos dentro de la carpeta.")

    category = (data.get("category") or "Accessory").strip() or "Accessory"
    brand = (data.get("brand") or "Pendiente").strip() or "Pendiente"
    size = (data.get("size") or "Pendiente").strip() or "Pendiente"

    data["title"] = f"{category} {brand}".strip()
    data["brand"] = brand
    data["category"] = category
    data["size"] = size
    data["shopify_category"] = map_shopify_category(category)

    description = clean_editorial_text((data.get("ai_description") or "").strip())
    notes = clean_notes_text((data.get("notes") or "").strip())

    data["ai_description"] = f"{description}\n\n{LE_SANG_TEXT}"
    data["notes"] = notes

    print("Resultado análisis producto:")
    print(json.dumps(data, indent=2, ensure_ascii=False))
    print()

    return data


# =========================
# SUPABASE SAVE
# =========================

def build_item_payload(folder: dict, images: list[dict], ai_result: dict) -> dict:
    image_data = build_image_data(images)

    notes = ai_result["notes"]
    folder_note = f"Carpeta Drive origen: {folder['name']} ({folder['id']})"

    if notes:
        notes = f"{notes}\n\n{folder_note}"
    else:
        notes = folder_note

    return {
        "title": ai_result["title"],
        "brand": ai_result["brand"],
        "category": ai_result["category"],
        "size": ai_result["size"],
        "status": "ready_for_review",
        "drive_folder_id": folder["id"],
        "ai_description": ai_result["ai_description"],
        "notes": notes,
        "shopify_status": "draft",
        "shopify_category": ai_result["shopify_category"],
        "image_urls": image_data,
    }


def save_item_to_supabase(folder: dict, images: list[dict], ai_result: dict):
    image_data = build_image_data(images)
    existing_item = find_existing_item_by_images(image_data)
    payload = build_item_payload(folder, images, ai_result)

    if existing_item:
        print(f"Item existente detectado: {existing_item['id']}")
        print("Actualizando item en Supabase...\n")

        response = (
            supabase.table("items")
            .update(payload)
            .eq("id", existing_item["id"])
            .execute()
        )

        return "updated", response.data

    print("No existe item previo para esta carpeta.")
    print("Creando item nuevo en Supabase...\n")

    response = supabase.table("items").insert(payload).execute()
    return "created", response.data


def mark_folder_for_manual_review(folder: dict, images: list[dict], reason: str, confidence=None):
    image_ids = [img["id"] for img in images]
    signature = build_product_folder_signature(folder, images)

    print("Marcando carpeta para revisión manual...\n")

    upsert_cache_record(
        group_signature=signature,
        status="manual_review_required",
        image_ids=image_ids,
        resolved_groups=[],
        unassigned_image_ids=image_ids,
        reason=f"{folder['name']}: {reason}",
        confidence=confidence,
    )


# =========================
# PROCESS PRODUCT FOLDER
# =========================

def process_product_folder(service, folder: dict):
    print_section(f"PROCESANDO CARPETA: {folder['name']}")

    images = list_images_in_folder(service, folder["id"])

    if not images:
        print("La carpeta no tiene imágenes. Se omite.\n")
        return

    print(f"Imágenes encontradas en carpeta: {len(images)}")
    for idx, img in enumerate(images):
        print(f"{idx}: {img['name']} | id={img['id']}")
    print()

    signature = build_product_folder_signature(folder, images)
    image_ids = [img["id"] for img in images]

    cache_record = get_cache_record(signature)

    if cache_record:
        cache_status = cache_record.get("status")
        print(f"Cache encontrado para carpeta: {cache_status}\n")

        if cache_status == "processed":
            print("Esta carpeta ya fue procesada antes. Se omite.\n")
            return

        if cache_status == "manual_review_required":
            print("Esta carpeta ya está marcada para revisión manual. Se omite.\n")
            return

    existing_item = find_existing_item_by_images(build_image_data(images))

    if existing_item and item_is_complete(existing_item):
        print(f"Item completo ya existente: {existing_item['id']}")
        print("Se marca como processed en cache y se omite IA.\n")

        upsert_cache_record(
            group_signature=signature,
            status="processed",
            image_ids=image_ids,
            resolved_groups=[image_ids],
            reason="Item completo ya existente en Supabase",
            confidence=1.0,
        )
        return

    validation = validate_folder_product_with_ai(service, folder, images)
    confidence = float(validation.get("confidence") or 0)

    if (
        not validation.get("is_single_product")
        or confidence < VALIDATION_CONFIDENCE_MIN
        or not validation.get("same_category_likely")
        or not validation.get("label_color_matches_product")
        or float(validation.get("fabric_match_confidence") or 0) < FABRIC_MATCH_MIN
    ):
        reason = (
            "Validación rechazó carpeta. "
            f"confidence={confidence}. "
            f"fabric_match={validation.get('fabric_match_confidence')}. "
            f"reason={validation.get('reason')}"
        )
        print(reason)
        mark_folder_for_manual_review(folder, images, reason=reason, confidence=confidence)
        return

    try:
        ai_result = analyze_folder_product_with_ai(service, folder, images)
    except Exception as e:
        mark_folder_for_manual_review(
            folder,
            images,
            reason=f"Análisis final falló o detectó múltiples productos: {e}",
            confidence=confidence,
        )
        return

    action, saved_data = save_item_to_supabase(folder, images, ai_result)

    print(f"ACCIÓN EN SUPABASE: {action.upper()}")
    print(json.dumps(saved_data, indent=2, ensure_ascii=False))
    print()

    upsert_cache_record(
        group_signature=signature,
        status="processed",
        image_ids=image_ids,
        resolved_groups=[image_ids],
        reason="Carpeta de producto procesada correctamente",
        confidence=confidence,
    )


# =========================
# MAIN
# =========================

def main():
    print_section("INGEST LÉ SANG — MODO CARPETAS POR PRODUCTO")

    service = get_drive_service()
    product_folders = list_product_folders(service)

    if not product_folders:
        print("No se encontraron subcarpetas de producto.")
        print("Crea carpetas dentro de la carpeta principal de Drive. Cada carpeta debe representar 1 producto.")
        return

    print("Carpetas detectadas:")
    for folder in product_folders:
        print(f"- {folder['name']} | id={folder['id']}")
    print()

    for folder in product_folders:
        process_product_folder(service, folder)

    print_section("INGEST FINALIZADO")


if __name__ == "__main__":
    main()
