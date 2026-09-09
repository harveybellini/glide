"""Render the Glide architecture as editable SVG and PNG from one layout.

Run with ``uv run python scripts/export_architecture.py``. The layout below is
the editable source of truth; both outputs are regenerated from it so they
cannot drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 2340, 1080

COLORS = {
    "web": ("#e7edf5", "#243b53"),
    "compute": ("#eef2ff", "#3730a3"),
    "data": ("#ecfdf5", "#065f46"),
    "aws": ("#fef3c7", "#92400e"),
    "provider": ("#fdf2f8", "#9d174d"),
    "sample": ("#f1f5f9", "#475569"),
    "edge": "#64748b",
}


@dataclass(frozen=True)
class Box:
    key: str
    x: int
    y: int
    w: int
    h: int
    title: str
    lines: tuple[str, ...]
    palette: str

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def cy(self) -> int:
        return self.y + self.h // 2


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str = ""
    path: tuple[tuple[int, int], ...] = ()


BOXES = [
    Box("browser", 40, 470, 220, 110, "Browser", ("user or judge",), "web"),
    Box(
        "googleoauth",
        40,
        210,
        220,
        110,
        "Google sign-in",
        ("PKCE + state", "single-use callback"),
        "provider",
    ),
    Box(
        "cloudfront",
        340,
        470,
        230,
        110,
        "CloudFront",
        ("HTTPS edge", "no /api caching"),
        "aws",
    ),
    Box("s3", 340, 670, 230, 110, "S3 (private)", ("React build", "OAC only"), "aws"),
    Box("httpapi", 650, 470, 250, 110, "API Gateway", ("HTTP API", "/api/*"), "aws"),
    Box(
        "apilambda",
        980,
        470,
        270,
        130,
        "API Lambda",
        ("sample + Google identity", "auth, enqueue, reads"),
        "compute",
    ),
    Box(
        "sqs",
        1330,
        470,
        250,
        110,
        "SQS FIFO",
        ("one group per user", "DLQ after 3 receives"),
        "aws",
    ),
    Box(
        "worker",
        1660,
        470,
        300,
        130,
        "Worker Lambda",
        ("run processor", "plan, validate, reconcile"),
        "compute",
    ),
    Box(
        "eventbridge",
        1330,
        210,
        250,
        110,
        "EventBridge",
        ("rate(5 minutes)",),
        "aws",
    ),
    Box(
        "dispatcher",
        1660,
        210,
        300,
        110,
        "Dispatcher Lambda",
        ("enqueue enabled tenants",),
        "compute",
    ),
    Box(
        "dynamo",
        980,
        720,
        270,
        130,
        "DynamoDB",
        ("single table", "runs, plans, decisions", "receipts, blocks, snapshots"),
        "data",
    ),
    Box(
        "secrets",
        650,
        720,
        250,
        130,
        "Secrets Manager",
        ("session key", "per-user OAuth tokens", "refresh write-back"),
        "aws",
    ),
    Box(
        "sample",
        1660,
        850,
        300,
        130,
        "Sample mode",
        ("synthetic calendar + routes", "isolated, durable snapshots"),
        "sample",
    ),
    Box(
        "bedrock",
        2050,
        310,
        250,
        110,
        "Amazon Bedrock",
        ("Strands agent loop", "bounded turns + deadline"),
        "provider",
    ),
    Box(
        "location",
        2050,
        490,
        250,
        110,
        "Amazon Location",
        ("Places + Routes V2", "typed no-route errors"),
        "provider",
    ),
    Box(
        "google",
        2050,
        670,
        250,
        110,
        "Google Calendar",
        ("read source", "write Glide Travel"),
        "provider",
    ),
]

EDGES = [
    Edge("browser", "cloudfront", path=((260, 525), (340, 525))),
    Edge("browser", "googleoauth", path=((150, 470), (150, 320))),
    Edge(
        "googleoauth",
        "apilambda",
        "OAuth",
        ((260, 265), (260, 150), (1115, 150), (1115, 470)),
    ),
    Edge("cloudfront", "s3", path=((455, 580), (455, 670))),
    Edge("cloudfront", "httpapi", "/api/*", ((570, 525), (650, 525))),
    Edge("httpapi", "apilambda", path=((900, 525), (980, 535))),
    Edge("apilambda", "dynamo", "state", ((1115, 600), (1115, 720))),
    Edge("apilambda", "sqs", "enqueue", ((1250, 535), (1330, 525))),
    Edge(
        "apilambda",
        "secrets",
        "tokens",
        path=((1115, 600), (1115, 660), (900, 660), (900, 720)),
    ),
    Edge(
        "apilambda",
        "google",
        "live day reads",
        ((1250, 565), (1320, 565), (1320, 760), (2050, 760)),
    ),
    Edge("eventbridge", "dispatcher", path=((1580, 265), (1660, 265))),
    Edge(
        "dispatcher",
        "sqs",
        path=((1810, 320), (1810, 390), (1455, 390), (1455, 470)),
    ),
    Edge("sqs", "worker", "drain", ((1580, 525), (1660, 535))),
    Edge(
        "worker",
        "bedrock",
        path=((1960, 535), (2025, 535), (2025, 365), (2050, 365)),
    ),
    Edge(
        "worker",
        "location",
        path=((1960, 535), (2025, 535), (2025, 545), (2050, 545)),
    ),
    Edge(
        "worker",
        "google",
        path=((1960, 535), (2025, 535), (2025, 725), (2050, 725)),
    ),
    Edge(
        "worker",
        "dynamo",
        path=((1810, 600), (1810, 700), (1250, 700), (1250, 720)),
    ),
    Edge(
        "worker",
        "secrets",
        path=((1810, 600), (1810, 660), (900, 660), (900, 720)),
    ),
    Edge(
        "apilambda",
        "sample",
        "synthetic providers",
        ((980, 535), (960, 535), (960, 915), (1660, 915)),
    ),
    Edge("worker", "sample", "sample runs", ((1810, 600), (1810, 850))),
]


class Canvas(Protocol):
    def box(self, box: Box) -> None: ...

    def edge(self, points: list[tuple[int, int]], label: str) -> None: ...


class SvgCanvas:
    def __init__(self) -> None:
        self.parts: list[str] = []

    def box(self, box: Box) -> None:
        fill, outline = COLORS[box.palette]
        self.parts.append(
            f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}" '
            f'rx="14" fill="{fill}" stroke="{outline}" stroke-width="2"/>'
        )
        lines = [box.title, *box.lines]
        base = box.cy - (len(lines) - 1) * 15
        for index, line in enumerate(lines):
            self.parts.append(
                f'<text x="{box.cx}" y="{base + index * 30}" text-anchor="middle" '
                f'font-family="Segoe UI, sans-serif" '
                f'font-size="{24 if index == 0 else 19}" '
                f'fill="{outline}">{line}</text>'
            )

    def edge(self, points: list[tuple[int, int]], label: str) -> None:
        path = " ".join(f"{x},{y}" for x, y in points)
        self.parts.append(
            f'<polyline points="{path}" fill="none" stroke="{COLORS["edge"]}" '
            'stroke-width="2.5" marker-end="url(#arrow)"/>'
        )
        if label:
            x, y = points[len(points) // 2]
            self.parts.append(
                f'<text x="{x}" y="{y - 8}" text-anchor="middle" '
                f'font-family="Segoe UI, sans-serif" font-size="17" '
                f'fill="{COLORS["edge"]}">{label}</text>'
            )

    def render(self) -> str:
        marker = (
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" '
            'refX="9" refY="3" orient="auto"><path d="M0,0 L0,6 L9,3 z" '
            f'fill="{COLORS["edge"]}"/></marker></defs>'
        )
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
            f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
            f'font-family="Segoe UI, sans-serif">{marker}'
            + "".join(self.parts)
            + "</svg>"
        )


class PilCanvas:
    def __init__(self) -> None:
        self.image = Image.new("RGB", (WIDTH, HEIGHT), "#f8fafc")
        self.draw = ImageDraw.Draw(self.image)
        self.fonts = self._load_fonts()

    @staticmethod
    def _load_fonts() -> dict[tuple[int, bool], ImageFont.FreeTypeFont]:
        fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}
        candidates = [
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/arial.ttf"),
        ]
        for size in (16, 19, 24):
            for bold in (True, False):
                path = candidates[0] if bold else candidates[1]
                try:
                    fonts[(size, bold)] = ImageFont.truetype(str(path), size)
                except OSError:
                    fonts[(size, bold)] = ImageFont.load_default()
        return fonts

    def box(self, box: Box) -> None:
        fill, outline = COLORS[box.palette]
        self.draw.rounded_rectangle(
            [box.x, box.y, box.x + box.w, box.y + box.h],
            radius=14,
            fill=fill,
            outline=outline,
            width=2,
        )
        lines = [box.title, *box.lines]
        base = box.cy - (len(lines) - 1) * 15
        for index, line in enumerate(lines):
            font = self.fonts[(24 if index == 0 else 19, index == 0)]
            self.draw.text(
                (box.cx, base + index * 30),
                line,
                font=font,
                fill=outline,
                anchor="mm",
            )

    def edge(self, points: list[tuple[int, int]], label: str) -> None:
        self.draw.line(points, fill=COLORS["edge"], width=3, joint="curve")
        end = points[-1]
        start = points[-2]
        import math

        angle = math.atan2(end[1] - start[1], end[0] - start[0])
        for delta in (math.radians(150), math.radians(210)):
            tip = (
                end[0] + 12 * math.cos(angle + delta),
                end[1] + 12 * math.sin(angle + delta),
            )
            self.draw.line([end, tip], fill=COLORS["edge"], width=3)
        if label:
            x, y = points[len(points) // 2]
            self.draw.text(
                (x, y - 8),
                label,
                font=self.fonts[(16, False)],
                fill=COLORS["edge"],
                anchor="mm",
            )

    def save(self, path: Path) -> None:
        self.image.save(path)


def render(canvas: Canvas) -> None:
    for box in BOXES:
        canvas.box(box)
    for edge in EDGES:
        canvas.edge(list(edge.path), edge.label)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    docs = root / "docs"

    svg = SvgCanvas()
    render(svg)
    (docs / "architecture.svg").write_text(svg.render(), encoding="utf-8")

    png = PilCanvas()
    render(png)
    png.save(docs / "architecture.png")

    print(f"wrote {docs / 'architecture.svg'}")
    print(f"wrote {docs / 'architecture.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
