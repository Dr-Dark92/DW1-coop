"""Picture settings for the player: Beetle PSX core options (applied when the game starts) and screen shapes.

Measured on the user's Windows PC with the software renderer (emulated frame time, Jijimon's house):
native 3.4 ms, 2x 5 ms, 4x 10 ms with spikes to 25 ms, so 4x drops frames there and 2x is the default.
The core's widescreen hack is not offered: Digimon World's backgrounds are pre-rendered 320-pixel pictures,
so it stretches them and the 3D characters no longer stand where the background shows them.
"""
from __future__ import annotations

NATIVE_RESOLUTION = "Native (1x)"
RESOLUTIONS = {NATIVE_RESOLUTION: "1x(native)", "2x (recommended)": "2x", "4x (may stutter)": "4x"}
DEFAULT_RESOLUTION = "2x (recommended)"
SCREENS = ("4:3", "16:9 fill", "16:9 stretch", "Core aspect")
WIDE = 16 / 9


def core_options(resolution: str = DEFAULT_RESOLUTION, smooth_colours: bool = True, steady_3d: bool = False) -> dict:
    """Beetle PSX options for the chosen picture settings."""
    options = {"beetle_psx_internal_resolution": RESOLUTIONS.get(resolution, RESOLUTIONS[DEFAULT_RESOLUTION]),
               # PS1 dithering is the grain on gradients and shading; "internal resolution" would keep it, finer.
               "beetle_psx_dither_mode": "disabled" if smooth_colours else "1x(native)"}
    if steady_3d:  # PGXP: sub-pixel vertex positions and perspective-correct textures (less wobble and warping)
        options |= {"beetle_psx_pgxp_mode": "memory only", "beetle_psx_pgxp_texture": "enabled"}
    return options


def fit(width: float, height: float, aspect: float) -> tuple[float, float, float, float]:
    """The largest centred rectangle of `aspect` inside width × height, as (x, y, w, h)."""
    if width / height > aspect:
        shown = height * aspect
        return (width - shown) / 2, 0.0, shown, float(height)
    shown = width / aspect
    return 0.0, (height - shown) / 2, float(width), shown


def screen_rects(width: float, height: float, aspect: float, screen: str):
    """(screen area, game picture) for a view of width × height; the area is the 16:9 frame in the wide modes."""
    if screen == "Core aspect":
        game = fit(width, height, aspect)
        return game, game
    # Named 4:3 modes must not silently use the core's pixel-aspect-corrected ratio (Beetle reports ~1.219).
    aspect = 4 / 3
    if screen not in ("16:9 fill", "16:9 stretch"):
        game = fit(width, height, aspect)
        return game, game
    area = fit(width, height, WIDE)
    if screen == "16:9 stretch":
        return area, area
    x, y, w, h = area
    inner = h * aspect
    return area, (x + (w - inner) / 2, y, inner, h)
