"""DB 模型与持久化单测：建表幂等、CRUD、JSON 字段、级联删除。"""
import os
import stat

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, restrict_db_file_perms
import app.models  # noqa: F401
from app.models import AssessmentRecord, Session as SessionModel


def _make_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class TestDB:
    def test_create_session_and_assessment(self):
        db = _make_session()
        s = SessionModel(label="测试同学")
        db.add(s)
        db.commit()
        db.refresh(s)
        assert s.id and len(s.id) == 32
        assert s.label == "测试同学"

        rec = AssessmentRecord(
            session_id=s.id, scale_id="phq_a", scale_name="PHQ-A", total_score=12,
            severity="moderate", crisis_level="safe", crisis_triggers=[],
            interpretation="中度", answers={"1": 3},
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        assert rec.id
        assert rec.needs_crisis_escalation is False

        db.refresh(s)
        assert len(s.assessments) == 1
        assert s.assessments[0].scale_id == "phq_a"

    def test_cascade_delete(self):
        db = _make_session()
        s = SessionModel(label="x")
        db.add(s)
        db.commit()
        db.refresh(s)
        rec = AssessmentRecord(
            session_id=s.id, scale_id="phq_a", scale_name="PHQ-A", total_score=0,
            severity="none", crisis_level="safe", crisis_triggers=[],
            interpretation="", answers={},
        )
        db.add(rec)
        db.commit()
        db.delete(s)
        db.commit()
        assert db.get(AssessmentRecord, rec.id) is None

    def test_json_fields_roundtrip(self):
        db = _make_session()
        s = SessionModel(label=None)
        db.add(s)
        db.commit()
        db.refresh(s)
        rec = AssessmentRecord(
            session_id=s.id, scale_id="phq_a", scale_name="PHQ-A", total_score=1,
            severity="none", crisis_level="elevated",
            crisis_triggers=["第9题 自杀意念 得分 1"], interpretation="x",
            answers={"9": 1},
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        assert rec.crisis_triggers == ["第9题 自杀意念 得分 1"]
        assert rec.answers == {"9": 1}
        assert rec.needs_crisis_escalation is True

    def test_create_all_idempotent(self):
        """重复 create_all 不报错、表仍在。"""
        engine = create_engine("sqlite:///:memory:", poolclass=StaticPool)
        Base.metadata.create_all(engine)
        Base.metadata.create_all(engine)  # 幂等
        assert "sessions" in Base.metadata.tables
        assert "assessment_records" in Base.metadata.tables


class TestDbFilePerms:
    """合规：SQLite 文件权限收紧为 0600。"""

    def test_restrict_sets_600(self, tmp_path):
        p = tmp_path / "test.db"
        p.write_text("")  # 默认权限通常 644
        restrict_db_file_perms(str(p))
        mode = stat.S_IMODE(os.stat(str(p)).st_mode)
        assert mode == 0o600

    def test_restrict_missing_file_noop(self, tmp_path):
        """文件不存在时不抛异常。"""
        restrict_db_file_perms(str(tmp_path / "nonexistent.db"))
