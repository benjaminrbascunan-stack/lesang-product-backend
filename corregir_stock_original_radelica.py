"""
Corrige el stock original de Radelica sumando disponible actual + vendido en Junio.
Ejecutar: python3 corregir_stock_original_radelica.py
"""
import requests

API = "https://lesang-product-backend-production.up.railway.app"
MARCA = "Radelica"
MES = "Junio"

def main():
    print(f"Leyendo stock actual de {MARCA}...")
    r = requests.get(f"{API}/pos/stock-marcas", timeout=30)
    items = {i["row_index"]: i for i in r.json().get("items", []) 
             if i["marca"] == MARCA}
    print(f"✓ {len(items)} productos {MARCA} encontrados")

    print(f"\nLeyendo historial de ventas de {MES}...")
    r2 = requests.get(f"{API}/pos/historial/{MES}", timeout=30)
    ventas = r2.json().get("ventas", [])
    
    # Filtrar ventas de Radelica
    ventas_radelica = [v for v in ventas if v.get("marca") == MARCA]
    print(f"✓ {len(ventas_radelica)} ventas de {MARCA} en {MES}")

    # Contar vendidos por nombre_prenda + talla
    vendidos = {}  # {nombre: {talla: cantidad}}
    for v in ventas_radelica:
        nombre = v.get("nombre_prenda", "").strip()
        talla  = v.get("talla", "").strip().upper()
        if not nombre: continue
        if nombre not in vendidos: vendidos[nombre] = {}
        vendidos[nombre][talla] = vendidos[nombre].get(talla, 0) + 1

    TALLAS = ["XXS","XS","S","M","L","XL","XXL"]
    
    # Calcular stock original = actual + vendido
    correcciones = []
    for row_idx, item in items.items():
        nombre = item["nombre_prenda"]
        v_nombre = vendidos.get(nombre, {})
        orig = []
        cambios = []
        for t in TALLAS:
            actual   = int(item["tallas"].get(t, 0) or 0)
            vendido  = v_nombre.get(t, 0)
            original = actual + vendido
            orig.append(original)
            if vendido > 0:
                cambios.append(f"{t}:{actual}+{vendido}={original}")
        
        if any(cambios):
            print(f"  {nombre}: {', '.join(cambios)}")
        correcciones.append({"row": row_idx, "orig": orig})

    total_vendido = sum(len(v) for v in ventas_radelica)
    print(f"\nTotal vendido a corregir: {total_vendido} unidades")
    confirm = input(f"¿Actualizar stock original de {len(correcciones)} productos? (s/n): ").strip().lower()
    if confirm != 's':
        print("Cancelado.")
        return

    # Enviar corrección via endpoint
    r3 = requests.post(f"{API}/pos/stock-marcas/actualizar-originales",
        json=correcciones, timeout=60)
    if not r3.ok:
        print(f"Error: {r3.status_code} {r3.text[:300]}")
        return
    print(f"\n✓ Stock original corregido para {len(correcciones)} productos de {MARCA}")

if __name__ == "__main__":
    main()