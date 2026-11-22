import os

from app import dev_mode, initialize_database, main as run_development_server


def main():
    if dev_mode:
        run_development_server()
        return

    initialize_database()

    host = os.environ.get("app_host", "0.0.0.0")
    port = int(os.environ.get("app_port", "8000"))

    # En worker äger den roterande loggfilen. Trådar hanterar samtidiga anrop.
    os.execvp("gunicorn", [
        "gunicorn", "--bind", f"{host}:{port}",
        "--workers", "1", "--threads", "4", "app:app",
    ])


if __name__ == "__main__":
    main()
