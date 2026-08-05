import uvicorn

from .app import Settings, create_app


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        create_app(settings), host=settings.host, port=settings.port, log_level="info"
    )


if __name__ == "__main__":
    main()
