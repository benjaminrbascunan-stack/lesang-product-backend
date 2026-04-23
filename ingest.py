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
SAME_ITEM_THRESHOLD = 0.70

CACHE_TABLE = "ingest_groups_cache"

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
# TIME HELPERS
# =========================

def parse_google_time(value: str | None) -> datetime | None:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def resolve_sort_time(file: dict) -> datetime:
    metadata = file.get("imageMediaMetadata") or {}
    exif_time = None

    if isinstance(metadata, dict):
        exif_time = parse_google_time(metadata.get("time"))

    drive_time = parse_google_time(file.get("createdTime"))

    if exif_time:
        return exif_time
    if drive_time:
        return drive_time

    raise ValueError(f"No se pudo resolver tiempo para {file.get('name', 'archivo desconocido')}")


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
            pageSize=300,
            fields="files(id, name, mimeType, createdTime, imageMediaMetadata(time))",
            orderBy="createdTime"
        )
        .execute()
    )

    files = results.get("files", [])

    for file in files:
        sort_time = resolve_sort_time(file)
        file["sort_time"] = sort_time.isoformat()

    files.sort(key=lambda f: f["sort_time"])

    print(f"Se encontraron {len(files)} imágenes.\n")
    return files


def group_images(files):
    print("Agrupando imágenes por metadata temporal...")

    groups = []
    current_group = []
    previous_time = None

    for file in files:
        current_time = datetime.fromisoformat(file["sort_time"])

        if previous_time:
            diff = (current_time - previous_time).total_seconds()
            if diff > GROUP_GAP_SECONDS:
                groups.append(current_group)
                current_group = []

        current_group.append(file)
        previous_time = current_time

    if current_group:
        groups.append(current_group)

    print(f"Se detectaron {len(groups)} grupos candidatos.\n")
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

    for idx, file in enumerate(group):
        print(f"Usando imagen {idx}: {file['name']} | id={file['id']}")
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
# GROUP SIGNATURE / CACHE
# =========================

def build_group_signature_from_ids(image_ids: list[str]) -> str:
    return "|".join(sorted(image_ids))


def build_group_signature(group: list[dict]) -> str:
    image_ids = [file["id"] for file in group]
    return build_group_signature_from_ids(image_ids)


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
        "updated_at": datetime.utcnow().isoformat(),
    }

    response = (
        supabase.table(CACHE_TABLE)
        .upsert(payload, on_conflict="group_signature")
        .execute()
    )

    return response.data


# =========================
# DUPLICATES / EXISTING ITEMS
# =========================

def build_group_signature_from_image_data(image_data):
    ids = sorted([img["id"] for img in image_data])
    return "|".join(ids)


def find_existing_item_by_images(image_data):
    signature_to_find = build_group_signature_from_image_data(image_data)

    response = supabase.table("items").select("*").execute()
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
            if cleaned == "" or cleaned.lower() == "pendiente":
                return False

    return True


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
# IA VALIDATION
# =========================

def validate_group_with_ai(service, group):
    image_inputs = build_openai_image_inputs(service, group)

    print("Validando visualmente grupo con IA...\n")

    response = client.responses.create(
        model="gpt-5.4-mini",
        instructions=(
            "Evalúa si varias imágenes corresponden al mismo producto de moda. "
            "Debes priorizar silueta, tela, color, estampado, costuras, construcción, bolsillos, cierres y detalles materiales. "
            "No rechaces automáticamente un grupo porque las etiquetas aparezcan en ángulos distintos o porque algunas fotos sean detalles y otras sean vistas generales. "
            "Solo rechaza si hay señales realmente fuertes de que son productos distintos. "
            "Responde con criterio útil para operación de catálogo, no con criterio excesivamente conservador."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Analiza estas imágenes y devuelve JSON con: same_item_likely, same_item_confidence, reason. "
                            "same_item_likely: true si es probable que todas las imágenes correspondan al mismo producto. "
                            "same_item_confidence: número entre 0 y 1 que represente la probabilidad de que sea el mismo producto. "
                            "reason: explicación breve en español. "
                            "En esta evaluación debes dar más peso a tela, color, print, silueta y construcción que a pequeñas diferencias en etiquetas o encuadres."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "group_validation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "same_item_likely": {"type": "boolean"},
                        "same_item_confidence": {"type": "number"},
                        "reason": {"type": "string"}
                    },
                    "required": ["same_item_likely", "same_item_confidence", "reason"],
                    "additionalProperties": False
                }
            }
        }
    )

    result = json.loads(response.output_text)

    print("Resultado validación IA:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()

    return result


def split_group_with_ai(service, group):
    print("IA detectó mezcla, reagrupando...\n")

    image_inputs = build_openai_image_inputs(service, group)

    id_map_lines = []
    for file in group:
        id_map_lines.append(f"{file['id']} -> {file['name']}")

    id_map_text = "\n".join(id_map_lines)

    response = client.responses.create(
        model="gpt-5.4-mini",
        instructions=(
            "Vas a recibir múltiples imágenes que pueden pertenecer a distintos productos. "
            "Tu tarea es agruparlas correctamente por producto. "
            "Agrupa por similitud visual: silueta, tela, color, print, construcción. "
            "Ignora diferencias de ángulo o etiquetas. "
            "Debes devolver grupos usando únicamente los IDs de Drive entregados. "
            "No repitas IDs en más de un grupo. "
            "No inventes IDs. "
            "Cada ID puede aparecer como máximo una vez."
        ),
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Estas son las imágenes disponibles con su ID de Drive:\n\n"
                            f"{id_map_text}\n\n"
                            "Devuelve JSON con un array llamado groups. "
                            "Cada grupo debe contener los IDs de Drive de las imágenes que pertenecen al mismo producto. "
                            "Usa únicamente los IDs entregados. "
                            "No repitas ningún ID en dos grupos. "
                            "Ejemplo correcto: [[\"id_1\",\"id_2\"],[\"id_3\",\"id_4\"]]."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "group_split",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "groups": {
                            "type": "array",
                            "items": {
                                "type": "array",
                                "items": {"type": "string"}
                            }
                        }
                    },
                    "required": ["groups"],
                    "additionalProperties": False
                }
            }
        }
    )

    result = json.loads(response.output_text)

    print("Reagrupación IA:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()

    return result["groups"]


def sanitize_split_groups_by_ids(raw_groups, group):
    valid_ids = {file["id"] for file in group}
    globally_used = set()
    cleaned_groups = []

    for raw_group in raw_groups:
        current_group_ids = []
        current_seen = set()

        for raw_id in raw_group:
            if not isinstance(raw_id, str):
                continue

            image_id = raw_id.strip()

            if not image_id:
                continue

            if image_id not in valid_ids:
                print(f"Advertencia: ID inexistente ignorado -> {image_id}")
                continue

            if image_id in current_seen:
                print(f"Advertencia: ID duplicado dentro del mismo subgrupo -> {image_id}")
                continue

            if image_id in globally_used:
                print(f"Advertencia: ID repetido entre subgrupos, se ignora -> {image_id}")
                continue

            current_group_ids.append(image_id)
            current_seen.add(image_id)
            globally_used.add(image_id)

        if current_group_ids:
            cleaned_groups.append(current_group_ids)

    print("Subgrupos sanitizados por ID:")
    print(json.dumps(cleaned_groups, indent=2, ensure_ascii=False))
    print()

    return cleaned_groups


# =========================
# IA ANALYSIS
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
    existing_item = find_existing_item_by_images(image_data)
    payload = build_item_payload(group, ai_result)

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

    print("No existe item previo para este grupo.")
    print("Creando item nuevo en Supabase...\n")

    response = supabase.table("items").insert(payload).execute()
    return "created", response.data


# =========================
# MANUAL REVIEW
# =========================

def mark_group_for_manual_review(group, reason, confidence=None, resolved_groups=None, unassigned_ids=None):
    image_ids = [file["id"] for file in group]
    signature = build_group_signature(group)

    print("Marcando grupo para revisión manual...\n")

    upsert_cache_record(
        group_signature=signature,
        status="manual_review_required",
        image_ids=image_ids,
        resolved_groups=resolved_groups,
        unassigned_image_ids=unassigned_ids,
        reason=reason,
        confidence=confidence,
    )


# =========================
# PROCESS ONE GROUP
# =========================

def process_group(service, group, group_index):
    print("=" * 70)
    print(f"PROCESANDO GRUPO {group_index}\n")

    print("Imágenes del grupo:")
    for idx, file in enumerate(group):
        print(f"{idx}: {file['name']} | id={file['id']} | sort_time={file['sort_time']}")
    print()

    signature = build_group_signature(group)
    image_ids = [file["id"] for file in group]

    cache_record = get_cache_record(signature)

    if cache_record:
        cache_status = cache_record.get("status")
        print(f"Cache encontrado para este grupo: {cache_status}\n")

        if cache_status == "processed":
            print("Este grupo ya fue procesado antes. Se omite.\n")
            return

        if cache_status == "manual_review_required":
            print("Este grupo ya quedó marcado para revisión manual. Se omite.\n")
            return

        if cache_status == "reagrouped_processed":
            resolved_groups = cache_record.get("resolved_groups") or []
            print("Usando reagrupación guardada en cache.\n")

            for i, id_group in enumerate(resolved_groups, start=1):
                new_group = [file for file in group if file["id"] in id_group]

                if not new_group:
                    continue

                print(f"\n--- Subgrupo cacheado {group_index}.{i} ---\n")
                process_group(service, new_group, f"{group_index}.{i}")

            return

    existing_item = find_existing_item_by_images(build_image_data(group))
    if existing_item and item_is_complete(existing_item):
        print(f"Item completo ya existente para este grupo: {existing_item['id']}")
        print("Se marca como processed en cache y se omite IA.\n")

        upsert_cache_record(
            group_signature=signature,
            status="processed",
            image_ids=image_ids,
            reason="Item completo ya existente en Supabase",
            confidence=1.0,
        )
        return

    validation = validate_group_with_ai(service, group)

    confidence = float(validation["same_item_confidence"])
    likely = bool(validation["same_item_likely"])
    reason = validation["reason"]

    # IMPORTANTE:
    # si likely es False, NO se acepta nunca como un solo producto.
    if not likely:
        print("La IA indica que este grupo NO corresponde a un solo producto.\n")
        print("Se intentará reagrupación automática.\n")

        raw_split_groups = split_group_with_ai(service, group)
        split_groups = sanitize_split_groups_by_ids(raw_split_groups, group)

        assigned_ids = {image_id for subgroup in split_groups for image_id in subgroup}
        original_ids = set(image_ids)
        unassigned_ids = sorted(list(original_ids - assigned_ids))

        if not split_groups:
            print("La reagrupación no devolvió subgrupos válidos.\n")
            mark_group_for_manual_review(
                group=group,
                reason=f"Grupo mezclado detectado. Reagrupación IA inválida. Motivo IA: {reason}",
                confidence=confidence,
                resolved_groups=[],
                unassigned_ids=image_ids,
            )
            return

        if unassigned_ids:
            print("La reagrupación dejó imágenes sin asignar. Se marca revisión manual.\n")
            print(f"Imágenes sin asignar: {unassigned_ids}\n")

            mark_group_for_manual_review(
                group=group,
                reason=f"Grupo mezclado detectado. Reagrupación incompleta. Motivo IA: {reason}",
                confidence=confidence,
                resolved_groups=split_groups,
                unassigned_ids=unassigned_ids,
            )
            return

        print("Reagrupación válida. Se guardará en cache y se procesarán subgrupos.\n")

        upsert_cache_record(
            group_signature=signature,
            status="reagrouped_processed",
            image_ids=image_ids,
            resolved_groups=split_groups,
            unassigned_image_ids=[],
            reason=f"Grupo mezclado resuelto por IA. Motivo IA: {reason}",
            confidence=confidence,
        )

        for i, id_group in enumerate(split_groups, start=1):
            new_group = [file for file in group if file["id"] in id_group]

            if not new_group:
                continue

            print(f"\n--- Subgrupo {group_index}.{i} ---\n")
            process_group(service, new_group, f"{group_index}.{i}")

        return

    print(
        f"Grupo aceptado para catalogación automática "
        f"(same_item_likely={likely}, confidence={confidence}).\n"
    )

    ai_result = analyze_group_with_ai(service, group)

    print("RESULTADO IA:\n")
    print(json.dumps(ai_result, indent=2, ensure_ascii=False))
    print()

    action, saved_data = save_item_to_supabase(group, ai_result)

    print(f"ACCIÓN EN SUPABASE: {action.upper()}\n")
    print(json.dumps(saved_data, indent=2, ensure_ascii=False))
    print()

    upsert_cache_record(
        group_signature=signature,
        status="processed",
        image_ids=image_ids,
        reason="Grupo procesado correctamente",
        confidence=1.0,
    )


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
        capture_time = (file.get("imageMediaMetadata") or {}).get("time")
        print(
            f"- {file['name']} | "
            f"id={file['id']} | "
            f"capture_time={capture_time or 'N/A'} | "
            f"drive_created={file['createdTime']} | "
            f"sort_time={file['sort_time']}"
        )
    print()

    groups = group_images(files)

    if not groups:
        print("No hay grupos.")
        return

    print("Grupos detectados:\n")
    for i, group in enumerate(groups, start=1):
        print(f"Grupo {i}:")
        for file in group:
            print(f"  - {file['name']} | id={file['id']}")
        print()

    for i, group in enumerate(groups, start=1):
        process_group(service, group, i)


if __name__ == "__main__":
    main()