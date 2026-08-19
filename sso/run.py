from __future__ import annotations

import os
from wsgiref.simple_server import make_server

from app import app


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.environ.get("PORT", "8000").strip() or "8000")
    with make_server(host, port, app) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    main()
