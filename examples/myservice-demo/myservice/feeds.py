from .models import Item


def parse_feed(xml: bytes) -> list[Item]:
    from lxml import etree  # only needed for partner XML feeds

    root = etree.fromstring(xml)
    return [Item(sku=e.get("sku"), price=float(e.get("price"))) for e in root.iter("item")]
