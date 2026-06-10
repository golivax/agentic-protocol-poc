function clamp(value, min, max) {
  if (min > max) {
    throw new RangeError("min must be <= max");
  }
  return Math.min(Math.max(value, min), max);
}

module.exports = { clamp };
