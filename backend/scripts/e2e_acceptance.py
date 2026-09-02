# -*- coding: utf-8 -*-
"""PsycheFlow 端到端验收脚本：覆盖主链路（健康→登录→会话→对话→危机→报告→审计）。

运行方式（推荐，容器内）：
    docker exec psycheflow-backend uv run python scripts/e2e_acceptance.py

也可在宿主机直连运行（HTTP 部分照常；DB/文件检查会自动回退到 docker exec）：
    uv run python backend/scripts/e2e_acceptance.py --base-url http://localhost:8000

幂等：每次跑都用新的 session_id，不依赖固定数据；登录优先复用固定 e2e 教师
账号（首次 register，后续 login_by_password）。

SSE 用 httpx stream=True 逐行读 event:/data:。
"""
import argparse
import asyncio
import glob
import json
import os
import subprocess
import sys
import time

import httpx

# 容器内 /app 是工作目录；宿主机运行时下面这行无效但无害
sys.path.insert(0, "/app")

BASE_URL_DEFAULT = "http://localhost:8000"
TIMEOUT = 120.0

# 固定 e2e 教师账号：首次 register，后续 login_by_password（幂等）
E2E_LABEL = "e2e-runner"
E2E_PASSWORD = "e2e_runner_pwd"

# MHT 报告 6 大章节标题（report.html 静态文案）
MHT_CHAPTERS = [
    "1. 测评工具介绍",
    "2. 测评结果解读注意事项",
    "3. 测评人员信息",
    "4. 测评结果",
    "5. 测评结果剖析",
    "6. 发展建议",
]


# ---------------------------------------------------------------------------
# 结果收集
# ---------------------------------------------------------------------------
class Step:
    def __init__(self, name: str):
        self.name = name
        self.ok = False
        self.evidence = ""
        self.error = ""

    def pass_(self, evidence: str):
        self.ok = True
        self.evidence = evidence
        print(f"[PASS] {self.name}: {evidence}")

    def fail(self, error: str, evidence: str = ""):
        self.ok = False
        self.error = error
        self.evidence = evidence
        print(f"[FAIL] {self.name}: {error}  {evidence}")


RESULTS: list[Step] = []


def step(name: str) -> Step:
    s = Step(name)
    RESULTS.append(s)
    return s


# ---------------------------------------------------------------------------
# SSE 解析
# ---------------------------------------------------------------------------
def parse_sse_block(block: str):
    """解析单条 SSE 事件块 → (event_type, data_dict)。"""
    event = "message"
    data_str = ""
    for line in block.split("\n"):
        if line.startswith("event: "):
            event = line[7:].strip()
        elif line.startswith("data: "):
            data_str += line[6:]
    try:
        data = json.loads(data_str) if data_str else {}
    except json.JSONDecodeError:
        data = {"raw": data_str}
    return event, data


async def stream_chat(base_url: str, headers: dict, payload: dict, timeout: float = TIMEOUT):
    """跑一次 SSE 流式对话。

    返回 (events, tokens_str, done_data, crisis_data)。
    events: [(event_type, data_dict), ...]
    """
    events: list[tuple[str, dict]] = []
    tokens: list[str] = []
    done_data: dict = {}
    crisis_data: dict = {}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as c:
        async with c.stream("POST", "/api/chat/stream", json=payload, headers=headers) as r:
            if r.status_code != 200:
                body = await r.aread()
                raise RuntimeError(f"HTTP {r.status_code}: {body[:300]!r}")
            buf = ""
            async for chunk in r.aiter_text():
                buf += chunk
                while "\n\n" in buf:
                    block, buf = buf.split("\n\n", 1)
                    if not block.strip():
                        continue
                    ev, data = parse_sse_block(block)
                    events.append((ev, data))
                    if ev == "token":
                        tokens.append(data.get("token", ""))
                    elif ev == "crisis":
                        crisis_data = data
                    elif ev == "done":
                        done_data = data
    return events, "".join(tokens), done_data, crisis_data


# ---------------------------------------------------------------------------
# 容器内/宿主机自适应：DB 与文件检查
# ---------------------------------------------------------------------------
_IN_CONTAINER: bool | None = None


def in_container() -> bool:
    """是否能在当前进程直接 import app（容器内运行时为 True）。"""
    global _IN_CONTAINER
    if _IN_CONTAINER is None:
        try:
            import app  # noqa: F401
            _IN_CONTAINER = True
        except Exception:
            _IN_CONTAINER = False
    return _IN_CONTAINER


def _docker_exec_py(code: str, timeout: int = 90) -> str:
    """宿主机回退：docker exec psycheflow-backend uv run python -c <code>，返回 stdout。"""
    proc = subprocess.run(
        ["docker", "exec", "psycheflow-backend", "uv", "run", "python", "-c", code],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"docker exec 失败(rc={proc.returncode}): {proc.stderr[:500]}")
    return proc.stdout.strip()


def query_audit_logs(session_id: str) -> list[dict]:
    """查 AuditLog 表中本次会话相关行，返回 [{event_type, ts, payload}]。"""
    if in_container():
        from app.db import SessionLocal  # type: ignore
        from app.models import AuditLog  # type: ignore
        from sqlalchemy import select  # type: ignore
        db = SessionLocal()
        try:
            rows = db.execute(
                select(AuditLog).where(AuditLog.session_id == session_id)
            ).scalars().all()
            return [
                {
                    "event_type": r.event_type,
                    "ts": r.ts.isoformat() if r.ts else None,
                    "payload": r.payload,
                }
                for r in rows
            ]
        finally:
            db.close()
    code = (
        "import json; from app.db import SessionLocal; from app.models import AuditLog; "
        "from sqlalchemy import select; db=SessionLocal(); "
        f"rows=db.execute(select(AuditLog).where(AuditLog.session_id=={session_id!r})).scalars().all(); "
        "print(json.dumps([{'event_type':r.event_type,'ts':r.ts.isoformat() if r.ts else None,"
        "'payload':r.payload} for r in rows], ensure_ascii=False, default=str)); db.close()"
    )
    out = _docker_exec_py(code)
    return json.loads(out) if out else []


def find_crisis_files(session_id: str) -> list[str]:
    """返回 crisis_{session_id}_*.json 审计文件路径列表。"""
    if in_container():
        from app.core.config import settings  # type: ignore
        pattern = os.path.join(settings.logs_dir, f"crisis_{session_id}_*.json")
        return sorted(glob.glob(pattern))
    code = (
        "import json, glob, os; from app.core.config import settings; "
        f"files=sorted(glob.glob(os.path.join(settings.logs_dir, 'crisis_'+{session_id!r}+'_*.json'))); "
        "print(json.dumps(files, ensure_ascii=False))"
    )
    out = _docker_exec_py(code)
    return json.loads(out) if out else []


def render_report_html_for_session(session_id: str) -> str:
    """渲染报告 HTML（narrative 传空，免 LLM），用于校验 6 章标识。"""
    if in_container():
        from app.db import SessionLocal  # type: ignore
        from app.models import Session as SessionModel  # type: ignore
        from app.reports.service import render_report_html  # type: ignore
        db = SessionLocal()
        try:
            s = db.get(SessionModel, session_id)
            if s is None:
                return ""
            assessments = [
                {
                    "scale_id": a.scale_id,
                    "scale_name": a.scale_name,
                    "total_score": a.total_score,
                    "severity": a.severity,
                    "crisis_level": a.crisis_level,
                    "crisis_triggers": a.crisis_triggers,
                    "interpretation": a.interpretation,
                    "needs_crisis_escalation": a.needs_crisis_escalation,
                    "answers": a.answers,
                }
                for a in s.assessments
            ]
            return render_report_html(s, assessments, "")
        finally:
            db.close()
    code = (
        "import json; from app.db import SessionLocal; from app.models import Session as S; "
        "from app.reports.service import render_report_html; db=SessionLocal(); "
        f"s=db.get(S, {session_id!r}); "
        "assessments=[{'scale_id':a.scale_id,'scale_name':a.scale_name,'total_score':a.total_score,"
        "'severity':a.severity,'crisis_level':a.crisis_level,'crisis_triggers':a.crisis_triggers,"
        "'interpretation':a.interpretation,'needs_crisis_escalation':a.needs_crisis_escalation,"
        "'answers':a.answers} for a in (s.assessments if s else [])]; "
        "html=render_report_html(s, assessments, '') if s else ''; db.close(); "
        "print(json.dumps(html, ensure_ascii=False))"
    )
    return json.loads(_docker_exec_py(code))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
async def main(base_url: str):
    headers: dict = {}
    auth_note = ""

    # ===== 步骤 1：健康检查 =====
    s1 = step("1. 健康检查 GET /api/health")
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=20) as c:
            r = await c.get("/api/health")
        if r.status_code == 200 and r.json().get("status") == "ok":
            s1.pass_(f"HTTP {r.status_code} status=ok bailian_configured={r.json().get('bailian_configured')}")
        else:
            s1.fail(f"健康检查异常 HTTP {r.status_code}", str(r.text[:200]))
    except Exception as e:
        s1.fail(f"请求失败: {type(e).__name__}: {e}")

    # ===== 步骤 2：登录拿 token（优先 login_by_password，否则 register 教师）=====
    s2 = step("2. 登录拿 token（login_by_password 优先）")
    token = None
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
            r = await c.post(
                "/api/auth/login_by_password",
                json={"label": E2E_LABEL, "password": E2E_PASSWORD},
            )
        if r.status_code == 200:
            token = r.json().get("token")
            auth_note = f"login_by_password 复用现有账号 label={E2E_LABEL}"
            s2.pass_(f"{auth_note}  token={token[:8]}...")
        else:
            # 不存在或密码不对 → 注册教师（label 冲突则用唯一后缀回退）
            reg_label = E2E_LABEL
            async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
                rr = await c.post(
                    "/api/auth/register",
                    json={
                        "consents": {"tool": True, "guardian": True, "privacy14": True, "crisis": True},
                        "profile": {"name": "e2e-runner", "teacher_email": "e2e@example.com"},
                        "role": "teacher",
                        "label": E2E_LABEL,
                        "password": E2E_PASSWORD,
                    },
                )
            if rr.status_code == 200:
                reg_label = E2E_LABEL
                token = rr.json().get("token")
                auth_note = f"register 新教师 label={E2E_LABEL}（login_by_password 不存在该账号，首次注册）"
            elif rr.status_code in (409, 422):
                # 固定 label 被占（密码不一致）→ 回退唯一 label 注册
                import uuid as _uuid
                reg_label = f"e2e-runner-{_uuid.uuid4().hex[:8]}"
                async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
                    rr2 = await c.post(
                        "/api/auth/register",
                        json={
                            "consents": {"tool": True, "guardian": True, "privacy14": True, "crisis": True},
                            "profile": {"name": reg_label},
                            "role": "teacher",
                            "label": reg_label,
                            "password": E2E_PASSWORD,
                        },
                    )
                if rr2.status_code != 200:
                    raise RuntimeError(f"register 回退失败 HTTP {rr2.status_code}: {rr2.text[:200]}")
                token = rr2.json().get("token")
                auth_note = f"register 新教师 label={reg_label}（固定 label 被占，回退唯一 label）"
            else:
                raise RuntimeError(f"register 失败 HTTP {rr.status_code}: {rr.text[:200]}")
            # 注册成功后用 login_by_password 验证密码登录链路
            async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
                rl = await c.post(
                    "/api/auth/login_by_password",
                    json={"label": reg_label, "password": E2E_PASSWORD},
                )
            if rl.status_code == 200:
                token = rl.json().get("token")
                auth_note += "；随后 login_by_password 验证 OK"
            s2.pass_(f"{auth_note}  token={token[:8]}...")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception as e:
        s2.fail(f"登录失败: {type(e).__name__}: {e}")

    if not token:
        print("\n[致命] 未拿到 token，后续步骤无法继续。")
        return _summary()

    # ===== 步骤 3：建会话 =====
    s3 = step("3. 建会话 POST /api/sessions")
    session_id = None
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
            r = await c.post("/api/sessions", json={"label": "e2e-acceptance"}, headers=headers)
        if r.status_code == 200 and r.json().get("session_id"):
            session_id = r.json()["session_id"]
            s3.pass_(f"session_id={session_id} created_at={r.json().get('created_at')}")
        else:
            s3.fail(f"建会话异常 HTTP {r.status_code}", r.text[:200])
    except Exception as e:
        s3.fail(f"请求失败: {type(e).__name__}: {e}")

    if not session_id:
        print("\n[致命] 未拿到 session_id，后续步骤无法继续。")
        return _summary()

    # ===== 步骤 4：正常对话（非危机）=====
    s4 = step("4. 正常对话 POST /api/chat/stream（非危机，断言 token + intervention）")
    try:
        events, tokens_str, done_data, crisis_data = await stream_chat(
            base_url, headers,
            {"message": "最近考试压力有点大，晚上总是睡不好，心里很烦。", "session_id": session_id},
        )
        ev_types = [e[0] for e in events]
        n_tokens = ev_types.count("token")
        final_agent = done_data.get("current_agent", "")
        is_crisis = done_data.get("crisis", False)
        if n_tokens > 0 and final_agent == "intervention" and not is_crisis:
            s4.pass_(
                f"token事件={n_tokens} current_agent={final_agent} crisis={is_crisis} "
                f"reply预览={tokens_str[:60]!r} 事件序列={ev_types}"
            )
        else:
            s4.fail(
                f"断言失败 token事件={n_tokens} current_agent={final_agent!r} crisis={is_crisis}",
                f"事件序列={ev_types} reply={tokens_str[:80]!r}",
            )
    except Exception as e:
        s4.fail(f"请求失败: {type(e).__name__}: {e}")

    # ===== 步骤 5：危机对话 =====
    s5 = step("5. 危机对话 POST /api/chat/stream（断言 crisis 事件 + 12355 + 落盘文件）")
    try:
        events, tokens_str, done_data, crisis_data = await stream_chat(
            base_url, headers,
            {"message": "我真的很痛苦，我想结束生命。", "session_id": session_id},
        )
        ev_types = [e[0] for e in events]
        has_crisis_ev = "crisis" in ev_types
        crisis_reply = crisis_data.get("reply", "") or done_data.get("reply", "")
        has_hotline = "12355" in crisis_reply
        final_agent = done_data.get("current_agent", "")
        is_crisis_done = bool(done_data.get("crisis", False))
        crisis_path_ok = has_crisis_ev and has_hotline and is_crisis_done and final_agent in ("escalation", "crisis")

        # 落盘文件检查
        files = find_crisis_files(session_id)
        file_ok = len(files) > 0

        if crisis_path_ok and file_ok:
            s5.pass_(
                f"crisis事件={has_crisis_ev} 含12355={has_hotline} current_agent={final_agent} "
                f"crisis={is_crisis_done} 落盘文件={len(files)}个  事件序列={ev_types}  "
                f"文件={os.path.basename(files[0])}"
            )
        else:
            s5.fail(
                f"crisis事件={has_crisis_ev} 含12355={has_hotline} current_agent={final_agent!r} "
                f"crisis={is_crisis_done} 落盘文件={len(files)}个",
                f"事件序列={ev_types} reply={crisis_reply[:120]!r}",
            )
    except Exception as e:
        s5.fail(f"请求失败: {type(e).__name__}: {e}")

    # ===== 步骤 6：生成报告（先提交评估，再 POST/GET 报告 + 6 章校验 + 发展建议非空）=====
    s6 = step("6. 生成报告（提交评估→POST/GET 报告→MHT 6 章→发展建议非空）")
    try:
        # 6a 提交 PHQ-A 评估（低分、q9=0 非危机，使报告可生成）
        async with httpx.AsyncClient(base_url=base_url, timeout=30) as c:
            ra = await c.post(
                f"/api/sessions/{session_id}/assessments",
                json={
                    "scale_id": "phq_a",
                    "answers": {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "7": 1, "8": 1, "9": 0},
                },
                headers=headers,
            )
        if ra.status_code != 200:
            raise RuntimeError(f"提交评估失败 HTTP {ra.status_code}: {ra.text[:200]}")
        assess = ra.json()

        # 6b POST 生成报告
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as c:
            rp = await c.post(f"/api/sessions/{session_id}/report", headers=headers)
        post_ok = rp.status_code == 200 and rp.headers.get("content-type", "").startswith("application/pdf") and len(rp.content) > 1000

        # 6c GET 取报告
        async with httpx.AsyncClient(base_url=base_url, timeout=120) as c:
            rg = await c.get(f"/api/sessions/{session_id}/report", headers=headers)
        get_ok = rg.status_code == 200 and rg.headers.get("content-type", "").startswith("application/pdf") and len(rg.content) > 1000

        # 6d 渲染 HTML 校验 6 章标识
        html = render_report_html_for_session(session_id)
        chapters_present = [t for t in MHT_CHAPTERS if t in html]
        chapters_ok = len(chapters_present) == len(MHT_CHAPTERS)

        # 6e 发展建议非空：取报告审计行的 narrative_len
        audit_rows = query_audit_logs(session_id)
        report_audit = next((r for r in audit_rows if r["event_type"] == "report"), None)
        narrative_len = (report_audit or {}).get("payload", {}).get("narrative_len", 0) if report_audit else 0
        narrative_ok = narrative_len > 0

        if post_ok and get_ok and chapters_ok and narrative_ok:
            s6.pass_(
                f"POST报告={len(rp.content)}B GET报告={len(rg.content)}B "
                f"评估={assess.get('scale_id')}={assess.get('total_score')}分({assess.get('severity')}) "
                f"6章齐全={chapters_ok} 发展建议={narrative_len}字"
            )
        else:
            s6.fail(
                f"POST_ok={post_ok} GET_ok={get_ok} 6章齐全={chapters_ok}({len(chapters_present)}/{len(MHT_CHAPTERS)}) "
                f"发展建议非空={narrative_ok}({narrative_len}字)",
                f"已有章节={chapters_present}",
            )
    except Exception as e:
        s6.fail(f"请求失败: {type(e).__name__}: {e}")

    # ===== 步骤 7：审计落库 =====
    s7 = step("7. 审计落库（AuditLog 表有本次会话相关行）")
    try:
        # 重新查（步骤 6 已写 report 审计）
        audit_rows = query_audit_logs(session_id)
        event_types = sorted({r["event_type"] for r in audit_rows})
        has_crisis_audit = "crisis" in event_types
        has_report_audit = "report" in event_types
        if has_crisis_audit and has_report_audit and len(audit_rows) >= 2:
            detail = ", ".join(f"{r['event_type']}@{r['ts']}" for r in audit_rows)
            s7.pass_(f"共{len(audit_rows)}行 event_types={event_types}  [{detail}]")
        else:
            s7.fail(
                f"审计行不完整 共{len(audit_rows)}行 event_types={event_types} "
                f"crisis审计={has_crisis_audit} report审计={has_report_audit}",
                str([r["event_type"] for r in audit_rows]),
            )
    except Exception as e:
        s7.fail(f"查询审计失败: {type(e).__name__}: {e}")

    return _summary()


def _summary() -> int:
    print("\n" + "=" * 70)
    print("端到端验收摘要")
    print("=" * 70)
    n_pass = 0
    for s in RESULTS:
        flag = "PASS" if s.ok else "FAIL"
        line = f"  [{flag}] {s.name}"
        if s.ok:
            line += f"  — {s.evidence}"
        else:
            line += f"  — {s.error}"
            if s.evidence:
                line += f"  | {s.evidence}"
        print(line)
        if s.ok:
            n_pass += 1
    print("-" * 70)
    print(f"结果：{n_pass}/{len(RESULTS)} 步通过")
    failed = [s for s in RESULTS if not s.ok]
    if failed:
        print("\n失败原因：")
        for s in failed:
            print(f"  • {s.name}: {s.error}")
            if s.evidence:
                print(f"      证据: {s.evidence}")
    return 0 if not failed else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="PsycheFlow 端到端验收脚本")
    p.add_argument("--base-url", default=BASE_URL_DEFAULT, help="后端 base url")
    args = p.parse_args()
    t0 = time.time()
    rc = asyncio.run(main(args.base_url))
    print(f"\n总耗时：{time.time() - t0:.1f}s")
    sys.exit(rc)
