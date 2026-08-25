"""生产测试包的静态前端路由。"""

from pathlib import Path

from app.main import _resolve_frontend_file


def test_existing_frontend_asset_is_served(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    asset = tmp_path / "worklets" / "recorder.js"
    index.write_text("index", encoding="utf-8")
    asset.parent.mkdir()
    asset.write_text("worklet", encoding="utf-8")

    assert _resolve_frontend_file(tmp_path, "worklets/recorder.js") == asset


def test_client_route_falls_back_to_index(tmp_path: Path) -> None:
    index = tmp_path / "index.html"
    index.write_text("index", encoding="utf-8")

    assert _resolve_frontend_file(tmp_path, "training") == index
    assert _resolve_frontend_file(tmp_path, "../private.env") == index
