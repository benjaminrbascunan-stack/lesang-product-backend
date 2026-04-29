from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
PYTHON = sys.executable

STEPS = [
    {
        "name": "INGEST / IA / SUPABASE",
        "script": "ingest.py",
        "required": True,
    },
    {
        "name": "PUSH A SHOPIFY",
        "script": "push_to_shopify.py",
        "required": True,
    },
    {
        "name": "REVIEW DASHBOARD",
        "script": "review_dashboard.py",
        "required": False,
    },
]


def print_section(title: str):
    print("\n" + "=" * 90)
    print(title)
    print("=" * 90)


def run_step(step: dict) -> bool:
    name = step["name"]
    script = step["script"]
    required = step.get("required", True)

    script_path = PROJECT_DIR / script

    print_section(f"CORRIENDO: {name}")

    if not script_path.exists():
        print(f"✘ No existe el archivo: {script}")
        return not required

    result = subprocess.run(
        [PYTHON, str(script_path)],
        cwd=PROJECT_DIR,
        text=True,
    )

    if result.returncode != 0:
        print(f"\n✘ Error en paso: {name}")
        print(f"Script: {script}")
        print(f"Código de salida: {result.returncode}")

        if required:
            print("\nPipeline detenido.")
            return False

        print("\nPaso opcional falló, pero el pipeline continúa.")
        return True

    print(f"\n✔ Paso completado: {name}")
    return True


def main():
    start = datetime.now()

    print_section("INICIANDO PIPELINE LÉ SANG")
    print(f"Inicio: {start.isoformat(timespec='seconds')}")
    print(f"Proyecto: {PROJECT_DIR}")

    for step in STEPS:
        ok = run_step(step)

        if not ok:
            end = datetime.now()
            print_section("PIPELINE FALLÓ")
            print(f"Inicio: {start.isoformat(timespec='seconds')}")
            print(f"Fin: {end.isoformat(timespec='seconds')}")
            print(f"Duración: {end - start}")
            sys.exit(1)

    end = datetime.now()

    print_section("PIPELINE COMPLETADO")
    print(f"Inicio: {start.isoformat(timespec='seconds')}")
    print(f"Fin: {end.isoformat(timespec='seconds')}")
    print(f"Duración: {end - start}")


if __name__ == "__main__":
    main()