"""数据模型：评估会话 + 评估记录。

- Session：一次评估会话（匿名 label，不收真实姓名）
- AssessmentRecord：会话内一条量表计分结果（含 answers 留档可审计）
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    assessments: Mapped[list["AssessmentRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AssessmentRecord.created_at"
    )


class AssessmentRecord(Base):
    __tablename__ = "assessment_records"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    scale_id: Mapped[str] = mapped_column(String(32))
    scale_name: Mapped[str] = mapped_column(String(128))
    total_score: Mapped[int] = mapped_column(Integer)
    severity: Mapped[str] = mapped_column(String(32))
    crisis_level: Mapped[str] = mapped_column(String(32))
    crisis_triggers: Mapped[list] = mapped_column(JSON, default=list)
    interpretation: Mapped[str] = mapped_column(Text, default="")
    answers: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["Session"] = relationship(back_populates="assessments")

    @property
    def needs_crisis_escalation(self) -> bool:
        return self.crisis_level == "elevated"
