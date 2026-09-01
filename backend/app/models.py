"""数据模型：评估会话 + 评估记录 + 用户 + 对话轮次。

- User：用户账号（匿名 label，含知情同意与 token）
- Session：一次评估会话（匿名 label，不收真实姓名）
- ConversationTurn：对话内一轮 user/assistant/system 消息留痕
- AssessmentRecord：会话内一条量表计分结果（含 answers 留档可审计）
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    label: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String(16), default="student")
    profile: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    consents: Mapped[dict | None] = mapped_column(JSON, nullable=True, default=None)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    sessions: Mapped[list["Session"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    conversation_turns: Mapped[list["ConversationTurn"]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    account_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped["User | None"] = relationship(back_populates="sessions")
    assessments: Mapped[list["AssessmentRecord"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="AssessmentRecord.created_at"
    )
    conversation_turns: Mapped[list["ConversationTurn"]] = relationship(
        back_populates="session"
    )


class ConversationTurn(Base):
    __tablename__ = "conversation_turns"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    session_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    account_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    sources_json: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    crisis_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    session: Mapped["Session | None"] = relationship(back_populates="conversation_turns")
    account: Mapped["User | None"] = relationship(back_populates="conversation_turns")


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
