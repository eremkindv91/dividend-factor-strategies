const { test, expect } = require('playwright/test');

async function openBonds(page) {
  await page.goto('/#bonds');
  await expect(page.getByRole('tab', { name: 'Надёжный портфель', exact: true })).toBeVisible();
}

test('three bond modes render production artifacts', async ({ page, viewport }) => {
  test.skip(viewport.width <= 768, 'desktop table contract');
  await openBonds(page);
  await expect(page.locator('#bondlab-panel')).toContainText('Quality gate: PASS');

  await page.getByRole('tab', { name: 'Все возможности', exact: true }).click();
  await expect(page.locator('.bav4-summary')).toContainText('Портфель сформирован');
  await expect(page.locator('.bav4-summary')).toContainText('12');
  await expect(page.locator('.bonds-table tbody tr').first()).toBeVisible();

  await page.getByRole('tab', { name: 'Все выпуски', exact: true }).click();
  await expect(page.locator('.bav4-found')).toContainText('857');
  await expect(page.locator('.bav4-table tbody tr').first()).toBeVisible();
});

test('bond details load lazily and scenario lab stays interactive', async ({ page, viewport }) => {
  test.skip(viewport.width <= 768, 'desktop lazy-detail contract');
  const detailRequests = [];
  page.on('request', (request) => {
    if (request.url().includes('/bonds/details/')) detailRequests.push(request.url());
  });

  await openBonds(page);
  await page.getByRole('tab', { name: 'Все выпуски', exact: true }).click();
  expect(detailRequests).toHaveLength(0);

  const openButton = page.locator('.bav4-table [data-bond-open]').first();
  await openButton.click();
  const dialog = page.locator('#bond-detail-dialog');
  await expect(dialog).toHaveAttribute('open', '');
  await expect(dialog.locator('.bav4-detail')).toBeVisible();
  expect(detailRequests).toHaveLength(1);
  await expect(dialog).toContainText('Relative Value');
  await expect(dialog).toContainText('Сценарный анализ');

  const result = dialog.locator('[data-scenario-result]');
  const before = await result.textContent();
  await dialog.locator('.bav4-heatmap button').last().click();
  await expect.poll(() => result.textContent()).not.toBe(before);
});

test('mobile bond explorer uses cards without page overflow', async ({ page, viewport }) => {
  test.skip(viewport.width > 768, 'mobile and tablet only');
  await openBonds(page);
  await page.getByRole('tab', { name: 'Все выпуски', exact: true }).click();
  await expect(page.locator('.bav4-table')).toBeHidden();
  await expect(page.locator('.bav4-card').first()).toBeVisible();
  const geometry = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(geometry.content).toBeLessThanOrEqual(geometry.viewport + 1);
});
