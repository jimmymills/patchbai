from dataclasses import dataclass, field
from enum import Enum


class AgentState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING = "waiting"
    DONE = "done"
    ERROR = "error"

    @property
    def is_terminal(self) -> bool:
        return self in (AgentState.DONE, AgentState.ERROR)


@dataclass
class AgentInfo:
    id: str
    name: str
    cwd: str
    started_at: float
    state: AgentState = AgentState.IDLE
    ended_at: float | None = None
    last_activity: float = field(default=0.0)
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def __post_init__(self) -> None:
        if self.last_activity == 0.0:
            self.last_activity = self.started_at

    def elapsed_seconds(self) -> float:
        end = self.ended_at if self.ended_at is not None else self.last_activity
        return end - self.started_at

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "cwd": self.cwd,
            "started_at": self.started_at,
            "state": self.state.value,
            "ended_at": self.ended_at,
            "last_activity": self.last_activity,
            "cost": self.cost,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentInfo":
        return cls(
            id=d["id"],
            name=d["name"],
            cwd=d["cwd"],
            started_at=d["started_at"],
            state=AgentState(d["state"]),
            ended_at=d.get("ended_at"),
            last_activity=d.get("last_activity", d["started_at"]),
            cost=d.get("cost", 0.0),
            tokens_in=d.get("tokens_in", 0),
            tokens_out=d.get("tokens_out", 0),
        )
