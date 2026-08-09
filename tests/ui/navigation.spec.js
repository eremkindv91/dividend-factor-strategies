const { test, expect } = require('playwright/test');

async function goto(page, hash) {
  await page.goto(`/${hash}`);
  await page.locator('#app-nav .section-tab').first().waitFor({ state: 'attached' });
}

test.describe('desktop navigation', () => {
  test.skip(({ viewport }) => viewport.width < 1000, 'desktop only');

  test('has one deliberate top-level hierarchy', async ({ page }) => {
    await goto(page, '#market');
    await expect(page.locator('#app-nav .section-tab')).toHaveText([
      'Обзор', 'Портфель', 'Акции', 'Облигации', 'Стратегии', 'Методология',
    ]);
    const labels = await page.locator('#app-nav .section-tab').allTextContents();
    expect(labels).not.toContain('Новости');
    expect(labels).not.toContain('Банки РФ');
  });

  test('summary is first and does not fetch the full news feed', async ({ page }) => {
    const newsRequests = [];
    page.on('request', (request) => {
      if (/\/news\.json(?:\?|$)/.test(request.url())) newsRequests.push(request.url());
    });
    await goto(page, '#market');
    await expect(page.locator('#market-summary-panel')).toBeVisible();
    await expect(page.locator('#market-news-panel')).toBeHidden();
    await expect(page.locator('#market-instrument-grid')).toBeVisible();
    await page.waitForTimeout(500);
    expect(newsRequests).toHaveLength(0);

    await page.locator('#news-teaser').click();
    await expect(page).toHaveURL(/#market\?tab=news$/);
    await expect(page.locator('#market-news-panel')).toBeVisible();
    await expect.poll(() => newsRequests.length).toBeGreaterThan(0);
    await expect(page.locator('#app-nav .section-tab.active')).toHaveText('Обзор');
  });

  test('stocks does not fetch bank data before its nested tab opens', async ({ page }) => {
    const bankRequests = [];
    page.on('request', (request) => {
      if (/\/cbr\//.test(request.url())) bankRequests.push(request.url());
    });
    await goto(page, '#stocks');
    await page.waitForTimeout(500);
    expect(bankRequests).toHaveLength(0);

    await page.locator('[data-subtab="stocks:banks"]').click();
    await expect(page).toHaveURL(/#stocks\?tab=banks$/);
    await expect(page.locator('#stocks-banks-panel')).toBeVisible();
    await expect.poll(() => bankRequests.length).toBeGreaterThan(0);
    await expect(page.locator('#app-nav .section-tab.active')).toHaveText('Акции');
  });
});

test.describe('routing', () => {
  for (const [legacy, canonical, panel] of [
    ['#news', /#market\?tab=news$/, '#market-news-panel'],
    ['#cbr', /#stocks\?tab=banks$/, '#stocks-banks-panel'],
    ['#banks', /#stocks\?tab=banks$/, '#stocks-banks-panel'],
    ['#pro', /#market$/, '#market-summary-panel'],
    ['#about', /#market$/, '#market-summary-panel'],
  ]) {
    test(`${legacy} is canonicalized on initial load`, async ({ page }) => {
      await goto(page, legacy);
      await expect(page).toHaveURL(canonical);
      await expect(page.locator(panel)).toBeVisible();
    });
  }

  test('tab switches preserve dividend calendar deep-link parameters', async ({ page }) => {
    await goto(page, '#market?calendar=dividends&ticker=SBER');
    await expect(page.locator('#dividend-calendar')).toHaveAttribute('open', '');
    await page.locator('[data-subtab="market:news"]').click();
    await expect(page).toHaveURL(/calendar=dividends/);
    await expect(page).toHaveURL(/ticker=SBER/);
    await expect(page).toHaveURL(/tab=news/);
  });
});

test('global tax profile is unique and updates its compact label', async ({ page }) => {
  await goto(page, '#market');
  await expect(page.locator('#tax-profile')).toHaveCount(1);
  await page.locator('#tax-profile summary').click();
  await page.locator('#tax-profile-opts [data-rate="0.15"]').click();
  await expect(page.locator('#tax-profile-current')).toHaveText('15%');
  await goto(page, '#my-portfolio');
  await expect(page.locator('#tax-profile-current')).toHaveText('15%');
});

test('strategy controls use investor-facing wording without disabled Extended mode', async ({ page }) => {
  await goto(page, '#strategies');
  await expect(page.locator('label', { has: page.locator('#pf-method') })).toContainText('Метод формирования корзины');
  await expect(page.getByRole('button', { name: 'Extended', exact: true })).toHaveCount(0);
});

test('market phase details are closed by default and resolve their loader', async ({ page }) => {
  await goto(page, '#market');
  const details = page.locator('#marketsaw');
  await expect(details).not.toHaveAttribute('open', '');
  await details.locator('summary').click();
  await expect(details).toHaveAttribute('open', '');
  await expect(page.locator('#saw-body')).not.toContainText('Загрузим индекс MCFTR', { timeout: 15_000 });
});

test.describe('mobile navigation', () => {
  test.skip(({ viewport }) => viewport.width >= 1000, 'mobile only');

  test('shows four primary actions and only two secondary actions', async ({ page }) => {
    await goto(page, '#market');
    await expect(page.locator('#app-bottomnav .section-tab')).toHaveText(['Обзор', 'Портфель', 'Акции', 'Облигации']);
    await page.locator('#app-more-btn').click();
    await expect(page.locator('#app-more-nav .section-tab')).toHaveText(['Стратегии', 'Методология']);
  });

  test('key routes have no page-level horizontal overflow', async ({ page }) => {
    for (const hash of ['#market', '#market?tab=news', '#stocks', '#stocks?tab=banks', '#strategies']) {
      await goto(page, hash);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      expect(overflow, `${hash} must fit the viewport`).toBe(false);
    }
  });
});

test('navigation renders unique ids and no application exceptions', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await goto(page, '#market');
  const duplicates = await page.evaluate(() => {
    const ids = [...document.querySelectorAll('[id]')].map((node) => node.id);
    return ids.filter((id, index) => ids.indexOf(id) !== index);
  });
  expect(duplicates).toEqual([]);
  expect(errors).toEqual([]);
});
