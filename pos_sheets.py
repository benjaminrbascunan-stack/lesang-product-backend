"""
Lé Sang POS — Google Sheets sync
Estilo inspirado en el Excel original: encabezados oscuros, columnas de resumen,
una hoja por mes con totales automáticos.
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

# Columnas — idénticas al Excel original
HEADERS = [
    "Nombre prenda", "Talla", "Propietario prenda", "Vendedor",
    "Precio venta (BRUTO)", "Tipo de pago", "IVA (19%)",
    "% comisión bancaria", "$ comisión bancaria",
    "Base comisión vendedor", "% comisión vendedor", "$ comisión vendedor",
    "Neto tienda", "Observaciones", "Marca (Tienda/externa)",
    "Fecha", "Hora", "Orden Shopify"
]

# Columnas de resumen (lado derecho, col R en adelante)
RESUMEN_COL = 19  # columna T (0-indexed)

# Colores marca Lé Sang
COLOR_BG_HEADER = {"red": 0.0, "green": 0.0, "blue": 0.0}        # negro
COLOR_TEXT_HEADER = {"red": 1.0, "green": 1.0, "blue": 1.0}       # blanco
COLOR_ACCENT = {"red": 1.0, "green": 0.357, "blue": 0.0}          # #FF5B00
COLOR_ACCENT_TEXT = {"red": 1.0, "green": 1.0, "blue": 1.0}       # blanco
COLOR_ROW_ALT = {"red": 0.98, "green": 0.98, "blue": 0.98}        # gris muy claro
COLOR_TOTAL_BG = {"red": 0.0, "green": 0.0, "blue": 0.0}          # negro
COLOR_TOTAL_TEXT = {"red": 1.0, "green": 1.0, "blue": 1.0}        # blanco

_SHEET_ID = os.environ.get("POS_SHEET_ID", "")


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
            "backgroundColor": COLOR_BG_HEADER,
            "textFormat": {
                "bold": True,
                "foregroundColor": COLOR_TEXT_HEADER,
                "fontSize": 9,
            },
            "horizontalAlignment": "CENTER",
            "verticalAlignment": "MIDDLE",
            "wrapStrategy": "WRAP",
        }
    }


def _resumen_label_cell(text):
    return {
        "userEnteredValue": {"stringValue": text},
        "userEnteredFormat": {
            "textFormat": {"bold": True, "fontSize": 9},
            "horizontalAlignment": "LEFT",
        }
    }


def _resumen_value_cell(formula_or_value, is_currency=True):
    cell = {
        "userEnteredValue": {"formulaValue": formula_or_value} if formula_or_value.startswith("=") else {"numberValue": 0},
        "userEnteredFormat": {
            "textFormat": {"bold": True, "fontSize": 10, "foregroundColor": COLOR_ACCENT},
            "horizontalAlignment": "RIGHT",
        }
    }
    if is_currency:
        cell["userEnteredFormat"]["numberFormat"] = {"type": "CURRENCY", "pattern": '"$"#,##0'}
    return cell


def _format_mes_requests(sheet_gid: int, mes: str) -> list:
    """Genera todos los requests de formato para una hoja."""
    requests = []

    # Título del mes en fila 0
    requests.append({
        "updateCells": {
            "rows": [{"values": [{
                "userEnteredValue": {"stringValue": f"{mes.upper()} — VENTAS LÉ SANG"},
                "userEnteredFormat": {
                    "backgroundColor": COLOR_BG_HEADER,
                    "textFormat": {
                        "bold": True,
                        "fontSize": 13,
                        "foregroundColor": COLOR_TEXT_HEADER,
                        "fontFamily": "Arial",
                    },
                    "horizontalAlignment": "LEFT",
                    "verticalAlignment": "MIDDLE",
                }
            }]}],
            "fields": "userEnteredValue,userEnteredFormat",
            "start": {"sheetId": sheet_gid, "rowIndex": 0, "columnIndex": 0}
        }
    })

    # Merge título fila 0
    requests.append({
        "mergeCells": {
            "range": {"sheetId": sheet_gid, "startRowIndex": 0, "endRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": 16},
            "mergeType": "MERGE_ALL"
        }
    })

    # Cabeceras en fila 1
    requests.append({
        "updateCells": {
            "rows": [{"values": [_header_cell(h) for h in HEADERS]}],
            "fields": "userEnteredValue,userEnteredFormat",
            "start": {"sheetId": sheet_gid, "rowIndex": 1, "columnIndex": 0}
        }
    })

    # Altura fila título
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_gid, "dimension": "ROWS", "startIndex": 0, "endIndex": 1},
            "properties": {"pixelSize": 40},
            "fields": "pixelSize"
        }
    })

    # Altura fila cabeceras
    requests.append({
        "updateDimensionProperties": {
            "range": {"sheetId": sheet_gid, "dimension": "ROWS", "startIndex": 1, "endIndex": 2},
            "properties": {"pixelSize": 36},
            "fields": "pixelSize"
        }
    })

    # Anchos de columnas
    col_widths = [180, 60, 130, 100, 130, 120, 100, 120, 120, 140, 120, 120, 110, 180, 140, 90, 70, 120]
    for ci, w in enumerate(col_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_gid, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci+1},
                "properties": {"pixelSize": w},
                "fields": "pixelSize"
            }
        })

    # Congelar fila 0 y 1
    requests.append({
        "updateSheetProperties": {
            "properties": {"sheetId": sheet_gid, "gridProperties": {"frozenRowCount": 2}},
            "fields": "gridProperties.frozenRowCount"
        }
    })

    # Bloque de resumen (columna Q-S, filas 0-8)
    resumen_rows = [
        [{"userEnteredValue": {"stringValue": f"RESUMEN {mes.upper()}"},
          "userEnteredFormat": {
              "backgroundColor": COLOR_BG_HEADER,
              "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": COLOR_ACCENT},
              "horizontalAlignment": "CENTER"
          }},
         {"userEnteredValue": {"stringValue": ""}},
         {"userEnteredValue": {"stringValue": ""}}],
        [_resumen_label_cell("Total ventas"),
         _resumen_value_cell("=COUNTA(A3:A)", False),
         {"userEnteredValue": {"stringValue": ""}}],
        [_resumen_label_cell("Total bruto"),
         _resumen_value_cell("=SUM(E3:E)"),
         {"userEnteredValue": {"stringValue": ""}}],
        [_resumen_label_cell("Total IVA"),
         _resumen_value_cell("=SUM(G3:G)"),
         {"userEnteredValue": {"stringValue": ""}}],
        [_resumen_label_cell("Total com. bancaria"),
         _resumen_value_cell("=SUM(I3:I)"),
         {"userEnteredValue": {"stringValue": ""}}],
        [_resumen_label_cell("Total com. vendedores"),
         _resumen_value_cell("=SUM(L3:L)"),
         {"userEnteredValue": {"stringValue": ""}}],
        [{"userEnteredValue": {"stringValue": ""}},
         {"userEnteredValue": {"stringValue": ""}},
         {"userEnteredValue": {"stringValue": ""}}],
        [{"userEnteredValue": {"stringValue": "NETO TIENDA"},
          "userEnteredFormat": {
              "backgroundColor": COLOR_ACCENT,
              "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": COLOR_ACCENT_TEXT},
              "horizontalAlignment": "LEFT"
          }},
         {"userEnteredValue": {"formulaValue": "=SUM(M3:M)"},
          "userEnteredFormat": {
              "backgroundColor": COLOR_ACCENT,
              "textFormat": {"bold": True, "fontSize": 14, "foregroundColor": COLOR_ACCENT_TEXT},
              "horizontalAlignment": "RIGHT",
              "numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0'}
          }},
         {"userEnteredValue": {"stringValue": ""},
          "userEnteredFormat": {"backgroundColor": COLOR_ACCENT}}],
    ]

    requests.append({
        "updateCells": {
            "rows": [{"values": r} for r in resumen_rows],
            "fields": "userEnteredValue,userEnteredFormat",
            "start": {"sheetId": sheet_gid, "rowIndex": 0, "columnIndex": RESUMEN_COL}
        }
    })

    # Merge resumen título
    requests.append({
        "mergeCells": {
            "range": {"sheetId": sheet_gid,
                      "startRowIndex": 0, "endRowIndex": 1,
                      "startColumnIndex": RESUMEN_COL, "endColumnIndex": RESUMEN_COL+3},
            "mergeType": "MERGE_ALL"
        }
    })

    # Ancho columnas resumen
    for ci in range(RESUMEN_COL, RESUMEN_COL+3):
        w = 160 if ci == RESUMEN_COL else (140 if ci == RESUMEN_COL+1 else 20)
        requests.append({
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_gid, "dimension": "COLUMNS", "startIndex": ci, "endIndex": ci+1},
                "properties": {"pixelSize": w},
                "fields": "pixelSize"
            }
        })

    # Borde inferior cabeceras
    requests.append({
        "updateBorders": {
            "range": {"sheetId": sheet_gid, "startRowIndex": 1, "endRowIndex": 2, "startColumnIndex": 0, "endColumnIndex": len(HEADERS)},
            "bottom": {"style": "SOLID_MEDIUM", "color": COLOR_ACCENT}
        }
    })

    return requests


def get_or_create_sheet() -> str:
    global _SHEET_ID
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    drive  = build("drive",  "v3", credentials=creds)

    # Verificar si el sheet existente es válido
    if _SHEET_ID:
        try:
            meta = sheets.spreadsheets().get(spreadsheetId=_SHEET_ID).execute()
            existing_sheets = meta.get("sheets", [])
            # Verificar que tenga las hojas de los meses
            existing_titles = [s["properties"]["title"] for s in existing_sheets]
            if all(m in existing_titles for m in MESES):
                return _SHEET_ID
        except Exception as e:
            print(f"[Sheets] Sheet existente inválido ({e}), recreando...")
        _SHEET_ID = ""

    # Buscar si ya existe uno válido en Drive
    q = "name='Lé Sang — Ventas POS' and mimeType='application/vnd.google-apps.spreadsheet' and trashed=false"
    results = drive.files().list(q=q, fields="files(id,name)").execute()
    files = results.get("files", [])
    if files:
        candidate = files[0]["id"]
        try:
            meta = sheets.spreadsheets().get(spreadsheetId=candidate).execute()
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            if all(m in titles for m in MESES):
                _SHEET_ID = candidate
                return _SHEET_ID
        except Exception:
            pass
        # Borrar el corrupto
        try:
            drive.files().delete(fileId=candidate).execute()
            print("[Sheets] Sheet corrupto eliminado, recreando...")
        except Exception:
            pass

    # Crear nuevo spreadsheet
    body = {
        "properties": {"title": "Lé Sang — Ventas POS"},
        "sheets": [{"properties": {"title": m, "gridProperties": {"rowCount": 1000, "columnCount": 25}}}
                   for m in MESES]
    }
    resp = sheets.spreadsheets().create(body=body).execute()
    _SHEET_ID = resp["spreadsheetId"]

    # Obtener sheetIds
    meta      = sheets.spreadsheets().get(spreadsheetId=_SHEET_ID).execute()
    sheet_map = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}

    # Aplicar formato a todas las hojas
    all_requests = []
    for mes in MESES:
        gid = sheet_map[mes]
        all_requests.extend(_format_mes_requests(gid, mes))

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=_SHEET_ID,
        body={"requests": all_requests}
    ).execute()

    # Compartir — cualquiera con el link puede editar
    drive.permissions().create(
        fileId=_SHEET_ID,
        body={"type": "anyone", "role": "writer"},
    ).execute()

    url = f"https://docs.google.com/spreadsheets/d/{_SHEET_ID}"
    print(f"[Sheets] Sheet creado: {url}")
    return _SHEET_ID


def _data_row(venta: dict) -> list:
    ts = venta.get("timestamp", "")
    try:
        dt = datetime.fromisoformat(ts)
        fecha = dt.strftime("%d/%m/%Y")
        hora  = dt.strftime("%H:%M:%S")
    except Exception:
        fecha = venta.get("fecha", "")
        hora  = venta.get("hora", "")

    return [
        venta.get("nombre_prenda", ""),
        venta.get("talla", "—"),
        venta.get("propietario", ""),
        venta.get("vendedor", ""),
        venta.get("precio_bruto", 0),
        venta.get("tipo_pago", ""),
        round(venta.get("iva", 0), 2),
        venta.get("pct_com_bancaria", 0),
        round(venta.get("com_bancaria", 0), 2),
        venta.get("base_com_vendedor", 0),
        venta.get("pct_com_vendedor", 0),
        round(venta.get("com_vendedor", 0), 2),
        round(venta.get("neto_tienda", 0), 2),
        venta.get("observaciones", ""),
        venta.get("marca", ""),
        fecha,
        hora,
        venta.get("order_name", "") or "",
    ]


def append_venta(venta: dict) -> dict:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()

    ts = venta.get("timestamp", datetime.now().isoformat())
    try:
        mes_nombre = MESES[datetime.fromisoformat(ts).month - 1]
    except Exception:
        mes_nombre = MESES[datetime.now().month - 1]

    row = _data_row(venta)

    # Append desde fila 3 (después de título + cabeceras)
    result = sheets.spreadsheets().values().append(
        spreadsheetId=sid,
        range=f"{mes_nombre}!A3:R",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()

    # Obtener índice de la fila recién agregada para formatearla
    updated_range = result.get("updates", {}).get("updatedRange", "")
    try:
        row_num = int(updated_range.split("!")[1].split(":")[0][1:]) - 1  # 0-indexed
        meta    = sheets.spreadsheets().get(spreadsheetId=sid).execute()
        gid     = next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == mes_nombre)

        # Formato de la fila de datos
        fmt_requests = [{
            "repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_num, "endRowIndex": row_num+1,
                          "startColumnIndex": 0, "endColumnIndex": len(HEADERS)},
                "cell": {"userEnteredFormat": {
                    "verticalAlignment": "MIDDLE",
                    "textFormat": {"fontSize": 10},
                    "borders": {
                        "bottom": {"style": "SOLID", "color": {"red": 0.9, "green": 0.9, "blue": 0.9}}
                    }
                }},
                "fields": "userEnteredFormat"
            }
        }, {
            # Precio bruto en naranja
            "repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_num, "endRowIndex": row_num+1,
                          "startColumnIndex": 4, "endColumnIndex": 5},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "foregroundColor": COLOR_ACCENT, "fontSize": 11},
                    "numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0'}
                }},
                "fields": "userEnteredFormat"
            }
        }, {
            # Neto tienda en negrita
            "repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": row_num, "endRowIndex": row_num+1,
                          "startColumnIndex": 12, "endColumnIndex": 13},
                "cell": {"userEnteredFormat": {
                    "textFormat": {"bold": True, "fontSize": 11},
                    "numberFormat": {"type": "CURRENCY", "pattern": '"$"#,##0'}
                }},
                "fields": "userEnteredFormat"
            }
        }]
        sheets.spreadsheets().batchUpdate(spreadsheetId=sid, body={"requests": fmt_requests}).execute()
    except Exception as e:
        print(f"[Sheets] formato fila: {e}")

    url = f"https://docs.google.com/spreadsheets/d/{sid}"
    print(f"[Sheets] venta guardada en {mes_nombre}: {venta.get('nombre_prenda')} ${venta.get('precio_bruto')}")
    return {"sheet_url": url, "mes": mes_nombre}


def get_ventas_mes(mes: str) -> list:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()

    result = sheets.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{mes}!A3:R",
    ).execute()

    rows   = result.get("values", [])
    ventas = []
    for i, row in enumerate(rows):
        if not row or not any(row):
            continue
        while len(row) < len(HEADERS):
            row.append("")
        try:
            def to_float(val):
                if not val: return 0.0
                # Limpiar formato moneda: $120.000 -> 120000
                clean = str(val).replace('$','').replace('.','').replace(',','.').strip()
                try: return float(clean)
                except: return 0.0

            ventas.append({
                "row_index": i + 3,
                "nombre_prenda":      row[0],
                "talla":              row[1],
                "propietario":        row[2],
                "vendedor":           row[3],
                "precio_bruto":       to_float(row[4]),
                "tipo_pago":          row[5],
                "iva":                to_float(row[6]),
                "pct_com_bancaria":   to_float(row[7]),
                "com_bancaria":       to_float(row[8]),
                "base_com_vendedor":  to_float(row[9]),
                "pct_com_vendedor":   to_float(row[10]),
                "com_vendedor":       to_float(row[11]),
                "neto_tienda":        to_float(row[12]),
                "observaciones":      row[13],
                "marca":              row[14],
                "fecha":              row[15],
                "hora":               row[16],
                "order_name":         row[17] if len(row) > 17 else "",
            })
        except Exception as e:
            print(f"[Sheets] error leyendo fila {i}: {e}")
            continue
    return ventas


def update_venta(mes: str, row_index: int, venta: dict) -> bool:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()
    row    = _data_row(venta)
    sheets.spreadsheets().values().update(
        spreadsheetId=sid,
        range=f"{mes}!A{row_index}:R{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values": [row]},
    ).execute()
    return True


def delete_venta(mes: str, row_index: int) -> bool:
    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()
    meta   = sheets.spreadsheets().get(spreadsheetId=sid).execute()
    gid    = next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == mes)
    sheets.spreadsheets().batchUpdate(
        spreadsheetId=sid,
        body={"requests": [{"deleteDimension": {
            "range": {"sheetId": gid, "dimension": "ROWS",
                      "startIndex": row_index-1, "endIndex": row_index}
        }}]}
    ).execute()
    return True