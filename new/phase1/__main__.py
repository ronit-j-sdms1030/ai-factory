import os
from pathlib import Path

from phase1.server import serve

if __name__ == "__main__":
    host = os.getenv("PHASE1_HOST", "127.0.0.1")
    port = int(os.getenv("PHASE1_PORT", "8787"))
    root = os.getenv("RUNTIME_ROOT")
    serve(host=host, port=port, root=Path(root) if root else None)
