// Retakes the guide's screenshots from the demo panel (dev/guide_demo.py).
//   node dev/guide_shots.js [http://127.0.0.1:8099]
// Pictures go to panel/static/guide/.

const path = require("path");
const { execSync } = require("child_process");
const { chromium } = require(path.join(execSync("npm root -g").toString().trim(), "playwright"));

const BASE = process.argv[2] || "http://127.0.0.1:8099";
const OUT = path.join(__dirname, "..", "panel", "static", "guide");
const ROOK = "a0b1c2d3-e4f5-4a6b-8c7d-9e0f1a2b3c4d";

async function mark(page, selector) {
  await page.locator(selector).first().evaluate((el) => {
    el.style.outline = "3px solid #e74c3c";
    el.style.outlineOffset = "3px";
  });
}

async function unmark(page) {
  await page.evaluate(() => document.querySelectorAll("[style*=outline]").forEach((el) => {
    el.style.outline = "";
    el.style.outlineOffset = "";
  }));
}

async function shot(page, name, target) {
  const file = path.join(OUT, name + ".jpg");
  const options = { path: file, type: "jpeg", quality: 82 };
  if (!target) await page.screenshot(options);
  else if (typeof target === "string") await page.locator(target).first().screenshot(options);
  else await page.screenshot({ ...options, clip: target });
  console.log("saved", name);
}

async function around(page, selectors, pad = 12) {
  const boxes = [];
  for (const s of selectors) boxes.push(await page.locator(s).first().boundingBox());
  const x = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad);
  const y = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
  const right = Math.max(...boxes.map((b) => b.x + b.width)) + pad;
  const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + pad;
  return { x, y, width: right - x, height: bottom - y };
}

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

  await page.goto(BASE + "/login");
  await page.fill("input[name=username]", "gazlagom");
  await shot(page, "login");
  await page.fill("input[name=password]", "guide-demo-1");
  await Promise.all([page.waitForNavigation(), page.click("form button")]);

  await page.goto(BASE + "/");
  await page.waitForTimeout(500);
  await shot(page, "home");
  await shot(page, "home-cards", ".cards");

  await page.goto(BASE + "/server/eu1");
  await page.waitForTimeout(500);
  await mark(page, ".controls");
  await shot(page, "server-top", await around(page, ["h1", ".controls", ".summary"]));
  await unmark(page);
  await shot(page, "server-overview");
  await mark(page, ".tabs");
  await shot(page, "server-tabs", await around(page, [".tabs"], 20));
  await unmark(page);

  const players = page.locator("section.pane").nth(0);
  await mark(page, "section.pane:nth-of-type(1) form[action$='/kick'] button");
  await players.screenshot({ path: path.join(OUT, "players-pane.jpg"), type: "jpeg", quality: 82 });
  console.log("saved players-pane");
  await unmark(page);

  const feed = page.locator("section.pane[data-filter]");
  await feed.screenshot({ path: path.join(OUT, "feed.jpg"), type: "jpeg", quality: 82 });
  console.log("saved feed");
  await feed.locator("[data-show=sus]").click();
  await feed.screenshot({ path: path.join(OUT, "feed-sus.jpg"), type: "jpeg", quality: 82 });
  console.log("saved feed-sus");
  await feed.locator("[data-show=all]").click();
  await feed.locator("button.expand").click();
  await page.waitForTimeout(200);
  await shot(page, "fullscreen");
  await page.keyboard.press("Escape");

  await page.goto(BASE + "/server/eu1#health");
  await page.waitForTimeout(400);
  await shot(page, "health", "[data-tab-panel=health] section");

  await page.goto(BASE + "/server/eu1#history");
  await page.waitForTimeout(400);
  await shot(page, "history", "[data-tab-panel=history] section");
  await Promise.all([page.waitForNavigation(), page.locator("[data-tab-panel=history] tbody tr").last().locator("a").click()]);
  await page.waitForTimeout(300);
  await shot(page, "game");

  await page.goto(BASE + "/players?q=rook");
  await shot(page, "players-search");
  await page.goto(BASE + "/player/" + ROOK);
  await shot(page, "player");
  await page.goto(BASE + "/player/" + ROOK);
  const sections = page.locator("section.card");
  const count = await sections.count();
  for (let i = 0; i < count; i++) {
    const title = (await sections.nth(i).locator("h2").first().textContent()) || "";
    if (title.startsWith("Connections")) {
      await sections.nth(i).screenshot({ path: path.join(OUT, "player-connections.jpg"), type: "jpeg", quality: 82 });
      console.log("saved player-connections");
    }
    if (title.startsWith("Admin notes")) {
      await sections.nth(i).screenshot({ path: path.join(OUT, "player-notes.jpg"), type: "jpeg", quality: 82 });
      console.log("saved player-notes");
    }
  }

  await page.goto(BASE + "/bans");
  await page.locator("[data-player-search]").pressSequentially("ro", { delay: 80 });
  await page.waitForSelector(".suggest:not([hidden]) li");
  await page.waitForTimeout(200);
  await shot(page, "ban-typing", await around(page, ["form.card", ".suggest"]));
  await page.locator(".suggest li").first().dispatchEvent("mousedown");
  await page.selectOption("select[name=duration]", "604800");
  await page.fill("input[name=reason]", "TK after a warning");
  await mark(page, "label.check");
  await shot(page, "ban-form", "form.card");
  await unmark(page);
  await shot(page, "bans-list", "section.card");

  await page.goto(BASE + "/console");
  await shot(page, "console");
  await page.goto(BASE + "/audit");
  await shot(page, "audit");
  await page.goto(BASE + "/users");
  await shot(page, "users");
  await page.goto(BASE + "/account");
  await shot(page, "account");

  await browser.close();
})();
