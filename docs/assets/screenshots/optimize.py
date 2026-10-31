from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import GifImagePlugin, Image, ImageOps

from bolivia_fire.animations import media_info


def write_navigation(frames: list[Image.Image], destination: Path) -> None:
    sample = Image.new("RGB", (160, 90 * len(frames)), "white")
    for index, frame in enumerate(frames):
        sample.paste(frame.resize((160, 90)), (0, 90 * index))
    palette = sample.quantize(colors=220)
    entries = (palette.getpalette() + [0] * 768)[:768]
    stops = np.array([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]])
    colors = [
        [round(float(np.interp(value, range(5), stops[:, channel]))) for channel in range(3)]
        for value in np.linspace(0, 4, 17)
    ]
    colors.extend([[162, 62, 146], [233, 216, 238], [174, 181, 182], [139, 74, 24]])
    for index, color in enumerate(colors, start=220):
        entries[index * 3 : index * 3 + 3] = color
    entries[254 * 3 :] = [255, 255, 255, 0, 0, 0]
    palette.putpalette(entries)
    quantized = []
    try:
        for frame in frames:
            padded = ImageOps.expand(frame, border=18, fill="white")
            image = padded.quantize(palette=palette, dither=Image.Dither.NONE)
            for bounds in (
                (0, 0, image.width, 18),
                (0, image.height - 18, image.width, image.height),
                (0, 0, 18, image.height),
                (image.width - 18, 0, image.width, image.height),
            ):
                image.paste(254, bounds)
            quantized.append(image)
            padded.close()
        header, _ = GifImagePlugin.getheader(quantized[0], info={"loop": 0, "optimize": False})
        with destination.open("wb") as stream:
            for chunk in header:
                stream.write(chunk)
            previous = None
            for index in range(61):
                frame = quantized[min(index // 15, 3)]
                values = np.asarray(frame)
                bounds = (0, 0, frame.width, frame.height)
                if previous is not None:
                    ys, xs = np.nonzero(values != previous)
                    bounds = (
                        (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
                        if len(xs)
                        else (0, 0, 1, 1)
                    )
                cropped = frame.crop(bounds)
                for chunk in GifImagePlugin.getdata(
                    cropped, offset=bounds[:2], duration=2500 if index == 60 else 50, disposal=1
                ):
                    stream.write(chunk)
                cropped.close()
                previous = values.copy()
            stream.write(b";")
    finally:
        sample.close()
        palette.close()
        for image in quantized:
            image.close()


def optimize(frames_directory: Path) -> dict[str, int]:
    destination = Path(__file__).resolve().parent
    for path in destination.glob("*.png"):
        with Image.open(path) as image:
            image.convert("RGB").quantize(colors=256, dither=Image.Dither.NONE).save(
                path, optimize=True
            )
    frame_paths = sorted(frames_directory.rglob("week-*.png"))
    if len(frame_paths) != 4 or len({path.name for path in frame_paths}) != 4:
        raise ValueError("Provide exactly four distinct weekly frames")
    frames = []
    try:
        for path in frame_paths:
            with Image.open(path) as image:
                if image.size != (1440, 900):
                    raise ValueError("Navigation frames must be 1440 by 900 pixels")
                frames.append(image.convert("RGB"))
        write_navigation(frames, destination / "week_navigation.gif")
    finally:
        for frame in frames:
            frame.close()
    manifest_path = destination.parent / "media.json"
    manifest = json.loads(manifest_path.read_bytes())
    sizes = {}
    for path in sorted(destination.iterdir()):
        if path.suffix not in {".png", ".gif"}:
            continue
        sizes[path.name] = path.stat().st_size
        maximum = 3_000_000 if path.suffix == ".gif" else 400_000
        if sizes[path.name] > maximum:
            raise ValueError(f"Media exceeds its size limit: {path.name}")
        manifest["media"][f"screenshots/{path.name}"] = media_info(path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return sizes


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Optimize browser media and update its inventory")
    parser.add_argument("--frames", type=Path, required=True)
    print(json.dumps(optimize(parser.parse_args().frames), indent=2))
