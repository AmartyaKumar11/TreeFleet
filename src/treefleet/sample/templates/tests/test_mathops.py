from tfsample.mathops import add_one, double


def test_double() -> None:
    assert double(3) == 6


def test_add_one() -> None:
    assert add_one(1) == 2
