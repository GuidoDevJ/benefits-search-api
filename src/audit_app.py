"""
Audit Dashboard — Entry point standalone para el panel de auditoría.

Lanzar con:
    python -m src.audit_app
    python -m src.audit_app --port 7861 --host 0.0.0.0
"""

import argparse
import sys

from .ui.audit_interface import create_audit_interface


def parse_args():
    parser = argparse.ArgumentParser(
        description="Audit Dashboard para el sistema de beneficios TeVaBien"
    )
    parser.add_argument("--port", type=int, default=7861)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--share", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()

    print("\n" + "=" * 70)
    print(" Audit Dashboard — TeVaBien AI ".center(70, "="))
    print("=" * 70)
    print(f"\n🔍 Iniciando en http://{args.host}:{args.port}")
    print("💡 Presiona Ctrl+C para detener\n")
    print("=" * 70 + "\n")

    try:
        demo = create_audit_interface()
        demo.launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share,
            show_error=True,
            show_api=False,
        )
    except KeyboardInterrupt:
        print("\n\n👋 Audit Dashboard detenido.")
        sys.exit(0)
    except Exception as exc:
        print(f"\n❌ Error al iniciar el Audit Dashboard: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
