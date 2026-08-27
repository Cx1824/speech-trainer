#!/usr/bin/env python3
"""Serve the deterministic promo renderer and receive its generated frames."""

from __future__ import annotations

import argparse
import re
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


FRAME_PATH = re.compile(r"^/__promo_frame__/(frame-\d{3}\.jpg)$")
MAX_FRAME_BYTES = 2 * 1024 * 1024


class PromoHandler(SimpleHTTPRequestHandler):
    output_dir: Path

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        match = FRAME_PATH.fullmatch(self.path)
        if not match:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            self.send_error(HTTPStatus.LENGTH_REQUIRED)
            return
        length = int(raw_length)
        if length <= 0 or length > MAX_FRAME_BYTES:
            self.send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            return
        if self.headers.get_content_type() != "image/jpeg":
            self.send_error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE)
            return

        destination = self.output_dir / match.group(1)
        payload = self.rfile.read(length)
        if len(payload) != length or not payload.startswith(b"\xff\xd8"):
            self.send_error(HTTPStatus.BAD_REQUEST)
            return
        destination.write_bytes(payload)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        if self.command != "POST":
            super().log_message(format, *args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--port", type=int, default=17920)
    args = parser.parse_args()

    root = args.root.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not output.is_relative_to(root):
        raise SystemExit("Output directory must stay inside the project root")

    handler = lambda *h_args, **h_kwargs: PromoHandler(  # noqa: E731
        *h_args,
        directory=str(root),
        **h_kwargs,
    )
    PromoHandler.output_dir = output
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"Promo renderer: http://127.0.0.1:{args.port}/scripts/promo_video/promo.html")
    print(f"Frames: {output}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
