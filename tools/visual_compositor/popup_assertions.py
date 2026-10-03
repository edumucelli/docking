"""Locate the real GTK popup template in compositor pixels and detect clipping."""

import json

import numpy as np
from PIL import Image
from skimage.feature import match_template


def check_popup(evidence, case):
    kind = case.action.removeprefix("popup-")
    observation = json.loads((evidence / f"{case.name}.popup.json").read_text())
    candidates = [
        p
        for p in observation["popups"]
        if (
            p["class"] == "PreviewPopup"
            if kind == "preview"
            else p["class"] != "PreviewPopup"
            and p["kind"] == ("popup-menu" if kind == "menu" else "tooltip")
        )
    ]
    if len(candidates) != 1:
        return False, "popup surface not uniquely observed"
    surface = candidates[0]
    with (
        Image.open(evidence / f"{case.name}.popup-template.png") as template,
        Image.open(evidence / f"{case.name}.effect.png") as effect,
        Image.open(evidence / f"{case.name}.resting.png") as resting,
    ):
        if effect.size != resting.size:
            return False, "popup captures changed canvas dimensions"
        if template.size != (surface["width"], surface["height"]):
            return False, "template and GTK popup allocation disagree"
        if template.width > effect.width or template.height > effect.height:
            return False, "popup allocation exceeds the output canvas"
        rgba = np.asarray(template.convert("RGBA"))
        opaque = rgba[:, :, 3] >= 245
        if opaque.sum() < 80:
            return False, "popup has insufficient opaque content to locate"
        backdrop = Image.new("RGBA", template.size, (20, 46, 71, 255))
        rgb = np.asarray(
            Image.alpha_composite(backdrop, template.convert("RGBA")).convert("RGB")
        )
        pixels = np.asarray(effect.convert("RGB"))
        correlation = match_template(pixels.mean(axis=2), rgb.mean(axis=2))
        y, x = np.unravel_index(correlation.argmax(), correlation.shape)
        patch = pixels[y : y + template.height, x : x + template.width]
        delta = np.max(np.abs(patch.astype(np.int16) - rgb.astype(np.int16)), axis=2)
        agreement = float((delta[opaque] <= 16).mean())
        # Transparent shadows may composite over the dock instead of the blue
        # canvas. Check opaque pixels independently of those transparent edges.
        if agreement < 0.98:
            return (
                False,
                (
                    f"popup clipped or absent: correlation={correlation[y, x]:.3f}, "
                    f"opaque agreement={agreement:.3f}"
                ),
            )
        old = np.asarray(resting.convert("RGB"))[
            y : y + template.height, x : x + template.width
        ]
        changed = np.any(
            np.abs(patch.astype(np.int16) - old.astype(np.int16)) > 24, axis=2
        )
        if (changed & opaque).sum() < 80:
            return False, "pre-existing pixels do not establish a new popup"
        return (
            True,
            (
                f"{kind}: full {template.width}x{template.height} popup at {x},{y}; "
                f"opaque agreement={agreement:.3f}"
            ),
        )
