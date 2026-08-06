#!/usr/bin/env python3
"""Upload a PNG to Drive, make it link-readable, and insert it as an inline image
into a Google Doc at a chosen heading anchor.

Usage:
  python insert_diagram.py <DOC_ID> <PNG_PATH> [--anchor 'Full image:']
                                              [--parent-folder <DRIVE_FOLDER_ID>]
                                              [--width-pt 460] [--height-pt N]

The script:
  1. Reads the PNG's real pixel dimensions from its IHDR header so the inserted
     image keeps its aspect ratio (--height-pt overrides; a 0.6 ratio is the
     fallback if the header cannot be parsed)
  2. Uploads PNG to Drive (multipart) — title derived from filename, MIME=image/png
  3. Sets sharing to "anyone with the link can view" so the Docs API can fetch
  4. Locates the anchor heading paragraph in the Doc
  5. Issues a batchUpdate with insertInlineImage at the position right after the anchor

It does NOT remove the {{ARCHITECTURE_DIAGRAM}} anchor text or the legacy image —
see SKILL.md Phase D steps 2 and 3b, which are separate and mandatory.
"""

import argparse
import json
import struct
import subprocess
import sys
from pathlib import Path

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
FALLBACK_ASPECT = 0.6  # height / width, used only when the PNG header is unreadable


def png_dimensions(png_path: Path) -> tuple[int, int] | None:
    """Return (width_px, height_px) by parsing the PNG IHDR chunk. stdlib only.

    Layout: 8-byte signature, then the IHDR chunk — 4-byte length, b'IHDR',
    then width and height as big-endian uint32. Returns None if the file is not
    a parseable PNG, so the caller can fall back to a fixed aspect ratio.
    """
    try:
        with open(png_path, "rb") as f:
            header = f.read(24)
    except OSError:
        return None
    if len(header) < 24 or header[:8] != PNG_SIGNATURE or header[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", header[16:24])
    if width <= 0 or height <= 0:
        return None
    return width, height


def run_gws(*args: str) -> dict:
    """Run a gws CLI command and return the parsed JSON output."""
    proc = subprocess.run(["gws", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"gws CLI failed (exit {proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
    out = proc.stdout
    if not out.lstrip().startswith("{"):
        out = out[out.index("{"):]
    return json.loads(out)


def upload_png(png_path: Path, parent_folder: str | None) -> str:
    """Upload PNG to Drive and return the new file ID."""
    metadata: dict[str, object] = {"name": png_path.name, "mimeType": "image/png"}
    if parent_folder:
        metadata["parents"] = [parent_folder]
    result = run_gws(
        "drive",
        "files",
        "create",
        "--params",
        json.dumps({"supportsAllDrives": True, "fields": "id,webViewLink,webContentLink"}),
        "--json",
        json.dumps(metadata),
        "--upload",
        str(png_path),
    )
    return result["id"]


def make_link_readable(file_id: str) -> None:
    run_gws(
        "drive",
        "permissions",
        "create",
        "--params",
        json.dumps({"fileId": file_id, "supportsAllDrives": True, "fields": "id"}),
        "--json",
        json.dumps({"role": "reader", "type": "anyone", "allowFileDiscovery": False}),
    )


def find_anchor_index(doc_id: str, anchor: str) -> int:
    """Return the endIndex of the first paragraph whose text contains the anchor.

    The endIndex is where we want to insert the image — at the start of the next paragraph.
    """
    doc = run_gws(
        "docs",
        "documents",
        "get",
        "--params",
        json.dumps({"documentId": doc_id}),
        "--format",
        "json",
    )
    for el in doc.get("body", {}).get("content", []):
        if "paragraph" in el:
            text = "".join(
                r.get("textRun", {}).get("content", "")
                for r in el["paragraph"].get("elements", [])
            )
            if anchor in text:
                return el["endIndex"]
    sys.exit(f"Anchor heading {anchor!r} not found in Doc {doc_id}")


def insert_image(
    doc_id: str, file_id: str, location_index: int, width_pt: float, height_pt: float
) -> None:
    uri = f"https://drive.google.com/uc?export=view&id={file_id}"
    body = {
        "requests": [
            {
                "insertInlineImage": {
                    "uri": uri,
                    "location": {"index": location_index},
                    "objectSize": {
                        "width": {"magnitude": width_pt, "unit": "PT"},
                        "height": {"magnitude": height_pt, "unit": "PT"},
                    },
                }
            }
        ]
    }
    run_gws(
        "docs",
        "documents",
        "batchUpdate",
        "--params",
        json.dumps({"documentId": doc_id}),
        "--json",
        json.dumps(body),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("doc_id")
    ap.add_argument("png_path")
    ap.add_argument("--anchor", default="Full image:")
    ap.add_argument(
        "--parent-folder",
        default=None,
        help="Drive folder ID to upload PNG into (default: My Drive root)",
    )
    ap.add_argument(
        "--width-pt",
        type=float,
        default=460.0,
        help="Image width in points (default: 460pt ~ full page width)",
    )
    ap.add_argument(
        "--height-pt",
        type=float,
        default=None,
        help=(
            "Override the image height in points. By default the height is derived "
            "from the PNG's real pixel dimensions so the diagram is not stretched."
        ),
    )
    args = ap.parse_args()

    png_path = Path(args.png_path)
    if not png_path.exists():
        sys.exit(f"PNG not found: {png_path}")

    if args.height_pt is not None:
        height_pt = args.height_pt
        print(f"Using --height-pt override: {height_pt:.1f}pt")
    else:
        dims = png_dimensions(png_path)
        if dims:
            px_w, px_h = dims
            height_pt = args.width_pt * (px_h / px_w)
            print(
                f"PNG is {px_w}x{px_h}px; scaling to "
                f"{args.width_pt:.1f}x{height_pt:.1f}pt (aspect preserved)"
            )
        else:
            height_pt = args.width_pt * FALLBACK_ASPECT
            print(
                f"WARNING: could not read PNG dimensions from {png_path.name}; "
                f"falling back to a {FALLBACK_ASPECT} aspect ratio "
                f"({height_pt:.1f}pt). The image may look stretched — check the Doc, "
                "or pass --height-pt.",
                file=sys.stderr,
            )

    print(f"Uploading {png_path.name} to Drive...")
    file_id = upload_png(png_path, args.parent_folder)
    print(f"  Drive file ID: {file_id}")

    print("Setting link-readable sharing...")
    make_link_readable(file_id)

    print(f"Locating anchor heading {args.anchor!r} in Doc {args.doc_id}...")
    location_index = find_anchor_index(args.doc_id, args.anchor)
    print(f"  Anchor end index: {location_index}")

    print("Inserting inline image into Doc...")
    insert_image(args.doc_id, file_id, location_index, args.width_pt, height_pt)
    print("Done.")
    print(f"  Doc: https://docs.google.com/document/d/{args.doc_id}/edit")
    print(f"  Image: https://drive.google.com/file/d/{file_id}/view")


if __name__ == "__main__":
    main()
