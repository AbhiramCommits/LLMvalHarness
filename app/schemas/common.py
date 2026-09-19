from pydantic import BaseModel


class HealthStatus(BaseModel):
    status: str


class CheckStatus(BaseModel):
    status: str
    error: str | None = None


class ReadyStatus(BaseModel):
    status: str
    checks: dict[str, CheckStatus]
