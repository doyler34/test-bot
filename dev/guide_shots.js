// Retakes the guide's screenshots from the demo panel (dev/guide_demo.py).
//   node dev/guide_shots.js [http://127.0.0.1:8099]
// Each picture is taken on a computer-sized window into panel/static/guide/ and
// on a phone-sized one into panel/static/guide/phone/; the guide shows whichever
// matches the screen it's read on.

const path = require("path");
const fs = require("fs");
const { execSync } = require("child_process");
const { chromium } = require(path.join(execSync("npm root -g").toString().trim(), "playwright"));

const BASE = process.argv[2] || "http://127.0.0.1:8099";
const GUIDE = path.join(__dirname, "..", "panel", "static", "guide");
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

async function around(page, selectors, pad = 12) {
  const boxes = [];
  for (const s of selectors) boxes.push(await page.locator(s).first().boundingBox());
  const x = Math.max(0, Math.min(...boxes.map((b) => b.x)) - pad);
  const y = Math.max(0, Math.min(...boxes.map((b) => b.y)) - pad);
  const right = Math.min(page.viewportSize().width, Math.max(...boxes.map((b) => b.x + b.width)) + pad);
  const bottom = Math.max(...boxes.map((b) => b.y + b.height)) + pad;
  return { x, y, width: right - x, height: bottom - y };
}

async function shot(page, name, target) {
  const options = { path: path.join(GUIDE, name + ".jpg"), type: "jpeg", quality: 82 };
  if (!target) await page.screenshot(options);
  else await page.locator(target).first().screenshot(options);
  console.log("desk ", name);
}

async function take(page, phone, out) {
  const shot = async (name, target) => {
    const file = path.join(out, name + ".jpg");
    const options = { path: file, type: "jpeg", quality: phone ? 72 : 82 };
    if (!target) await page.screenshot(options);
    else if (typeof target === "string") await page.locator(target).first().screenshot(options);
    else if (typeof target.screenshot === "function") await target.screenshot(options);
    else await page.screenshot({ ...options, clip: target });
    console.log(phone ? "phone" : "desk ", name);
  };
  const open = async (url, wait = 600) => { await page.goto(BASE + url); await page.waitForTimeout(wait); };

  await open("/login", 200);
  await page.fill("input[name=username]", "gazlagom");
  await shot("login");
  await page.fill("input[name=password]", "guide-demo-1");
  await Promise.all([page.waitForNavigation(), page.click("form button")]);
  await page.evaluate(() => { try { localStorage.clear(); } catch (err) { /* fine */ } });

  await open("/?server=eu1", 1500);
  if (await page.locator(".tour-skip").count()) { await page.click(".tour-skip"); await page.waitForTimeout(300); }
  await shot("dashboard");
  if (phone) {
    await page.click("nav.dock details.menu summary");
    await page.waitForTimeout(200);
    const height = page.viewportSize().height;
    await shot("dock", { x: 0, y: height - 420, width: page.viewportSize().width, height: 420 });
    await page.click("nav.dock details.menu summary");
  } else {
    await shot("sidebar", "aside.side");
  }
  await page.click("details.bell summary");
  await page.waitForTimeout(200);
  await shot("topbar", await around(page, ["header.topbar", "details.bell .pop"], 6));
  await page.click("details.bell summary");
  await shot("cards", ".ctl-card");
  await page.locator(".ctl-map-panel").scrollIntoViewIfNeeded();
  await page.selectOption("[data-map-range]", "24h");
  await page.click("[data-map-layers]");
  await page.waitForTimeout(600);
  await shot("map", ".ctl-map-panel");
  await page.click("[data-map-layers]");
  await page.selectOption("[data-map-range]", "30m");
  await page.click("[data-side-tab=feed]");
  await page.waitForTimeout(300);
  await shot("side-feed", ".ctl-panel:has(.ctl-switch)");
  await page.click("[data-side-tab=players]");
  await shot("charts", ".ctl-charts");

  await open("/server/eu1", 1500);
  await shot("server-page");
  await shot("server-tabs", await around(page, [".srv-head", ".srv-tabs", ".srv-summary"]));
  await open("/server/eu1#live");
  await mark(page, "section.pane:nth-of-type(1) form[action$='/kick'] button");
  await shot("players-pane", "section.pane");
  await unmark(page);
  const feed = page.locator("section.pane[data-filter]");
  await shot("feed", "section.pane[data-filter]");
  await feed.locator("[data-show=sus]").click();
  await shot("feed-sus", "section.pane[data-filter]");
  await feed.locator("[data-show=all]").click();
  await feed.locator("button.expand").click();
  await page.waitForTimeout(200);
  await shot("fullscreen");
  await feed.locator("button.expand").click();

  await open("/server/eu1#health", 500);
  await shot("health", "[data-tab-panel=health] section");
  await open("/server/eu1/monitor", 500);
  await shot("monitor");
  await open("/server/eu1#history", 500);
  await shot("history", "[data-tab-panel=history] section");
  await Promise.all([page.waitForNavigation(), page.locator("[data-tab-panel=history] tbody tr").last().locator("a").click()]);
  await page.waitForTimeout(300);
  await shot("game");

  await open("/players/unusual", 300);
  await shot("unusual");
  await open("/players?q=rook", 300);
  await shot("players-search");
  await open("/player/" + ROOK, 300);
  await shot("player");
  const sections = page.locator("section.card");
  const count = await sections.count();
  for (let i = 0; i < count; i++) {
    if (!(await sections.nth(i).isVisible())) continue;
    const title = (await sections.nth(i).locator("h2").first().textContent()) || "";
    if (title.startsWith("Admin notes")) await shot("player-notes", sections.nth(i));
  }

  await open("/player/" + ROOK + "#connections", 300);
  await shot("player-connections", "[data-tab-panel=connections] section.card");

  await open("/bans", 300);
  await page.locator("[data-player-search]").pressSequentially("ro", { delay: 80 });
  await page.waitForSelector(".suggest:not([hidden]) li");
  await page.waitForTimeout(200);
  await shot("ban-typing", await around(page, ["form.card", ".suggest"]));
  await page.locator(".suggest li").first().dispatchEvent("mousedown");
  await page.selectOption("select[name=duration]", "604800");
  await page.fill("input[name=reason]", "TK after a warning");
  await mark(page, "label.check");
  await shot("ban-form", "form.card");
  await unmark(page);
  await shot("bans-list", "section.card");

  for (const [url, name] of [["/audit", "audit"], ["/network", "box"], ["/attacks", "attacks"], ["/settings", "settings"],
    ["/settings/pictures", "pictures"], ["/users", "users"], ["/users/2/access", "access"], ["/account", "account"]]) {
    await open(url, 500);
    await shot(name);
  }
}

(async () => {
  const browser = await chromium.launch();
  fs.mkdirSync(path.join(GUIDE, "phone"), { recursive: true });
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  await take(phone, true, path.join(GUIDE, "phone"));
  await phone.close();
  // The phone's bottom bar has no computer version; the page only shows it on a phone.
  fs.copyFileSync(path.join(GUIDE, "phone", "dock.jpg"), path.join(GUIDE, "dock.jpg"));
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await take(page, false, GUIDE);
  // The Discord pages are only ever edited on a computer, so they're taken once.
  await page.setViewportSize({ width: 1400, height: 900 });
  await page.goto(BASE + "/discord/welcome");
  await page.waitForTimeout(300);
  await shot(page, "discord-welcome");
  await page.click("#dc-add-button");
  const added = page.locator("#dc-buttons .dc-item").last();
  await added.locator("input").nth(0).fill("Event pings");
  await added.locator("input").nth(1).fill("🔔");
  await added.locator("select").nth(2).selectOption({ label: "Event pings" });
  await added.locator("select").nth(1).selectOption("3");
  await page.waitForTimeout(200);
  await page.evaluate(() => { document.querySelector(".dc-actions").style.display = "none"; });
  await shot(page, "discord-button", "#dc-buttons .dc-editing");
  await page.evaluate(() => { document.querySelector(".dc-actions").style.display = ""; });
  await shot(page, "discord-preview", ".dc-preview");
  await page.goto(BASE + "/discord/greeting");
  await page.waitForTimeout(300);
  await shot(page, "discord-greeting");
  await page.goto(BASE + "/discord/names");
  await page.fill('[data-server="server-3"]', "Classic Nights");
  await page.waitForTimeout(200);
  await shot(page, "discord-names");
  await page.goto(BASE + "/discord/factions");
  await page.fill('[data-faction="FIA"] [data-look="name"]', "Resistance");
  await page.waitForTimeout(200);
  await shot(page, "discord-factions");
  await page.goto(BASE + "/discord/serverinfo");
  await page.waitForTimeout(300);
  await shot(page, "discord-serverinfo");
  await page.goto(BASE + "/discord/bans");
  await page.waitForTimeout(300);
  await shot(page, "discord-bans");
  await page.goto(BASE + "/discord/links");
  await page.waitForTimeout(300);
  await shot(page, "discord-links");
  await page.goto(BASE + "/discord/posts");
  await Promise.all([page.waitForNavigation(), page.click("text=+ New post")]);
  await page.waitForTimeout(300);
  await page.selectOption("#dc-channel", { label: "#announcements" });
  await page.fill('[data-field="title"]', "Event night: Friday 8pm");
  await page.locator("#dc-sections textarea").first().fill("Full server op on **Classic**. Bring your squad and a mic.\n\nPress the button to get pinged when it starts.");
  await page.click("#dc-add-button");
  const going = page.locator("#dc-buttons .dc-item").last();
  await going.locator("input").nth(0).fill("I'm in");
  await going.locator("input").nth(1).fill("✅");
  await going.locator("select").nth(2).selectOption({ label: "Event pings" });
  await page.click("#dc-add-button");
  const rules = page.locator("#dc-buttons .dc-item").last();
  await rules.locator("select").nth(0).selectOption("url");
  await rules.locator("input").nth(0).fill("Event rules");
  await rules.locator('input[placeholder="https://"]').fill("https://example.com/rules");
  await page.waitForTimeout(200);
  await shot(page, "discord-post");
  await Promise.all([page.waitForNavigation(), page.click('button[value="publish"]')]);
  await page.goto(BASE + "/discord/posts");
  await shot(page, "discord-posts");

  await browser.close();
})();
