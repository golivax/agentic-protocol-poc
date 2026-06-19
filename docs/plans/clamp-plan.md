# Plan — implement `clamp(value, low, high)`

Implements `docs/specs/clamp-spec.md`.

## Steps

1. Create `examples/clamp.py` with a single function `clamp(value, low, high)`.
2. Validate the range first: if `low > high`, raise `ValueError` naming both
   bounds (an empty range is a caller error, not a silent result).
3. Return `low` when `value < low`, `high` when `value > high`, else `value`.
4. Document the contract in the docstring, including the inclusive bounds and a
   usage example.

## Verification

- `clamp(5, 0, 10) == 5` (in range)
- `clamp(-3, 0, 10) == 0` (below → low)
- `clamp(42, 0, 10) == 10` (above → high)
- `clamp(0, 0, 10) == 0` and `clamp(10, 0, 10) == 10` (inclusive bounds)
- `clamp(1, 10, 0)` raises `ValueError` (empty range)
