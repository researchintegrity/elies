"""
ELIES Scientific Image Analysis System

This module allows running the application as a package:
    python -m app
"""

import uvicorn
import sys
from pathlib import Path

# Ensure the project root is in the path
project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))


def main():
    """Run the FastAPI application"""
    try:
        uvicorn.run(
            "app.main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            log_level="info"
        )
    except KeyboardInterrupt:
        print("Server shutdown requested")
        sys.exit(0)
    except Exception as e:
        print(f"Error starting server: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
