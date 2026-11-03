"""Frozen upstream results for Docking's narrowly ported SSIM and PSNR."""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tests.visual.metrics import structural_similarity
from tests.visual.support import VisualThresholds, compute_metrics

REFERENCE_FILE = Path(__file__).parent / "visual" / "metrics_reference.json"
REFERENCE = json.loads(REFERENCE_FILE.read_text())


def metric_images(case: str) -> tuple[Image.Image, Image.Image]:
    """Build deterministic 8-bit inputs without a version-sensitive PRNG."""
    shape = (7, 7, 4) if case == "minimum-window" else (32, 47, 4)
    if case == "rgb":
        shape = (11, 53, 3)
    if case.startswith("threshold-"):
        shape = (64, 79, 4)
    y, x, channel = np.indices(shape)
    pixels = ((x * 37 + y * 13 + channel * 53 + (x * y) % 251) % 256).astype(np.uint8)
    expected = pixels.copy()
    actual = pixels.copy()
    if case == "identical":
        pass
    elif case == "black-white":
        expected.fill(0)
        actual.fill(255)
    elif case == "flat-offset":
        expected.fill(125)
        actual.fill(126)
    elif case == "low-variance":
        expected.fill(250)
        actual.fill(250)
        actual[::2, ::3] = 251
    elif case == "alpha-only":
        actual[..., 3] = 255 - actual[..., 3]
    elif case == "border-only":
        actual[0, :, :] = 255 - actual[0, :, :]
        actual[-1, :, :] = 255 - actual[-1, :, :]
        actual[:, 0, :] = 255 - actual[:, 0, :]
        actual[:, -1, :] = 255 - actual[:, -1, :]
    elif case == "center-impulse":
        actual[16, 23] = 255 - actual[16, 23]
    elif case == "minimum-window":
        actual[3, 3] = 255 - actual[3, 3]
    elif case == "complement":
        actual = 255 - actual
    elif case == "rgb":
        actual[::2, ::3] = 255 - actual[::2, ::3]
    elif case.startswith("threshold-"):
        delta = int(case.removeprefix("threshold-"))
        perturbation = np.where((x + y + channel) % 2, delta, -delta)
        actual = np.clip(actual.astype(np.int16) + perturbation, 0, 255).astype(
            np.uint8
        )
    else:
        raise ValueError(case)
    return Image.fromarray(expected), Image.fromarray(actual)


@pytest.mark.parametrize("case", REFERENCE["cases"], ids=lambda case: case["name"])
def test_metrics_match_frozen_upstream_values(case):
    expected, actual = metric_images(case["name"])
    metrics = compute_metrics(expected, actual)
    assert metrics.ssim == pytest.approx(case["ssim"], abs=1e-7, rel=0)
    if case["psnr"] is None:
        assert math.isinf(metrics.psnr) and metrics.psnr > 0
    else:
        assert metrics.psnr == pytest.approx(case["psnr"], abs=1e-12, rel=0)


@pytest.mark.parametrize(
    "case",
    [case for case in REFERENCE["cases"] if case["name"].startswith("threshold-")],
)
@pytest.mark.parametrize(
    "threshold", [VisualThresholds(), VisualThresholds(0.985, 30.0)]
)
def test_threshold_decisions_match_upstream(case, threshold):
    expected, actual = metric_images(case["name"])
    metrics = compute_metrics(expected, actual)
    assert (metrics.ssim >= threshold.ssim_min) == (case["ssim"] >= threshold.ssim_min)
    assert (metrics.psnr >= threshold.psnr_min) == (case["psnr"] >= threshold.psnr_min)


@pytest.mark.parametrize("size", [(6, 7), (7, 6), (1, 1)])
def test_ssim_rejects_images_smaller_than_its_window(size):
    image = Image.new("RGBA", size)
    with pytest.raises(ValueError, match="at least 7x7"):
        compute_metrics(image, image)


def test_metrics_reject_different_shapes():
    with pytest.raises(ValueError, match="same dimensions"):
        compute_metrics(Image.new("RGBA", (8, 8)), Image.new("RGBA", (9, 8)))


@pytest.mark.parametrize("shape", [(8, 8), (8, 8, 0), (8, 8, 1), (8, 8, 2), (8, 8, 5)])
def test_ssim_rejects_inputs_outside_rgb_and_rgba(shape):
    image = np.zeros(shape, dtype=np.float32)
    with pytest.raises(ValueError, match="RGB or RGBA"):
        structural_similarity(image, image)


def test_visual_metrics_work_without_removed_dependencies():
    root = Path(__file__).resolve().parents[1]
    script = """
import importlib.abc
import sys

blocked = {'skimage', 'scipy', 'networkx', 'imageio', 'tifffile', 'lazy_loader'}
class BlockDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in blocked:
            raise ModuleNotFoundError(fullname)
sys.meta_path.insert(0, BlockDependencies())
sys.path.insert(0, sys.argv[1])
from PIL import Image
from tests.visual.support import compute_metrics
first = Image.new('RGBA', (16, 16), (100, 150, 200, 255))
second = Image.new('RGBA', (16, 16), (101, 151, 201, 254))
result = compute_metrics(first, second)
assert 0 < result.ssim < 1
assert result.psnr > 0
assert not blocked.intersection(sys.modules)
"""
    subprocess.run(
        [sys.executable, "-I", "-c", script, str(root)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
