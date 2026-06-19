"""Numeric clamping helper (v4 gate live-test fixture)."""


def clamp(value, low, high):
    """Return ``value`` constrained to the inclusive range ``[low, high]``.

    Args:
        value: the number to constrain.
        low: lower bound (inclusive).
        high: upper bound (inclusive).

    Returns:
        ``low`` if ``value < low``, ``high`` if ``value > high``, otherwise
        ``value`` unchanged.

    Raises:
        ValueError: if ``low`` is greater than ``high`` (an empty range).

    Example:
        >>> clamp(5, 0, 10)
        5
        >>> clamp(-3, 0, 10)
        0
        >>> clamp(42, 0, 10)
        10
    """
    if low > high:
        raise ValueError(f"low ({low}) must not exceed high ({high})")
    if value < low:
        return low
    if value > high:
        return high
    return value
