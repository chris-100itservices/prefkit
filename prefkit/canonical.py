"""Canonical records. Every dataset is mapped into these shapes, and nothing downstream
knows where the data came from."""

from typing import Literal

from pydantic import BaseModel


class Prompt(BaseModel):
    id: str
    text: str
    category: str | None = None


class Response(BaseModel):
    id: str
    prompt_id: str
    model: str | None = None
    text: str


class Comparison(BaseModel):
    id: str
    prompt_id: str
    a_id: str
    b_id: str
    # "a" or "b" names the preferred response. "tie" means no preference.
    # None means the row is not labeled yet.
    winner: Literal["a", "b", "tie"] | None = None
    gold: bool = False
    source: str  # "human", "judge", "gold", "execution"
