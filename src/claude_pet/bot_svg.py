"""SVG generator for the Claude desktop pet — dark capsule with halo + chevron eyes."""

import math
from functools import lru_cache

SIZE = 256

EMOTIONS = {
    "idle":      {"eye": "chevron_up", "color": "#3FA3FF", "halo": 0.85, "led": "#3FA3FF"},
    "thinking":  {"eye": "spin",       "color": "#A78BFA", "halo": 0.80, "led": "#A78BFA"},
    "reading":   {"eye": "scan",       "color": "#22D3EE", "halo": 0.85, "led": "#22D3EE"},
    "writing":   {"eye": "sparkle",    "color": "#60A5FA", "halo": 0.85, "led": "#60A5FA"},
    "running":   {"eye": "chevron_dn", "color": "#4ADE80", "halo": 0.85, "led": "#4ADE80"},
    "working":   {"eye": "ring",       "color": "#38BDF8", "halo": 0.85, "led": "#38BDF8"},
    "success":   {"eye": "chevron_up", "color": "#4ADE80", "halo": 1.00, "led": "#4ADE80"},
    "error":     {"eye": "cross",      "color": "#F87171", "halo": 0.95, "led": "#F87171"},
    "curious":   {"eye": "pixel_circle","color": "#3B82F6", "halo": 0.85, "led": "#3B82F6"},
    "sleeping":  {"eye": "closed",     "color": "#94A3B8", "halo": 0.45, "led": "#475569"},
    "proud":     {"eye": "star",       "color": "#60A5FA", "halo": 1.00, "led": "#3FA3FF"},
}


TIER_BADGE = {
    "hatchling":  ("🥚", "#3FA3FF"),
    "apprentice": ("🐣", "#22D3EE"),
    "senior":     ("🦉", "#60A5FA"),
    "master":   ("🦄", "#F97316"),
}


def make_svg(state, frame=0, time_str=None, override_eye=None, tier=None):
    cfg = EMOTIONS.get(state, EMOTIONS["idle"])
    color = cfg["color"]
    halo_alpha = cfg["halo"]
    eye_kind = override_eye or cfg["eye"]

    breath = 1.0 + 0.018 * math.sin(frame * math.pi / 9)
    body_w = 204 * breath
    body_h = 144 * breath
    body_x = (SIZE - body_w) / 2
    body_y = (SIZE - body_h) / 2 + 4
    rx = min(body_w, body_h) / 2

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {SIZE} {SIZE}" '
        f'width="{SIZE}" height="{SIZE}">'
    ]
    parts.append(_defs(color))

    halo_pad = 16
    parts.append(
        f'<rect x="{body_x - halo_pad}" y="{body_y - halo_pad}" '
        f'width="{body_w + 2*halo_pad}" height="{body_h + 2*halo_pad}" '
        f'rx="{rx + halo_pad}" ry="{rx + halo_pad}" '
        f'fill="white" opacity="{halo_alpha * 0.55:.2f}" filter="url(#haloblur)"/>'
    )
    parts.append(
        f'<rect x="{body_x - 7}" y="{body_y - 7}" '
        f'width="{body_w + 14}" height="{body_h + 14}" '
        f'rx="{rx + 7}" ry="{rx + 7}" '
        f'fill="none" stroke="white" stroke-width="5" '
        f'opacity="{halo_alpha * 0.95:.2f}"/>'
    )
    parts.append(
        f'<rect x="{body_x}" y="{body_y}" width="{body_w}" height="{body_h}" '
        f'rx="{rx}" ry="{rx}" fill="url(#body)"/>'
    )
    parts.append(
        f'<rect x="{body_x}" y="{body_y}" width="{body_w}" height="{body_h}" '
        f'rx="{rx}" ry="{rx}" fill="url(#bodyRim)" opacity="0.85"/>'
    )
    parts.append(_boomerang(body_x, body_y, body_w, body_h))
    parts.append(
        f'<ellipse cx="{body_x + body_w*0.22}" cy="{body_y + body_h*0.72}" '
        f'rx="{body_w*0.08}" ry="{body_h*0.05}" '
        f'fill="white" opacity="0.18" transform="rotate(-18 '
        f'{body_x + body_w*0.22} {body_y + body_h*0.72})"/>'
    )

    eye_y = body_y + body_h * 0.52
    eye_l = (body_x + body_w * 0.34, eye_y)
    eye_r = (body_x + body_w * 0.66, eye_y)
    parts.append(_render_eye_pair(eye_kind, eye_l, eye_r, frame, color, time_str=time_str))

    # Optional tier badge — tiny colored dot in the top-right of the halo.
    if tier and tier in TIER_BADGE:
        _, tcol = TIER_BADGE[tier]
        bx2 = body_x + body_w - 4
        by2 = body_y - 8
        parts.append(
            f'<circle cx="{bx2:.1f}" cy="{by2:.1f}" r="7" '
            f'fill="{tcol}" stroke="white" stroke-width="2" opacity="0.95"/>'
        )
        # Level pips: one dot per tier (1..4).
        tier_index = {"hatchling": 1, "apprentice": 2, "senior": 3, "master": 4}.get(tier, 1)
        for i in range(tier_index):
            parts.append(
                f'<circle cx="{bx2 - 3 + i*3:.1f}" cy="{by2:.1f}" r="1.2" fill="white"/>'
            )

    parts.append('</svg>')
    return "".join(parts)


def _defs(color):
    return (
        '<defs>'
        '<radialGradient id="body" cx="38%" cy="28%" r="85%">'
        '<stop offset="0%" stop-color="#2C2D4A"/>'
        '<stop offset="45%" stop-color="#0E0F22"/>'
        '<stop offset="100%" stop-color="#040410"/>'
        '</radialGradient>'
        '<linearGradient id="bodyRim" x1="0%" y1="0%" x2="0%" y2="100%">'
        '<stop offset="0%" stop-color="black" stop-opacity="0"/>'
        '<stop offset="70%" stop-color="black" stop-opacity="0"/>'
        '<stop offset="100%" stop-color="black" stop-opacity="0.55"/>'
        '</linearGradient>'
        '<pattern id="eyegrid" width="7" height="7" patternUnits="userSpaceOnUse">'
        f'<rect width="7" height="7" fill="{color}"/>'
        '<path d="M 0 0 L 7 0 M 0 0 L 0 7" '
        'stroke="#0A0A1F" stroke-width="0.8" opacity="0.35"/>'
        '</pattern>'
        '<filter id="haloblur" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="11"/></filter>'
        '<filter id="softglow" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur stdDeviation="2"/></filter>'
        '</defs>'
    )


def _boomerang(bx, by, bw, bh):
    p1 = (bx + bw * 0.30, by + bh * 0.18)
    c1 = (bx + bw * 0.42, by + bh * 0.04)
    p2 = (bx + bw * 0.78, by + bh * 0.17)
    c2 = (bx + bw * 0.94, by + bh * 0.30)
    p3 = (bx + bw * 0.88, by + bh * 0.62)
    c3 = (bx + bw * 0.76, by + bh * 0.48)
    p4 = (bx + bw * 0.74, by + bh * 0.34)
    c4 = (bx + bw * 0.50, by + bh * 0.24)
    p5 = (bx + bw * 0.34, by + bh * 0.32)
    c5 = (bx + bw * 0.28, by + bh * 0.26)
    d = (
        f"M {p1[0]:.1f} {p1[1]:.1f} "
        f"Q {c1[0]:.1f} {c1[1]:.1f}, {p2[0]:.1f} {p2[1]:.1f} "
        f"Q {c2[0]:.1f} {c2[1]:.1f}, {p3[0]:.1f} {p3[1]:.1f} "
        f"Q {c3[0]:.1f} {c3[1]:.1f}, {p4[0]:.1f} {p4[1]:.1f} "
        f"Q {c4[0]:.1f} {c4[1]:.1f}, {p5[0]:.1f} {p5[1]:.1f} "
        f"Q {c5[0]:.1f} {c5[1]:.1f}, {p1[0]:.1f} {p1[1]:.1f} Z"
    )
    return (
        f'<path d="{d}" fill="white" opacity="0.62" filter="url(#softglow)"/>'
        f'<path d="M {p1[0]:.1f} {p1[1]+2:.1f} '
        f'Q {c1[0]:.1f} {c1[1]+1:.1f}, {p2[0]:.1f} {p2[1]+2:.1f}" '
        f'stroke="white" stroke-width="1.5" fill="none" opacity="0.75"/>'
    )


def _render_eye_pair(kind, left, right, frame, color, time_str=None):
    lx, ly = left
    rx, ry = right
    out = []
    ink = "#0A0A1F"
    if kind == "chevron_up":
        for (cx, cy) in (left, right):
            out.append(_chevron(cx, cy, "up"))
    elif kind == "chevron_dn":
        for (cx, cy) in (left, right):
            out.append(_chevron(cx, cy, "down"))
    elif kind == "spin":
        ang = (frame * 28) % 360
        for (cx, cy), sign in ((left, 1), (right, -1)):
            out.append(
                f'<g transform="translate({cx},{cy}) rotate({ang*sign})">'
                f'<circle r="15" fill="{color}" opacity="0.25"/>'
                f'<path d="M 0 -15 A 15 15 0 0 1 15 0" '
                f'stroke="{color}" stroke-width="5" fill="none" stroke-linecap="round"/>'
                '</g>'
            )
    elif kind == "scan":
        off = 7 * math.sin(frame * math.pi / 8)
        for (cx, cy) in (left, right):
            out.append(
                f'<rect x="{cx-16}" y="{cy-4}" width="32" height="8" rx="4" '
                f'fill="{color}" opacity="0.35"/>'
            )
            out.append(
                f'<rect x="{cx-8+off:.1f}" y="{cy-4}" width="16" height="8" '
                f'rx="4" fill="{color}"/>'
            )
    elif kind == "sparkle":
        scale = 1.0 + 0.32 * math.sin(frame * math.pi / 4)
        for (cx, cy) in (left, right):
            s = 15 * scale
            out.append(
                f'<path d="M {cx} {cy-s:.1f} L {cx+4} {cy-4} L {cx+s:.1f} {cy} '
                f'L {cx+4} {cy+4} L {cx} {cy+s:.1f} L {cx-4} {cy+4} '
                f'L {cx-s:.1f} {cy} L {cx-4} {cy-4} Z" fill="{color}"/>'
            )
    elif kind == "ring":
        pulse = 1.0 + 0.18 * math.sin(frame * math.pi / 5)
        for (cx, cy) in (left, right):
            out.append(
                f'<circle cx="{cx}" cy="{cy}" r="{15*pulse:.1f}" '
                f'fill="none" stroke="{color}" stroke-width="5"/>'
            )
            out.append(f'<circle cx="{cx}" cy="{cy}" r="5" fill="{color}"/>')
    elif kind == "cross":
        shake = 1.2 * math.sin(frame * math.pi / 2)
        for (cx, cy) in (left, right):
            cxs = cx + shake
            out.append(
                f'<line x1="{cxs-12:.1f}" y1="{cy-12}" x2="{cxs+12:.1f}" y2="{cy+12}" '
                f'stroke="{color}" stroke-width="6" stroke-linecap="round"/>'
            )
            out.append(
                f'<line x1="{cxs-12:.1f}" y1="{cy+12}" x2="{cxs+12:.1f}" y2="{cy-12}" '
                f'stroke="{color}" stroke-width="6" stroke-linecap="round"/>'
            )
    elif kind == "pixel_circle":
        pulse = 1.0 + 0.06 * math.sin(frame * math.pi / 6)
        r = 19 * pulse
        for (cx, cy) in (left, right):
            out.append(
                f'<circle cx="{cx}" cy="{cy}" r="{r:.1f}" '
                f'fill="url(#eyegrid)" stroke="#0A0A1F" stroke-width="0.6" '
                f'stroke-opacity="0.4"/>'
            )
    elif kind == "closed":
        for (cx, cy) in (left, right):
            out.append(
                f'<path d="M {cx-14} {cy} Q {cx} {cy+8} {cx+14} {cy}" '
                f'stroke="{color}" stroke-width="5" fill="none" stroke-linecap="round"/>'
            )
        zalpha = 0.45 + 0.35 * (0.5 + 0.5 * math.sin(frame * math.pi / 8))
        zoff = (frame % 18) * 0.7
        out.append(
            f'<text x="{rx+28}" y="{ry-14-zoff:.1f}" font-family="Helvetica" '
            f'font-size="18" font-weight="bold" fill="{color}" '
            f'opacity="{zalpha:.2f}">z</text>'
        )
        out.append(
            f'<text x="{rx+40}" y="{ry-30-zoff:.1f}" font-family="Helvetica" '
            f'font-size="24" font-weight="bold" fill="{color}" '
            f'opacity="{zalpha:.2f}">Z</text>'
        )
    elif kind == "star":
        scale = 1.0 + 0.18 * math.sin(frame * math.pi / 5)
        for (cx, cy) in (left, right):
            out.append(_star(cx, cy, 16 * scale, 6.8 * scale, 5, color))
    elif kind == "clock":
        time_text = time_str or "--:--"
        cx = (left[0] + right[0]) / 2
        cy = (left[1] + right[1]) / 2 + 8
        hh, _, mm = time_text.partition(":")
        colon_alpha = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(frame * math.pi / 6))
        font = "Menlo,Monaco,Consolas,monospace"
        out.append(
            f'<text x="{cx - 8:.1f}" y="{cy:.1f}" font-family="{font}" '
            f'font-size="34" font-weight="bold" text-anchor="end" '
            f'fill="url(#eyegrid)" stroke="#0A0A1F" stroke-width="0.5">{hh}</text>'
        )
        out.append(
            f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="{font}" '
            f'font-size="34" font-weight="bold" text-anchor="middle" '
            f'fill="{color}" opacity="{colon_alpha:.2f}">:</text>'
        )
        out.append(
            f'<text x="{cx + 8:.1f}" y="{cy:.1f}" font-family="{font}" '
            f'font-size="34" font-weight="bold" text-anchor="start" '
            f'fill="url(#eyegrid)" stroke="#0A0A1F" stroke-width="0.5">{mm}</text>'
        )
    return "".join(out)


def _chevron(cx, cy, direction="up"):
    w = 26
    h = 18
    t = 11
    if direction == "up":
        outer_path = (
            f"M {cx - w:.1f} {cy + h:.1f} "
            f"L {cx:.1f} {cy - h:.1f} "
            f"L {cx + w:.1f} {cy + h:.1f} "
            f"L {cx + w - t * 0.8:.1f} {cy + h:.1f} "
            f"L {cx:.1f} {cy - h + t * 1.3:.1f} "
            f"L {cx - w + t * 0.8:.1f} {cy + h:.1f} Z"
        )
    else:
        outer_path = (
            f"M {cx - w:.1f} {cy - h:.1f} "
            f"L {cx:.1f} {cy + h:.1f} "
            f"L {cx + w:.1f} {cy - h:.1f} "
            f"L {cx + w - t * 0.8:.1f} {cy - h:.1f} "
            f"L {cx:.1f} {cy + h - t * 1.3:.1f} "
            f"L {cx - w + t * 0.8:.1f} {cy - h:.1f} Z"
        )
    return (
        f'<path d="{outer_path}" fill="url(#eyegrid)" '
        f'stroke="#0A0A1F" stroke-width="0.6" stroke-opacity="0.4" '
        f'stroke-linejoin="miter"/>'
    )


def _star(cx, cy, ro, ri, points, color):
    pts = []
    for i in range(points * 2):
        r = ro if i % 2 == 0 else ri
        ang = -math.pi / 2 + i * math.pi / points
        pts.append(f"{cx + r*math.cos(ang):.1f},{cy + r*math.sin(ang):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="{color}"/>'


@lru_cache(maxsize=512)
def make_svg_cached(state, frame_mod):
    return make_svg(state, frame_mod)


ANIM_CYCLE = 48
