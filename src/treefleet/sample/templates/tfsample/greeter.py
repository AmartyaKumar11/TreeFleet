"""Greeting helper with a deliberate bug for the sample goal."""


def greet(name: str) -> str:
    # Bug: should say hello, not goodbye.
    return f"goodbye {name}"
