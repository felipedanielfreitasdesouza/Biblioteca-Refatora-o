from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


COLORS = {
    "before": (190, 65, 65),
    "after": (35, 120, 75),
    "grid": (220, 225, 230),
    "axis": (70, 75, 80),
    "text": (25, 30, 35),
}


def load_results(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {(
        item["funcao"],
        item["n"],
    ): item for item in data["resultados"]}


def font(size):
    return ImageFont.load_default(size=size)


def log_position(value, low, high, start, end):
    if high == low:
        return (start + end) / 2
    value = math.log10(max(value, 0.000001))
    low = math.log10(max(low, 0.000001))
    high = math.log10(max(high, 0.000001))
    return start + (value - low) / (high - low) * (end - start)


def wrap_title(draw, title, width, title_font):
    words = title.split("_")
    lines = []
    current = ""
    for word in words:
        candidate = f"{current}_{word}" if current else word
        if draw.textbbox((0, 0), candidate, font=title_font)[2] <= width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_panel(draw, box, name, before, after, metric, title_font, small_font):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=12, fill=(250, 251, 252), outline=(210, 215, 220), width=2)
    title_lines = wrap_title(draw, name, right - left - 24, title_font)
    for index, line in enumerate(title_lines[:2]):
        draw.text((left + 12, top + 10 + index * 16), line, fill=COLORS["text"], font=title_font)

    plot_left = left + 52
    plot_top = top + 48
    plot_right = right - 14
    plot_bottom = bottom - 42
    values = [item[metric] for item in before + after if item[metric] is not None and item[metric] > 0]
    if not values:
        return
    low = min(values)
    high = max(values)
    low = 10 ** math.floor(math.log10(low))
    high = 10 ** math.ceil(math.log10(high))
    if low == high:
        high = low * 10

    sizes = [item["n"] for item in before]
    x_low = min(sizes)
    x_high = max(sizes)
    for exponent in range(math.floor(math.log10(low)), math.ceil(math.log10(high)) + 1):
        value = 10 ** exponent
        y = log_position(value, low, high, plot_bottom, plot_top)
        draw.line((plot_left, y, plot_right, y), fill=COLORS["grid"], width=1)
        draw.text((left + 6, y - 6), f"{value:g}", fill=COLORS["axis"], font=small_font)

    for value in sizes:
        x = log_position(value, x_low, x_high, plot_left, plot_right)
        draw.line((x, plot_top, x, plot_bottom), fill=COLORS["grid"], width=1)
        draw.text((x - 10, plot_bottom + 8), str(value), fill=COLORS["axis"], font=small_font)

    draw.line((plot_left, plot_top, plot_left, plot_bottom), fill=COLORS["axis"], width=2)
    draw.line((plot_left, plot_bottom, plot_right, plot_bottom), fill=COLORS["axis"], width=2)

    for label, values_for_line, color in (
        ("antes", before, COLORS["before"]),
        ("depois", after, COLORS["after"]),
    ):
        points = []
        for item in values_for_line:
            value = item[metric]
            if value is None or value <= 0:
                continue
            x = log_position(item["n"], x_low, x_high, plot_left, plot_right)
            y = log_position(value, low, high, plot_bottom, plot_top)
            points.append((x, y))
        if len(points) > 1:
            draw.line(points, fill=color, width=3)
        for x, y in points:
            draw.ellipse((x - 5, y - 5, x + 5, y + 5), fill=color, outline=(255, 255, 255), width=1)


def create_chart(before_path, after_path, metric, output):
    before_map = load_results(before_path)
    after_map = load_results(after_path)
    names = list(dict.fromkeys(name for name, _ in before_map))
    sizes = sorted({size for _, size in before_map})
    before = {
        name: [before_map[(name, size)] for size in sizes]
        for name in names
    }
    after = {
        name: [after_map[(name, size)] for size in sizes]
        for name in names
    }
    image = Image.new("RGB", (1800, 1180), (240, 243, 246))
    draw = ImageDraw.Draw(image)
    title_font = font(18)
    small_font = font(13)
    draw.text(
        (40, 24),
        "Comparacao antes/depois - " + ("tempo medio (ms)" if metric == "tempo_medio_ms" else "consultas ao banco"),
        fill=COLORS["text"],
        font=font(28),
    )
    draw.line((40, 70, 1780, 70), fill=(190, 195, 200), width=2)
    panel_width = 420
    panel_height = 500
    for index, name in enumerate(names):
        column = index % 4
        row = index // 4
        left = 40 + column * 440
        top = 95 + row * 535
        draw_panel(
            draw,
            (left, top, left + panel_width, top + panel_height),
            name,
            before[name],
            after[name],
            metric,
            title_font,
            small_font,
        )
    draw.line((40, 1140, 60, 1140), fill=COLORS["before"], width=5)
    draw.text((68, 1133), "antes", fill=COLORS["text"], font=small_font)
    draw.line((150, 1140, 170, 1140), fill=COLORS["after"], width=5)
    draw.text((178, 1133), "depois", fill=COLORS["text"], font=small_font)
    image.save(output)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--output-prefix", type=Path, required=True)
    args = parser.parse_args()
    create_chart(args.before, args.after, "tempo_medio_ms", args.output_prefix.with_name(args.output_prefix.name + "_tempo.png"))
    create_chart(args.before, args.after, "consultas", args.output_prefix.with_name(args.output_prefix.name + "_consultas.png"))
    print(f"graficos salvos em {args.output_prefix.parent}")


if __name__ == "__main__":
    main()
