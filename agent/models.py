from typing import Literal
from pydantic import BaseModel, Field, model_validator


class Plan(BaseModel):
    kind: Literal["query", "clarify", "unsupported", "create"]
    explanation: str = Field(max_length=2000)
    sql: str = Field(default="", max_length=16000)
    definitions: list[str] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def query_requires_sql(self):
        if self.kind == "query" and not self.sql.strip():
            raise ValueError("A query plan must include nonempty SQL")
        return self


class FieldInfo(BaseModel):
    name: str
    label: str = ""
    type: str = ""


class TableInfo(BaseModel):
    name: str
    fields: list[FieldInfo]


class Schema(BaseModel):
    version: str
    refreshed_at: float
    tables: list[TableInfo]
    failures: list[dict] = Field(default_factory=list)
