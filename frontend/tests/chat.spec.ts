import { test, expect } from '@playwright/test';

test.describe('PsycheFlow E2E Chat Flow', () => {
  test('should register and chat successfully', async ({ page }) => {
    // 1. 访问注册页面
    await page.goto('/register');

    // 2. 勾选所有知情同意
    await page.locator('input[type="checkbox"]').nth(0).check();
    await page.locator('input[type="checkbox"]').nth(1).check();
    await page.locator('input[type="checkbox"]').nth(2).check();
    await page.locator('input[type="checkbox"]').nth(3).check();

    // 3. 点击注册按钮
    const registerBtn = page.getByRole('button', { name: '生成账号' });
    await expect(registerBtn).toBeEnabled();
    await registerBtn.click();

    // 4. 等待结果（增加超时时间，并检查是否有报错提示）
    const successModal = page.getByText('注册成功');
    const errorMsg = page.locator('.text-red-600, .bg-red-50'); // 捕获可能的错误提示
    
    await Promise.race([
      successModal.waitFor({ state: 'visible', timeout: 20000 }).catch(() => {}),
      errorMsg.waitFor({ state: 'visible', timeout: 20000 }).catch(() => {})
    ]);

    if (await errorMsg.isVisible()) {
      const txt = await errorMsg.textContent();
      throw new Error(`注册失败，后端返回错误: ${txt}`);
    }

    await expect(successModal).toBeVisible({ timeout: 5000 });
    await page.getByRole('button', { name: '前往测评' }).click();

    // 5. 跳转至测评选择页，点击“跳过测评，直接对话” (如果有的话，或者直接导航到 /chat)
    // 观察 PortalPage.tsx 或 HomePage.tsx
    await page.goto('/chat');

    // 6. 在聊天框输入信息
    const input = page.getByPlaceholder('输入你想聊的…');
    await input.fill('你好，我最近压力有点大');
    await page.keyboard.press('Enter');

    // 7. 检查是否有 AI 回复
    // 助手回复的容器包含 .bg-slate-100 类名
    const aiMessage = page.locator('.bg-slate-100');
    await expect(aiMessage.first()).toBeVisible({ timeout: 60000 }); // 增加到 60s，考虑到本地模型推理速度
    
    // 8. 验证是否包含 RAG 来源卡片 (如果触发了 RAG)
    // 根据 ChatPage.tsx，知识参考卡片在 .bg-slate-50.border-t.border-slate-200 容器中
    const sourcesContainer = page.locator('text=知识参考');
    if (await sourcesContainer.isVisible({ timeout: 5000 })) {
        console.log('RAG sources detected');
    }
  });
});
