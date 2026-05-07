"""
Lé Sang POS — Google Sheets sync
Una hoja por mes. Datos en columnas A-T únicamente.
El resumen se calcula desde el POS, no desde el Sheet.
"""

import os, json
from datetime import datetime
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
         "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

# 20 columnas exactas — A hasta T
HEADERS = [
    "Nombre prenda",           # A
    "Talla",                   # B
    "Propietario prenda",      # C
    "Vendedor",                # D
    "Precio venta (BRUTO)",    # E
    "Tipo de pago",            # F
    "IVA (19%)",               # G
    "% comisión bancaria",     # H
    "$ comisión bancaria",     # I
    "Base comisión vendedor",  # J
    "% comisión vendedor",     # K
    "$ comisión vendedor",     # L
    "Neto tienda",             # M
    "Observaciones",           # N
    "Marca",                   # O
    "Fecha",                   # P
    "Hora",                    # Q
    "Orden Shopify",           # R
    "Valor de compra",         # S
    "Margen real",             # T
]

N_COLS = len(HEADERS)  # 20

# Colores
C_BLACK  = {"red": 0.0,  "green": 0.0,  "blue": 0.0}
C_WHITE  = {"red": 1.0,  "green": 1.0,  "blue": 1.0}
C_ORANGE = {"red": 1.0,  "green": 0.357,"blue": 0.0}
C_GRAY1  = {"red": 0.98, "green": 0.98, "blue": 0.98}
C_GRAY2  = {"red": 0.85, "green": 0.85, "blue": 0.85}

_SHEET_ID = ""


def get_creds() -> Credentials:
    raw = os.environ.get("GOOGLE_TOKEN_JSON", "")
    if not raw:
        raise RuntimeError("GOOGLE_TOKEN_JSON no configurado")
    info = json.loads(raw)
    creds = Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=SCOPES,
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _header_cell(text):
    return {
        "userEnteredValue": {"stringValue": text},
        "userEnteredFormat": {
            "backgroundColor": C_BLACK,
            "textFormat": {"bold": True, "foregroundColor": C_WHITE, "fontSize": 9},
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }
    }


def _format_sheet_requests(gid: int, mes: str) -> list:
    reqs = []

    # Fila 0: título del mes
    reqs.append({
        "updateCells": {
            "rows": [{"values": [{
                "userEnteredValue": {"stringValue": f"{mes.upper()} — VENTAS LÉ SANG"},
                "userEnteredFormat": {
                    "backgroundColor": C_BLACK,
                    "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": C_WHITE},
                    "horizontalAlignment": "LEFT", "verticalAlignment": "MIDDLE",
                }
            }]}],
            "fields": "userEnteredValue,userEnteredFormat",
            "start": {"sheetId": gid, "rowIndex": 0, "columnIndex": 0}
        }
    })

    # Merge título fila 0 completa
    reqs.append({
        "mergeCells": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": 0, "endColumnIndex": N_COLS},
            "mergeType": "MERGE_ALL"
        }
    })

    # Fila 1: cabeceras
    reqs.append({
        "updateCells": {
            "rows": [{"values": [_header_cell(h) for h in HEADERS]}],
            "fields": "userEnteredValue,userEnteredFormat",
            "start": {"sheetId": gid, "rowIndex": 1, "columnIndex": 0}
        }
    })

    # Borde naranja bajo cabeceras
    reqs.append({
        "updateBorders": {
            "range": {"sheetId": gid, "startRowIndex": 1, "endRowIndex": 2,
                      "startColumnIndex": 0, "endColumnIndex": N_COLS},
            "bottom": {"style": "SOLID_MEDIUM", "color": C_ORANGE}
        }
    })

    # Alturas
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": gid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
        "properties": {"pixelSize": 40}, "fields": "pixelSize"
    }})
    reqs.append({"updateDimensionProperties": {
        "range": {"sheetId": gid, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
        "properties": {"pixelSize": 36}, "fields": "pixelSize"
    }})

    # Anchos de columnas A-T
    widths = [180,55,120,90,120,110,85,100,100,120,100,100,110,180,110,90,70,110,110,90]
    for ci, w in enumerate(widths[:N_COLS]):
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci+1},
            "properties": {"pixelSize": w}, "fields": "pixelSize"
        }})

    # Congelar 2 filas
    reqs.append({"updateSheetProperties": {
        "properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 2}},
        "fields": "gridProperties.frozenRowCount"
    }})

    return reqs



def _build_resumen_sheet(sheets_svc, sid: str, sheet_map: dict):
    """Crea la hoja de resumen anual con fórmulas que referencian cada mes."""
    gid = sheet_map.get("📊 Resumen")
    if gid is None:
        return

    C_BK = {"red":0.0,"green":0.0,"blue":0.0}
    C_WH = {"red":1.0,"green":1.0,"blue":1.0}
    C_OR = {"red":1.0,"green":0.357,"blue":0.0}
    C_G1 = {"red":0.97,"green":0.97,"blue":0.97}

    def hdr(txt, bg=C_BK, fg=C_WH, bold=True, size=10, align="CENTER"):
        return {"userEnteredValue":{"stringValue":txt},"userEnteredFormat":{
            "backgroundColor":bg,"textFormat":{"bold":bold,"foregroundColor":fg,"fontSize":size},
            "horizontalAlignment":align,"verticalAlignment":"MIDDLE"}}

    def frm(formula, bold=False, color=None, num_fmt=None, bg=None, align="RIGHT"):
        fmt = {"textFormat":{"bold":bold,"fontSize":10 if not bold else 11},
               "horizontalAlignment":align,"verticalAlignment":"MIDDLE"}
        if color: fmt["textFormat"]["foregroundColor"] = color
        if num_fmt: fmt["numberFormat"] = {"type":"NUMBER","pattern":"#,##0"}
        if bg: fmt["backgroundColor"] = bg
        return {"userEnteredValue":{"formulaValue":formula},"userEnteredFormat":fmt}

    rows = []

    # Fila 0: título
    rows.append({"values":[
        {"userEnteredValue":{"stringValue":"LÉ SANG — RESUMEN ANUAL"},
         "userEnteredFormat":{"backgroundColor":C_BK,
                              "textFormat":{"bold":True,"fontSize":14,"foregroundColor":C_OR},
                              "horizontalAlignment":"LEFT","verticalAlignment":"MIDDLE"}}
    ]})

    # Fila 1: cabeceras
    cols = ["MES","VENTAS","BRUTO","IVA","COM. BANCO","COM. VEND.","NETO TIENDA"]
    rows.append({"values":[hdr(c) for c in cols]})

    # Filas 2-13: un mes por fila con fórmulas
    for mes in MESES:
        safe = mes.replace("'","''")
        rows.append({"values":[
            {"userEnteredValue":{"stringValue":mes.upper()},
             "userEnteredFormat":{"textFormat":{"bold":True,"fontSize":10},
                                  "horizontalAlignment":"LEFT"}},
            frm(f"=COUNTA('{safe}'!E3:E)"),
            frm(f"=SUM('{safe}'!E3:E)", bold=True, color=C_OR, num_fmt=True),
            frm(f"=SUM('{safe}'!G3:G)", color={"red":0.8,"green":0.2,"blue":0.2}, num_fmt=True),
            frm(f"=SUM('{safe}'!I3:I)", color={"red":0.8,"green":0.2,"blue":0.2}, num_fmt=True),
            frm(f"=SUM('{safe}'!L3:L)", color={"red":0.8,"green":0.2,"blue":0.2}, num_fmt=True),
            frm(f"=SUM('{safe}'!M3:M)", bold=True, num_fmt=True),
        ]})

    # Fila 14: separador vacío
    rows.append({"values":[]})

    # Fila 15: TOTALES
    rows.append({"values":[
        {"userEnteredValue":{"stringValue":"TOTAL AÑO"},
         "userEnteredFormat":{"backgroundColor":C_BK,
                              "textFormat":{"bold":True,"fontSize":11,"foregroundColor":C_WH},
                              "horizontalAlignment":"LEFT"}},
        frm("=SUM(C3:C14)", bold=True),
        frm("=SUM(C3:C14)", bold=True, color=C_OR, num_fmt=True,
            bg=C_BK),
        frm("=SUM(D3:D14)", bold=True, num_fmt=True, bg=C_BK,
            color={"red":1.0,"green":0.6,"blue":0.6}),
        frm("=SUM(E3:E14)", bold=True, num_fmt=True, bg=C_BK,
            color={"red":1.0,"green":0.6,"blue":0.6}),
        frm("=SUM(F3:F14)", bold=True, num_fmt=True, bg=C_BK,
            color={"red":1.0,"green":0.6,"blue":0.6}),
        {"userEnteredValue":{"formulaValue":"=SUM(G3:G14)"},
         "userEnteredFormat":{"backgroundColor":C_OR,
                              "textFormat":{"bold":True,"fontSize":13,"foregroundColor":C_WH},
                              "horizontalAlignment":"RIGHT",
                              "numberFormat":{"type":"NUMBER","pattern":"#,##0"}}},
    ]})

    reqs = [
        {"updateCells":{
            "rows": rows,
            "fields":"userEnteredValue,userEnteredFormat",
            "start":{"sheetId":gid,"rowIndex":0,"columnIndex":0}
        }},
        # Merge título
        {"mergeCells":{"range":{"sheetId":gid,"startRowIndex":0,"endRowIndex":1,
                                "startColumnIndex":0,"endColumnIndex":7},"mergeType":"MERGE_ALL"}},
        # Merge total fila label
        {"mergeCells":{"range":{"sheetId":gid,"startRowIndex":15,"endRowIndex":16,
                                "startColumnIndex":0,"endColumnIndex":2},"mergeType":"MERGE_ALL"}},
        # Anchos
        *[{"updateDimensionProperties":{
            "range":{"sheetId":gid,"dimension":"COLUMNS","startIndex":i,"endIndex":i+1},
            "properties":{"pixelSize":w},"fields":"pixelSize"
        }} for i,w in enumerate([120,70,130,110,110,110,140])],
        # Altura filas
        {"updateDimensionProperties":{
            "range":{"sheetId":gid,"dimension":"ROWS","startIndex":0,"endIndex":1},
            "properties":{"pixelSize":44},"fields":"pixelSize"}},
        {"updateDimensionProperties":{
            "range":{"sheetId":gid,"dimension":"ROWS","startIndex":1,"endIndex":2},
            "properties":{"pixelSize":32},"fields":"pixelSize"}},
        {"updateDimensionProperties":{
            "range":{"sheetId":gid,"dimension":"ROWS","startIndex":2,"endIndex":15},
            "properties":{"pixelSize":28},"fields":"pixelSize"}},
        # Congelar 2 filas
        {"updateSheetProperties":{
            "properties":{"sheetId":gid,"gridProperties":{"frozenRowCount":2}},
            "fields":"gridProperties.frozenRowCount"}},
        # Borde naranja bajo cabeceras
        {"updateBorders":{
            "range":{"sheetId":gid,"startRowIndex":1,"endRowIndex":2,
                     "startColumnIndex":0,"endColumnIndex":7},
            "bottom":{"style":"SOLID_MEDIUM","color":C_OR}}},
    ]

    sheets_svc.spreadsheets().batchUpdate(
        spreadsheetId=sid, body={"requests":reqs}
    ).execute()
    print("[Sheets] Hoja resumen creada")


def get_or_create_sheet() -> str:
    global _SHEET_ID
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    drive  = build("drive",  "v3", credentials=creds)

    # Verificar ID en memoria
    if _SHEET_ID:
        try:
            meta = sheets.spreadsheets().get(spreadsheetId=_SHEET_ID).execute()
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            if all(m in titles for m in MESES):
                return _SHEET_ID
        except Exception:
            _SHEET_ID = ""

    # Buscar en Drive por nombre
    q = "name='Lé Sang — Ventas POS' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    results = drive.files().list(q=q, fields="files(id)", orderBy="createdTime desc").execute()
    files = results.get("files", [])

    for f in files:
        try:
            meta = sheets.spreadsheets().get(spreadsheetId=f["id"]).execute()
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            if all(m in titles for m in MESES):
                _SHEET_ID = f["id"]
                print(f"[Sheets] Sheet encontrado: {_SHEET_ID}")
                return _SHEET_ID
        except Exception:
            continue

    # Borrar corruptos
    for f in files:
        try:
            drive.files().delete(fileId=f["id"]).execute()
            print(f"[Sheets] Corrupto eliminado: {f['id']}")
        except Exception:
            pass

    # Crear nuevo
    body = {
        "properties": {"title": "Lé Sang — Ventas POS"},
        "sheets": [
            {"properties": {"title": "📊 Resumen", "tabColor": {"red":1.0,"green":0.357,"blue":0.0},
                             "gridProperties": {"rowCount": 50, "columnCount": 10}}}
        ] + [
            {"properties": {"title": m, "gridProperties": {"rowCount": 1000, "columnCount": N_COLS + 2}}}
            for m in MESES
        ]
    }
    resp   = sheets.spreadsheets().create(body=body).execute()
    _SHEET_ID = resp["spreadsheetId"]

    # Formato
    meta      = sheets.spreadsheets().get(spreadsheetId=_SHEET_ID).execute()
    sheet_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    all_reqs  = []
    for mes in MESES:
        all_reqs.extend(_format_sheet_requests(sheet_map[mes], mes))

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=_SHEET_ID, body={"requests": all_reqs}
    ).execute()

    # Compartir
    drive.permissions().create(
        fileId=_SHEET_ID, body={"type": "anyone", "role": "writer"}
    ).execute()

    # Agregar fórmulas de resumen en hoja "📊 Resumen"
    try:
        _build_resumen_sheet(sheets, _SHEET_ID, sheet_map)
    except Exception as e:
        print(f"[Sheets] resumen: {e}")

    print(f"[Sheets] Sheet creado: https://docs.google.com/spreadsheets/d/{_SHEET_ID}")
    return _SHEET_ID


def _build_row(venta: dict) -> list:
    ts = venta.get("timestamp", "")
    try:
        dt    = datetime.fromisoformat(ts)
        fecha = dt.strftime("%d/%m/%Y")
        hora  = dt.strftime("%H:%M:%S")
    except Exception:
        fecha = venta.get("fecha", "")
        hora  = venta.get("hora", "")

    def n(val):
        try: return float(val or 0)
        except: return 0.0

    vc     = venta.get("valor_compra")
    margen = venta.get("margen")

    return [
        str(venta.get("nombre_prenda") or ""),   # A
        str(venta.get("talla") or "—"),           # B
        str(venta.get("propietario") or ""),      # C
        str(venta.get("vendedor") or ""),         # D
        n(venta.get("precio_bruto")),             # E — número puro
        str(venta.get("tipo_pago") or ""),        # F
        n(venta.get("iva")),                      # G
        n(venta.get("pct_com_bancaria")),         # H
        n(venta.get("com_bancaria")),             # I
        n(venta.get("base_com_vendedor")),        # J
        n(venta.get("pct_com_vendedor")),         # K
        n(venta.get("com_vendedor")),             # L
        n(venta.get("neto_tienda")),              # M
        str(venta.get("observaciones") or ""),   # N
        str(venta.get("marca") or ""),            # O
        fecha,                                    # P
        hora,                                     # Q
        str(venta.get("order_name") or ""),       # R
        float(vc) if vc else "",                  # S
        float(margen) if margen else "",          # T
    ]


def append_venta(venta: dict) -> dict:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()

    ts = venta.get("timestamp", datetime.now().isoformat())
    try:
        mes = MESES[datetime.fromisoformat(ts).month - 1]
    except Exception:
        mes = MESES[datetime.now().month - 1]

    row = _build_row(venta)

    # Usar batchUpdate para escribir en la próxima fila vacía después de la fila 2
    # Primero obtener cuántas filas hay
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{mes}!A3:A",
        majorDimension="COLUMNS",
    ).execute()
    existing = result.get("values", [[]])[0] if result.get("values") else []
    next_row = len(existing) + 3  # fila 3 = primera de datos (1-indexed)

    # Escribir directamente en esa fila
    sheets.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{mes}!A{next_row}:T{next_row}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()

    # Formato de la fila recién escrita
    try:
        meta = sheets.spreadsheets().get(spreadsheetId=sid).execute()
        gid  = next(s["properties"]["sheetId"] for s in meta["sheets"]
                    if s["properties"]["title"] == mes)
        row_idx = next_row - 1  # 0-indexed

        fmt_reqs = [
            # Formato general fila
            {"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_idx, "endRowIndex": row_idx+1,
                          "startColumnIndex": 0, "endColumnIndex": N_COLS},
                "cell": {"userEnteredFormat": {
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {"fontSize": 10},
                    "borders": {"bottom": {"style": "SOLID",
                                           "color": {"red":0.9,"green":0.9,"blue":0.9}}}
                }},
                "fields": "userEnteredFormat"
            }},
            # Precio naranja (col E = index 4)
            {"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_idx, "endRowIndex": row_idx+1,
                          "startColumnIndex": 4, "endColumnIndex": 5},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "foregroundColor": C_ORANGE, "fontSize": 11},
                    "numberFormat": {"type": "NUMBER", "pattern": '#,##0'}
                }},
                "fields": "userEnteredFormat"
            }},
            # Neto negrita (col M = index 12)
            {"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_idx, "endRowIndex": row_idx+1,
                          "startColumnIndex": 12, "endColumnIndex": 13},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "fontSize": 11},
                    "numberFormat": {"type": "NUMBER", "pattern": '#,##0'}
                }},
                "fields": "userEnteredFormat"
            }},
            # Fila alternada (pares gris muy claro)
            *([{"repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_idx, "endRowIndex": row_idx+1,
                          "startColumnIndex": 0, "endColumnIndex": N_COLS},
                "cell": {"userEnteredFormat": {"backgroundColor": C_GRAY1}},
                "fields": "userEnteredFormat.backgroundColor"
            }}] if row_idx % 2 == 0 else []),
        ]
        sheets.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": fmt_reqs}
        ).execute()
    except Exception as e:
        print(f"[Sheets] formato fila: {e}")

    print(f"[Sheets] ✓ fila {next_row} — {venta.get('nombre_prenda')} ${venta.get('precio_bruto')}")
    return {
        "sheet_url": f"https://docs.google.com/spreadsheets/d/{sid}",
        "mes": mes,
    }


def get_ventas_mes(mes: str) -> list:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()

    result = sheets.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{mes}!A3:T",
        valueRenderOption="UNFORMATTED_VALUE",  # números como números, sin formato
    ).execute()

    rows   = result.get("values", [])
    ventas = []

    for i, row in enumerate(rows):
        if not row or not any(str(c).strip() for c in row):
            continue
        while len(row) < N_COLS:
            row.append("")

        def f(val):
            if val == "" or val is None: return 0.0
            try: return float(val)
            except:
                clean = str(val).replace("$","").replace(".","").replace(",",".").strip()
                try: return float(clean)
                except: return 0.0

        try:
            ventas.append({
                "row_index":          i + 3,
                "nombre_prenda":      str(row[0]),
                "talla":              str(row[1]),
                "propietario":        str(row[2]),
                "vendedor":           str(row[3]),
                "precio_bruto":       f(row[4]),
                "tipo_pago":          str(row[5]),
                "iva":                f(row[6]),
                "pct_com_bancaria":   f(row[7]),
                "com_bancaria":       f(row[8]),
                "base_com_vendedor":  f(row[9]),
                "pct_com_vendedor":   f(row[10]),
                "com_vendedor":       f(row[11]),
                "neto_tienda":        f(row[12]),
                "observaciones":      str(row[13]),
                "marca":              str(row[14]),
                "fecha":              str(row[15]),
                "hora":               str(row[16]),
                "order_name":         str(row[17]) if len(row) > 17 else "",
                "valor_compra":       f(row[18]) if len(row) > 18 and row[18] != "" else None,
                "margen":             f(row[19]) if len(row) > 19 and row[19] != "" else None,
            })
        except Exception as e:
            print(f"[Sheets] fila {i+3} error: {e}")
            continue

    return ventas


def update_venta(mes: str, row_index: int, venta: dict) -> bool:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()
    row    = _build_row(venta)
    sheets.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{mes}!A{row_index}:T{row_index}",
        valueInputOption="RAW",
        body={"values": [row]},
    ).execute()
    return True


def delete_venta(mes: str, row_index: int) -> bool:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()
    meta   = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    gid    = next(s["properties"]["sheetId"] for s in meta["sheets"]
                  if s["properties"]["title"] == mes)
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={"requests": [{"deleteDimension": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": row_index - 1, "endIndex": row_index}
        }}]}
    ).execute()
    return True