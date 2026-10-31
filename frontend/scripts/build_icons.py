import argparse
import json
import math
from pathlib import Path
from statistics import median

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps


def load_rgb(path: Path) -> Image.Image:
    with Image.open(path) as source:
        rgba = source.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    return Image.alpha_composite(background, rgba).convert("RGB")


def sample_green(symbol: Image.Image) -> tuple[int, int, int]:
    border = [
        symbol.getpixel(point)
        for x in range(symbol.width)
        for point in ((x, 0), (x, symbol.height - 1))
    ] + [
        symbol.getpixel(point)
        for y in range(symbol.height)
        for point in ((0, y), (symbol.width - 1, y))
    ]
    return tuple(int(median(pixel[channel] for pixel in border)) for channel in range(3))


def symbol_mask(symbol: Image.Image) -> Image.Image:
    red, green, blue = symbol.split()
    mask = ImageChops.darker(ImageChops.darker(red, green), blue).point(
        lambda value: max(0, min(255, round((value - 150) * 255 / 100)))
    )
    bounds = mask.getbbox()
    if bounds is None:
        raise ValueError("The symbol source must contain a white symbol on green.")
    return mask.crop(bounds)


def make_icon(
    mask: Image.Image,
    green: tuple[int, int, int],
    size: int,
    *,
    maskable: bool = False,
) -> Image.Image:
    if maskable:
        radius = max(
            math.hypot(x + 0.5 - mask.width / 2, y + 0.5 - mask.height / 2)
            for y in range(mask.height)
            for x in range(mask.width)
            if mask.getpixel((x, y))
        )
        # Keep the entire symbol inside the central safe circle, including filter edges.
        scale = size * 0.38 / radius
    else:
        scale = size * 0.88 / max(mask.size)
    resized = mask.resize(
        (round(mask.width * scale), round(mask.height * scale)),
        Image.Resampling.LANCZOS,
    )
    icon = Image.new("RGB", (size, size), green)
    icon.paste(
        "white",
        ((size - resized.width) // 2, (size - resized.height) // 2),
        resized,
    )
    return icon


def make_preview(
    logotype: Image.Image,
    green: tuple[int, int, int],
    font_path: Path,
    bold_font_path: Path,
) -> Image.Image:
    red, source_green, _ = logotype.split()
    mask = ImageChops.subtract(source_green, red).point(
        lambda value: max(0, min(255, round((value - 10) * 255 / 70)))
    )
    bounds = mask.getbbox()
    if bounds is None:
        raise ValueError("The logotype source must contain a green logotype on white.")
    mask = ImageOps.contain(mask.crop(bounds), (720, 220), Image.Resampling.LANCZOS)
    preview = Image.new("RGB", (1200, 630), "white")
    preview.paste(green, ((1200 - mask.width) // 2, 115), mask)
    draw = ImageDraw.Draw(preview)
    draw.text(
        (600, 382),
        "Bolivia Fire",
        font=ImageFont.truetype(str(bold_font_path), 64),
        fill=green,
        anchor="mt",
    )
    draw.text(
        (600, 477),
        "Weekly satellite fire detections in Bolivia",
        font=ImageFont.truetype(str(font_path), 30),
        fill="#3d5145",
        anchor="mt",
    )
    return preview


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the site icons and social preview.")
    parser.add_argument("--symbol", required=True, type=Path)
    parser.add_argument("--logotype", required=True, type=Path)
    parser.add_argument("--font", required=True, type=Path)
    parser.add_argument("--font-bold", required=True, type=Path)
    parser.add_argument(
        "--output", type=Path, default=Path(__file__).resolve().parents[1] / "public"
    )
    args = parser.parse_args()
    symbol = load_rgb(args.symbol)
    green = sample_green(symbol)
    mask = symbol_mask(symbol)
    theme = "#{:02x}{:02x}{:02x}".format(*green)
    args.output.mkdir(parents=True, exist_ok=True)
    small_icons = {size: make_icon(mask, green, size) for size in (16, 32, 48)}
    small_icons[48].save(
        args.output / "favicon.ico",
        sizes=[(16, 16), (32, 32), (48, 48)],
        append_images=[small_icons[16], small_icons[32]],
    )
    small_icons[32].save(args.output / "favicon-32.png", optimize=True)
    make_icon(mask, green, 180).save(args.output / "apple-touch-icon.png", optimize=True)
    for size in (192, 512):
        make_icon(mask, green, size, maskable=True).save(
            args.output / f"icon-{size}.png", optimize=True
        )
    manifest = {
        "name": "Bolivia Fire",
        "short_name": "Bolivia Fire",
        "start_url": "./",
        "scope": "./",
        "theme_color": theme,
        "background_color": "#ffffff",
        "display": "standalone",
        "icons": [
            {
                "src": f"icon-{size}.png",
                "sizes": f"{size}x{size}",
                "type": "image/png",
                "purpose": "any maskable",
            }
            for size in (192, 512)
        ],
    }
    (args.output / "site.webmanifest").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    preview = make_preview(load_rgb(args.logotype), green, args.font, args.font_bold)
    preview.save(args.output / "og-image.png", optimize=True)
    if (args.output / "og-image.png").stat().st_size >= 300_000:
        raise ValueError("The social preview exceeds the 300 KB size limit.")
    print(f"Brand theme: {theme}")
    for filename in (
        "favicon.ico",
        "favicon-32.png",
        "apple-touch-icon.png",
        "icon-192.png",
        "icon-512.png",
        "site.webmanifest",
        "og-image.png",
    ):
        print(f"{filename}: {(args.output / filename).stat().st_size} bytes")


if __name__ == "__main__":
    main()
