# Copyright: 2009-2022 the scikit-image team
# Copyright (C) 2003-2005 Peter J. Verveer
# SPDX-License-Identifier: BSD-3-Clause
# See LICENSE.scikit-image and LICENSE.scipy-ndimage in this directory.

"""The scikit-image SSIM/PSNR paths used by Docking's visual comparisons.

Adapted from scikit-image 0.26.0 metrics/_structural_similarity.py and
metrics/simple_metrics.py. The uniform filter ports SciPy 1.17.1
ndimage/src/ni_filters.c:NI_UniformFilter1D to NumPy.

Only Docking's float32 RGB/RGBA configuration is supported: channel_axis=-1,
data_range=255, win_size=7, uniform weights, and sample covariance. Preserve
arithmetic order, float32 filter intermediates and channel averaging when
updating this port. Upstream provenance and licenses are recorded alongside it.
"""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def _uniform_filter(image: NDArray[np.float32]) -> NDArray[np.float32]:
    """Apply SciPy's separable 7x7 reflect filter with double rolling sums."""
    result = image
    for axis in (0, 1):
        values = np.moveaxis(result, axis, 0)
        # SciPy's half-sample 'reflect' mode is NumPy's 'symmetric' padding.
        padded = np.pad(values, ((3, 3), (0, 0)), mode="symmetric")
        sums = np.empty(result.shape, dtype=np.float64)
        lines = np.moveaxis(sums, axis, 0)
        lines[0] = padded[:7].sum(axis=0, dtype=np.float64)
        np.subtract(padded[7:], padded[:-7], dtype=np.float64, out=lines[1:])
        np.cumsum(lines, axis=0, out=lines)
        # SciPy writes float32 output after each axis, in C-contiguous order.
        result = (sums / 7).astype(np.float32)
    return result


def structural_similarity(
    image_true: NDArray[np.float32], image_test: NDArray[np.float32]
) -> float:
    """Compute scikit-image's mean channel SSIM for 8-bit image data."""
    if image_true.shape != image_test.shape:
        raise ValueError("Input images must have the same dimensions.")
    if image_true.ndim != 3 or image_true.shape[-1] not in (3, 4):
        raise ValueError("Expected RGB or RGBA image arrays.")
    if min(image_true.shape[:2]) < 7:
        raise ValueError("Images must be at least 7x7 pixels.")

    nch = image_true.shape[-1]
    mssim = np.empty(nch, dtype=np.float32)
    for channel in range(nch):
        im1 = image_true[..., channel]
        im2 = image_test[..., channel]
        cov_norm = 49 / 48  # 7x7 window, sample covariance (NP / (NP - 1)).

        # Arithmetic order follows scikit-image's uniform-weight SSIM path.
        ux = _uniform_filter(im1)
        uy = _uniform_filter(im2)
        uxx = _uniform_filter(im1 * im1)
        uyy = _uniform_filter(im2 * im2)
        uxy = _uniform_filter(im1 * im2)
        vx = cov_norm * (uxx - ux * ux)
        vy = cov_norm * (uyy - uy * uy)
        vxy = cov_norm * (uxy - ux * uy)

        c1 = (0.01 * 255.0) ** 2
        c2 = (0.03 * 255.0) ** 2
        a1, a2, b1, b2 = (
            2 * ux * uy + c1,
            2 * vxy + c2,
            ux**2 + uy**2 + c1,
            vx + vy + c2,
        )
        denominator = b1 * b2
        similarity = (a1 * a2) / denominator
        # Match the float64 spatial mean followed by float32 channel storage.
        mssim[channel] = similarity[3:-3, 3:-3].mean(dtype=np.float64)
    return float(mssim.mean())


def peak_signal_noise_ratio(
    image_true: NDArray[np.float32], image_test: NDArray[np.float32]
) -> float:
    """Compute scikit-image's PSNR with an explicit data range of 255."""
    if image_true.shape != image_test.shape:
        raise ValueError("Input images must have the same dimensions.")
    error = np.mean((image_true - image_test) ** 2, dtype=np.float64)
    if error == 0:
        return float("inf")
    return float(10 * np.log10((255.0**2) / error))
