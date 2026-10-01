// PsycheFlow 对话接口压测（k6）
//
// 目标端点：
//   - GET  /api/health       基线对照（无 LLM）
//   - POST /api/chat         真实对话（非流式，登录用户豁免 IP 限流）
//
// 运行：
//   k6 run backend/scripts/loadtest/chat_load.js
//   BASE_URL=http://localhost:8000 k6 run ...
//
// 说明：/api/chat 走完整多智能体链路（triage→RAG→LoRA 生成→质检），
// 本地模式单轮 ~3-7s，属预期；压测关注的是服务端在并发下的 P95/错误率，
// 而非绝对延迟（GPU 单卡串行推理，天然排队）。

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

// 自定义指标
const chatErrors = new Rate('chat_errors');
const chatLatency = new Trend('chat_latency_ms', true);
const healthLatency = new Trend('health_latency_ms', true);

export const options = {
  scenarios: {
    // 基线：health 端点高并发（验证 HTTP 栈本身）
    health_baseline: {
      executor: 'constant-vus',
      exec: 'hitHealth',
      vus: 50,
      duration: '30s',
      startTime: '0s',
    },
    // 对话：低并发长时（GPU 推理串行，10 VU 已能压满队列）
    chat_load: {
      executor: 'constant-vus',
      exec: 'hitChat',
      vus: 10,
      duration: '2m',
      startTime: '5s',
    },
  },
  thresholds: {
    chat_errors: ['rate<0.05'],           // 对话错误率 <5%
    http_req_failed: ['rate<0.05'],
  },
};

// setup：注册一个压测专用学生账号（登录用户豁免 IP 限流）
export function setup() {
  const res = http.post(`${BASE_URL}/api/auth/register`, JSON.stringify({
    consents: { tool: true, guardian: true, privacy14: true, crisis: true },
    profile: { name: 'loadtest', grade: '初三' },
    role: 'student',
  }), { headers: { 'Content-Type': 'application/json' } });

  if (res.status !== 200) {
    throw new Error(`注册失败: ${res.status} ${res.body}`);
  }
  const body = JSON.parse(res.body);
  return { token: body.token };
}

const CHAT_PROMPTS = [
  '我最近压力很大，晚上睡不着',
  '什么是焦虑？',
  '我总是担心考试成绩',
  '和同学闹矛盾了，心情不好',
  '怎样才能放松一点',
];

export function hitHealth() {
  const res = http.get(`${BASE_URL}/api/health`);
  healthLatency.add(res.timings.duration);
  check(res, { 'health 200': (r) => r.status === 200 });
}

export function hitChat(data) {
  const prompt = CHAT_PROMPTS[Math.floor(Math.random() * CHAT_PROMPTS.length)];
  const payload = JSON.stringify({
    message: prompt,
    history: [],
    persona_id: 'default',
  });

  const res = http.post(`${BASE_URL}/api/chat`, payload, {
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${data.token}`,
    },
    timeout: '120s',  // 本地 GPU 推理，单轮可能较慢
  });

  chatLatency.add(res.timings.duration);
  const ok = check(res, {
    'chat 200': (r) => r.status === 200,
    'chat 有回复': (r) => {
      try { return JSON.parse(r.body).reply?.length > 0; } catch { return false; }
    },
  });
  chatErrors.add(!ok);

  sleep(1);  // 模拟真实用户思考间隔
}

export function teardown(data) {
  // 压测账号保留（注册幂等冲突会 409，不影响）；如需清理可手动删 DB 记录
}
