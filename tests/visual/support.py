"""Shared helpers for screenshot-based visual regression tests."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import cairo
import numpy as np
from PIL import Image, ImageChops
from skimage.metrics import peak_signal_noise_ratio, structural_similarity

BASELINE_DIR = Path(__file__).resolve().parent / "baselines"
OUTPUT_DIR = Path(__file__).resolve().parent / "output"


@dataclass(frozen=True)
class VisualThresholds:
    """Strict-but-practical comparison thresholds for stable rendered states."""

    ssim_min: float = 0.995
    psnr_min: float = 35.0


@dataclass(frozen=True)
class VisualMetrics:
    """Computed image similarity metrics."""

    ssim: float
    psnr: float


def write_surface_png(surface: cairo.ImageSurface, path: Path) -> None:
    """Persist a Cairo surface as PNG, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    surface.write_to_png(str(path))


def surface_image(surface: cairo.ImageSurface) -> Image.Image:
    """Convert a Cairo surface to a Pillow RGBA image."""
    payload = BytesIO()
    surface.write_to_png(payload)
    payload.seek(0)
    with Image.open(payload) as image:
        return image.convert("RGBA")


def load_image(path: Path) -> Image.Image:
    """Load a baseline image as RGBA."""
    with Image.open(path) as image:
        return image.convert("RGBA")


def compute_metrics(expected: Image.Image, actual: Image.Image) -> VisualMetrics:
    """Compute SSIM and PSNR for two equal-sized RGBA images."""
    expected_pixels = np.asarray(expected, dtype=np.float32)
    actual_pixels = np.asarray(actual, dtype=np.float32)
    ssim = structural_similarity(
        expected_pixels,
        actual_pixels,
        channel_axis=-1,
        data_range=255.0,
    )
    if np.array_equal(expected_pixels, actual_pixels):
        psnr = float("inf")
    else:
        psnr = peak_signal_noise_ratio(
            expected_pixels,
            actual_pixels,
            data_range=255.0,
        )
    return VisualMetrics(ssim=float(ssim), psnr=float(psnr))


def diff_image(expected: Image.Image, actual: Image.Image) -> Image.Image:
    """Build a high-contrast diff image for CI artifacts."""
    diff = ImageChops.difference(expected, actual)
    return diff.convert("RGBA")


def assert_surface_matches_baseline(
    *,
    request,
    case_name: str,
    surface: cairo.ImageSurface,
    thresholds: VisualThresholds | None = None,
) -> VisualMetrics:
    """Compare a rendered surface against its baseline, or update it."""
    thresholds = thresholds or VisualThresholds()
    baseline_path = BASELINE_DIR / f"{case_name}.png"
    actual = surface_image(surface=surface)

    if request.config.getoption("--update-visual-baselines"):
        write_surface_png(surface=surface, path=baseline_path)
        return VisualMetrics(ssim=1.0, psnr=float("inf"))

    if not baseline_path.exists():
        raise AssertionError(
            f"Missing visual baseline {baseline_path}. "
            "Run tools/update_visual_baselines.py to create it."
        )

    expected = load_image(path=baseline_path)
    if expected.size != actual.size:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        actual.save(OUTPUT_DIR / f"{case_name}.actual.png")
        expected.save(OUTPUT_DIR / f"{case_name}.expected.png")
        raise AssertionError(
            f"{case_name} size mismatch: expected {expected.size}, got {actual.size}"
        )

    metrics = compute_metrics(expected=expected, actual=actual)
    if metrics.ssim >= thresholds.ssim_min and metrics.psnr >= thresholds.psnr_min:
        return metrics

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    actual_path = OUTPUT_DIR / f"{case_name}.actual.png"
    expected_path = OUTPUT_DIR / f"{case_name}.expected.png"
    diff_path = OUTPUT_DIR / f"{case_name}.diff.png"
    actual.save(actual_path)
    expected.save(expected_path)
    diff_image(expected=expected, actual=actual).save(diff_path)
    raise AssertionError(
        f"{case_name} visual regression: "
        f"SSIM {metrics.ssim:.5f} < {thresholds.ssim_min:.5f} or "
        f"PSNR {metrics.psnr:.2f} < {thresholds.psnr_min:.2f}. "
        f"Artifacts: {actual_path}, {expected_path}, {diff_path}"
    )
