"""
Lé Sang POS — Google Sheets sync
Escribe cada venta en tiempo real al Google Sheet de la tienda.
Reutiliza el token de Google Drive ya configurado en Railway.
"""

import os, json
from datetime import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# ID del spreadsheet — se crea automáticamente la primera vez y se guarda en env
_SHEET_ID = os.environ.get("POS_SHEET_ID", "")

# Cabeceras de columnas — idénticas al Excel original
HEADERS = [
    "Fecha","Hora","Nombre prenda","Talla","Propietario prenda",
    "Vendedor","Precio venta (BRUTO)","Tipo de pago",
    "IVA (19%)","% comisión bancaria","$ comisión bancaria",
    "Base comisión vendedor","% comisión vendedor","$ comisión vendedor",
    "Neto tienda","Observaciones","Marca (Tienda/externa)","Orden Shopify"
]

def get_creds() -> Credentials:
    token_raw = os.environ.get("GOOGLE_TOKEN_JSON", "")
    if not token_raw:
        raise RuntimeError("GOOGLE_TOKEN_JSON no configurado")
    info = json.loads(token_raw)
    return Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri","https://oauth2.googleapis.com/token"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=SCOPES,
    )

def get_or_create_sheet() -> str:
    """Devuelve el ID del spreadsheet POS, creándolo si no existe."""
    global _SHEET_ID
    if _SHEET_ID:
        # Verificar que el sheet existente es válido
        try:
            creds  = get_creds()
            sheets = build("sheets","v4",credentials=creds)
            meta = sheets.spreadsheets().get(spreadsheetId=_SHEET_ID).execute()
            # Si tiene hojas válidas, retornar
            if meta.get("sheets"):
                return _SHEET_ID
        except Exception:
            pass
        # Si falló, resetear y recrear
        _SHEET_ID = ""

    creds = get_creds()
    sheets = build("sheets","v4",credentials=creds)
    drive  = build("drive","v3",credentials=creds)

    # Buscar si ya existe
    q = "name='Lé Sang — Ventas POS' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    results = drive.files().list(q=q, fields="files(id,name)").execute()
    files = results.get("files",[])
    if files:
        _SHEET_ID = files[0]["id"]
        return _SHEET_ID

    # Crear nuevo spreadsheet con una hoja por mes
    body = {
        "properties": {"title": "Lé Sang — Ventas POS"},
        "sheets": [{"properties":{"title": m}} for m in MESES]
    }
    resp = sheets.spreadsheets().create(body=body).execute()
    _SHEET_ID = resp["spreadsheetId"]

    # Formatear cabeceras en cada hoja
    requests = []
    for i, mes in enumerate(MESES):
        requests.append({
            "updateCells": {
                "rows": [{"values": [
                    {"userEnteredValue":{"stringValue": h},
                     "userEnteredFormat":{
                         "backgroundColor":{"red":0.08,"green":0.08,"blue":0.08},
                         "textFormat":{"bold":True,"foregroundColor":{"red":0.78,"green":0.66,"blue":0.43}},
                         "horizontalAlignment":"CENTER"
                     }}
                    for h in HEADERS
                ]}],
                "fields": "userEnteredValue,userEnteredFormat",
                "start": {"sheetId": i, "rowIndex": 0, "columnIndex": 0}
            }
        })
        # Congelar primera fila
        requests.append({
            "updateSheetProperties": {
                "properties": {"sheetId": i, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount"
            }
        })
        # Ancho de columnas
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": i, "dimension": "COLUMNS", "startIndex": 0, "endIndex": len(HEADERS)},
                "properties": {"pixelSize": 160},
                "fields": "pixelSize"
            }
        })

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=_SHEET_ID,
        body={"requests": requests}
    ).execute()

    # Compartir con todos los que tengan el link
    drive.permissions().create(
        fileId=_SHEET_ID,
        body={"type":"anyone","role":"writer"},
    ).execute()

    print(f"[Sheets] Spreadsheet creado: https://docs.google.com/spreadsheets/d/{_SHEET_ID}")
    return _SHEET_ID


def append_venta(venta: dict) -> dict:
    """
    Escribe una venta en la hoja del mes correspondiente.
    venta debe tener todos los campos calculados.
    Retorna el URL del sheet.
    """
    creds  = get_creds()
    sheets = build("sheets","v4",credentials=creds)
    sheet_id = get_or_create_sheet()

    ts = datetime.fromisoformat(venta.get("timestamp", datetime.now().isoformat()))
    mes_nombre = MESES[ts.month - 1]

    row = [
        ts.strftime("%d/%m/%Y"),
        ts.strftime("%H:%M:%S"),
        venta.get("nombre_prenda",""),
        venta.get("talla","—"),
        venta.get("propietario",""),
        venta.get("vendedor",""),
        venta.get("precio_bruto", 0),
        venta.get("tipo_pago",""),
        round(venta.get("iva", 0), 2),
        venta.get("pct_com_bancaria", 0),
        round(venta.get("com_bancaria", 0), 2),
        venta.get("base_com_vendedor", 0),
        venta.get("pct_com_vendedor", 0),
        round(venta.get("com_vendedor", 0), 2),
        round(venta.get("neto_tienda", 0), 2),
        venta.get("observaciones",""),
        venta.get("marca",""),
        venta.get("order_name","") or "",
    ]

    sheets.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{mes_nombre}!A:R",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values":[row]},
    ).execute()

    return {
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sheet_id}",
        "mes": mes_nombre,
    }


def get_ventas_mes(mes: str) -> list:
    """Lee todas las ventas de un mes desde el Sheet."""
    creds  = get_creds()
    sheets = build("sheets","v4",credentials=creds)
    sheet_id = get_or_create_sheet()

    result = sheets.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{mes}!A2:R",
    ).execute()

    rows = result.get("values", [])
    ventas = []
    for i, row in enumerate(rows):
        if not row or not any(row):
            continue
        # Pad row to expected length
        while len(row) < len(HEADERS):
            row.append("")
        try:
            ventas.append({
                "row_index": i + 2,  # 1-indexed, skip header
                "fecha": row[0], "hora": row[1],
                "nombre_prenda": row[2], "talla": row[3],
                "propietario": row[4], "vendedor": row[5],
                "precio_bruto": float(row[6] or 0),
                "tipo_pago": row[7],
                "iva": float(row[8] or 0),
                "pct_com_bancaria": float(row[9] or 0),
                "com_bancaria": float(row[10] or 0),
                "base_com_vendedor": float(row[11] or 0),
                "pct_com_vendedor": float(row[12] or 0),
                "com_vendedor": float(row[13] or 0),
                "neto_tienda": float(row[14] or 0),
                "observaciones": row[15],
                "marca": row[16],
                "order_name": row[17] if len(row) > 17 else "",
            })
        except (ValueError, IndexError):
            continue
    return ventas


def update_venta(mes: str, row_index: int, venta: dict) -> bool:
    """Actualiza una fila existente (edición desde historial)."""
    creds  = get_creds()
    sheets = build("sheets","v4",credentials=creds)
    sheet_id = get_or_create_sheet()

    ts_str = venta.get("timestamp", datetime.now().isoformat())
    try:
        ts = datetime.fromisoformat(ts_str)
        fecha = ts.strftime("%d/%m/%Y")
        hora  = ts.strftime("%H:%M:%S")
    except Exception:
        fecha = venta.get("fecha","")
        hora  = venta.get("hora","")

    row = [
        fecha, hora,
        venta.get("nombre_prenda",""),
        venta.get("talla","—"),
        venta.get("propietario",""),
        venta.get("vendedor",""),
        venta.get("precio_bruto",0),
        venta.get("tipo_pago",""),
        round(venta.get("iva",0),2),
        venta.get("pct_com_bancaria",0),
        round(venta.get("com_bancaria",0),2),
        venta.get("base_com_vendedor",0),
        venta.get("pct_com_vendedor",0),
        round(venta.get("com_vendedor",0),2),
        round(venta.get("neto_tienda",0),2),
        venta.get("observaciones",""),
        venta.get("marca",""),
        venta.get("order_name","") or "",
    ]

    sheets.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{mes}!A{row_index}:R{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values":[row]},
    ).execute()
    return True


def delete_venta(mes: str, row_index: int) -> bool:
    """Elimina una fila del Sheet."""
    creds  = get_creds()
    sheets = build("sheets","v4",credentials=creds)
    sheet_id = get_or_create_sheet()

    # Obtener sheetId numérico
    meta = sheets.spreadsheets().get(spreadsheetId=sheet_id).execute()
    sheet_gid = next(
        (s["properties"]["sheetId"] for s in meta["sheets"]
         if s["properties"]["title"] == mes), 0
    )

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests":[{
            "deleteDimension":{
                "range":{"sheetId":sheet_gid,"dimension":"ROWS",
                         "startIndex":row_index-1,"endIndex":row_index}
            }
        }]}
    ).execute()
    return True