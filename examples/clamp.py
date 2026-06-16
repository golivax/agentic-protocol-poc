"""Small, self-contained utility used as a clean negative-control diff for the
M2b generic-orchestrator live equivalence test."""


def clamp(value, low, high):
    """Return value constrained to the inclusive range [low, high].

    Raises ValueError if low > high so the bounds can't be passed swapped.
    """
    if low > high:
        raise ValueError(f"low ({low}) must not exceed high ({high})")
    if value < low:
        return low
    if value > high:
        return high
    return value
