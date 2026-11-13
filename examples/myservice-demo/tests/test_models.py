import numpy as np
from myservice.models import Item


def test_item():
    assert Item(sku="x", price=np.float64(1.5)).price == 1.5
