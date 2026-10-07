"""Read-only checks of the live desktop viewer and the complete public installer."""

import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from PIL import Image

root = Path(__file__).resolve().parents[1]
session = json.loads((root / "artifacts/desktop-preview-session.json").read_text())
assert session["status"] == "running" and session["packaged"]
link = urlsplit(session["url"])
base = f"{link.scheme}://{link.netloc}"
token = parse_qs(link.query)["session"][0]
binary = root / "artifacts/installers/CytoForge-0.1.0.AppImage"
with binary.open("rb") as stream:
    expected_hash = hashlib.file_digest(stream, "sha256").hexdigest()
with httpx.Client(timeout=30, trust_env=False) as client:
    health = client.get(base + "/health")
    health.raise_for_status()
    assert health.json() == {"running": True, "desktop": True, "packaged": True}
    assert client.get(base + "/frame.jpg").status_code == 401
    downloaded = hashlib.sha256()
    downloaded_bytes = 0
    with client.stream("GET", base + "/app") as response:
        response.raise_for_status()
        assert int(response.headers["content-length"]) == binary.stat().st_size
        for chunk in response.iter_bytes(1024 * 1024):
            downloaded.update(chunk)
            downloaded_bytes += len(chunk)
    assert downloaded_bytes == binary.stat().st_size
    assert downloaded.hexdigest() == expected_hash
    client.cookies.set("cytoforge_desktop_preview", token)
    state_response = client.get(base + "/state")
    state_response.raise_for_status()
    state = state_response.json()
    assert any(window["id"] == state["selectedWindowId"] for window in state["windows"])
    frame = client.get(base + "/frame.jpg")
    frame.raise_for_status()
    assert int(frame.headers["x-cytoforge-window"]) == state["selectedWindowId"]
    with Image.open(io.BytesIO(frame.content)) as image:
        assert image.size == (1540, 990) and image.format == "JPEG"
    screenshot = root / "artifacts/screenshots/desktop-progress-current.jpg"
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    screenshot.write_bytes(frame.content)
report = {
    "status": "passed",
    "checked_at": datetime.now(UTC).isoformat(),
    "base": base,
    "host": session["host"],
    "port": session["port"],
    "health": health.json(),
    "desktop_pid": session["desktop_pid"],
    "private_engine_pid": session["private_engine_pid"],
    "downloaded_bytes": downloaded_bytes,
    "installed_appimage_sha256": expected_hash,
    "public_download_sha256": downloaded.hexdigest(),
    "complete_public_download_matches_installed": True,
    "frame_dimensions": [1540, 990],
    "frame_identity_matches_selected_native_window": True,
    "native_window_count": len(state["windows"]),
    "unauthenticated_frame_rejected": True,
    "screenshot": str(screenshot),
}
(root / "artifacts/desktop-progress-current-validation.json").write_text(
    json.dumps(report, indent=2) + "\n"
)
print(json.dumps(report, indent=2))
