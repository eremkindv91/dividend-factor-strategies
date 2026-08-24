const { test, expect } = require('playwright/test');

async function goto(page, hash) {
  await page.goto(`/${hash}`);
  await page.locator('#app-nav .section-tab').first().waitFor({ state: 'attached' });
}

async function expectNoPageOverflow(page, hash) {
  await goto(page, hash);
  await expect.poll(() => page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }))).toEqual(expect.objectContaining({ clientWidth: page.viewportSize().width }));
  const geometry = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  const offenders = geometry.scrollWidth > geometry.clientWidth + 1
    ? await page.evaluate(() => [...document.querySelectorAll('body *')]
      .map((node) => {
        const bounds = node.getBoundingClientRect();
        return {
          selector: `${node.tagName.toLowerCase()}${node.id ? `#${node.id}` : ''}${node.classList.length ? `.${[...node.classList].join('.')}` : ''}`,
          left: Math.round(bounds.left),
          right: Math.round(bounds.right),
        };
      })
      .filter((item) => item.left < -1 || item.right > document.documentElement.clientWidth + 1)
      .slice(0, 8))
    : [];
  expect(
    geometry.scrollWidth,
    `${hash} must fit the viewport; offenders: ${JSON.stringify(offenders)}`,
  ).toBeLessThanOrEqual(geometry.clientWidth + 1);
}

test.describe('mobile responsive data surfaces', () => {
  test.skip(({ viewport }) => viewport.width > 768, 'mobile and tablet only');

  test('key routes do not create page-level horizontal scrolling', async ({ page }) => {
    for (const hash of [
      '#market', '#market?tab=news', '#stocks', '#stocks?tab=banks',
      '#my-portfolio', '#strategies', '#bonds',
    ]) {
      await expectNoPageOverflow(page, hash);
    }
  });

  test('bond explorer keeps filters compact and switches to cards', async ({ page }) => {
    await goto(page, '#bonds');
    await page.getByRole('tab', { name: 'Все выпуски', exact: true }).click();

    const controls = page.locator('.bav4-explorer-controls');
    await expect(controls).toBeVisible();
    await expect(controls.locator('[data-bav4-control="query"]')).toBeVisible();
    await expect(controls.locator('[data-bav4-control="structure"]')).toBeVisible();
    await expect(page.locator('.bav4-table')).toBeHidden();
    await expect(page.locator('.bav4-cards')).toBeVisible();
    await expect(page.locator('.bav4-card').first()).toBeVisible();

    const controlHeights = await controls.locator('input, select').evaluateAll(
      (nodes) => nodes.map((node) => node.getBoundingClientRect().height),
    );
    expect(Math.min(...controlHeights)).toBeGreaterThanOrEqual(38);
  });

  test('strategy tables keep ticker and header visible while scrolling', async ({ page }) => {
    await goto(page, '#strategies');
    const wrapper = page.locator('.quality-table-wrap');
    await expect(wrapper).toHaveClass(/is-long-table/);
    await wrapper.evaluate((node) => node.scrollTo({ left: 260, top: 420 }));

    const sticky = await wrapper.evaluate((node) => {
      const bounds = node.getBoundingClientRect();
      const header = node.querySelector('thead th:first-child').getBoundingClientRect();
      const rank = node.querySelector('tbody td:first-child').getBoundingClientRect();
      const ticker = node.querySelector('tbody td:nth-child(2)').getBoundingClientRect();
      return {
        headerTopDelta: Math.abs(header.top - bounds.top),
        rankLeftDelta: Math.abs(rank.left - bounds.left),
        tickerLeftDelta: Math.abs(ticker.left - bounds.left - 40),
      };
    });
    expect(sticky.headerTopDelta).toBeLessThanOrEqual(2);
    expect(sticky.rankLeftDelta).toBeLessThanOrEqual(2);
    expect(sticky.tickerLeftDelta).toBeLessThanOrEqual(2);
  });

  test('bank tables keep the bank identity column pinned', async ({ page }) => {
    await goto(page, '#stocks?tab=banks');
    const wrapper = page.locator('.riv-table-wrap');
    await wrapper.waitFor({ state: 'visible' });
    await expect(wrapper).toHaveClass(/has-sticky-identity/);
    const identityPosition = await wrapper.locator('tbody td:first-child').first().evaluate(
      (node) => getComputedStyle(node).position,
    );
    expect(identityPosition).toBe('sticky');
  });

  test('stock and market charts remain readable and touch friendly', async ({ page }) => {
    await goto(page, '#stocks');
    const stockDetails = page.locator('#cards details[data-i]').first();
    let stockChart;
    if (await stockDetails.locator('summary').isVisible()) {
      await stockDetails.locator('summary').click();
      stockChart = stockDetails.locator('.stock-chart');
    } else {
      await page.locator('.table-card tbody tr.data-row').first().click();
      stockChart = page.locator('.table-card tr.detail-row .stock-chart');
    }
    await expect(stockChart).toBeVisible();
    const stockCanvasHeight = await stockChart.locator('.sc-canvas').evaluate(
      (node) => node.getBoundingClientRect().height,
    );
    expect(stockCanvasHeight).toBeGreaterThanOrEqual(300);
    const stockControlHeights = await stockChart.locator(
      '.sc-periods button, .sc-pf-toggle, .sc-fi-toggle, .chart-fs-toggle',
    ).evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height));
    expect(Math.min(...stockControlHeights)).toBeGreaterThanOrEqual(44);
    const fullscreenControlWidth = await stockChart.locator('.chart-fs-toggle').evaluate(
      (node) => node.getBoundingClientRect().width,
    );
    expect(fullscreenControlWidth).toBeGreaterThanOrEqual(44);

    if (page.viewportSize().width === 390) {
      await stockChart.locator('.chart-fs-toggle').click();
      const fullscreenChart = page.locator('body > .stock-chart.is-chart-fullscreen');
      await expect(fullscreenChart).toBeVisible();
      await page.setViewportSize({ width: 844, height: 390 });
      const landscape = await fullscreenChart.evaluate((node) => {
        const canvas = node.querySelector('.sc-canvas').getBoundingClientRect();
        const bounds = node.getBoundingClientRect();
        return {
          width: bounds.width,
          height: bounds.height,
          canvasWidth: canvas.width,
          canvasHeight: canvas.height,
          pageOverflow: document.documentElement.scrollWidth - window.innerWidth,
        };
      });
      expect(landscape.width).toBeGreaterThanOrEqual(843);
      expect(landscape.height).toBeGreaterThanOrEqual(389);
      expect(landscape.canvasWidth).toBeGreaterThanOrEqual(700);
      expect(landscape.canvasHeight).toBeGreaterThanOrEqual(180);
      expect(landscape.pageOverflow).toBeLessThanOrEqual(1);
      await page.setViewportSize({ width: 390, height: 844 });
      await fullscreenChart.locator('.chart-fs-toggle').click();
      await expect(page.locator('body')).not.toHaveClass(/has-chart-fullscreen/);
    }

    await goto(page, '#market');
    const marketCard = page.locator('[data-market-id]').first();
    await marketCard.waitFor({ state: 'visible' });
    await marketCard.click();
    const dialog = page.locator('#market-chart-dialog');
    await expect(dialog).toHaveAttribute('open', '');
    const marketGeometry = await dialog.evaluate((node) => {
      const canvas = node.querySelector('.market-chart-canvas').getBoundingClientRect();
      const close = node.querySelector('.market-chart-close').getBoundingClientRect();
      const periodHeights = [...node.querySelectorAll('.market-periods button')]
        .map((button) => button.getBoundingClientRect().height);
      const bounds = node.getBoundingClientRect();
      return {
        canvasHeight: canvas.height,
        closeSize: Math.min(close.width, close.height),
        minPeriodHeight: Math.min(...periodHeights),
        width: bounds.width,
        height: bounds.height,
      };
    });
    expect(marketGeometry.canvasHeight).toBeGreaterThanOrEqual(320);
    expect(marketGeometry.closeSize).toBeGreaterThanOrEqual(44);
    expect(marketGeometry.minPeriodHeight).toBeGreaterThanOrEqual(44);
    if (page.viewportSize().width <= 520) {
      expect(marketGeometry.width).toBeGreaterThanOrEqual(page.viewportSize().width - 1);
      expect(marketGeometry.height).toBeGreaterThanOrEqual(page.viewportSize().height - 1);
    }
  });
});

test.describe('desktop responsive data surfaces', () => {
  test.skip(({ viewport }) => viewport.width <= 768, 'desktop only');

  test('bond explorer filters and full table remain visible', async ({ page }) => {
    await goto(page, '#bonds');
    await page.getByRole('tab', { name: 'Все выпуски', exact: true }).click();
    await expect(page.locator('.bav4-explorer-controls')).toBeVisible();
    await expect(page.locator('.bav4-table')).toBeVisible();
    const maxHeight = await page.locator('.bav4-table').evaluate(
      (node) => getComputedStyle(node).maxHeight,
    );
    expect(maxHeight).toBe('none');
  });
});
