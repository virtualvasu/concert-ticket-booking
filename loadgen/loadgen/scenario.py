"""Scenario definition, loaded from YAML."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class Wave(BaseModel):
    at_s: float
    users: int
    spread_s: float


class Arrival(BaseModel):
    kind: Literal["spike", "poisson", "waves"]
    spread_s: float = 0
    rate_per_s: float | None = None
    waves: list[Wave] = Field(default_factory=list)


class Behavior(BaseModel):
    poll_seconds: float = 2
    pay_delay_s: tuple[float, float] = (3, 15)
    abandon_prob: float = 0.05
    max_concurrency: int = 2000


class Scenario(BaseModel):
    name: str
    seed: int = 1
    base_url: str = "http://localhost:8080"
    admin_token: str = "admin"
    users: int
    arrival: Arrival
    behavior: Behavior = Field(default_factory=Behavior)
    sale: dict = Field(default_factory=dict)  # SaleConfig-shaped
    gateway: dict = Field(default_factory=dict)
    max_duration_s: float = 1800

    @classmethod
    def load(cls, path: str | Path) -> "Scenario":
        return cls.model_validate(yaml.safe_load(Path(path).read_text()))
