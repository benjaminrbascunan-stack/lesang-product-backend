import re
import os
import subprocess
import threading
from pathlib import Path
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from pydantic import BaseModel
import httpx
import json

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Lé Sang Pipeline Control Panel")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Pipeline state ─────────────────────────────────────────────────────────────
LAST_LOG = ""
PROGRESS = 0
STATUS = "idle"
CURRENT_STEP = "Esperando acción"
IS_RUNNING = False


def set_progress(value: int, step: str | None = None):
    global PROGRESS, CURRENT_STEP
    PROGRESS = max(0, min(100, value))
    if step:
        CURRENT_STEP = step


def parse_progress_from_line(line: str, script_name: str):
    match = re.search(r"PROGRESS:\s*(\d+)\s*/\s*(\d+)", line)
    if match:
        current = int(match.group(1))
        total = int(match.group(2))
        if total > 0:
            set_progress(int((current / total) * 100))
        return

    if script_name == "push_to_shopify.py":
        total_match = re.search(r"Se encontraron\s+(\d+)\s+items", line)
        if total_match:
            parse_progress_from_line.total_items = int(total_match.group(1))
            parse_progress_from_line.current_item = 0
            set_progress(5, "Productos encontrados")
            return

        if "Procesando:" in line:
            total = getattr(parse_progress_from_line, "total_items", 0)
            current = getattr(parse_progress_from_line, "current_item", 0) + 1
            parse_progress_from_line.current_item = current
            if total > 0:
                percent = int((current / total) * 100)
                set_progress(percent, line.strip())
            return


def run_script_thread(script_name: str):
    global LAST_LOG, STATUS, IS_RUNNING

    script_path = BASE_DIR / script_name

    if not script_path.exists():
        LAST_LOG = f"ERROR: No existe {script_name}"
        STATUS = "error"
        IS_RUNNING = False
        return

    LAST_LOG = ""
    STATUS = "running"
    IS_RUNNING = True
    set_progress(0, f"Ejecutando {script_name}")

    process = subprocess.Popen(
        ["python", "-u", str(script_path)],
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    output_lines = []
    for line in process.stdout:
        output_lines.append(line)
        LAST_LOG = "".join(output_lines)
        parse_progress_from_line(line, script_name)

    process.wait()
    LAST_LOG = "".join(output_lines)

    if process.returncode == 0:
        STATUS = "done"
        set_progress(100, "Completado")
    else:
        STATUS = "error"

    IS_RUNNING = False


def start_script(script_name: str):
    global IS_RUNNING
    if IS_RUNNING:
        return False, "Ya hay un proceso corriendo."
    thread = threading.Thread(target=run_script_thread, args=(script_name,))
    thread.start()
    return True, f"Iniciado: {script_name}"


# ══════════════════════════════════════════════════════════════════════════════
# POS — Configuración dinámica
# ══════════════════════════════════════════════════════════════════════════════
SHOPIFY_DOMAIN    = os.environ.get("SHOPIFY_STORE_DOMAIN", "").strip().strip('"').strip("'").replace("https://","").replace("http://","").rstrip("/")
SHOPIFY_CLIENT_ID = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
SHOPIFY_CLIENT_SECRET = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
LOCATION_ID       = os.environ.get("SHOPIFY_LOCATION_NUMERIC_ID", "96183910707")
SHOPIFY_GQL       = f"https://{SHOPIFY_DOMAIN}/admin/api/2026-04/graphql.json"
SHOPIFY_TOKEN_URL = f"https://{SHOPIFY_DOMAIN}/admin/oauth/access_token"


async def get_shopify_token() -> str:
    """Genera un access token dinámico igual que push_to_shopify.py"""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(
            SHOPIFY_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "client_credentials",
                "client_id": SHOPIFY_CLIENT_ID,
                "client_secret": SHOPIFY_CLIENT_SECRET,
            },
        )
    if r.status_code >= 400:
        raise RuntimeError(f"Error obteniendo token Shopify: {r.status_code} {r.text[:500]}")
    token = r.json().get("access_token")
    if not token:
        raise RuntimeError(f"Shopify no devolvió access_token: {r.json()}")
    return token

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# ── Config persistence ───────────────────────────────────────────────────────
CONFIG_FILE = BASE_DIR / "pos_config.json"

DEFAULT_CONFIG = {
    "vendedores": ["Aaron", "Benja R", "Fabio", "Marenna", "Michelle"],
    "propietarios": ["Tienda", "Aaron", "Benja R", "Fabio", "Consignacion"],
    "marcas": [
        {"nombre": "Tienda",         "comision": 0,    "paga_iva": False},
        {"nombre": "Pop disaster",   "comision": 0.25, "paga_iva": False},
        {"nombre": "Pedritos",       "comision": 0.25, "paga_iva": False},
        {"nombre": "Season Archive", "comision": 0.25, "paga_iva": False},
        {"nombre": "Consignacion",   "comision": 0.25, "paga_iva": False},
    ],
    "vendedores_externos": ["Marenna", "Michelle"],
    "com_externo_pct": 0.05,
    "com_bancaria": {
        "Débito": 0.0125, "Crédito": 0.0295,
        "Transferencia": 0, "Efectivo": 0, "Internet(Shopify)": 0.02,
        "SumUp": 0.01535,
    },
    "iva_tipos": ["Débito", "Crédito", "SumUp"],
}

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
                # Merge with defaults to ensure all keys exist
                merged = {**DEFAULT_CONFIG, **saved}
                return merged
        except Exception:
            pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[Config] Error guardando: {e}")

POS_CONFIG = load_config()


# ── POS Models ────────────────────────────────────────────────────────────────
class VentaIn(BaseModel):
    timestamp:         Optional[str]   = None
    nombre_prenda:     str
    talla:             Optional[str]   = "—"
    propietario:       str
    vendedor:          str
    precio_bruto:      float
    tipo_pago:         str
    iva:               float
    pct_com_bancaria:  float
    com_bancaria:      float
    base_com_vendedor: float
    pct_com_vendedor:  float
    com_vendedor:      float
    neto_tienda:       float
    observaciones:     Optional[str]   = ""
    marca:             str
    order_name:        Optional[str]   = ""
    foto_link:         Optional[str]   = None
    shopify_id:        Optional[str]   = None
    shopify_variant:   Optional[str]   = None

class VentaUpdate(VentaIn):
    row_index: int
    mes:       str

class ConfigUpdate(BaseModel):
    vendedores:          Optional[List[str]] = None
    propietarios:        Optional[List[str]] = None
    marcas:              Optional[list]      = None
    vendedores_externos: Optional[List[str]] = None
    com_externo_pct:     Optional[float]     = None


# ── Shopify helper ────────────────────────────────────────────────────────────
async def gql(query: str, variables: dict = {}, idempotent: bool = False) -> dict:
    from uuid import uuid4
    token = await get_shopify_token()
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
    if idempotent:
        headers["Shopify-Idempotency-Key"] = str(uuid4())
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(SHOPIFY_GQL, headers=headers,
                         json={"query": query, "variables": variables})
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise HTTPException(500, detail=str(data["errors"]))
        return data["data"]


# ── Sheets helpers (lazy import) ──────────────────────────────────────────────
def sheets_append(venta: dict):
    try:
        from pos_sheets import append_venta
        return append_venta(venta)
    except Exception as e:
        print(f"[Sheets] append: {e}")
        return None

def sheets_get(mes: str):
    try:
        from pos_sheets import get_ventas_mes
        return get_ventas_mes(mes)
    except Exception as e:
        print(f"[Sheets] get: {e}")
        return []

def sheets_update(mes: str, row: int, venta: dict):
    try:
        from pos_sheets import update_venta
        return update_venta(mes, row, venta)
    except Exception as e:
        print(f"[Sheets] update: {e}")
        return False

def sheets_delete(mes: str, row: int):
    try:
        from pos_sheets import delete_venta
        return delete_venta(mes, row)
    except Exception as e:
        print(f"[Sheets] delete: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# POS ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/pos/config")
def get_pos_config():
    return POS_CONFIG

@app.patch("/pos/config")
def update_pos_config(body: ConfigUpdate):
    if body.vendedores          is not None: POS_CONFIG["vendedores"]          = body.vendedores
    if body.propietarios        is not None: POS_CONFIG["propietarios"]        = body.propietarios
    if body.marcas              is not None: POS_CONFIG["marcas"]              = body.marcas
    if body.vendedores_externos is not None: POS_CONFIG["vendedores_externos"] = body.vendedores_externos
    if body.com_externo_pct     is not None: POS_CONFIG["com_externo_pct"]     = body.com_externo_pct
    save_config(POS_CONFIG)  # persistir en disco
    return POS_CONFIG

@app.get("/pos/products")
async def pos_get_products():
    query = """query($cursor:String){
      products(first:250,after:$cursor,query:"status:active"){
        pageInfo{hasNextPage endCursor}
        edges{node{id title featuredImage{url}
          variants(first:1){edges{node{id price inventoryQuantity inventoryItem{id}}}}
        }}
      }
    }"""
    all_p, cursor = [], None
    while True:
        data = await gql(query, {"cursor": cursor})
        for e in data["products"]["edges"]:
            n = e["node"]
            v = n["variants"]["edges"][0]["node"] if n["variants"]["edges"] else None
            if not v: continue
            qty = v.get("inventoryQuantity", 0) or 0
            all_p.append({
                "id": n["id"], "title": n["title"],
                "image": n["featuredImage"]["url"] if n["featuredImage"] else None,
                "price": float(v["price"]), "stock": qty,
                "variant_id": v["id"], "inventory_item_id": v["inventoryItem"]["id"],
                "available": qty > 0,
            })
        pi = data["products"]["pageInfo"]
        if not pi["hasNextPage"]: break
        cursor = pi["endCursor"]
    all_p.sort(key=lambda p: (0 if p["available"] else 1, p["title"]))
    return {"products": all_p, "total": len(all_p)}

@app.post("/pos/venta")
async def pos_registrar_venta(venta: VentaIn):
    from datetime import datetime
    v = venta.dict()
    print(f"[POS] venta recibida: precio={v.get('precio_bruto')} vendedor={v.get('vendedor')} variant={v.get('shopify_variant','')[:30] if v.get('shopify_variant') else None}")
    if not v.get("timestamp"):
        v["timestamp"] = datetime.now().isoformat()

    order_name = None
    if v.get("shopify_variant"):
        try:
            loc_gid = f"gid://shopify/Location/{LOCATION_ID}"

            # 1. Obtener inventory_item_id numérico via GraphQL
            q_item = """
query GetVariant($vid: ID!) {
  productVariant(id: $vid) {
    inventoryItem { id }
  }
}"""
            vd = await gql(q_item, {"vid": v["shopify_variant"]})
            inv_gid = vd["productVariant"]["inventoryItem"]["id"]
            # GID format: gid://shopify/InventoryItem/12345 -> extraer número
            inv_item_id = inv_gid.split("/")[-1]

            # 2. Usar REST API para ajustar inventario — no requiere @idempotent
            token = await get_shopify_token()
            rest_url = f"https://{SHOPIFY_DOMAIN}/admin/api/2026-04/inventory_levels/adjust.json"
            async with httpx.AsyncClient(timeout=30) as c:
                rest_r = await c.post(
                    rest_url,
                    headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                    json={
                        "location_id": int(LOCATION_ID),
                        "inventory_item_id": int(inv_item_id),
                        "available_adjustment": -1,
                    }
                )
            if rest_r.status_code >= 400:
                print(f"[Shopify] REST inventory error {rest_r.status_code}: {rest_r.text[:300]}")
            else:
                data = rest_r.json()
                after = data.get("inventory_level", {}).get("available", "?")
                print(f"[Shopify] stock OK -> disponible ahora: {after}")

            # 3. Draft order para historial
            m_draft = """
            mutation CreateDraft($input: DraftOrderInput!) {
              draftOrderCreate(input: $input) {
                draftOrder { name }
                userErrors { field message }
              }
            }"""
            dr = await gql(m_draft, {"input": {
                "lineItems": [{"variantId": v["shopify_variant"], "quantity": 1}],
                "note": f"POS — {v.get('vendedor','')} — {v.get('tipo_pago','')}",
                "tags": ["POS", v.get("tipo_pago", "")],
            }})
            if not dr["draftOrderCreate"]["userErrors"]:
                order_name = dr["draftOrderCreate"]["draftOrder"]["name"]
        except Exception as e:
            print(f"[Shopify] {e}")

    v["order_name"] = order_name
    result = sheets_append(v)

    # Auto-marcar consignación como vendida si hay match por GID
    if v.get("shopify_id"):
        try:
            from pos_sheets import get_consignaciones, marcar_consignacion_vendida
            consigs = get_consignaciones()
            for c in consigs:
                if c["estado"] == "Activa" and c.get("shopify_gid") == v["shopify_id"]:
                    marcar_consignacion_vendida(c["row_index"], order_name or "")
                    print(f"[Consig] Auto-marcada vendida: {c['nombre_prenda']}")
                    break
        except Exception as e:
            print(f"[Consig] Auto-mark error: {e}")

    return {
        "success": True,
        "order_name": order_name,
        "sheet_url": result["sheet_url"] if result else None,
        "mes": result["mes"] if result else MESES[datetime.now().month - 1],
        "neto_tienda": v["neto_tienda"],
    }


@app.get("/pos/historial/{mes}")
def pos_get_historial(mes: str):
    if mes not in MESES:
        raise HTTPException(400, "Mes inválido")
    ventas = sheets_get(mes)
    by_vend = {}
    for v in ventas:
        vend = v["vendedor"]
        if vend not in by_vend:
            by_vend[vend] = {"ventas": 0, "bruto": 0, "neto": 0, "comision": 0}
        by_vend[vend]["ventas"]   += 1
        by_vend[vend]["bruto"]    += v["precio_bruto"]
        by_vend[vend]["neto"]     += v["neto_tienda"]
        by_vend[vend]["comision"] += v["com_vendedor"]
    return {
        "mes": mes, "ventas": ventas,
        "resumen": {
            "total_ventas":       len(ventas),
            "total_bruto":        round(sum(v["precio_bruto"] for v in ventas), 2),
            "total_neto":         round(sum(v["neto_tienda"]  for v in ventas), 2),
            "total_iva":          round(sum(v["iva"]          for v in ventas), 2),
            "total_com_vendedor": round(sum(v["com_vendedor"] for v in ventas), 2),
        },
        "by_vendedor": by_vend,
    }

@app.put("/pos/venta")
def pos_editar_venta(body: VentaUpdate):
    if not sheets_update(body.mes, body.row_index, body.dict()):
        raise HTTPException(500, "No se pudo actualizar")
    return {"success": True}

@app.delete("/pos/venta/{mes}/{row_index}")
def pos_eliminar_venta(mes: str, row_index: int):
    if mes not in MESES:
        raise HTTPException(400, "Mes inválido")
    if not sheets_delete(mes, row_index):
        raise HTTPException(500, "No se pudo eliminar")
    return {"success": True}

@app.get("/pos/sheet-url")
def pos_sheet_url():
    try:
        from pos_sheets import get_or_create_sheet, _SHEET_ID
        sid = get_or_create_sheet()
        return {"url": f"https://docs.google.com/spreadsheets/d/{sid}", "error": None}
    except Exception as e:
        err = str(e)
        print(f"[Sheets] sheet-url error: {err}")
        return {"url": None, "error": err}

# ── Subir foto de venta ──────────────────────────────────────────────────────
from fastapi import UploadFile, File, Form as FastForm
import base64, io

def compress_image(image_bytes: bytes, max_size: int = 1200, quality: int = 75) -> bytes:
    """Comprime imagen, corrige orientación EXIF y redimensiona."""
    try:
        from PIL import Image as PILImage, ImageOps
        img = PILImage.open(io.BytesIO(image_bytes))

        # Corregir orientación EXIF — método más confiable
        img = ImageOps.exif_transpose(img)

        # Convertir a RGB
        if img.mode != 'RGB':
            img = img.convert('RGB')

        # Redimensionar si es muy grande
        w, h = img.size
        if max(w, h) > max_size:
            ratio = max_size / max(w, h)
            img = img.resize((int(w*ratio), int(h*ratio)), PILImage.LANCZOS)

        # Guardar sin metadatos EXIF
        out = io.BytesIO()
        img.save(out, format='JPEG', quality=quality, optimize=True)
        compressed = out.getvalue()
        print(f"[Foto] OK: {len(image_bytes)//1024}KB → {len(compressed)//1024}KB")
        return compressed
    except Exception as e:
        print(f"[Foto] Error: {e}, usando original")
        return image_bytes

@app.post("/pos/foto")
async def subir_foto(
    foto: UploadFile = File(...),
    nombre_prenda: str = FastForm(""),
    precio: str = FastForm(""),
    vendedor: str = FastForm(""),
    quality: str = FastForm("75"),
):
    from datetime import datetime as dt
    try:
        from pos_sheets import get_creds
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaIoBaseUpload

        creds  = get_creds()
        drive  = build("drive", "v3", credentials=creds)

        # Crear carpeta "Fotos POS" si no existe
        q = "name='Fotos POS — Lé Sang' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        res = drive.files().list(q=q, fields="files(id)").execute()
        folders = res.get("files", [])
        if folders:
            folder_id = folders[0]["id"]
        else:
            folder = drive.files().create(
                body={"name": "Fotos POS — Lé Sang", "mimeType": "application/vnd.google-apps.folder"},
                fields="id"
            ).execute()
            folder_id = folder["id"]

        # Nombre del archivo: fecha_hora_prenda_precio
        ts = dt.now().strftime("%Y%m%d_%H%M%S")
        prenda_safe = nombre_prenda.replace(" ", "_")[:30] if nombre_prenda else "sin_nombre"
        filename = f"{ts}_{prenda_safe}_{precio}CLP.jpg"

        # Subir foto a Drive (comprimida)
        content_bytes = await foto.read()
        content_bytes = compress_image(content_bytes)
        media = MediaIoBaseUpload(
            io.BytesIO(content_bytes),
            mimetype="image/jpeg",
            resumable=False
        )
        file_meta = {"name": filename, "parents": [folder_id]}
        uploaded = drive.files().create(
            body=file_meta, media_body=media, fields="id,webViewLink"
        ).execute()

        # Hacer pública la foto
        drive.permissions().create(
            fileId=uploaded["id"],
            body={"type": "anyone", "role": "reader"}
        ).execute()

        link = uploaded.get("webViewLink", "")
        print(f"[Foto] Subida: {filename} -> {link}")
        return {"success": True, "filename": filename, "link": link, "file_id": uploaded["id"]}

    except Exception as e:
        print(f"[Foto] Error: {e}")
        return {"success": False, "error": str(e)}


# ── Consignaciones ───────────────────────────────────────────────────────────
class ConsignacionIn(BaseModel):
    nombre_prenda:  str
    talla:          Optional[str] = ""
    dueno:          str
    instagram:      Optional[str] = ""
    email:          Optional[str] = ""
    telefono:       Optional[str] = ""
    precio_venta:   float
    valor_acordado: float
    marca:          Optional[str] = "Consignacion"
    notas:          Optional[str] = ""
    foto_link:      Optional[str] = ""

@app.post("/pos/consignacion")
async def crear_consignacion(
    nombre_prenda:  str   = FastForm(...),
    dueno:          str   = FastForm(...),
    precio_venta:   str   = FastForm(...),
    valor_acordado: str   = FastForm(...),
    talla:          str   = FastForm(""),
    instagram:      str   = FastForm(""),
    email:          str   = FastForm(""),
    telefono:       str   = FastForm(""),
    marca:          str   = FastForm("Consignacion"),
    notas:          str   = FastForm(""),
    foto: UploadFile = File(None),
):
    from datetime import datetime
    import io

    consig = {
        "nombre_prenda": nombre_prenda,
        "dueno": dueno,
        "precio_venta": float(precio_venta),
        "valor_acordado": float(valor_acordado),
        "talla": talla,
        "instagram": instagram,
        "email": email,
        "telefono": telefono,
        "marca": marca,
        "notas": notas,
        "foto_link": "",
    }

    foto_link = ""

    # 1. Subir foto a Drive (obligatoria)
    if foto:
        try:
            from pos_sheets import get_creds
            from googleapiclient.discovery import build as gdrive_build
            from googleapiclient.http import MediaIoBaseUpload

            creds  = get_creds()
            drive  = gdrive_build("drive","v3",credentials=creds)

            # Carpeta consignaciones
            q="name='Consignaciones — Lé Sang' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            res=drive.files().list(q=q,fields="files(id)").execute()
            folders=res.get("files",[])
            if folders:
                folder_id=folders[0]["id"]
            else:
                folder=drive.files().create(
                    body={"name":"Consignaciones — Lé Sang","mimeType":"application/vnd.google-apps.folder"},
                    fields="id"
                ).execute()
                folder_id=folder["id"]

            ts=datetime.now().strftime("%Y%m%d_%H%M%S")
            nombre_safe=consig["nombre_prenda"].replace(" ","_")[:30]
            filename=f"CONSIG_{ts}_{nombre_safe}.jpg"

            content_bytes=await foto.read()
            content_bytes=compress_image(content_bytes)
            media=MediaIoBaseUpload(io.BytesIO(content_bytes),
                                    mimetype="image/jpeg",
                                    resumable=False)
            uploaded=drive.files().create(
                body={"name":filename,"parents":[folder_id]},
                media_body=media,fields="id,webViewLink"
            ).execute()
            drive.permissions().create(
                fileId=uploaded["id"],body={"type":"anyone","role":"reader"}
            ).execute()
            foto_link=uploaded.get("webViewLink","")
            consig["foto_link"]=foto_link
            print(f"[Consig] Foto subida: {filename}")
        except Exception as e:
            print(f"[Consig] Error foto: {e}")

    shopify_gid = ""
    # 2. Registrar en Sheets
    try:
        from pos_sheets import append_consignacion
        result = append_consignacion(consig)
    except Exception as e:
        print(f"[Consig] Sheets error: {e}")
        result = {}

    return {
        "success": True,
        "foto_link": foto_link,
        "shopify_gid": shopify_gid,
        "row": result.get("row"),
    }


@app.get("/pos/consignaciones")
def get_consignaciones():
    try:
        from pos_sheets import get_consignaciones
        consigs = get_consignaciones()
        activas  = [c for c in consigs if c["estado"] == "Activa"]
        vendidas = [c for c in consigs if c["estado"] == "Vendida"]
        pagadas  = [c for c in consigs if c["estado"] == "Pagada"]
        return {"activas": activas, "vendidas": vendidas, "pagadas": pagadas, "total": len(consigs)}
    except Exception as e:
        return {"activas":[],"vendidas":[],"total":0,"error":str(e)}


@app.patch("/pos/consignacion/{row_index}/vendida")
async def marcar_vendida(row_index: int, order_name: str = ""):
    try:
        from pos_sheets import marcar_consignacion_vendida
        marcar_consignacion_vendida(row_index, order_name)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))




@app.get("/pos/shopify-product-title")
async def get_shopify_product_title(gid: str):
    """Obtiene el titulo de un producto de Shopify por GID."""
    try:
        query = """
        query GetProduct($id: ID!) {
          product(id: $id) { id title }
        }"""
        data = await gql(query, {"id": gid})
        title = data.get("product", {}).get("title", "")
        return {"title": title}
    except Exception as e:
        return {"title": "", "error": str(e)}

@app.get("/pos/consignacion/buscar-match")
async def buscar_match_shopify(q: str):
    """Busca productos en Shopify por nombre para linkear con consignación."""
    gql_query = """
    query SearchProducts($q: String!) {
      products(first: 8, query: $q) {
        edges { node {
          id title status
          featuredImage { url }
          variants(first:1) { edges { node { price inventoryQuantity } } }
        }}
      }
    }"""
    try:
        data = await gql(gql_query, {"q": q})
        products = []
        for e in data["products"]["edges"]:
            n = e["node"]
            v = n["variants"]["edges"][0]["node"] if n["variants"]["edges"] else {}
            qty = v.get("inventoryQuantity", 0) or 0
            products.append({
                "id":      n["id"],
                "title":   n["title"],
                "status":  n.get("status","ACTIVE"),
                "image":   n["featuredImage"]["url"] if n.get("featuredImage") else None,
                "price":   float(v.get("price",0)),
                "stock":   qty,
                "available": qty > 0,
            })
        return {"products": products}
    except Exception as e:
        return {"products": [], "error": str(e)}


@app.patch("/pos/consignacion/{row_index}/linkear")
async def linkear_consignacion(row_index: int, shopify_gid: str):
    """Linkea una consignación con un producto de Shopify."""
    try:
        from pos_sheets import get_creds
        from googleapiclient.discovery import build as sheets_build
        from pos_sheets import get_or_create_sheet
        creds  = get_creds()
        sheets = sheets_build("sheets","v4",credentials=creds)
        sid    = get_or_create_sheet()
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"📦 Consignaciones!K{row_index}",
            valueInputOption="RAW",
            body={"values":[[shopify_gid]]}
        ).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


# ── Stock Marcas ──────────────────────────────────────────────────────────────


@app.patch("/pos/consignacion/{row_index}")
async def actualizar_consignacion(
    row_index: int,
    nombre_prenda:  str = FastForm(...),
    dueno:          str = FastForm(...),
    precio_venta:   str = FastForm(...),
    valor_acordado: str = FastForm(...),
    talla:          str = FastForm(""),
    instagram:      str = FastForm(""),
    email:          str = FastForm(""),
    telefono:       str = FastForm(""),
    notas:          str = FastForm(""),
):
    try:
        from pos_sheets import get_creds, get_or_create_sheet
        from googleapiclient.discovery import build as sbuild
        creds  = get_creds()
        sheets = sbuild("sheets","v4",credentials=creds)
        sid    = get_or_create_sheet()
        # Read existing foto and shopify_gid to preserve them
        existing = sheets.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"📦 Consignaciones!A{row_index}:N{row_index}",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        row_data = existing.get("values",[[]])[0] if existing.get("values") else []
        while len(row_data) < 15: row_data.append("")
        estado      = row_data[0]  # preserve
        foto_link   = row_data[9]  # preserve
        shopify_gid = row_data[10] # preserve
        fecha_ing   = row_data[11] # preserve
        fecha_venta = row_data[12] # preserve
        order_name  = row_data[13] # preserve

        new_row = [
            estado,
            nombre_prenda,
            talla,
            dueno,
            instagram,
            email,
            telefono,
            float(precio_venta),
            float(valor_acordado),
            foto_link,
            shopify_gid,
            fecha_ing,
            fecha_venta,
            order_name,
            notas,
        ]
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"📦 Consignaciones!A{row_index}:O{row_index}",
            valueInputOption="RAW",
            body={"values":[new_row]},
        ).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.patch("/pos/consignacion/{row_index}/pagada")
async def marcar_consig_pagada(row_index: int):
    try:
        from pos_sheets import marcar_consignacion_pagada
        marcar_consignacion_pagada(row_index)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/pos/consignacion/{row_index}")
async def eliminar_consignacion(row_index: int):
    try:
        from pos_sheets import delete_consignacion
        delete_consignacion(row_index)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/pos/stock-marcas")
def get_stock_marcas():
    try:
        from pos_sheets import get_stock_marcas
        items = get_stock_marcas()
        return {"items": items, "total": len(items)}
    except Exception as e:
        return {"items": [], "total": 0, "error": str(e)}


@app.post("/pos/stock-marcas")
async def crear_stock_marca(
    nombre_prenda: str = FastForm(...),
    marca:         str = FastForm(...),
    precio_venta:  str = FastForm(...),
    notas:         str = FastForm(""),
    tallas_json:   str = FastForm("{}"),
    foto: UploadFile = File(None),
    quality: str = FastForm("75"),
):
    from datetime import datetime
    import json as _json
    item = {
        "nombre_prenda": nombre_prenda,
        "marca": marca,
        "precio_venta": float(precio_venta),
        "notas": notas,
        "tallas": _json.loads(tallas_json),
        "foto_link": "",
    }

    # Subir foto a Drive
    if foto:
        try:
            from pos_sheets import get_creds
            from googleapiclient.discovery import build as gbuild
            from googleapiclient.http import MediaIoBaseUpload
            import io as _io
            creds = get_creds()
            drive = gbuild("drive","v3",credentials=creds)
            q = "name='Stock Marcas — Lé Sang' and mimeType='application/vnd.google-apps.folder' and trashed=false"
            res = drive.files().list(q=q, fields="files(id)").execute()
            folders = res.get("files",[])
            folder_id = folders[0]["id"] if folders else drive.files().create(
                body={"name":"Stock Marcas — Lé Sang","mimeType":"application/vnd.google-apps.folder"},
                fields="id"
            ).execute()["id"]
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            fname = f"STOCK_{ts}_{nombre_prenda.replace(' ','_')[:25]}.jpg"
            content_bytes = await foto.read()
            content_bytes = compress_image(content_bytes)
            uploaded = drive.files().create(
                body={"name":fname,"parents":[folder_id]},
                media_body=MediaIoBaseUpload(_io.BytesIO(content_bytes),
                    mimetype=foto.content_type or "image/jpeg", resumable=False),
                fields="id,webViewLink"
            ).execute()
            drive.permissions().create(
                fileId=uploaded["id"], body={"type":"anyone","role":"reader"}
            ).execute()
            item["foto_link"] = uploaded.get("webViewLink","")
        except Exception as e:
            print(f"[Stock] foto error: {e}")

    try:
        from pos_sheets import append_stock_marca
        result = append_stock_marca(item)
        return {"success": True, "row": result.get("row"), "foto_link": item["foto_link"]}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.patch("/pos/stock-marcas/{row_index}")
async def actualizar_stock_marca(row_index: int, body: dict):
    try:
        from pos_sheets import get_creds, get_or_create_sheet
        from googleapiclient.discovery import build as sbuild
        creds  = get_creds()
        sheets = sbuild("sheets","v4",credentials=creds)
        sid    = get_or_create_sheet()

        # Leer foto existente para no perderla
        existing = sheets.spreadsheets().values().get(
            spreadsheetId=sid,
            range=f"📦 Stock Marcas!D{row_index}",
            valueRenderOption="UNFORMATTED_VALUE",
        ).execute()
        foto_link = ""
        try:
            foto_link = existing["values"][0][0]
        except Exception:
            pass

        tallas = body.get("tallas",{})
        TALLAS = ["XXS","XS","S","M","L","XL","XXL"]
        total  = sum(int(tallas.get(t,0) or 0) for t in TALLAS)
        row = [
            body.get("nombre_prenda",""),
            body.get("marca",""),
            float(body.get("precio_venta",0)),
            foto_link,  # preservar foto existente
            int(tallas.get("XXS",0) or 0),
            int(tallas.get("XS",0) or 0),
            int(tallas.get("S",0) or 0),
            int(tallas.get("M",0) or 0),
            int(tallas.get("L",0) or 0),
            int(tallas.get("XL",0) or 0),
            int(tallas.get("XXL",0) or 0),
            total,
        ]
        # Update cols A-L (keep M-N: fecha y notas)
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range=f"📦 Stock Marcas!A{row_index}:L{row_index}",
            valueInputOption="RAW",
            body={"values":[row]},
        ).execute()
        if "notas" in body:
            sheets.spreadsheets().values().update(
                spreadsheetId=sid,
                range=f"📦 Stock Marcas!N{row_index}",
                valueInputOption="RAW",
                body={"values":[[body["notas"]]]},
            ).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/pos/stock-marcas/{row_index}")
async def eliminar_stock_marca(row_index: int):
    try:
        from pos_sheets import get_creds, get_or_create_sheet
        from googleapiclient.discovery import build as sbuild
        creds  = get_creds()
        sheets = sbuild("sheets","v4",credentials=creds)
        sid    = get_or_create_sheet()
        meta   = sheets.spreadsheets().get(spreadsheetId=sid).execute()
        gid    = next(s["properties"]["sheetId"] for s in meta["sheets"]
                      if s["properties"]["title"] == "📦 Stock Marcas")
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid,
            body={"requests":[{"deleteDimension":{
                "range":{"sheetId":gid,"dimension":"ROWS",
                         "startIndex":row_index-1,"endIndex":row_index}
            }}]}
        ).execute()
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/pos/stock-marcas/{row_index}/vender")
async def vender_stock_marca(row_index: int, venta: VentaIn):
    """Vende un item del stock interno: descuenta talla y registra venta."""
    from datetime import datetime
    try:
        from pos_sheets import descontar_talla
        talla = venta.talla or ""
        descontar_talla(row_index, talla)
    except Exception as e:
        print(f"[Stock] descontar error: {e}")

    v = venta.dict()
    if not v.get("timestamp"):
        v["timestamp"] = datetime.now().isoformat()
    v["order_name"] = None
    result = sheets_append(v)
    return {
        "success": True,
        "sheet_url": result["sheet_url"] if result else None,
        "mes": result["mes"] if result else "",
        "neto_tienda": v["neto_tienda"],
    }


# ── Gastos ────────────────────────────────────────────────────────────────────
@app.get("/pos/gastos/categorias")
def get_categorias_gastos():
    from pos_sheets import CATEGORIAS_FIJAS
    return {"categorias": CATEGORIAS_FIJAS}

@app.post("/pos/gastos")
async def crear_gasto(
    mes:         str = FastForm(...),
    categoria:   str = FastForm(...),
    descripcion: str = FastForm(""),
    monto:       str = FastForm(...),
    notas:       str = FastForm(""),
):
    try:
        from pos_sheets import append_gasto
        result = append_gasto({
            "mes": mes, "categoria": categoria,
            "descripcion": descripcion,
            "monto": float(monto), "notas": notas,
        })
        return {"success": True, "row": result.get("row")}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.get("/pos/gastos/{mes}")
def listar_gastos(mes: str):
    try:
        from pos_sheets import get_gastos
        gastos = get_gastos(mes)
        total = sum(g["monto"] for g in gastos)
        by_cat = {}
        for g in gastos:
            cat = g["categoria"]
            by_cat[cat] = by_cat.get(cat, 0) + g["monto"]
        return {"gastos": gastos, "total": total, "by_categoria": by_cat}
    except Exception as e:
        return {"gastos": [], "total": 0, "by_categoria": {}, "error": str(e)}

@app.delete("/pos/gastos/{row_index}")
def eliminar_gasto(row_index: int):
    try:
        from pos_sheets import delete_gasto
        delete_gasto(row_index)
        return {"success": True}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/pos/stock-marcas/guardar-original")
async def guardar_stock_original():
    """Copia el stock actual de cada talla a columnas O-U como stock original."""
    try:
        from pos_sheets import get_creds, get_or_create_sheet, get_stock_marcas
        from googleapiclient.discovery import build as sbuild
        
        items = get_stock_marcas()
        if not items:
            return {"success": True, "count": 0, "message": "Sin items"}
        
        creds  = get_creds()
        sheets = sbuild("sheets", "v4", credentials=creds)
        sid    = get_or_create_sheet()
        
        # Agregar header O1:U1
        sheets.spreadsheets().values().update(
            spreadsheetId=sid,
            range="📦 Stock Marcas!O1:U1",
            valueInputOption="RAW",
            body={"values": [["Orig_XXS","Orig_XS","Orig_S","Orig_M","Orig_L","Orig_XL","Orig_XXL"]]}
        ).execute()
        
        TALLAS = ["XXS","XS","S","M","L","XL","XXL"]
        orig_rows = []
        for item in items:
            row = [int(item["tallas"].get(t, 0) or 0) for t in TALLAS]
            orig_rows.append({"row": item["row_index"], "vals": row})
        
        # Escribir cada fila individualmente para respetar row_index
        data = []
        for item in orig_rows:
            data.append({
                "range": f"📦 Stock Marcas!O{item['row']}:U{item['row']}",
                "values": [item["vals"]]
            })
        
        sheets.spreadsheets().values().batchUpdate(
            spreadsheetId=sid,
            body={"valueInputOption": "RAW", "data": data}
        ).execute()
        
        return {"success": True, "count": len(orig_rows)}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.post("/pos/stock-marcas/actualizar-originales")
async def actualizar_stock_originales(request: Request):
    data = await request.json()
    """Actualiza columnas O-U con stock original corregido."""
    try:
        from pos_sheets import get_creds, get_or_create_sheet
        from googleapiclient.discovery import build as sbuild
        creds  = get_creds()
        sheets = sbuild("sheets", "v4", credentials=creds)
        sid    = get_or_create_sheet()
        batch  = []
        for item in data:
            row = item["row"]
            vals = item["orig"]
            batch.append({
                "range": f"📦 Stock Marcas!O{row}:U{row}",
                "values": [vals]
            })
        if batch:
            sheets.spreadsheets().values().batchUpdate(
                spreadsheetId=sid,
                body={"valueInputOption":"RAW","data":batch}
            ).execute()
        return {"success": True, "count": len(batch)}
    except Exception as e:
        raise HTTPException(500, str(e))

# ── Servir el POS frontend ────────────────────────────────────────────────────
app.mount("/pos/static", StaticFiles(directory=str(BASE_DIR / "pos_static")), name="pos_static")

@app.get("/pos")
@app.get("/pos/")
async def serve_pos():
    return FileResponse(str(BASE_DIR / "pos_static" / "index.html"))

@app.get("/pos/sw.js")
async def serve_sw():
    """Service Worker debe servirse desde /pos/ scope"""
    return FileResponse(
        str(BASE_DIR / "pos_static" / "sw.js"),
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/pos/"}
    )


# ══════════════════════════════════════════════════════════════════════════════
# PANEL HTML (sin cambios)
# ══════════════════════════════════════════════════════════════════════════════
HTML = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Lé Sang — Panel</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

  :root {
    --black: #0a0a0a;
    --white: #f5f4f0;
    --accent: #e05a00;
    --gray: #888;
    --border: #d0cfc9;
  }

  html, body {
    height: 100%;
    background: var(--white);
    color: var(--black);
    font-family: 'Space Mono', monospace;
    font-size: 11px;
    letter-spacing: 0.08em;
    overflow: hidden;
  }

  header {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    align-items: end;
    padding: 28px 40px 18px;
    border-bottom: 1px solid var(--black);
  }

  .logo {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 26px;
    letter-spacing: 0.25em;
    line-height: 1;
  }

  .header-center {
    text-align: center;
    font-size: 9px;
    letter-spacing: 0.3em;
    text-transform: uppercase;
    color: var(--gray);
  }

  .header-right {
    text-align: right;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    font-family: 'Space Mono', monospace;
  }

  .layout {
    display: grid;
    grid-template-columns: 1fr 1fr;
    height: calc(100vh - 109px);
  }

  .panel-left {
    border-right: 1px solid var(--black);
    display: flex;
    flex-direction: column;
  }

  .panel-title {
    font-size: 9px;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    padding: 16px 40px 14px;
    border-bottom: 1px solid var(--border);
    color: var(--gray);
  }

  .actions {
    flex: 1;
    display: flex;
    flex-direction: column;
    justify-content: stretch;
  }

  .action-btn {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 40px;
    border: none;
    border-bottom: 1px solid var(--border);
    background: transparent;
    cursor: pointer;
    text-align: left;
    transition: background 0.15s;
    font-family: inherit;
    flex: 1;
  }

  .action-btn:last-child { border-bottom: none; }
  .action-btn:hover:not(:disabled) { background: var(--black); }
  .action-btn:hover:not(:disabled) .btn-label { color: var(--white); }
  .action-btn:hover:not(:disabled) .btn-sub { color: #666; }
  .action-btn:hover:not(:disabled) .btn-arrow { color: var(--white); }
  .action-btn:hover:not(:disabled) .btn-num { color: #555; }

  .action-btn.active { background: var(--black); }
  .action-btn.active .btn-label { color: var(--white); }
  .action-btn.active .btn-sub { color: var(--accent); }
  .action-btn.active .btn-arrow { color: var(--accent); }
  .action-btn.active .btn-num { color: #555; }

  .action-btn:disabled { opacity: 0.35; cursor: not-allowed; }

  .btn-left { display: flex; flex-direction: column; gap: 5px; }

  .btn-num {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 10px;
    color: var(--gray);
    letter-spacing: 0.25em;
    transition: color 0.15s;
  }

  .btn-label {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 30px;
    letter-spacing: 0.15em;
    line-height: 1;
    color: var(--black);
    transition: color 0.15s;
  }

  .btn-sub {
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    text-transform: uppercase;
    transition: color 0.15s;
  }

  .btn-arrow {
    font-size: 18px;
    color: var(--border);
    transition: color 0.15s;
    font-family: 'Bebas Neue', sans-serif;
  }

  .panel-right {
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .status-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 16px 40px 14px;
    border-bottom: 1px solid var(--border);
  }

  .status-title {
    font-size: 9px;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--gray);
  }

  .status-badge {
    font-size: 9px;
    letter-spacing: 0.2em;
    text-transform: uppercase;
    padding: 3px 8px;
  }

  .status-badge.idle    { background: var(--border); color: var(--gray); }
  .status-badge.running { background: var(--black);  color: var(--white); }
  .status-badge.done    { background: var(--accent);  color: var(--white); }
  .status-badge.error   { background: #c0392b;        color: var(--white); }

  .progress-section {
    padding: 24px 40px 20px;
    border-bottom: 1px solid var(--border);
  }

  .progress-top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 10px;
  }

  .progress-label {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 12px;
    letter-spacing: 0.3em;
  }

  .progress-pct {
    font-family: 'Bebas Neue', sans-serif;
    font-size: 52px;
    line-height: 1;
    letter-spacing: 0.03em;
    transition: all 0.3s;
  }

  .progress-bar-track {
    width: 100%;
    height: 1px;
    background: var(--border);
    margin-top: 14px;
    position: relative;
  }

  .progress-bar-fill {
    height: 100%;
    background: var(--black);
    transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);
    width: 0%;
  }

  .progress-bar-fill.done { background: var(--accent); }

  .progress-step {
    margin-top: 8px;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    text-transform: uppercase;
    min-height: 14px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .log-section {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .log-header {
    padding: 14px 40px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 9px;
    letter-spacing: 0.35em;
    text-transform: uppercase;
    color: var(--gray);
    display: flex;
    justify-content: space-between;
    align-items: center;
  }

  .log-clear {
    background: none;
    border: none;
    font-family: inherit;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    cursor: pointer;
    text-transform: uppercase;
  }
  .log-clear:hover { color: var(--black); }

  .log-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 40px 80px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .log-body::-webkit-scrollbar { width: 2px; }
  .log-body::-webkit-scrollbar-thumb { background: var(--border); }

  .log-entry {
    display: grid;
    grid-template-columns: 64px 1fr;
    gap: 14px;
    align-items: baseline;
    animation: fadeIn 0.15s ease;
  }

  @keyframes fadeIn {
    from { opacity: 0; transform: translateY(3px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .log-time {
    font-size: 9px;
    color: var(--border);
    letter-spacing: 0.1em;
    white-space: nowrap;
  }

  .log-msg {
    font-size: 10px;
    letter-spacing: 0.06em;
    line-height: 1.5;
    word-break: break-word;
  }

  .log-msg.ok     { color: var(--black); }
  .log-msg.info   { color: var(--gray); }
  .log-msg.accent { color: var(--accent); }
  .log-msg.error  { color: #c0392b; }
  .log-msg.sep    { color: var(--border); }

  footer {
    border-top: 1px solid var(--black);
    padding: 10px 40px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 9px;
    letter-spacing: 0.2em;
    color: var(--gray);
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: var(--white);
  }

  .pos-link {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border: 1px solid var(--black);
    color: var(--black);
    font-family: 'Bebas Neue', sans-serif;
    font-size: 13px;
    letter-spacing: 0.2em;
    text-decoration: none;
    transition: background 0.15s, color 0.15s;
  }
  .pos-link:hover { background: var(--black); color: var(--white); }

  .spinner {
    display: none;
    width: 8px;
    height: 8px;
    border: 1px solid var(--border);
    border-top-color: var(--black);
    border-radius: 50%;
    animation: spin 0.7s linear infinite;
    margin-left: 6px;
    vertical-align: middle;
  }
  .spinner.visible { display: inline-block; }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>

<header>
  <div class="logo">LÉ SANG</div>
  <div class="header-center">PANEL DE OPERACIONES</div>
  <div class="header-right" id="clock">—</div>
</header>

<div class="layout">

  <div class="panel-left">
    <div class="panel-title">ACCIONES</div>
    <div class="actions">

      <button class="action-btn" id="btn-group" onclick="run('/run-group', 'btn-group', 'CREAR CARPETAS')">
        <div class="btn-left">
          <span class="btn-num">01</span>
          <span class="btn-label">CREAR CARPETAS</span>
          <span class="btn-sub">Agrupar imágenes en Drive</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

      <button class="action-btn" id="btn-ingest" onclick="run('/run-ingest', 'btn-ingest', 'SUBIR A LA NUBE')">
        <div class="btn-left">
          <span class="btn-num">02</span>
          <span class="btn-label">SUBIR A LA NUBE</span>
          <span class="btn-sub">Procesar e ingestar en Supabase</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

      <button class="action-btn" id="btn-shopify" onclick="run('/run-shopify', 'btn-shopify', 'PUBLICAR')">
        <div class="btn-left">
          <span class="btn-num">03</span>
          <span class="btn-label">PUBLICAR</span>
          <span class="btn-sub">Crear drafts en Shopify</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

      <button class="action-btn" id="btn-activate" onclick="run('/run-activate', 'btn-activate', 'ACTIVAR')">
        <div class="btn-left">
          <span class="btn-num">04</span>
          <span class="btn-label">ACTIVAR</span>
          <span class="btn-sub">Publicar drafts en Online Store + POS</span>
        </div>
        <span class="btn-arrow">→</span>
      </button>

    </div>
  </div>

  <div class="panel-right">

    <div class="status-header">
      <span class="status-title">ESTADO DEL SISTEMA</span>
      <span class="status-badge idle" id="status-badge">ESPERANDO</span>
    </div>

    <div class="progress-section">
      <div class="progress-top">
        <span class="progress-label">PROGRESO</span>
        <span class="progress-pct" id="pct-display">0%</span>
      </div>
      <div class="progress-bar-track">
        <div class="progress-bar-fill" id="progress-fill"></div>
      </div>
      <div class="progress-step" id="step-label">Esperando acción...</div>
    </div>

    <div class="log-section">
      <div class="log-header">
        <span>LOG <span class="spinner" id="spinner"></span></span>
        <button class="log-clear" onclick="clearLog()">LIMPIAR</button>
      </div>
      <div class="log-body" id="log-body">
        <div class="log-entry">
          <span class="log-time">—</span>
          <span class="log-msg info">Sistema listo. Seleccioná una acción.</span>
        </div>
      </div>
    </div>

  </div>
</div>

<footer>
  <span>LÉ SANG © 2025</span>
  <a href="/pos" class="pos-link" target="_blank">◻ ABRIR POS →</a>
  <span>ALFREDO BARROS ERRAZURIZ #1982</span>
</footer>

<script>
  function updateClock() {
    const now = new Date();
    const pad = n => String(n).padStart(2,'0');
    document.getElementById('clock').textContent = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
  }
  setInterval(updateClock, 1000);
  updateClock();

  let prevLog = '';

  function addLog(msg, type = 'ok') {
    if (!msg.trim()) return;
    const body = document.getElementById('log-body');
    const now = new Date();
    const pad = n => String(n).padStart(2,'0');
    const time = pad(now.getHours()) + ':' + pad(now.getMinutes()) + ':' + pad(now.getSeconds());
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = '<span class="log-time">' + time + '</span><span class="log-msg ' + type + '">' + msg + '</span>';
    body.appendChild(entry);
    body.scrollTop = body.scrollHeight;
  }

  function clearLog() {
    document.getElementById('log-body').innerHTML = '';
    prevLog = '';
  }

  function processNewLog(fullLog) {
    const newText = fullLog.slice(prevLog.length);
    prevLog = fullLog;
    const lines = newText.split('\\n').filter(l => l.trim());
    lines.forEach(line => {
      if (/^=+$/.test(line.trim())) return;
      const type = line.includes('✔') ? 'ok'
                 : line.includes('✘') ? 'error'
                 : line.startsWith('  ') ? 'info'
                 : 'ok';
      addLog(line.trim(), type);
    });
  }

  function setBadge(state) {
    const badge = document.getElementById('status-badge');
    badge.className = 'status-badge ' + state;
    const labels = { idle: 'ESPERANDO', running: 'EN CURSO', done: 'COMPLETADO', error: 'ERROR' };
    badge.textContent = labels[state] || state.toUpperCase();
  }

  function setProgress(pct, step) {
    document.getElementById('pct-display').textContent = Math.round(pct) + '%';
    const fill = document.getElementById('progress-fill');
    fill.style.width = pct + '%';
    if (pct >= 100) fill.classList.add('done');
    else fill.classList.remove('done');
    if (step) document.getElementById('step-label').textContent = step.toUpperCase();
  }

  function setAllDisabled(disabled) {
    ['btn-group','btn-ingest','btn-shopify','btn-activate'].forEach(id => {
      document.getElementById(id).disabled = disabled;
    });
  }

  function setActive(btnId) {
    ['btn-group','btn-ingest','btn-shopify','btn-activate'].forEach(id => {
      document.getElementById(id).classList.remove('active');
    });
    if (btnId) document.getElementById(btnId).classList.add('active');
  }

  let pollInterval = null;

  function startPolling() {
    pollInterval = setInterval(async () => {
      try {
        const res = await fetch('/status');
        const data = await res.json();
        setProgress(data.progress, data.step);
        setBadge(data.status);
        if (data.log) processNewLog(data.log);
        if (data.status === 'done' || data.status === 'error') {
          clearInterval(pollInterval);
          setAllDisabled(false);
          setActive(null);
          document.getElementById('spinner').classList.remove('visible');
          document.getElementById('footer-action').textContent = '—';
          if (data.status === 'done') addLog('Proceso completado.', 'accent');
          else addLog('Error en el proceso. Revisá el log.', 'error');
        }
      } catch (e) {}
    }, 500);
  }

  async function run(endpoint, btnId, label) {
    setAllDisabled(true);
    setActive(btnId);
    setBadge('running');
    setProgress(0, 'Iniciando...');
    prevLog = '';
    document.getElementById('spinner').classList.add('visible');
    addLog('→ Iniciando: ' + label, 'accent');

    try {
      const res = await fetch(endpoint, { method: 'POST' });
      const data = await res.json();
      if (!data.ok) {
        setBadge('error');
        addLog('✘ ' + data.message, 'error');
        setAllDisabled(false);
        setActive(null);
        document.getElementById('spinner').classList.remove('visible');
        return;
      }
      startPolling();
    } catch (e) {
      setBadge('error');
      addLog('✘ Error de conexión: ' + e.message, 'error');
      setAllDisabled(false);
      setActive(null);
      document.getElementById('spinner').classList.remove('visible');
    }
  }
</script>
</body>
</html>"""


# ══════════════════════════════════════════════════════════════════════════════
# PANEL ENDPOINTS (sin cambios)
# ══════════════════════════════════════════════════════════════════════════════

@app.get("/", response_class=HTMLResponse)
def home():
    return HTML

@app.get("/status")
def status():
    return JSONResponse({
        "progress": PROGRESS,
        "status": STATUS,
        "step": CURRENT_STEP,
        "log": LAST_LOG,
        "running": IS_RUNNING,
    })

@app.post("/run-group")
def run_group():
    ok, message = start_script("auto_group_to_drive.py")
    return JSONResponse({"ok": ok, "message": message})

@app.post("/run-ingest")
def run_ingest():
    ok, message = start_script("ingest.py")
    return JSONResponse({"ok": ok, "message": message})

@app.post("/run-shopify")
def run_shopify():
    ok, message = start_script("push_to_shopify.py")
    return JSONResponse({"ok": ok, "message": message})

@app.post("/run-activate")
def run_activate():
    ok, message = start_script("publish_channels.py")
    return JSONResponse({"ok": ok, "message": message})

@app.get("/publications")
def get_publications():
    domain = os.getenv("SHOPIFY_STORE_DOMAIN", "").strip().strip('"').strip("'")
    domain = domain.replace("https://", "").replace("http://", "").rstrip("/")
    client_id     = os.getenv("SHOPIFY_CLIENT_ID", "").strip().strip('"').strip("'")
    client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip().strip('"').strip("'")

    token_res = requests.post(
        f"https://{domain}/admin/oauth/access_token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    if token_res.status_code >= 400:
        return JSONResponse({"error": token_res.text}, status_code=400)

    token = token_res.json().get("access_token")
    if not token:
        return JSONResponse({"error": "No se pudo obtener token", "response": token_res.json()}, status_code=400)

    res = requests.post(
        f"https://{domain}/admin/api/2026-04/graphql.json",
        headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
        json={"query": "{ publications(first: 10) { nodes { id name } } }"},
        timeout=30,
    )
    return res.json()