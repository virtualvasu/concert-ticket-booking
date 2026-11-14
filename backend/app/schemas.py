from typing import Literal

from pydantic import BaseModel, Field


class SectionSpec(BaseModel):
    name: str
    rows: int = Field(ge=1)
    seats_per_row: int = Field(ge=1)
    price: int = Field(ge=0)


def _default_sections() -> list[SectionSpec]:
    return [
        SectionSpec(name="VIP", rows=2, seats_per_row=25, price=5000),
        SectionSpec(name="Gold", rows=6, seats_per_row=25, price=3000),
        SectionSpec(name="Silver", rows=12, seats_per_row=25, price=1500),
    ]


class SaleConfig(BaseModel):
    mode: Literal["bucket", "baseline"] = "bucket"
    label: str = ""
    sections: list[SectionSpec] = Field(default_factory=_default_sections)
    seats_per_tick: int = Field(default=10, ge=1)
    tick_seconds: float = Field(default=10, gt=0)
    hold_seconds: float = Field(default=60, gt=0)
    max_payment_attempts: int = Field(default=3, ge=1)
    tickets_per_user: int = Field(default=1, ge=1)
