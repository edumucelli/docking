# SPDX-License-Identifier: BSD-3-Clause
#
# scikit-image SSIM/PSNR:
# Copyright: 2009-2022 the scikit-image team
# License: BSD-3-Clause
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
# 3. Neither the name of the University nor the names of its contributors
#    may be used to endorse or promote products derived from this software
#    without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
# ``AS IS'' AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
# LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
# A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE HOLDERS OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL,
# EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR
# PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#
# SciPy uniform filter:
# Copyright (C) 2003-2005 Peter J. Verveer
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions
# are met:
#
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above
#    copyright notice, this list of conditions and the following
#    disclaimer in the documentation and/or other materials provided
#    with the distribution.
#
# 3. The name of the author may not be used to endorse or promote
#    products derived from this software without specific prior
#    written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE
# GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY,
# WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING
# NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""The scikit-image SSIM/PSNR paths used by Docking's visual comparisons.

Adapted from scikit-image 0.26.0 metrics/_structural_similarity.py and
metrics/simple_metrics.py. The uniform filter ports SciPy 1.17.1
ndimage/src/ni_filters.c:NI_UniformFilter1D to NumPy.

Only Docking's float32 RGB/RGBA configuration is supported: channel_axis=-1,
data_range=255, win_size=7, uniform weights, and sample covariance. Preserve
arithmetic order, float32 filter intermediates and channel averaging when
updating this port. Both upstream license notices are reproduced in the header.
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
