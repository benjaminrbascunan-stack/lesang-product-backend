"""
Recalcula comisiones de Mayo de Marenna/Michelle de 5% a 7%
Solo ventas de marca Tienda o Consignacion.
Ejecutar: python recalcular_comisiones_mayo.py
"""
import sys, os
sys.path.insert(0, '.')

# Cargar token local si existe
if not os.environ.get('GOOGLE_TOKEN_JSON'):
    token_path = os.path.join(os.path.dirname(__file__), 'token_local.json')
    if os.path.exists(token_path):
        with open(token_path, 'r') as f:
            os.environ['GOOGLE_TOKEN_JSON'] = f.read()
    else:
        print("ERROR: Crea un archivo token_local.json en la carpeta del proyecto")
        print("Pega ahí el contenido de GOOGLE_TOKEN_JSON de Railway")
        sys.exit(1)

from pos_sheets import get_creds, get_or_create_sheet
from googleapiclient.discovery import build

VENDEDORAS_EXT = ["Marenna", "Michelle"]
MARCAS_CON_COMISION = ["Tienda", "Consignacion"]
PCT_VIEJO = 0.05
PCT_NUEVO = 0.07
MES = "Mayo"

# Columnas (0-indexed): 
# A=Nombre, B=Talla, C=Propietario, D=Vendedor, E=PrecioBruto, F=Pago
# G=IVA, H=%ComBanco, I=$ComBanco, J=BaseComVend, K=%ComVend, L=$ComVend
# M=Neto, N=Obs, O=Marca, P=Fecha, Q=Hora, R=OrdenShopify, S=FotoLink

COL_VENDEDOR   = 3   # D
COL_PRECIO     = 4   # E
COL_IVA        = 6   # G
COL_PCT_COM_B  = 7   # H
COL_COM_B      = 8   # I
COL_BASE_COM_V = 9   # J
COL_PCT_COM_V  = 10  # K
COL_COM_V      = 11  # L
COL_NETO       = 12  # M
COL_MARCA      = 14  # O

def main():
    print(f"Recalculando comisiones Mayo: {PCT_VIEJO*100:.0f}% → {PCT_NUEVO*100:.0f}%")
    print(f"Vendedoras: {VENDEDORAS_EXT}")
    print(f"Marcas: {MARCAS_CON_COMISION}\n")

    creds  = get_creds()
    sheets = build("sheets", "v4", credentials=creds)
    sid    = get_or_create_sheet()

    # Leer todas las filas de Mayo
    result = sheets.spreadsheets().values().get(
        spreadsheetId=sid,
        range=f"{MES}!A3:S",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute()

    rows = result.get("values", [])
    updates = []
    skipped = 0
    updated = 0

    for i, row in enumerate(rows):
        while len(row) < 15: row.append("")
        
        vendedor = str(row[COL_VENDEDOR]).strip()
        marca    = str(row[COL_MARCA]).strip()
        precio   = float(row[COL_PRECIO]) if row[COL_PRECIO] else 0
        pct_com_v_actual = float(row[COL_PCT_COM_V]) if row[COL_PCT_COM_V] else 0

        # Solo vendedoras externas con comision
        if vendedor not in VENDEDORAS_EXT:
            skipped += 1
            continue
        if marca not in MARCAS_CON_COMISION:
            skipped += 1
            continue
        if abs(pct_com_v_actual - PCT_VIEJO) > 0.001:
            print(f"  Fila {i+3}: {vendedor} - {marca} - ya tiene {pct_com_v_actual*100:.1f}%, saltando")
            skipped += 1
            continue

        # Recalcular
        iva     = float(row[COL_IVA]) if row[COL_IVA] else 0
        com_b   = float(row[COL_COM_B]) if row[COL_COM_B] else 0
        nueva_com_v = round(precio * PCT_NUEVO, 2)
        nuevo_neto  = round(precio - iva - com_b - nueva_com_v, 2)
        row_num = i + 3  # fila real en Sheet (datos empiezan en fila 3)

        print(f"  Fila {row_num}: {vendedor} | {marca} | ${precio:,.0f} | "
              f"Com: ${float(row[COL_COM_V]):,.0f} → ${nueva_com_v:,.0f} | "
              f"Neto: ${float(row[COL_NETO]):,.0f} → ${nuevo_neto:,.0f}")

        updates.append({
            "range": f"{MES}!K{row_num}:M{row_num}",
            "values": [[PCT_NUEVO, nueva_com_v, nuevo_neto]]
        })
        updated += 1

    print(f"\nFilas a actualizar: {updated} | Saltadas: {skipped}")

    if not updates:
        print("Nada que actualizar.")
        return

    confirm = input(f"\n¿Confirmar actualización de {updated} ventas? (s/n): ").strip().lower()
    if confirm != 's':
        print("Cancelado.")
        return

    sheets.spreadsheets().values().batchUpdate(
        spreadsheetId=sid,
        body={
            "valueInputOption": "RAW",
            "data": updates
        }
    ).execute()

    print(f"\n✓ {updated} ventas actualizadas a {PCT_NUEVO*100:.0f}%")

if __name__ == "__main__":
    main()
