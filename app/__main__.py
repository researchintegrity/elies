"""
Run the API with uvicorn: ``python -m app``.

Development reload is off unless ELIES_RELOAD=true; host and port come from
API_HOST (default 127.0.0.1) and API_PORT (default 8000).
"""
import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host=os.getenv("API_HOST", "127.0.0.1"),
        port=int(os.getenv("API_PORT", "8000")),
        reload=os.getenv("ELIES_RELOAD", "false").lower() in ("1", "true", "yes"),
    )


if __name__ == "__main__":
    main()
