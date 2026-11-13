from myservice.metrics import line


def test_line():
    assert line("orders", 3) == "orders value=3"
