"""Sample module for the Stage 4c deep-review-stub live walk."""


def add(a, b):
    # planted: no type checks, no docstring — something for the stub agents to note
    return a + b


def divide(a, b):
    return a / b  # planted: no zero-division guard
