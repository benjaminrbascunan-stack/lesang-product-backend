from __future__ import annotations

import os
import re
import json
import base64
import time
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
SUPABASE_URL   = os.getenv("SUPABASE_URL")
SUPABASE_KEY   = os.getenv("SUPABASE_KEY")

SCOPES = ["https://www.googleapis.com/auth/drive"]

FOLDER_ID = "1yfADUnnIXsCTqRI7wcZ6ctao60VHXu-R"

IMAGE_CACHE_BUCKET = "ai-image-cache"

PAIR_MAX_SIZE  = 1024
PAIR_QUALITY   = 78

GROUP_VALIDATION_MAX_SIZE = 768   # 768px suficiente para detectar mezclas
GROUP_VALIDATION_QUALITY  = 72

NAME_MAX_SIZE = 1024
NAME_QUALITY  = 78

PAIR_SAME_THRESHOLD        = 85
PAIR_REVIEW_THRESHOLD      = 80
GROUP_VALIDATION_THRESHOLD = 85
LARGE_GROUP_SIZE           = 8

CREATE_REVIEW_FOLDER = True
REVIEW_FOLDER_NAME   = "REVISAR"

# ── Rate limit y checkpoint ───────────────────────────────────────────────────
PAIR_DELAY_SECONDS = 1.5
CHECKPOINT_FILE    = "/tmp/auto_group_checkpoint.json"

# =========================
# COSTO ESTIMADO gpt-4o
# Input:  $2.50  / 1M tokens
# Output: $10.00 / 1M tokens
# Imagen 1024px ≈ 1700 tokens de imagen
# =========================
COST_INPUT_PER_TOKEN  = 2.50  / 1_000_000
COST_OUTPUT_PER_TOKEN = 10.00 / 1_000_000
COST_IMAGE_TOKENS     = 1700  # tokens por imagen 1024px aprox

# Acumulador global de costo
_total_cost_usd = 0.0
_total_calls    = 0


def add_cost(n_images: int, model: str = "gpt-4o"):
    global _total_cost_usd, _total_calls
    # Estimado: tokens de imagen + ~300 tokens de texto input + ~100 output
    input_tokens  = n_images * COST_IMAGE_TOKENS + 300
    output_tokens = 100
    cost = (input_tokens * COST_INPUT_PER_TOKEN) + (output_tokens * COST_OUTPUT_PER_TOKEN)
    _total_cost_usd += cost
    _total_calls += 1
    return cost


def print_cost(label: str, cost: float):
    print(f"  💰 {label}: ~${cost:.4f} USD | acumulado: ~${_total_cost_usd:.4f} USD ({_total_calls} llamadas)")


if not OPENAI_API_KEY:
    raise ValueError("Falta OPENAI_API_KEY en .env")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Faltan SUPABASE_URL o SUPABASE_KEY en .env")

client   = OpenAI(api_key=OPENAI_API_KEY)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# =========================
# HELPERS
# =========================

def print_section(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def natural_sort_key(text: str):
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]


def extract_filename_number(name: str) -> int | None:
    matches = re.findall(r"(\d+)", name)
    if not matches:
        return None
    return int(matches[-1])


def sanitize_folder_name(name: str) -> str:
    name = name.strip().lower()
    name = name.replace("&", "and")
    name = re.sub(r"[^a-z0-9áéíóúñü\s\-_]+", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.replace(" ", "-")
    name = re.sub(r"-+", "-", name)
    return name[:80] or "producto"


def clamp_confidence(value) -> float:
    try:
        v = float(value)
    except Exception:
        return 0.0
    if v <= 1:
        v = v * 100
    return max(0.0, min(100.0, v))


# =========================
# CHECKPOINT
# =========================

def save_checkpoint(decisions: list[dict], images: list[dict]):
    """Guarda el progreso de comparaciones al disco."""
    data = {
        "decisions":  decisions,
        "image_ids":  [img["id"] for img in images],
        "timestamp":  time.time(),
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  💾 Checkpoint guardado: {len(decisions)} comparaciones")


def load_checkpoint(images: list[dict]) -> list[dict] | None:
    """
    Carga checkpoint si existe y corresponde al mismo batch de imágenes.
    Retorna las decisions guardadas o None si no hay checkpoint válido.
    """
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        saved_ids  = data.get("image_ids", [])
        current_ids = [img["id"] for img in images]

        if saved_ids != current_ids:
            print("  ⚠ Checkpoint de otro batch — ignorando.")
            return None

        decisions = data.get("decisions", [])
        age_min   = (time.time() - data.get("timestamp", 0)) / 60

        print(f"  ✔ Checkpoint encontrado: {len(decisions)} comparaciones ({age_min:.0f} min atrás)")
        return decisions

    except (FileNotFoundError, json.JSONDecodeError):
        return None


def clear_checkpoint():
    """Elimina el checkpoint al terminar exitosamente."""
    try:
        os.remove(CHECKPOINT_FILE)
        print("  🗑 Checkpoint eliminado.")
    except FileNotFoundError:
        pass


# =========================
# GOOGLE DRIVE AUTH
# =========================

def get_drive_service():
    print("Conectando con Google Drive...")

    import os
    import json
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    creds = None

    token_json = os.getenv("GOOGLE_TOKEN_JSON")

    if token_json:
        print("Usando autenticación desde variables (nube)...")
        creds = Credentials.from_authorized_user_info(json.loads(token_json), SCOPES)
        print("✔ Google Drive conectado (nube)\n")
        return build("drive", "v3", credentials=creds)

    print("Usando autenticación local...")

    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow

    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            print("Refrescando token de Google...")
            creds.refresh(Request())
        else:
            print("Abriendo autenticación de Google...")
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    print("✔ Google Drive conectado (local)\n")
    return build("drive", "v3", credentials=creds)


# =========================
# DRIVE FILES
# =========================

def list_loose_images_in_root(service) -> list[dict]:
    print("Buscando imágenes sueltas en la carpeta madre...")
    query      = f"'{FOLDER_ID}' in parents and mimeType contains 'image/' and trashed = false"
    files      = []
    page_token = None

    while True:
        response = (
            service.files()
            .list(
                q=query,
                pageSize=500,
                fields="nextPageToken, files(id, name, mimeType, createdTime, parents)",
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
    print(f"Se encontraron {len(files)} imágenes sueltas.\n")
    return files


def find_or_create_folder(service, folder_name: str, parent_id: str) -> dict:
    safe_name = folder_name.replace("'", "\\'")
    query = (
        f"'{parent_id}' in parents and "
        "mimeType = 'application/vnd.google-apps.folder' and "
        f"name = '{safe_name}' and trashed = false"
    )
    response = service.files().list(q=query, pageSize=10, fields="files(id, name)").execute()
    folders  = response.get("files", [])
    if folders:
        return folders[0]

    metadata = {
        "name":     folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents":  [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id, name").execute()
    print(f"Carpeta creada: {folder['name']} | id={folder['id']}")
    return folder


def create_drive_folder(service, folder_name: str, parent_id: str = FOLDER_ID) -> dict:
    metadata = {
        "name":     folder_name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents":  [parent_id],
    }
    folder = service.files().create(body=metadata, fields="id, name").execute()
    print(f"Carpeta creada: {folder['name']} | id={folder['id']}")
    return folder


def move_file_to_folder(service, file_id: str, target_folder_id: str):
    file = service.files().get(fileId=file_id, fields="parents").execute()
    previous_parents = ",".join(file.get("parents", []))
    service.files().update(
        fileId=file_id,
        addParents=target_folder_id,
        removeParents=previous_parents,
        fields="id, parents",
    ).execute()


# =========================
# IMAGE DOWNLOAD / COMPRESSION / CACHE
# =========================

def download_drive_file_bytes(service, file_id: str) -> bytes:
    request     = service.files().get_media(fileId=file_id)
    file_buffer = BytesIO()
    downloader  = MediaIoBaseDownload(file_buffer, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return file_buffer.getvalue()


def compress_image_for_ai(file_bytes: bytes, max_size: int, quality: int) -> bytes:
    image = Image.open(BytesIO(file_bytes))
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    elif image.mode == "L":
        image = image.convert("RGB")
    image.thumbnail((max_size, max_size))
    output = BytesIO()
    image.save(output, format="JPEG", quality=quality, optimize=True, progressive=True)
    return output.getvalue()


def get_cached_compressed_image_from_supabase(
    service, file: dict, max_size: int, quality: int
) -> bytes:
    cache_path = f"{file['id']}_{max_size}_{quality}.jpg"
    try:
        cached = supabase.storage.from_(IMAGE_CACHE_BUCKET).download(cache_path)
        print(f"  usando cache Supabase: {cache_path}")
        return cached
    except Exception:
        pass

    original_bytes   = download_drive_file_bytes(service, file["id"])
    compressed_bytes = compress_image_for_ai(original_bytes, max_size=max_size, quality=quality)

    supabase.storage.from_(IMAGE_CACHE_BUCKET).upload(
        cache_path,
        compressed_bytes,
        {"content-type": "image/jpeg", "upsert": "true"},
    )
    print(
        f"  comprimida y subida a Supabase | "
        f"original: {round(len(original_bytes) / 1024)} KB | "
        f"IA: {round(len(compressed_bytes) / 1024)} KB"
    )
    return compressed_bytes


def build_openai_image_inputs(
    service, images: list[dict], max_size: int, quality: int
) -> list[dict]:
    content = []
    for index, image in enumerate(images):
        print(f"Imagen {index}: {image['name']} | id={image['id']}")
        compressed_bytes = get_cached_compressed_image_from_supabase(
            service=service, file=image, max_size=max_size, quality=quality,
        )
        base64_image = base64.b64encode(compressed_bytes).decode("utf-8")
        content.append({
            "type":      "input_image",
            "image_url": f"data:image/jpeg;base64,{base64_image}",
        })
    return content


# =========================
# AI: PAIR COMPARISON
# =========================

PAIR_INSTRUCTIONS = """Eres un comparador visual de productos para una tienda de moda de lujo de archivo/segunda mano.

REGLA PRINCIPAL — SESGO CONSERVADOR:
Ante cualquier duda, responde mismo_producto=false.
Es mejor crear dos carpetas que mezclar dos productos en una.
Los errores de separación se corrigen fácil. Los errores de mezcla son costosos.

TAREA: Determinar si las dos fotos muestran el MISMO objeto físico.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
REGLA ESPECIAL — FOTOS DE ETIQUETA O DETALLE (aplicar primero):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Si UNA de las dos fotos es claramente un close-up de:
etiqueta interior, etiqueta exterior, logo, costuras, cierre,
bolsillo, manga, bajo, tela, o cualquier detalle aislado sin
prenda completa visible:

→ Esa foto NUNCA es un producto independiente.
→ Compará la marca/color/material visible con la prenda de la otra foto.
→ Si son compatibles o no hay contradicción clara → mismo_producto=true.
→ Solo cortá si la etiqueta muestra una marca CLARAMENTE distinta a la otra foto.
→ En caso de duda con etiquetas → mismo_producto=true.
  (Esta es la única excepción al sesgo conservador.)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PARA EL RESTO DE CASOS, analizá en este orden:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

NIVEL 1 — ETIQUETAS Y TEXTO:
¿Hay marcas, logos o texto visible en ambas fotos?
- Misma marca → favorece mismo producto, continuá.
- Marcas distintas claramente legibles → mismo_producto=false, confianza=100.
- Solo una tiene etiqueta → usala como referencia.

NIVEL 2 — TIPO DE PRENDA:
¿El tipo de objeto es compatible?
- Campera ≠ pantalón. Cartera ≠ remera. Zapato ≠ cinturón.
- Tipo claramente distinto → mismo_producto=false, confianza=100.

NIVEL 3 — DETALLES CONSTRUCTIVOS:
Para prendas del mismo tipo, compará:
costuras, bolsillos, botones/cierres/herrajes, parches, bordados, desgaste.
Mismo color NO es evidencia suficiente. Necesitás detalles coincidentes.

NIVEL 4 — ÁNGULOS VÁLIDOS:
Son siempre el mismo producto si los detalles coinciden:
frente, espalda, lateral, interior, flat lay vs colgado, distinta luz.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CHECKLIST OBLIGATORIO PARA DENIM:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Para considerar dos fotos de denim como el MISMO producto
necesitás confirmar AL MENOS 3 de estos 4 puntos:
1. Tono y lavado idéntico (mismo nivel de desgaste, mismo azul exacto)
2. Misma construcción de bolsillos (forma, posición, costuras)
3. Mismo hilo de costura (color y patrón)
4. Misma etiqueta o marca visible

Si confirmás menos de 3 → mismo_producto=false.
Para denim, ante cualquier duda → cortar siempre.

Responde SOLO JSON, sin texto adicional, sin markdown."""


def compare_pair_with_ai(service, left: dict, right: dict, pair_index: int, total_pairs: int) -> dict:
    print_section(f"COMPARANDO PAR: {left['name']} → {right['name']}")
    print(f"PROGRESS: {pair_index} / {total_pairs}")

    image_inputs = build_openai_image_inputs(
        service=service,
        images=[left, right],
        max_size=PAIR_MAX_SIZE,
        quality=PAIR_QUALITY,
    )

    response = client.responses.create(
        model="gpt-4o",
        instructions=PAIR_INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            f"Foto A: {left['name']} | id={left['id']}\n"
                            f"Foto B: {right['name']} | id={right['id']}\n\n"
                            "¿Estas dos fotos pertenecen al mismo producto físico?\n"
                            "Responde solo JSON."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type":   "json_schema",
                "name":   "pair_product_comparison",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "mismo_producto": {"type": "boolean"},
                        "confianza":      {"type": "number"},
                        "nivel_decision": {
                            "type": "string",
                            "enum": ["etiqueta", "tipo_prenda", "detalles", "angulo", "duda"],
                        },
                        "es_detalle_o_etiqueta": {"type": "boolean"},
                        "razon": {"type": "string"},
                    },
                    "required": [
                        "mismo_producto",
                        "confianza",
                        "nivel_decision",
                        "es_detalle_o_etiqueta",
                        "razon",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    result = json.loads(response.output_text)
    result["confianza"] = clamp_confidence(result.get("confianza"))

    cost = add_cost(n_images=2)
    print_cost("comparación par", cost)
    print("Resultado par:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()
    return result


def run_pairwise_comparisons(service, images: list[dict]) -> list[dict]:
    print_section("ETAPA 1 — COMPARACIÓN DE PARES CONSECUTIVOS")

    # ── Intentar retomar desde checkpoint ─────────────────────────────────────
    saved = load_checkpoint(images)
    if saved:
        already_done = len(saved)
        total_pairs  = len(images) - 1
        if already_done >= total_pairs:
            print(f"✔ Todas las comparaciones ya estaban en checkpoint ({already_done}/{total_pairs})")
            return saved
        print(f"  Retomando desde comparación {already_done + 1} / {total_pairs}")
        decisions = saved
        start_idx = already_done
    else:
        decisions = []
        start_idx = 0

    total_pairs = len(images) - 1

    for i in range(start_idx, total_pairs):
        left  = images[i]
        right = images[i + 1]

        # ── Retry automático en caso de rate limit ────────────────────────────
        max_retries = 3
        for attempt in range(max_retries):
            try:
                result = compare_pair_with_ai(
                    service, left, right,
                    pair_index=i + 1,
                    total_pairs=total_pairs,
                )
                break
            except Exception as e:
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    wait = 15 * (attempt + 1)
                    print(f"  ⚠ Rate limit — esperando {wait}s antes de reintentar...")
                    time.sleep(wait)
                    if attempt == max_retries - 1:
                        raise
                else:
                    raise

        decisions.append({
            "index":                 i,
            "left_id":               left["id"],
            "left_name":             left["name"],
            "right_id":              right["id"],
            "right_name":            right["name"],
            "mismo_producto":        bool(result["mismo_producto"]),
            "confianza":             float(result["confianza"]),
            "nivel_decision":        result.get("nivel_decision", "duda"),
            "es_detalle_o_etiqueta": bool(result.get("es_detalle_o_etiqueta", False)),
            "razon":                 result.get("razon", ""),
        })

        # ── Guardar checkpoint y esperar antes de la próxima llamada ──────────
        save_checkpoint(decisions, images)
        if i < total_pairs - 1:
            time.sleep(PAIR_DELAY_SECONDS)

    return decisions


# =========================
# CUTS → GROUPS
# =========================

def build_candidate_groups_from_pairs(
    images: list[dict], decisions: list[dict]
) -> list[dict]:
    print_section("ETAPA 2 — DETECCIÓN DE CORTES")

    groups              = []
    current             = [images[0]] if images else []
    current_confidences = []
    current_reasons     = []
    current_levels      = []
    review_flag         = False

    for decision in decisions:
        right      = next(img for img in images if img["id"] == decision["right_id"])
        same       = decision["mismo_producto"]
        confidence = float(decision["confianza"])
        level      = decision.get("nivel_decision", "duda")
        is_detail  = decision.get("es_detalle_o_etiqueta", False)

        forced_cut = len(current) >= LARGE_GROUP_SIZE
        cut        = (not same) or (confidence < PAIR_SAME_THRESHOLD) or forced_cut

        detail_tag = " [DETALLE/ETIQUETA]" if is_detail else ""
        forced_tag = f" [CORTE FORZADO tamaño={len(current)}]" if forced_cut and same else ""

        print(
            f"{decision['left_name']} → {decision['right_name']} | "
            f"same={same} | conf={confidence} | nivel={level}{detail_tag}{forced_tag} | "
            f"{decision['razon']}"
        )

        if cut:
            avg_confidence = (
                sum(current_confidences) / len(current_confidences)
                if current_confidences else 100.0
            )
            groups.append({
                "images":              current,
                "pair_confidence_avg": avg_confidence,
                "review_flag":         review_flag or avg_confidence < PAIR_REVIEW_THRESHOLD,
                "pair_reasons":        current_reasons,
                "pair_levels":         current_levels,
                "forced_cut":          forced_cut,
            })
            current             = [right]
            current_confidences = []
            current_reasons     = []
            current_levels      = []
            review_flag         = confidence < PAIR_REVIEW_THRESHOLD
        else:
            current.append(right)
            current_confidences.append(confidence)
            current_reasons.append(decision["razon"])
            current_levels.append(level)
            if confidence < PAIR_REVIEW_THRESHOLD:
                review_flag = True

    if current:
        avg_confidence = (
            sum(current_confidences) / len(current_confidences)
            if current_confidences else 100.0
        )
        groups.append({
            "images":              current,
            "pair_confidence_avg": avg_confidence,
            "review_flag":         review_flag or avg_confidence < PAIR_REVIEW_THRESHOLD,
            "pair_reasons":        current_reasons,
            "pair_levels":         current_levels,
            "forced_cut":          False,
        })

    print("\nGrupos candidatos:")
    for idx, group in enumerate(groups, start=1):
        names = [img["name"] for img in group["images"]]
        print(
            f"  {idx}: {names} | "
            f"avg={round(group['pair_confidence_avg'], 1)} | "
            f"review={group['review_flag']}"
        )

    return groups


# =========================
# AI: GROUP VALIDATION
# =========================

GROUP_VALIDATION_INSTRUCTIONS = """Eres un validador visual de grupos de productos para una tienda de moda de lujo.

Recibirás todas las fotos de un grupo candidato.

TAREA PRINCIPAL: Determinar si TODAS las imágenes pertenecen a un único producto físico.

TAREA SECUNDARIA (solo si el grupo NO es válido):
Identificar cuántos productos distintos hay e intentar agrupar los índices
de imágenes por producto para subdividir automáticamente.

FOTOS VÁLIDAS DEL MISMO PRODUCTO:
frente, espalda, interior, etiqueta, close-up de detalles, bolsillos,
cierres, bajos, distintas luces. Una etiqueta sola es parte del producto,
nunca un producto independiente.

SEÑALES DE MEZCLA:
- Etiquetas de marcas distintas claramente legibles
- Tipos de prenda distintos
- Mismo tipo pero construcción diferente
- Para denim: tono, lavado, desgaste y construcción deben coincidir en 3 de 4 puntos

SOBRE suspicious_image_ids:
Si hay 1-2 fotos que no encajan pero el resto del grupo es coherente,
marcá SOLO esas fotos como suspicious.
El sistema las separará automáticamente sin mandar todo el grupo a revisión.

Responde SOLO JSON, sin texto adicional, sin markdown."""


def validate_candidate_group_with_ai(
    service, group: dict, group_index: int, total_groups: int, model: str = "gpt-4o"
) -> dict:
    images = group["images"]
    model_tag = "mini" if model == "gpt-4o-mini" else "4o"
    print_section(f"ETAPA 3 — VALIDANDO GRUPO {group_index} / {total_groups} [{model_tag}]")
    print(f"PROGRESS: {group_index} / {total_groups}")
    print("Fotos del grupo:")
    for image in images:
        print(f"- {image['name']} | id={image['id']}")

    image_inputs = build_openai_image_inputs(
        service=service,
        images=images,
        max_size=GROUP_VALIDATION_MAX_SIZE,
        quality=GROUP_VALIDATION_QUALITY,
    )
    lines = [f"{i}: {img['name']} | id={img['id']}" for i, img in enumerate(images)]

    response = client.responses.create(
        model=model,
        instructions=GROUP_VALIDATION_INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Grupo candidato:\n"
                            + "\n".join(lines)
                            + "\n\n¿Este grupo corresponde a un único producto físico?\n"
                            "Si no es válido, agrupá los índices por producto detectado."
                        ),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type":   "json_schema",
                "name":   "group_validation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "grupo_valido":         {"type": "boolean"},
                        "confianza":            {"type": "number"},
                        "razon":                {"type": "string"},
                        "suspicious_image_ids": {
                            "type":  "array",
                            "items": {"type": "string"},
                        },
                        "subgrupos_indices": {
                            "type":  "array",
                            "items": {
                                "type":  "array",
                                "items": {"type": "integer"},
                            },
                        },
                    },
                    "required": [
                        "grupo_valido",
                        "confianza",
                        "razon",
                        "suspicious_image_ids",
                        "subgrupos_indices",
                    ],
                    "additionalProperties": False,
                },
            }
        },
    )

    result = json.loads(response.output_text)
    result["confianza"] = clamp_confidence(result.get("confianza"))

    cost = add_cost(n_images=len(images))
    print_cost(f"validación grupo ({len(images)} fotos)", cost)
    print("Resultado validación:")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print()
    return result


def try_split_group(group: dict, validation: dict) -> list[dict]:
    suspicious_ids    = validation.get("suspicious_image_ids") or []
    subgrupos_indices = validation.get("subgrupos_indices") or []
    images            = group["images"]

    if 0 < len(suspicious_ids) <= 2:
        clean   = [img for img in images if img["id"] not in suspicious_ids]
        suspect = [img for img in images if img["id"] in suspicious_ids]
        print(f"  → Split liviano: {len(clean)} fotos aprobadas, {len(suspect)} a REVISAR.")
        result = []
        if clean:
            result.append({**group, "images": clean, "needs_review": False, "auto_split": True})
        if suspect:
            result.append({**group, "images": suspect, "needs_review": True, "auto_split": True})
        return result

    subgrupos_validos = [sg for sg in subgrupos_indices if len(sg) >= 1]
    if len(subgrupos_validos) >= 2:
        print(f"  → Subdividiendo en {len(subgrupos_validos)} subgrupos.")
        subgroups = []
        for i, indices in enumerate(subgrupos_validos):
            sub_images = [images[idx] for idx in indices if 0 <= idx < len(images)]
            if not sub_images:
                continue
            subgroups.append({
                **group,
                "images":       sub_images,
                "review_flag":  True,
                "needs_review": True,
                "auto_split":   True,
                "validation": {
                    **validation,
                    "razon": f"Subgrupo {i+1} detectado automáticamente.",
                },
            })
            print(f"    Subgrupo {i+1}: {[img['name'] for img in sub_images]}")
        return subgroups if subgroups else [group]

    print("  → Sin subgrupos claros. Grupo completo va a REVISAR.")
    group["needs_review"] = True
    return [group]


def validate_all_groups(service, candidate_groups: list[dict]) -> list[dict]:
    validated     = []
    groups_to_val = [g for g in candidate_groups if g["review_flag"] or len(g["images"]) > 2]
    total_to_val  = len(groups_to_val)
    val_counter   = 0

    for group in candidate_groups:
        should_validate = group["review_flag"] or len(group["images"]) > 2

        if not should_validate:
            group["validation"] = {
                "grupo_valido":         True,
                "confianza":            group["pair_confidence_avg"],
                "razon":                "Grupo corto aprobado por pares consecutivos.",
                "suspicious_image_ids": [],
                "subgrupos_indices":    [],
            }
            group["needs_review"] = group["pair_confidence_avg"] < PAIR_REVIEW_THRESHOLD
            validated.append(group)
            continue

        val_counter += 1

        avg_conf = group["pair_confidence_avg"]
        use_model = (
            "gpt-4o-mini"
            if avg_conf >= 90 and not group["review_flag"]
            else "gpt-4o"
        )
        print(f"  Confianza promedio pares: {avg_conf:.1f} → usando {use_model}")

        # Retry automático en validación de grupos igual que en pares
        max_retries = 3
        for attempt in range(max_retries):
            try:
                validation = validate_candidate_group_with_ai(
                    service, group,
                    group_index=val_counter,
                    total_groups=total_to_val,
                    model=use_model,
                )
                break
            except Exception as e:
                if "429" in str(e) or "rate_limit" in str(e).lower():
                    wait = 20 * (attempt + 1)
                    print(f"  ⚠ Rate limit en validación — esperando {wait}s...")
                    time.sleep(wait)
                    if attempt == max_retries - 1:
                        raise
                else:
                    raise

        time.sleep(1.5)  # delay entre validaciones
        group["validation"] = validation

        is_valid   = validation.get("grupo_valido", False)
        conf       = float(validation.get("confianza") or 0)
        suspicious = bool(validation.get("suspicious_image_ids"))

        group["needs_review"] = (
            not is_valid
            or conf < GROUP_VALIDATION_THRESHOLD
            or suspicious
            or group["review_flag"]
        )

        if not is_valid or suspicious:
            split_result = try_split_group(group, validation)
            validated.extend(split_result)
        else:
            validated.append(group)

    return validated


# =========================
# AI: FOLDER NAME
# =========================

NAME_INSTRUCTIONS = (
    "Propón un nombre corto y útil para una carpeta de producto de moda de lujo. "
    "Formato: marca (si se ve) + color o material + categoría. "
    "Ejemplos: 'versace-black-pants', 'gucci-beige-bag', 'stussy-black-hoodie', 'prada-brown-pants'. "
    "Si no ves la marca, omítela. Usa solo minúsculas y guiones, sin caracteres especiales. "
    "Máximo 5 palabras."
)


def propose_folder_name_with_ai(
    service, images: list[dict], index: int, needs_review: bool,
    folder_index: int, total_folders: int,
) -> str:
    print_section(f"NOMBRE DE CARPETA {folder_index} / {total_folders}")
    print(f"PROGRESS: {folder_index} / {total_folders}")

    image_inputs = build_openai_image_inputs(
        service=service,
        images=images,
        max_size=NAME_MAX_SIZE,
        quality=NAME_QUALITY,
    )
    lines = [f"{i}: {img['name']} | id={img['id']}" for i, img in enumerate(images)]

    response = client.responses.create(
        model="gpt-4o-mini",
        instructions=NAME_INSTRUCTIONS,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "Imágenes del producto:\n" + "\n".join(lines),
                    },
                    *image_inputs,
                ],
            }
        ],
        text={
            "format": {
                "type":   "json_schema",
                "name":   "folder_name_proposal",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "folder_name": {"type": "string"},
                        "confidence":  {"type": "number"},
                    },
                    "required":             ["folder_name", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
    )

    result = json.loads(response.output_text)
    cost   = add_cost(n_images=len(images))
    print_cost(f"nombre carpeta (mini, {len(images)} fotos)", cost)

    base   = sanitize_folder_name(result.get("folder_name") or "producto")
    prefix = "REVISAR" if needs_review else f"{index:03d}"
    return f"{prefix} {base}"


# =========================
# CREATE FOLDERS IN DRIVE
# =========================

def create_folders_from_groups(service, groups: list[dict]):
    print_section("ETAPA 4 — CREANDO CARPETAS EN DRIVE")

    review_root = None
    if CREATE_REVIEW_FOLDER:
        review_root = find_or_create_folder(service, REVIEW_FOLDER_NAME, FOLDER_ID)

    product_index = 1
    review_index  = 1
    total_groups  = len(groups)

    for folder_index, group in enumerate(groups, start=1):
        images       = group["images"]
        validation   = group.get("validation") or {}
        needs_review = bool(group.get("needs_review"))
        auto_split   = bool(group.get("auto_split"))

        folder_name = propose_folder_name_with_ai(
            service=service,
            images=images,
            index=product_index if not needs_review else review_index,
            needs_review=needs_review,
            folder_index=folder_index,
            total_folders=total_groups,
        )

        if needs_review:
            parent_id   = review_root["id"] if review_root else FOLDER_ID
            folder_name = f"{review_index:03d} {folder_name.replace('REVISAR ', '')}"
            if auto_split:
                folder_name = f"{folder_name} [SPLIT]"
            review_index += 1
        else:
            parent_id = FOLDER_ID
            product_index += 1

        print("\nCreando carpeta para grupo:")
        print(f"  Nombre:     {folder_name}")
        print(f"  Revisión:   {needs_review}")
        print(f"  Auto-split: {auto_split}")
        print(f"  Fotos:      {[img['name'] for img in images]}")

        folder = create_drive_folder(service, folder_name, parent_id=parent_id)

        for image in images:
            print(f"  moviendo: {image['name']}")
            move_file_to_folder(service, image["id"], folder["id"])


# =========================
# METRICS
# =========================

def print_metrics(images: list[dict], pair_decisions: list[dict], groups: list[dict]):
    print_section("MÉTRICAS DEL BATCH")

    review_groups = [g for g in groups if g.get("needs_review")]
    split_groups  = [g for g in groups if g.get("auto_split")]
    confidences   = [float(d["confianza"]) for d in pair_decisions]
    group_sizes   = [len(g["images"]) for g in groups]
    detail_count  = sum(1 for d in pair_decisions if d.get("es_detalle_o_etiqueta"))

    level_counts: dict[str, int] = {}
    for d in pair_decisions:
        lvl = d.get("nivel_decision", "duda")
        level_counts[lvl] = level_counts.get(lvl, 0) + 1

    avg_conf       = sum(confidences) / len(confidences) if confidences else 0
    avg_group_size = sum(group_sizes) / len(group_sizes) if group_sizes else 0
    review_pct     = (len(review_groups) / len(groups) * 100) if groups else 0

    print(f"Fotos procesadas:           {len(images)}")
    print(f"Comparaciones de pares:     {len(pair_decisions)}")
    print(f"Pares con detalle/etiqueta: {detail_count}")
    print(f"Grupos creados:             {len(groups)}")
    print(f"Grupos aprobados:           {len(groups) - len(review_groups)}")
    print(f"Grupos a revisión:          {len(review_groups)} ({review_pct:.1f}%)")
    print(f"Grupos auto-subdivididos:   {len(split_groups)}")
    print(f"Confianza promedio pares:   {avg_conf:.1f}")
    print(f"Tamaño promedio grupo:      {avg_group_size:.1f}")

    print("\nNiveles de decisión en pares:")
    for lvl, count in sorted(level_counts.items(), key=lambda x: -x[1]):
        pct = count / len(pair_decisions) * 100 if pair_decisions else 0
        print(f"  {lvl:<20} {count:>4} ({pct:.0f}%)")

    print("\nDetalle por grupo:")
    for i, g in enumerate(groups, start=1):
        status    = "REVISAR" if g.get("needs_review") else "OK"
        split_tag = " [SPLIT]" if g.get("auto_split") else ""
        names     = [img["name"] for img in g["images"]]
        print(f"  {i}: {len(g['images'])} fotos | {status}{split_tag} | {names}")

    print(f"\n{'='*80}")
    print(f"  💰 COSTO TOTAL ESTIMADO: ~${_total_cost_usd:.4f} USD")
    print(f"  📞 LLAMADAS A LA API:     {_total_calls}")
    print(f"{'='*80}")


# =========================
# MAIN
# =========================

def main():
    print_section("AUTO GROUP TO DRIVE — v4 (progreso + costo en vivo)")
    print(f"Carpeta objetivo: {FOLDER_ID}")
    print("Para cambiar la carpeta, editá FOLDER_ID en la sección CONFIG.\n")

    service = get_drive_service()
    images  = list_loose_images_in_root(service)

    if not images:
        print("No hay imágenes sueltas para agrupar.")
        return

    print("Imágenes sueltas detectadas:")
    for image in images:
        number = extract_filename_number(image["name"])
        print(f"- {image['name']} | numero={number} | id={image['id']}")
    print()

    if len(images) == 1:
        print("Solo hay una imagen. Creá una carpeta manual.")
        return

    pair_decisions   = run_pairwise_comparisons(service, images)
    candidate_groups = build_candidate_groups_from_pairs(images, pair_decisions)
    validated_groups = validate_all_groups(service, candidate_groups)

    print_metrics(images, pair_decisions, validated_groups)
    create_folders_from_groups(service, validated_groups)

    clear_checkpoint()

    print_section("FINALIZADO")
    print(f"💰 Costo total estimado del batch: ~${_total_cost_usd:.4f} USD")
    print(f"📞 Total llamadas a OpenAI: {_total_calls}")
    print("\n→ Carpetas aprobadas listas en Drive.")
    print("→ Dudosas en REVISAR/ | Subdivididas con [SPLIT]")
    print("\nCuando las carpetas estén correctas, corré: python3 ingest.py")


if __name__ == "__main__":
    main()