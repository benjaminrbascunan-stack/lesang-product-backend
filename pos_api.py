"""
Lé Sang — POS API completa
Endpoints: productos, ventas, historial, edición, configuración
Persistencia: Google Sheets (una hoja por mes)
"""

import os, json
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx

app = FastAPI(title="Lé Sang POS")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

SHOPIFY_DOMAIN = os.environ["SHOPIFY_STORE_DOMAIN"]
SHOPIFY_TOKEN  = os.environ["SHOPIFY_CLIENT_SECRET"]
LOCATION_ID    = os.environ.get("SHOPIFY_LOCATION_NUMERIC_ID","96183910707")
SHOPIFY_GQL    = f"https://{SHOPIFY_DOMAIN}/admin/api/2026-04/graphql.json"
HEADERS_SH     = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type":"application/json"}

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

CONFIG = {
    "vendedores": ["Aaron","Benja R","Fabio","Marenna","Michelle"],
    "propietarios": ["Tienda","Aaron","Benja R","Fabio","Consignacion"],
    "marcas": [
        {"nombre":"Tienda",         "comision":0,    "paga_iva":False},
        {"nombre":"Pop disaster",   "comision":0.25, "paga_iva":False},
        {"nombre":"Pedritos",       "comision":0.25, "paga_iva":False},
        {"nombre":"Season Archive", "comision":0.25, "paga_iva":False},
        {"nombre":"Consignacion",   "comision":0.25, "paga_iva":False},
    ],
    "vendedores_externos": ["Marenna","Michelle"],
    "com_externo_pct": 0.05,
    "com_bancaria": {
        "Débito":0.0125,"Crédito":0.0295,
        "Transferencia":0,"Efectivo":0,"Internet(Shopify)":0.02
    },
    "iva_tipos": ["Débito","Crédito"],
}

class VentaIn(BaseModel):
    timestamp:         Optional[str]  = None
    nombre_prenda:     str
    talla:             Optional[str]  = "—"
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
    observaciones:     Optional[str]  = ""
    marca:             str
    order_name:        Optional[str]  = ""
    shopify_id:        Optional[str]  = None
    shopify_variant:   Optional[str]  = None

class VentaUpdate(VentaIn):
    row_index: int
    mes:       str

class ConfigUpdate(BaseModel):
    vendedores:          Optional[List[str]] = None
    propietarios:        Optional[List[str]] = None
    marcas:              Optional[list]      = None
    vendedores_externos: Optional[List[str]] = None
    com_externo_pct:     Optional[float]     = None

class ShopifyItem(BaseModel):
    product_id:  str
    variant_id:  str
    title:       str
    price:       float
    quantity:    int = 1

async def gql(query: str, variables: dict = {}) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(SHOPIFY_GQL, headers=HEADERS_SH,
                         json={"query": query, "variables": variables})
        r.raise_for_status()
        data = r.json()
        if "errors" in data:
            raise HTTPException(500, detail=data["errors"])
        return data["data"]

def sheets_append(venta: dict):
    try:
        from pos_sheets import append_venta
        return append_venta(venta)
    except Exception as e:
        print(f"[Sheets] append error: {e}")
        return None

def sheets_get(mes: str):
    try:
        from pos_sheets import get_ventas_mes
        return get_ventas_mes(mes)
    except Exception as e:
        print(f"[Sheets] get error: {e}")
        return []

def sheets_update(mes: str, row: int, venta: dict):
    try:
        from pos_sheets import update_venta
        return update_venta(mes, row, venta)
    except Exception as e:
        print(f"[Sheets] update error: {e}")
        return False

def sheets_delete(mes: str, row: int):
    try:
        from pos_sheets import delete_venta
        return delete_venta(mes, row)
    except Exception as e:
        print(f"[Sheets] delete error: {e}")
        return False

@app.get("/pos/config")
def get_config():
    return CONFIG

@app.patch("/pos/config")
def update_config(body: ConfigUpdate):
    if body.vendedores          is not None: CONFIG["vendedores"]          = body.vendedores
    if body.propietarios        is not None: CONFIG["propietarios"]        = body.propietarios
    if body.marcas              is not None: CONFIG["marcas"]              = body.marcas
    if body.vendedores_externos is not None: CONFIG["vendedores_externos"] = body.vendedores_externos
    if body.com_externo_pct     is not None: CONFIG["com_externo_pct"]     = body.com_externo_pct
    return CONFIG

@app.get("/pos/products")
async def get_products():
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
async def registrar_venta(venta: VentaIn):
    v = venta.dict()
    if not v.get("timestamp"):
        v["timestamp"] = datetime.now().isoformat()

    order_name = None
    if v.get("shopify_variant"):
        try:
            vd = await gql("""query($id:ID!){productVariant(id:$id){inventoryItem{id}}}""",
                           {"id": v["shopify_variant"]})
            inv_id = vd["productVariant"]["inventoryItem"]["id"]
            await gql("""
            mutation($input:InventoryAdjustQuantitiesInput!){
              inventoryAdjustQuantities(input:$input){userErrors{field message}}
            }""", {"input": {
                "reason": "correction",
                "name": f"POS {datetime.now().strftime('%Y%m%d-%H%M%S')}",
                "changes": [{"inventoryItemId": inv_id,
                              "locationId": f"gid://shopify/Location/{LOCATION_ID}",
                              "delta": -1}]
            }})
            dr = await gql("""
            mutation($input:DraftOrderInput!){
              draftOrderCreate(input:$input){draftOrder{name} userErrors{field message}}
            }""", {"input": {
                "lineItems": [{"variantId": v["shopify_variant"], "quantity": 1}],
                "note": f"POS — {v.get('vendedor','')} — {v.get('tipo_pago','')}",
                "tags": ["POS", v.get("tipo_pago","")],
            }})
            if not dr["draftOrderCreate"]["userErrors"]:
                order_name = dr["draftOrderCreate"]["draftOrder"]["name"]
        except Exception as e:
            print(f"[Shopify] {e}")

    v["order_name"] = order_name
    result = sheets_append(v)

    return {
        "success": True,
        "order_name": order_name,
        "sheet_url": result["sheet_url"] if result else None,
        "mes": result["mes"] if result else MESES[datetime.now().month - 1],
        "neto_tienda": v["neto_tienda"],
    }

@app.get("/pos/historial/{mes}")
def get_historial(mes: str):
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
def editar_venta(body: VentaUpdate):
    if not sheets_update(body.mes, body.row_index, body.dict()):
        raise HTTPException(500, "No se pudo actualizar")
    return {"success": True}

@app.delete("/pos/venta/{mes}/{row_index}")
def eliminar_venta(mes: str, row_index: int):
    if mes not in MESES:
        raise HTTPException(400, "Mes inválido")
    if not sheets_delete(mes, row_index):
        raise HTTPException(500, "No se pudo eliminar")
    return {"success": True}

@app.get("/pos/sheet-url")
def get_sheet_url():
    try:
        from pos_sheets import get_or_create_sheet
        sid = get_or_create_sheet()
        return {"url": f"https://docs.google.com/spreadsheets/d/{sid}"}
    except Exception as e:
        return {"url": None, "error": str(e)}

app.mount("/pos/static", StaticFiles(directory="pos_static"), name="pos_static")

@app.get("/pos")
@app.get("/pos/")
async def serve_pos():
    return FileResponse("pos_static/index.html")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)