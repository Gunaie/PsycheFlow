# /// script
# requires-python = ">=3.11"
# dependencies = ["playwright>=1.40", "httpx>=0.27"]
# ///
# -*- coding: utf-8 -*-
"""README 界面截图脚本（P3 开源打磨）。

流程：
  1. API 造数：教师账号 + 演示批次（5 名学生，完成 4 份，其中 1 份触发危机红名单）
             + 演示学生账号 + 一份已提交的 PHQ-A 测评（供历史页展示）
  2. Playwright 无头浏览器按角色（访客/学生/教师）截取 8 张核心页面图
  3. 输出到 docs/screenshots/*.png（1440x900 @2x）

用法（宿主机，需前后端容器已启动；首次先装浏览器内核）：
  uv run --with playwright playwright install chromium   # 仅首次
  uv run scripts/screenshots.py                          # 从 backend/ 目录运行
"""
import os
import random
import sys
import time

import httpx
from playwright.sync_api import sync_playwright

FRONTEND = os.environ.get("SHOTS_FRONTEND", "http://localhost:5174")
BACKEND = os.environ.get("SHOTS_BACKEND", "http://localhost:8000")
OUT = os.environ.get("SHOTS_OUT", "docs/screenshots")

TEACHER_LABEL = "demo_teacher_shots"
TEACHER_PASSWORD = "Demo123456"
STUDENT_LABEL = "demo_student_shots"

CONSENTS = {"tool": True, "guardian": True, "privacy14": True, "crisis": True}

ROSTER = """学号,姓名,年级,班级
D2401,李明轩,初三,1班
D2402,王雨桐,初三,1班
D2403,张子航,初三,1班
D2404,陈思彤,初三,2班
D2405,刘一诺,初三,2班"""

VIEW = {"viewport": {"width": 1440, "height": 900}, "device_scale_factor": 2, "locale": "zh-CN"}


def api(client: httpx.Client, method: str, path: str, token: str | None = None, **kw) -> dict:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = client.request(method, f"{BACKEND}{path}", headers=headers, timeout=60, **kw)
    if r.status_code >= 300:
        raise RuntimeError(f"{method} {path} -> {r.status_code}: {r.text[:300]}")
    return r.json()


def seed(client: httpx.Client) -> dict:
    """造数：教师/批次/学生测评。返回截图所需的 id 与 token。"""
    # 教师账号（已存在则走密码登录）
    reg = {"consents": CONSENTS, "role": "teacher", "label": TEACHER_LABEL, "password": TEACHER_PASSWORD}
    try:
        teacher = api(client, "POST", "/api/auth/register", json=reg)
    except RuntimeError:
        teacher = api(
            client, "POST", "/api/auth/login_by_password",
            json={"label": TEACHER_LABEL, "password": TEACHER_PASSWORD},
        )
    ttoken = teacher["token"]

    # 演示批次：5 名学生，phq_a + scared
    batch = api(
        client, "POST", "/api/admin/batches", token=ttoken,
        json={"name": "演示批次-初三心理筛查", "scale_ids": ["phq_a", "scared"], "roster_csv": ROSTER},
    )
    entries = batch["entries"]

    def answers_for(scale_id: str, style: str) -> dict:
        n = 9 if scale_id == "phq_a" else 41
        if style == "zero":
            return {str(i): 0 for i in range(1, n + 1)}
        if style == "crisis":
            a = {str(i): random.randint(0, 2) for i in range(1, n + 1)}
            return {**a, "9": 1} if scale_id == "phq_a" else a  # 第 9 题=1 → 危机名单
        return {str(i): random.randint(0, 2) for i in range(1, n + 1)}

    styles = ["zero", "mild", "mild", "crisis"]  # 第 5 人不提交 → 批次有未完成进度
    for entry, style in zip(entries[:4], styles):
        api(
            client, "POST", f"/api/screening/{entry['entry_code']}/submit",
            json={"answers": {"phq_a": answers_for("phq_a", style), "scared": answers_for("scared", style)}},
        )

    # 演示学生账号 + 已提交的 PHQ-A 测评（历史页展示）
    sreg = {
        "consents": CONSENTS, "role": "student", "label": STUDENT_LABEL, "password": TEACHER_PASSWORD,
        "profile": {"name": "林小语", "gender": "女", "age": 14, "student_no": "S2401", "grade": "初三", "klass": "1班"},
    }
    try:
        student = api(client, "POST", "/api/auth/register", json=sreg)
    except RuntimeError:
        student = api(
            client, "POST", "/api/auth/login_by_password",
            json={"label": STUDENT_LABEL, "password": TEACHER_PASSWORD},
        )
    stoken = student["token"]
    session = api(client, "POST", "/api/sessions", token=stoken, json={"label": None})
    api(
        client, "POST", f"/api/sessions/{session['session_id']}/assessments", token=stoken,
        json={"scale_id": "phq_a", "answers": {str(i): 1 for i in range(1, 10) if i != 9} | {"9": 0}},
    )

    crisis_entry = next((e for e, s in zip(entries, styles) if s == "crisis"), None)
    return {"ttoken": ttoken, "stoken": stoken, "batch_id": batch["batch_id"], "batch_name": batch["name"]}


def auth_script(token: str, label: str, role: str) -> str:
    return (
        f"localStorage.setItem('psycheflow_token', '{token}');"
        f"localStorage.setItem('psycheflow_label', '{label}');"
        f"localStorage.setItem('psycheflow_role', '{role}');"
    )


def shoot(pw, data: dict) -> None:
    os.makedirs(OUT, exist_ok=True)
    browser = pw.chromium.launch()

    def snap(ctx, path: str, filename: str, settle: float = 1.2) -> None:
        page = ctx.new_page()
        page.goto(f"{FRONTEND}{path}", wait_until="networkidle")
        time.sleep(settle)
        page.screenshot(path=os.path.join(OUT, filename))
        print(f"  [ok] {filename}")
        page.close()

    # 1) 访客：门户
    anon = browser.new_context(**VIEW)
    snap(anon, "/", "01-portal.png", settle=2.0)
    anon.close()

    # 2) 学生：首页 / 量表选择 / 作答 / 对话 / 历史
    stu = browser.new_context(**VIEW)
    stu.add_init_script(auth_script(data["stoken"], STUDENT_LABEL, "student"))
    snap(stu, "/home", "02-home.png")
    snap(stu, "/assess", "03-scale-select.png")
    # 作答页：点 3 题让页面有进度
    page = stu.new_page()
    page.goto(f"{FRONTEND}/assess/phq_a", wait_until="networkidle")
    for _ in range(3):
        try:
            page.locator("text=完全不会").first.click(timeout=3000)
            time.sleep(0.4)
        except Exception:
            break
    time.sleep(1.0)
    page.screenshot(path=os.path.join(OUT, "04-assessment.png"))
    print("  [ok] 04-assessment.png")
    page.close()
    snap(stu, "/chat", "05-chat.png")
    snap(stu, "/history", "06-history.png")
    stu.close()

    # 3) 教师：批次列表 / 批次详情（含进度、严重度分布、危机红名单）
    tea = browser.new_context(**VIEW)
    tea.add_init_script(auth_script(data["ttoken"], TEACHER_LABEL, "teacher"))
    snap(tea, "/admin", "07-admin-batches.png")
    snap(tea, f"/admin/batches/{data['batch_id']}", "08-admin-batch-detail.png", settle=2.0)
    tea.close()

    browser.close()


def main() -> int:
    with httpx.Client() as client:
        print("== 造数中（教师/批次/学生测评）...")
        data = seed(client)
        print(f"   batch={data['batch_id']} name={data['batch_name']}")
    print("== 截图中 ...")
    with sync_playwright() as pw:
        shoot(pw, data)
    print(f"== 完成，输出目录: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
