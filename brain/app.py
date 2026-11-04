"""Start the brain API:  python app.py   (http://127.0.0.1:8000, docs at /docs)

Same as `brain serve` / `python -m app`. Settings come from .env (see .env.example).
"""

from app.cli import main

if __name__ == "__main__":
    main()
