from tfsample.greeter import greet


def test_greet() -> None:
    assert greet("Ada") == "hello Ada"
