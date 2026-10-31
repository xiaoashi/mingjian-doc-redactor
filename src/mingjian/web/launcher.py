import argparse
import threading
import time
import webbrowser
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="明鉴本地网页")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    import httpx
    try:
        with httpx.Client(trust_env=False) as client:
            existing = client.get(f"http://127.0.0.1:{args.port}/api/bootstrap", timeout=.6)
            if existing.status_code == 200 and existing.json().get("service") == "mingjian-web":
                if not args.no_browser:
                    webbrowser.open(f"http://127.0.0.1:{args.port}")
                return 0
    except (httpx.HTTPError, ValueError):
        pass
    from mingjian.web.server import create_app
    import uvicorn
    app = create_app(args.workspace)
    if not args.no_browser:
        def launch():
            import httpx
            for _ in range(50):
                try:
                    with httpx.Client(trust_env=False) as client:
                        if client.get(f"http://127.0.0.1:{args.port}/api/bootstrap", timeout=.5).status_code == 200:
                            webbrowser.open(f"http://127.0.0.1:{args.port}")
                            return
                except httpx.HTTPError:
                    time.sleep(.1)
        threading.Thread(target=launch, daemon=True).start()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.port, http="h11", loop="asyncio",
                                          ws="none", log_level="warning", log_config=None, access_log=False))
    app.state.shutdown_callback = lambda: setattr(server, "should_exit", True)
    server.run()


if __name__ == "__main__":
    main()
