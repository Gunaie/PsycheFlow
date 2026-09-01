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
    # B 端教师密码登录（SHA-256 加盐哈希），学生为空
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, default=None)
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


class ScreeningBatch(Base):
    """C 三期：教师创建的批量筛查批次。

    - teacher_id：创建教师（users.id，role=teacher）
    - scale_ids：本批施测量表列表，如 ["phq_a"] / ["phq_a", "scared"]
    - status：active 进行中 / closed 已关闭（关闭后学生筛查码失效）
    """
    __tablename__ = "screening_batches"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    teacher_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scale_ids: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    teacher: Mapped["User"] = relationship()
    entries: Mapped[list["BatchEntry"]] = relationship(
        back_populates="batch", cascade="all, delete-orphan",
        order_by="BatchEntry.student_no"
    )


class BatchEntry(Base):
    """批次内学生条目：名单由教师 CSV 上传，学生凭 entry_code 匿名进入答题。

    - student_no/grade/klass：教师自行上传的名单信息（B 端可见）
    - entry_code：6 位唯一筛查码（C 端唯一凭证，不含真实信息）
    - status：pending 未完成 / completed 已完成
    - session_id：完成后关联的评估会话（关联 AssessmentRecord 供统计）
    """
    __tablename__ = "batch_entries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    batch_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("screening_batches.id", ondelete="CASCADE"), index=True
    )
    student_no: Mapped[str] = mapped_column(String(64), nullable=False)
    student_name: Mapped[str] = mapped_column(String(64), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(32), nullable=True)
    klass: Mapped[str | None] = mapped_column(String(32), nullable=True)
    entry_code: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    session_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="SET NULL"), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    batch: Mapped["ScreeningBatch"] = relationship(back_populates="entries")
    session: Mapped["Session | None"] = relationship()


class AuditLog(Base):
    """审计日志 DB 镜像：与 logs/*.json 文件双写，便于查询/聚合/合规导出。

    - event_type：crisis（危机命中）/ report（报告生成）
    - payload：与同名 JSON 文件内容一致的完整快照
    - session_id/account_id：冗余索引列，便于按账号/会话检索审计
    """
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    event_type: Mapped[str] = mapped_column(String(16), index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
