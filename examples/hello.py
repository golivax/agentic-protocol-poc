"""A tiny sample module — the analysis target for a /recover smoke test."""


def greet(name: str) -> str:
    """Return a friendly greeting."""
    return f"Hello, {name}!"


if __name__ == "__main__":
    print(greet("world"))
