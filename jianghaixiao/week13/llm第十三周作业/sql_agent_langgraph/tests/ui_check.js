const { chromium } = require("C:/Users/江海啸/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright");
const path = require("path");

const result = {
  answer: "美国（USA）的客户消费总额最高，总计 523.06。",
  sql: "SELECT c.Country, ROUND(SUM(i.Total), 2) AS TotalRevenue\nFROM customers c\nJOIN invoices i ON c.CustomerId = i.CustomerId\nGROUP BY c.Country\nORDER BY TotalRevenue DESC\nLIMIT 5;",
  selected_tables: ["customers", "invoices"],
  selection_reason: "需要客户国家信息和订单金额",
  sql_explanation: "按国家聚合订单金额并降序排列",
  columns: ["Country", "TotalRevenue"],
  rows: [
    { Country: "USA", TotalRevenue: 523.06 },
    { Country: "Canada", TotalRevenue: 303.96 },
    { Country: "France", TotalRevenue: 195.1 },
    { Country: "Brazil", TotalRevenue: 190.1 },
    { Country: "Germany", TotalRevenue: 156.48 }
  ],
  sql_error: "",
  retry_count: 0
};

const sse = [
  `event: step\ndata: ${JSON.stringify({ node: "select_tables", label: "分析问题并选择相关数据表", selected_tables: result.selected_tables })}\n\n`,
  `event: step\ndata: ${JSON.stringify({ node: "generate_sql", label: "生成只读 SQL", sql: result.sql })}\n\n`,
  `event: step\ndata: ${JSON.stringify({ node: "execute_sql", label: "执行 SQL", success: true, row_count: 5 })}\n\n`,
  `event: step\ndata: ${JSON.stringify({ node: "summarize_answer", label: "整理自然语言答案" })}\n\n`,
  `event: result\ndata: ${JSON.stringify(result)}\n\n`,
].join("");

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console: ${msg.text()}`);
  });
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));

  await page.route("**/api/chat/stream", (route) => {
    route.fulfill({ status: 200, contentType: "text/event-stream; charset=utf-8", body: sse });
  });

  await page.goto("http://127.0.0.1:8000", { waitUntil: "networkidle" });
  await page.locator("#question").fill("哪个国家的客户消费总额最高？");
  await page.locator("#question").press("Enter");
  await page.locator(".details table").waitFor({ state: "visible", timeout: 10000 });

  const metrics = await page.evaluate(() => ({
    tableRows: document.querySelectorAll(".details table tbody tr").length,
    detailsOpen: document.querySelector(".details")?.open,
    horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    finalAnswer: [...document.querySelectorAll(".bubble")].at(-1)?.textContent,
  }));

  const artifacts = path.join(process.cwd(), "artifacts");
  await page.screenshot({ path: path.join(artifacts, "home.png"), fullPage: true });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(250);
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth
  );
  await page.screenshot({ path: path.join(artifacts, "home-mobile.png"), fullPage: true });

  if (errors.length || metrics.tableRows !== 5 || !metrics.detailsOpen || metrics.horizontalOverflow || mobileOverflow) {
    throw new Error(JSON.stringify({ errors, metrics, mobileOverflow }, null, 2));
  }

  console.log(JSON.stringify({ screenshot: path.join(artifacts, "home.png"), errors, metrics, mobileOverflow }, null, 2));
  await browser.close();
})();
