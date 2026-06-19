# Spec — `clamp(value, low, high)`

## Purpose

Provide a small, dependency-free helper that constrains a number to an
inclusive range. Used as the live-test fixture for the v4 approval gate.

## Contract

- **Signature:** `clamp(value, low, high)`.
- **Returns:** `value` constrained to `[low, high]`:
  - `low` when `value < low`,
  - `high` when `value > high`,
  - otherwise `value` unchanged.
- **Bounds are inclusive:** `clamp(low, low, high) == low` and
  `clamp(high, low, high) == high`.
- **Invalid range:** if `low > high` the range is empty; the function raises
  `ValueError` rather than returning a silently-wrong value.

## Non-goals

- No type coercion — callers pass comparable numbers.

## NaN handling

NaN is out of scope: `NaN` compares false against every bound, so a `NaN`
`value` falls through to the final branch and is returned unchanged. Callers
that must reject `NaN` should validate before calling `clamp`.
