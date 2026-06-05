from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class ReviewConfig(BaseModel):
    models: list[str] = Field(default_factory=list)
    max_loops: int = 3


class Config(BaseModel):
    profile: str
    memory_cloud_url: str
    context_id: str
    ollama_url: str = "http://localhost:11434"
    review: ReviewConfig = Field(default_factory=ReviewConfig)


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"config not found: {p}")
    data = yaml.safe_load(p.read_text()) or {}
    return Config.model_validate(data)
