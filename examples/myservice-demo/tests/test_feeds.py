from myservice.feeds import parse_feed


def test_parse_feed():
    items = parse_feed(b'<feed><item sku="a1" price="9.5"/></feed>')
    assert items[0].sku == "a1" and items[0].price == 9.5
