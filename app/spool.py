"""Genereert de SVG-spoel: filament in de juiste kleur, wikkeldikte op basis van percentage resterend."""

from __future__ import annotations

import math

from markupsafe import Markup, escape

VIEW = 120
CX = CY = 60
R_FLANGE = 57  # buitenrand van de spoel
R_MAX = 51  # maximale straal van het filament (volle spoel)
R_HUB = 21  # kern van de spoel
R_HOLE = 9  # asgat


def _clamp(v: float, lo: float = 0, hi: float = 255) -> int:
    return int(max(lo, min(hi, round(v))))


def shade(r: int, g: int, b: int, factor: float) -> str:
    """factor < 1 = donkerder, > 1 = lichter (mengt richting wit)."""
    if factor <= 1:
        return f"rgb({_clamp(r * factor)},{_clamp(g * factor)},{_clamp(b * factor)})"
    t = min(factor - 1, 1)
    return f"rgb({_clamp(r + (255 - r) * t)},{_clamp(g + (255 - g) * t)},{_clamp(b + (255 - b) * t)})"


def filament_radius(percent: float) -> float:
    pct = max(0.0, min(100.0, percent)) / 100
    if pct <= 0:
        return R_HUB
    # Altijd een zichtbaar laagje tonen zolang er nog filament is
    return max(R_HUB + 1.6, R_HUB + (R_MAX - R_HUB) * pct)


def spool_svg(
    r: int,
    g: int,
    b: int,
    transparent: bool = False,
    percent: float = 100.0,
    uid: str | int = "x",
    title: str | None = None,
    css_class: str = "spool",
) -> Markup:
    uid = f"sp{escape(str(uid))}"
    fill = f"rgb({r},{g},{b})"
    edge = shade(r, g, b, 0.62)
    line = shade(r, g, b, 0.72)
    light = shade(r, g, b, 1.55)
    outer = filament_radius(percent)
    fill_opacity = 0.55 if transparent else 1.0

    parts: list[str] = [
        f'<svg class="{escape(css_class)}" viewBox="0 0 {VIEW} {VIEW}" xmlns="http://www.w3.org/2000/svg" '
        f'role="img" aria-label="{escape(title or "Filamentspoel")}">',
    ]
    if title:
        parts.append(f"<title>{escape(title)}</title>")

    parts.append("<defs>")
    # Ronde schaduw/glans voor 3D-effect
    parts.append(
        f'<radialGradient id="{uid}-shine" cx="38%" cy="32%" r="75%">'
        '<stop offset="0%" stop-color="#fff" stop-opacity="0.45"/>'
        '<stop offset="45%" stop-color="#fff" stop-opacity="0.08"/>'
        '<stop offset="100%" stop-color="#000" stop-opacity="0.28"/>'
        "</radialGradient>"
    )
    if transparent:
        parts.append(
            f'<pattern id="{uid}-stripes" width="7" height="7" patternUnits="userSpaceOnUse" '
            'patternTransform="rotate(35)">'
            f'<rect width="2.2" height="7" fill="{light}" opacity="0.55"/>'
            "</pattern>"
        )
    parts.append(
        f'<clipPath id="{uid}-clip"><circle cx="{CX}" cy="{CY}" r="{outer:.2f}"/></clipPath>'
    )
    # Kruisjespatroon voor het lege deel van de spoel
    parts.append(
        f'<pattern id="{uid}-cross" width="5" height="5" patternUnits="userSpaceOnUse">'
        '<path class="spool-cross" d="M1.6 2.5h1.8M2.5 1.6v1.8"/>'
        "</pattern>"
    )
    parts.append("</defs>")

    # Spoel: buitenflens en (lege) binnenruimte
    parts.append(f'<circle class="spool-flange" cx="{CX}" cy="{CY}" r="{R_FLANGE}"/>')
    parts.append(f'<circle class="spool-inner" cx="{CX}" cy="{CY}" r="{R_MAX + 0.8}"/>')
    parts.append(f'<circle cx="{CX}" cy="{CY}" r="{R_MAX + 0.8}" fill="url(#{uid}-cross)"/>')

    # Filament
    if outer > R_HUB:
        parts.append(f'<g clip-path="url(#{uid}-clip)">')
        parts.append(
            f'<circle cx="{CX}" cy="{CY}" r="{outer:.2f}" fill="{fill}" fill-opacity="{fill_opacity}"/>'
        )
        # Wikkelingen: concentrische lijnen
        radius = R_HUB + 2.2
        while radius < outer - 0.5:
            parts.append(
                f'<circle cx="{CX}" cy="{CY}" r="{radius:.2f}" fill="none" stroke="{line}" '
                f'stroke-width="0.55" stroke-opacity="{0.35 if not transparent else 0.5}"/>'
            )
            radius += 2.2
        if transparent:
            parts.append(f'<circle cx="{CX}" cy="{CY}" r="{outer:.2f}" fill="url(#{uid}-stripes)"/>')
        parts.append(f'<circle cx="{CX}" cy="{CY}" r="{outer:.2f}" fill="url(#{uid}-shine)"/>')
        if transparent:
            # Glanslijn die "doorzichtig" suggereert
            parts.append(
                f'<path d="M {CX - outer * 0.62:.2f} {CY - outer * 0.42:.2f} '
                f'A {outer * 0.8:.2f} {outer * 0.8:.2f} 0 0 1 {CX + outer * 0.1:.2f} {CY - outer * 0.74:.2f}" '
                'fill="none" stroke="#fff" stroke-opacity="0.75" stroke-width="2.4" stroke-linecap="round"/>'
            )
        parts.append("</g>")
        parts.append(
            f'<circle cx="{CX}" cy="{CY}" r="{outer:.2f}" fill="none" stroke="{edge}" stroke-width="1"/>'
        )
        # Lichte rand zodat ook zwart filament zich aftekent tegen de spoel
        parts.append(f'<circle class="spool-glow" cx="{CX}" cy="{CY}" r="{outer + 0.9:.2f}"/>')

    # Kern en asgat
    parts.append(f'<circle class="spool-hub" cx="{CX}" cy="{CY}" r="{R_HUB}"/>')
    for i in range(3):
        a = math.radians(90 + i * 120)
        hx, hy = CX + math.cos(a) * 14.5, CY + math.sin(a) * 14.5
        parts.append(f'<circle class="spool-hole" cx="{hx:.2f}" cy="{hy:.2f}" r="2.6"/>')
    parts.append(f'<circle class="spool-hole" cx="{CX}" cy="{CY}" r="{R_HOLE}"/>')
    parts.append(f'<circle class="spool-rim" cx="{CX}" cy="{CY}" r="{R_FLANGE - 0.6}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))
