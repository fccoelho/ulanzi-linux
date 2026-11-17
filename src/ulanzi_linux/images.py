"""Image normalisation for key icons and the info-window background."""

from __future__ import annotations

import hashlib
import io

from PIL import Image, ImageOps

from . import protocol as P
from .config import ICONS_DIR


def _to_png(img: Image.Image, size: tuple[int, int], fit: str = "cover") -> bytes:
    img = ImageOps.exif_transpose(img)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")
    if fit == "contain":
        canvas = Image.new("RGBA", size, (0, 0, 0, 255))
        img.thumbnail(size, Image.LANCZOS)
        canvas.paste(img, ((size[0] - img.width) // 2, (size[1] - img.height) // 2), img if img.mode == "RGBA" else None)
        img = canvas
    else:
        img = ImageOps.fit(img, size, Image.LANCZOS)
    # Flatten onto black: the firmware's alpha handling is inconsistent.
    if img.mode == "RGBA":
        bg = Image.new("RGB", size, (0, 0, 0))
        bg.paste(img, mask=img.split()[3])
        img = bg
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


def store_image(data: bytes, kind: str = "icon", fit: str = "cover") -> str:
    """Normalise an uploaded image and store it content-addressed. Returns file name."""
    Image.MAX_IMAGE_PIXELS = 40_000_000
    img = Image.open(io.BytesIO(data))
    img.seek(0)  # first frame of animated images
    size = (P.ICON_SIZE, P.ICON_SIZE) if kind == "icon" else (P.INFO_WIDTH, P.INFO_HEIGHT)
    png = _to_png(img.copy(), size, fit)
    name = hashlib.sha256(png).hexdigest()[:20] + ".png"
    path = ICONS_DIR / name
    if not path.exists():
        path.write_bytes(png)
    return name


def load_icon(name: str | None) -> bytes | None:
    if not name:
        return None
    path = ICONS_DIR / name
    return path.read_bytes() if path.exists() else None


_FA_INDEX: dict[str, tuple[str, str]] | None = None


def _fa_index() -> dict[str, tuple[str, str]]:
    global _FA_INDEX
    if _FA_INDEX is None:
        import json
        from pathlib import Path

        p = Path(__file__).parent / "static/vendor/fontawesome/icons.json"
        _FA_INDEX = {}
        for name, code, style, *_ in json.loads(p.read_text()):
            _FA_INDEX.setdefault(name, (code, style))
    return _FA_INDEX


def render_glyph_icon(name: str, bg: str = "#3b5bdb", fg: str = "#ffffff") -> str:
    """Render a Font Awesome glyph on a rounded coloured tile; returns stored icon name."""
    from pathlib import Path

    from PIL import ImageDraw, ImageFont

    code, style = _fa_index().get(name, ("f128", "s"))
    font_file = {"s": "fa-solid-900", "r": "fa-regular-400", "b": "fa-brands-400"}[style]
    font = ImageFont.truetype(str(Path(__file__).parent / f"static/vendor/fontawesome/{font_file}.woff2"), 84)
    size = P.ICON_SIZE
    img = Image.new("RGB", (size, size), "#000000")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=28, fill=bg)
    d.text((size / 2, size / 2 - 14), chr(int(code, 16)), font=font, fill=fg, anchor="mm")
    out = io.BytesIO()
    img.save(out, "PNG")
    return store_image(out.getvalue())
