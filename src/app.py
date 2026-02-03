"""
Punto de entrada para la aplicación web del sistema de búsqueda de beneficios.

Este script inicia la interfaz web con Gradio para interactuar con el
sistema multiagente de búsqueda de beneficios TeVaBien.

Uso:
    python -m src.app
    python -m src.app --share  (para generar URL pública)
    python -m src.app --port 8080 (para usar puerto personalizado)
"""

import argparse
import sys
from .ui.chat_interface import create_chat_interface


def parse_arguments():
    """Parsea los argumentos de línea de comandos."""
    parser = argparse.ArgumentParser(
        description="Interfaz web para el sistema de beneficios TeVaBien"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="Puerto para el servidor web (default: 7860)"
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host para el servidor web (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="Generar URL pública compartible"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Habilitar modo debug"
    )

    return parser.parse_args()


def main():
    """Función principal que inicia la aplicación."""
    args = parse_arguments()

    print("\n" + "=" * 80)
    print(" Asistente de Beneficios TeVaBien ".center(80, "="))
    print("=" * 80)
    print(f"\n🚀 Iniciando servidor en http://{args.host}:{args.port}")

    if args.share:
        print("🌐 Se generará una URL pública compartible...")

    print("\n💡 Presiona Ctrl+C para detener el servidor\n")
    print("=" * 80 + "\n")

    try:
        # Crear y lanzar la interfaz
        chat = create_chat_interface()
        chat.launch(
            server_name=args.host,
            server_port=args.port,
            share=args.share,
            debug=args.debug,
            show_error=True
        )
    except KeyboardInterrupt:
        print("\n\n👋 Servidor detenido. ¡Hasta luego!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error al iniciar el servidor: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
