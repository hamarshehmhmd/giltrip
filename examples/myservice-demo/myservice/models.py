from pydantic import BaseModel


class Item(BaseModel):
    sku: str
    price: float
