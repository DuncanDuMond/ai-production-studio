from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class ProjectInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)


class DocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=50000)


class RunInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    prompt: str = Field(min_length=1, max_length=8000)
    mode: Literal["local", "live"] = "local"
    engine: Literal["pydantic-ai", "instructor"] = "pydantic-ai"
    top_k: int = Field(default=3, ge=1, le=10)
    reference: str = Field(default="", max_length=8000)
    allow_paid: StrictBool = False
