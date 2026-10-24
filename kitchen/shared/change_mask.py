"""The one change-mask function: consecutive RGB frames to a single-channel gradient (brighter = more movement).

Owner: platform (agents)
Step:  shared
Reads: two letterboxed RGB frames, the previous mask, gain / blur_sigma_px / decay
Emits: an H x W mask in [0, 1]
Rules: identical for live frames (vision) and sim renders (sim), so training matches live; parameters recorded in every clip
"""

import numpy as np

# The agreed clip geometry defaults (AGENTS.md, Open questions: clip geometry).
DEFAULT_GAIN = 4.0
DEFAULT_BLUR_SIGMA_PX = 1.5
DEFAULT_DECAY = 0.0


def gaussian_kernel(sigma_px: float) -> np.ndarray:
    """A normalized 1D Gaussian kernel that reaches three sigmas on each side."""
    radius = max(1, int(np.ceil(3 * sigma_px)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-0.5 * (offsets / sigma_px) ** 2)
    return kernel / kernel.sum()


def gaussian_blur(image: np.ndarray, sigma_px: float) -> np.ndarray:
    """Blur a 2D image with a separable Gaussian, reflecting at the edges, so single-pixel noise spreads out."""
    if sigma_px <= 0:
        return image

    kernel = gaussian_kernel(sigma_px)
    radius = len(kernel) // 2
    height, width = image.shape
    padded = np.pad(image, radius, mode="reflect")

    # Each pass is a weighted sum of shifted copies: first along rows, then along columns.
    rows = sum(weight * padded[:, i : i + width] for i, weight in enumerate(kernel))
    return sum(weight * rows[i : i + height, :] for i, weight in enumerate(kernel))


def change_mask(
    prev_rgb: np.ndarray,
    rgb: np.ndarray,
    prev_mask: np.ndarray | None,
    *,
    gain: float = DEFAULT_GAIN,
    blur_sigma_px: float = DEFAULT_BLUR_SIGMA_PX,
    decay: float = DEFAULT_DECAY,
) -> np.ndarray:
    """Return where pixels changed between two frames: a float32 [H, W] mask in [0, 1], brighter where more moved.

    Both frames are uint8 RGB [H, W, 3], already letterboxed to the same size. With decay > 0, the previous mask fades
    instead of vanishing, leaving a short motion trail.
    """
    if prev_rgb.shape != rgb.shape:
        raise ValueError(f"frames differ in shape: {prev_rgb.shape} vs {rgb.shape}")

    # The largest change in any color channel, so a change in hue counts as much as a change in brightness.
    diff = np.abs(rgb.astype(np.int16) - prev_rgb.astype(np.int16)).max(axis=2).astype(np.float32) / 255.0
    mask = np.clip(gain * gaussian_blur(diff, blur_sigma_px), 0.0, 1.0)

    if decay > 0 and prev_mask is not None:
        mask = np.maximum(mask, decay * prev_mask)
    return mask.astype(np.float32)
