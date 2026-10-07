import argparse
import json
import os
from pathlib import Path

import uvicorn

from .app import create_app


def main():
    # Required before parsing arguments when packaged analysis workers spawn on each OS.
    from multiprocessing import freeze_support

    freeze_support()
    parser = argparse.ArgumentParser(description="CytoForge local cytometry engine")
    parser.add_argument("--port", type=int, default=8765, help="Loopback HTTP port")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--frontend-dir", type=Path)
    parser.add_argument("--parent-pid", type=int, help="Exit when the desktop parent stops")
    args = parser.parse_args()
    # Desktop chooses an ephemeral port and reads this readiness line.
    import socket

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Permit fast fixed-port restarts after TIME_WAIT on Unix. Windows uses an
    # exclusive listener so another process cannot claim the same engine port.
    listener.setsockopt(
        socket.SOL_SOCKET,
        socket.SO_EXCLUSIVEADDRUSE if os.name == "nt" else socket.SO_REUSEADDR,
        1,
    )
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    actual_port = listener.getsockname()[1]
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    print(json.dumps({"event": "cytoforge-starting", "port": actual_port}), flush=True)
    app = create_app(args.data_dir, args.frontend_dir)
    config = uvicorn.Config(app, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    if args.parent_pid:
        import threading
        import time

        def watch_parent():
            if os.name == "nt":
                import ctypes

                kernel = ctypes.windll.kernel32
                kernel.OpenProcess.restype = ctypes.c_void_p
                parent_handle = kernel.OpenProcess(0x00100000, False, args.parent_pid)
                if not parent_handle:
                    server.should_exit = True
                    return
                try:
                    while kernel.WaitForSingleObject(ctypes.c_void_p(parent_handle), 1000) == 258:
                        if server.should_exit:
                            return
                    server.should_exit = True
                finally:
                    kernel.CloseHandle(ctypes.c_void_p(parent_handle))
            else:
                while not server.should_exit:
                    if os.getppid() != args.parent_pid:
                        server.should_exit = True
                        return
                    time.sleep(1)

        threading.Thread(target=watch_parent, daemon=True).start()
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()
