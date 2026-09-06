import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  use: {
    // 适配用户实际运行的 5174 端口
    baseURL: 'http://localhost:5174',
    trace: 'on-first-retry',
    viewport: { width: 1280, height: 720 },
  },
  // 自动化：跑测试前自动检查/启动前端服务
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5174',
    reuseExistingServer: true,
    stdout: 'ignore',
    stderr: 'pipe',
  },
});
