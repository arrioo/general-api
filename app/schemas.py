from pydantic import BaseModel, Field, field_validator, model_validator


# ---------- orders ----------
class OrderItemIn(BaseModel):
    product_id: int
    qty: int = Field(gt=0, le=1000)


class OrderCreate(BaseModel):
    warehouse_id: int
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)


# ---------- stock ----------
class ReceiveIn(BaseModel):
    product_id: int
    warehouse_id: int
    qty: int = Field(gt=0)
    note: str | None = None                # e.g. supplier / PO number
    idempotency_key: str | None = None     # send it to make retries safe


class AdjustIn(BaseModel):
    product_id: int
    warehouse_id: int
    qty_change: int                        # signed: -3 or +5
    note: str = Field(min_length=3)        # reason is mandatory
    idempotency_key: str | None = None

    @field_validator("qty_change")
    @classmethod
    def not_zero(cls, v: int) -> int:
        if v == 0:
            raise ValueError("qty_change must not be 0")
        return v


# ---------- transfers ----------
class TransferItemIn(BaseModel):
    product_id: int
    qty: int = Field(gt=0)


class TransferCreate(BaseModel):
    from_warehouse: int
    to_warehouse: int
    items: list[TransferItemIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def different_warehouses(self):
        if self.from_warehouse == self.to_warehouse:
            raise ValueError("from_warehouse and to_warehouse must differ")
        return self


# ---------- returns ----------
class ReturnItemIn(BaseModel):
    product_id: int
    qty: int = Field(gt=0)


class ReturnCreate(BaseModel):
    order_id: int
    reason: str | None = None
    items: list[ReturnItemIn] = Field(min_length=1, max_length=50)


class ReturnReceiveItem(BaseModel):
    product_id: int
    restock: bool                          # False = damaged, do not add back to stock


class ReturnReceiveIn(BaseModel):
    items: list[ReturnReceiveItem] = []    # optional overrides, default is restock=True
