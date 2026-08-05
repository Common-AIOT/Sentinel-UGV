"""Run the GPU ASR service with ``python -m gpu_server``."""

from __future__ import annotations

import os

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    # This must happen before qwen-asr, torch, or faster-whisper is imported.
    os.environ["CUDA_VISIBLE_DEVICES"] = settings.cuda_visible_devices

    import uvicorn

    from .app import create_app

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        access_log=True,
        log_level="info",
    )


if __name__ == "__main__":
    main()
