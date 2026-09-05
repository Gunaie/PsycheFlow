"""C 三期测试：教师认证 + 批量筛查全链路（批次创建/筛查码/统计/导出/报告/权限）。"""
import uuid

from unittest.mock import AsyncMock, patch

import pytest

from app.models import User

FULL_CONSENTS = {"tool": True, "guardian": True, "privacy14": True, "crisis": True}


def _register(client, role="student", label=None, password=None):
    payload = {"consents": FULL_CONSENTS, "role": role}
    if label:
        payload["label"] = label
    if password:
        payload["password"] = password
    return client.post("/api/auth/register", json=payload)


def _register_teacher(client, label="teacher1", password="pass123456"):
    return _register(client, role="teacher", label=label, password=password)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _roster_csv(n=3, with_class=True):
    header = "学号,姓名,年级,班级" if with_class else "学号,姓名"
    lines = [header]
    for i in range(1, n + 1):
        if with_class:
            lines.append(f"S{i:03d},学生{i},高一,1班")
        else:
            lines.append(f"S{i:03d},学生{i}")
    return "\n".join(lines)


PHQ_A_ALL_ZERO = {str(i): 0 for i in range(1, 10)}  # PHQ-A 9 题
PHQ_A_CRISIS = {**PHQ_A_ALL_ZERO, "9": 1}  # 第 9 题(自杀意念)>0 → elevated


# ---------------------------------------------------------------------------
# 教师认证
# ---------------------------------------------------------------------------

class TestTeacherAuth:
    def test_teacher_register_requires_password(self, client):
        r = _register(client, role="teacher", label="t1")
        assert r.status_code == 422
        assert r.json()["detail"]["code"] == "invalid_password"

    def test_teacher_register_short_password(self, client):
        r = _register(client, role="teacher", label="t1", password="123")
        assert r.status_code == 422

    def test_teacher_register_and_login_by_password(self, client):
        r = _register_teacher(client, label="wangls", password="secret66")
        assert r.status_code == 200
        body = r.json()
        assert body["label"] == "wangls"

        # 正确密码登录
        r2 = client.post("/api/auth/login_by_password",
                         json={"label": "wangls", "password": "secret66"})
        assert r2.status_code == 200
        assert r2.json()["token"] == body["token"]

        # 错误密码
        r3 = client.post("/api/auth/login_by_password",
                         json={"label": "wangls", "password": "wrong!"})
        assert r3.status_code == 401

        # 重复 label 注册 → 409
        r4 = _register_teacher(client, label="wangls", password="secret66")
        assert r4.status_code == 409

    def test_admin_requires_teacher_role(self, client):
        # 匿名 → 401
        assert client.get("/api/admin/batches").status_code == 401

        # 学生 token → 403
        stu = _register(client, role="student")
        r = client.get("/api/admin/batches", headers=_auth(stu.json()["token"]))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# 批次创建与名单解析
# ---------------------------------------------------------------------------

class TestBatchCreate:
    def test_create_batch_generates_unique_codes(self, client):
        t = _register_teacher(client).json()
        r = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "高一 3 月普测",
            "scale_ids": ["phq_a"],
            "roster_csv": _roster_csv(30),
        })
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 30
        codes = [e["entry_code"] for e in body["entries"]]
        assert len(codes) == len(set(codes)) == 30
        assert all(len(c) == 6 for c in codes)

    def test_create_batch_without_class_column(self, client):
        t = _register_teacher(client).json()
        r = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "无班级列", "scale_ids": ["phq_a"], "roster_csv": _roster_csv(2, with_class=False),
        })
        assert r.status_code == 200
        assert all(e["klass"] is None for e in r.json()["entries"])

    def test_create_batch_unknown_scale(self, client):
        t = _register_teacher(client).json()
        r = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "x", "scale_ids": ["mbti"], "roster_csv": _roster_csv(1),
        })
        assert r.status_code == 422

    def test_create_batch_missing_columns(self, client):
        t = _register_teacher(client).json()
        r = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "x", "scale_ids": ["phq_a"], "roster_csv": "姓名,年龄\n小明,15",
        })
        assert r.status_code == 422
        assert "缺少必填列" in r.json()["detail"]

    def test_create_batch_empty_cell_and_duplicate_no(self, client):
        t = _register_teacher(client).json()
        # 空学号
        r1 = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "x", "scale_ids": ["phq_a"],
            "roster_csv": "学号,姓名\nS001,小明\n,小红",
        })
        assert r1.status_code == 422
        # 学号重复
        r2 = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "x", "scale_ids": ["phq_a"],
            "roster_csv": "学号,姓名\nS001,小明\nS001,小红",
        })
        assert r2.status_code == 422
        assert "重复" in r2.json()["detail"]


# ---------------------------------------------------------------------------
# 学生筛查流程
# ---------------------------------------------------------------------------

class TestScreeningFlow:
    def _make_batch(self, client, scale_ids=None, n=2):
        t = _register_teacher(client).json()
        r = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": "测试批次",
            "scale_ids": scale_ids or ["phq_a"],
            "roster_csv": _roster_csv(n),
        })
        assert r.status_code == 200
        return t, r.json()

    def test_invalid_code_404(self, client):
        assert client.get("/api/screening/XXXXXX").status_code == 404

    def test_happy_path_and_stats(self, client):
        t, batch = self._make_batch(client, scale_ids=["phq_a"], n=2)
        code = batch["entries"][0]["entry_code"]

        # 1) 筛查码校验
        info = client.get(f"/api/screening/{code}")
        assert info.status_code == 200
        assert info.json()["status"] == "pending"
        assert info.json()["scale_ids"] == ["phq_a"]

        # 2) 小写输入也能命中（编码大小写不敏感）
        info_lower = client.get(f"/api/screening/{code.lower()}")
        assert info_lower.status_code == 200

        # 3) 提交作答（全 0 → 无症状）
        sub = client.post(f"/api/screening/{code}/submit",
                          json={"answers": {"phq_a": PHQ_A_ALL_ZERO}})
        assert sub.status_code == 200
        body = sub.json()
        assert body["has_crisis"] is False
        assert body["results"][0]["scale_id"] == "phq_a"

        # 4) 批次详情统计
        detail = client.get(f"/api/admin/batches/{batch['batch_id']}",
                            headers=_auth(t["token"]))
        assert detail.status_code == 200
        d = detail.json()
        assert d["total"] == 2 and d["completed"] == 1 and d["pending"] == 1
        assert d["severity_distribution"]["phq_a"]["none"] == 1
        assert d["by_class"]["高一 / 1班"]["completed"] == 1
        assert d["crisis_count"] == 0
        entry = next(e for e in d["entries"] if e["entry_code"] == code)
        assert entry["status"] == "completed"
        assert entry["assessments"][0]["total_score"] == 0

        # 5) 重复提交 → 409；再次查询 → completed
        assert client.post(f"/api/screening/{code}/submit",
                           json={"answers": {"phq_a": PHQ_A_ALL_ZERO}}).status_code == 409
        info2 = client.get(f"/api/screening/{code}")
        assert info2.json()["status"] == "completed"

    def test_crisis_entry_in_crisis_list(self, client):
        t, batch = self._make_batch(client, scale_ids=["phq_a"], n=2)
        code = batch["entries"][0]["entry_code"]
        sub = client.post(f"/api/screening/{code}/submit",
                          json={"answers": {"phq_a": PHQ_A_CRISIS}})
        assert sub.status_code == 200
        assert sub.json()["has_crisis"] is True

        d = client.get(f"/api/admin/batches/{batch['batch_id']}",
                       headers=_auth(t["token"])).json()
        assert d["crisis_count"] == 1
        c = d["crisis_list"][0]
        assert c["student_no"] == "S001"
        assert c["crisis_triggers"]

    def test_submit_wrong_scales_422(self, client):
        _, batch = self._make_batch(client, scale_ids=["phq_a", "scared"], n=1)
        code = batch["entries"][0]["entry_code"]
        # 缺 scared
        r = client.post(f"/api/screening/{code}/submit",
                        json={"answers": {"phq_a": PHQ_A_ALL_ZERO}})
        assert r.status_code == 422
        assert "缺少量表" in r.json()["detail"]

    def test_submit_missing_answers_422(self, client):
        _, batch = self._make_batch(client, scale_ids=["phq_a"], n=1)
        code = batch["entries"][0]["entry_code"]
        bad = dict(PHQ_A_ALL_ZERO)
        bad.pop("5")
        r = client.post(f"/api/screening/{code}/submit", json={"answers": {"phq_a": bad}})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# 批次管理：权限 / 关闭 / 导出 / 报告
# ---------------------------------------------------------------------------

class TestBatchManage:
    def _make_batch(self, client, label, scale_ids=None, n=2):
        t = _register_teacher(client, label=label).json()
        r = client.post("/api/admin/batches", headers=_auth(t["token"]), json={
            "name": f"批次-{label}", "scale_ids": scale_ids or ["phq_a"],
            "roster_csv": _roster_csv(n),
        })
        return t, r.json()

    def test_other_teacher_batch_404(self, client):
        _, batch_a = self._make_batch(client, "teachera")
        t_b, _ = self._make_batch(client, "teacherb")
        r = client.get(f"/api/admin/batches/{batch_a['batch_id']}",
                       headers=_auth(t_b["token"]))
        assert r.status_code == 404

    def test_list_only_own_batches(self, client):
        t_a, batch_a = self._make_batch(client, "teachera")
        t_b, _ = self._make_batch(client, "teacherb")
        items_a = client.get("/api/admin/batches", headers=_auth(t_a["token"])).json()["items"]
        assert len(items_a) == 1
        assert items_a[0]["batch_id"] == batch_a["batch_id"]

    def test_close_batch_disables_codes(self, client):
        t, batch = self._make_batch(client, "teacher1", n=1)
        code = batch["entries"][0]["entry_code"]
        assert client.get(f"/api/screening/{code}").status_code == 200

        r = client.post(f"/api/admin/batches/{batch['batch_id']}/close",
                        headers=_auth(t["token"]))
        assert r.status_code == 200
        assert r.json()["status"] == "closed"

        # 关闭后筛查码失效
        assert client.get(f"/api/screening/{code}").status_code == 410

    def test_export_csv(self, client):
        t, batch = self._make_batch(client, "teacher1", n=2)
        code = batch["entries"][0]["entry_code"]
        client.post(f"/api/screening/{code}/submit", json={"answers": {"phq_a": PHQ_A_ALL_ZERO}})

        r = client.get(f"/api/admin/batches/{batch['batch_id']}/export",
                       headers=_auth(t["token"]))
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        text = r.content.decode("utf-8-sig")
        assert "学号" in text and "S001" in text
        assert "已完成" in text and "未完成" in text

    def test_export_and_report_require_ownership(self, client):
        _, batch_a = self._make_batch(client, "teachera")
        t_b, _ = self._make_batch(client, "teacherb")
        bid = batch_a["batch_id"]
        assert client.get(f"/api/admin/batches/{bid}/export",
                          headers=_auth(t_b["token"])).status_code == 404
        assert client.get(f"/api/admin/batches/{bid}/entries/{batch_a['entries'][0]['entry_id']}/report",
                          headers=_auth(t_b["token"])).status_code == 404

    def test_entry_report_before_completion_400(self, client):
        t, batch = self._make_batch(client, "teacher1", n=1)
        eid = batch["entries"][0]["entry_id"]
        r = client.get(f"/api/admin/batches/{batch['batch_id']}/entries/{eid}/report",
                       headers=_auth(t["token"]))
        assert r.status_code == 400

    def test_entry_report_pdf_after_completion(self, client):
        t, batch = self._make_batch(client, "teacher1", n=1)
        code = batch["entries"][0]["entry_code"]
        sub = client.post(f"/api/screening/{code}/submit",
                          json={"answers": {"phq_a": PHQ_A_ALL_ZERO}})
        assert sub.status_code == 200

        eid = batch["entries"][0]["entry_id"]
        with patch("app.api.admin.generate_report_pdf", new=AsyncMock(return_value=b"%PDF-fake")):
            r = client.get(f"/api/admin/batches/{batch['batch_id']}/entries/{eid}/report",
                           headers=_auth(t["token"]))
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content.startswith(b"%PDF")

    def test_rename_batch(self, client):
        t, batch = self._make_batch(client, "teacher1", n=1)
        bid = batch["batch_id"]

        r = client.patch(f"/api/admin/batches/{bid}", headers=_auth(t["token"]),
                         json={"name": "新批次名"})
        assert r.status_code == 200
        assert r.json()["name"] == "新批次名"

        detail = client.get(f"/api/admin/batches/{bid}", headers=_auth(t["token"])).json()
        assert detail["name"] == "新批次名"

        # 空名 → 422
        assert client.patch(f"/api/admin/batches/{bid}", headers=_auth(t["token"]),
                            json={"name": ""}).status_code == 422

    def test_reopen_batch(self, client):
        t, batch = self._make_batch(client, "teacher1", n=1)
        bid = batch["batch_id"]

        client.post(f"/api/admin/batches/{bid}/close", headers=_auth(t["token"]))
        r = client.post(f"/api/admin/batches/{bid}/reopen", headers=_auth(t["token"]))
        assert r.status_code == 200
        assert r.json()["status"] == "active"

        # active 状态再 reopen → 400
        assert client.post(f"/api/admin/batches/{bid}/reopen",
                           headers=_auth(t["token"])).status_code == 400

    def test_delete_batch(self, client):
        t, batch = self._make_batch(client, "teacher1", n=2)
        bid = batch["batch_id"]

        r = client.delete(f"/api/admin/batches/{bid}", headers=_auth(t["token"]))
        assert r.status_code == 200
        assert r.json()["deleted_entries"] == 2

        # 列表不再有
        items = client.get("/api/admin/batches", headers=_auth(t["token"])).json()["items"]
        assert all(it["batch_id"] != bid for it in items)

        # 详情 404
        assert client.get(f"/api/admin/batches/{bid}",
                          headers=_auth(t["token"])).status_code == 404

        # 他人删除 → 404（不泄露存在性）
        t2, batch2 = self._make_batch(client, "teacher2", n=1)
        t_other, _ = self._make_batch(client, "teacher3", n=1)
        assert client.delete(f"/api/admin/batches/{batch2['batch_id']}",
                             headers=_auth(t_other["token"])).status_code == 404


# ---------------------------------------------------------------------------
# 旧库迁移：users.password_hash 列补齐
# ---------------------------------------------------------------------------

def test_migrate_users_password_hash_column():
    """模拟旧库（无 password_hash 列）→ init_db 后补列成功。"""
    import sqlite3
    import tempfile
    import os

    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.pool import StaticPool

    from app.db import Base, _migrate_sqlite_columns
    import app.models  # noqa: F401

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # 用原生 sqlite3 建一个"旧版 users 表"（无 password_hash）
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE users (id VARCHAR(32) PRIMARY KEY, label VARCHAR(64), "
            "role VARCHAR(16), profile JSON, consents JSON, "
            "token VARCHAR(64), created_at DATETIME)"
        )
        conn.commit()
        conn.close()

        engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        _migrate_sqlite_columns(engine)

        cols = [c["name"] for c in inspect(engine).get_columns("users")]
        assert "password_hash" in cols
        engine.dispose()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass
