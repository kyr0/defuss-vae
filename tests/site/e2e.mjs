// Browser e2e for the website in docs/: serves the folder over HTTP (ES modules do not run from file://) and drives
// the installed Google Chrome through Playwright, so CI needs no browser download. It covers the one page and each of
// its interactive parts: navigation anchors, the dark-mode switch, the phone menu, the animated diagram, the install
// tabs and their copy buttons, the scroll-to-top button, plus clean loading and layout at desktop and phone width.
// Evidence -> output/site-e2e.json and screenshots in output/site/.
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { chromium } from "playwright-core";

const root = resolve(import.meta.dir, "../../docs");
const out = resolve(import.meta.dir, "../../output");
const steps = [];

const server = Bun.serve({
  hostname: "127.0.0.1",
  port: 0,
  async fetch(request) {
    const path = decodeURIComponent(new URL(request.url).pathname);
    const file = resolve(join(root, path.endsWith("/") ? `${path}index.html` : path));
    // resolve() collapses "..", so a path outside docs/ cannot be served
    if (file !== root && !file.startsWith(root + sep)) return new Response("forbidden", { status: 403 });
    const body = Bun.file(file);
    return (await body.exists()) ? new Response(body) : new Response("not found", { status: 404 });
  },
});
const url = `http://127.0.0.1:${server.port}/`;

function check(name, ok, detail) {
  steps.push({ name, ok: Boolean(ok), detail });
  console.log(`${ok ? "ok  " : "FAIL"} ${name}${detail === undefined ? "" : ` ${JSON.stringify(detail)}`}`);
}

// Console errors, uncaught exceptions, failed requests and HTTP errors of one page, read after the scenario.
function watch(page) {
  const problems = [];
  page.on("console", (m) => { if (m.type() === "error") problems.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("requestfailed", (r) => problems.push(`requestfailed: ${r.url()} ${r.failure()?.errorText}`));
  page.on("response", (r) => { if (r.status() >= 400) problems.push(`http ${r.status()}: ${r.url()}`); });
  return problems;
}

// Elements that reach past the viewport. The page clips horizontal overflow, so scrollWidth alone would hide them;
// elements inside a scroll container (diagram, table, terminal) or a parallax stage scroll or clip on purpose.
const sticksOut = () => [...document.querySelectorAll(".vae-page *")].filter((e) => {
  const r = e.getBoundingClientRect();
  if (r.width === 0 || (r.right <= innerWidth + 1 && r.left >= -1)) return false;
  for (let p = e.parentElement; p && !p.classList.contains("vae-page"); p = p.parentElement) {
    if (/(auto|scroll|hidden|clip)/.test(getComputedStyle(p).overflowX) || p.matches(".parallax-stage, .parallax-layer")) return false;
  }
  return true;
}).map((e) => `${e.tagName.toLowerCase()}.${[...e.classList].join(".")}`).slice(0, 5);

const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  mkdirSync(join(out, "site"), { recursive: true });

  // Desktop, light: loading, references, diagram, tabs, copy buttons, dark-mode switch, scroll-to-top.
  const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light" });
  await desktop.grantPermissions(["clipboard-read", "clipboard-write"], { origin: url });
  const page = await desktop.newPage();
  const problems = watch(page);
  await page.goto(url, { waitUntil: "networkidle" });
  check("runtime.df$", await page.evaluate(() => typeof window.df$ === "function"));
  check("theme.light-by-default", !(await page.evaluate(() => document.documentElement.classList.contains("dark"))));
  const refs = await page.evaluate(() => ({
    anchors: [...document.querySelectorAll('a[href^="#"]')].map((a) => a.getAttribute("href").slice(1)).filter((id) => !document.getElementById(id)),
    icons: [...document.querySelectorAll('use[href^="#"]')].map((u) => u.getAttribute("href").slice(1)).filter((id) => !document.getElementById(id)),
    navLinks: document.querySelectorAll(".mk-header-nav a").length,
  }));
  check("links.in-page-anchors-resolve", refs.anchors.length === 0, refs.anchors);
  check("links.icons-resolve", refs.icons.length === 0, refs.icons);
  check("nav.links", refs.navLinks === 4, refs.navLinks);
  await page.screenshot({ path: join(out, "site", "desktop-light.png") });

  await page.locator("#loop").scrollIntoViewIfNeeded();
  // The last step and the edge count come from the markup, so the check follows the diagram as it changes.
  const shape = await page.evaluate(() => ({
    steps: Math.max(...[...document.querySelectorAll("#loop [data-step]")].map((e) => Number(e.dataset.step))),
    edges: document.querySelectorAll("#loop .diagram-edge").length,
  }));
  const played = await page.waitForFunction((n) => new RegExp(`Step ${n} of ${n}`).test(document.querySelector("#loop").textContent),
    shape.steps, { timeout: 30000 }).then(() => true, () => false);
  check("diagram.autoplay-reaches-last-step", played, shape.steps);
  const wires = await page.evaluate(() => document.querySelectorAll("#loop svg path").length);
  check("diagram.wires-drawn", wires >= shape.edges, { wires, edges: shape.edges });
  await page.locator("#loop").screenshot({ path: join(out, "site", "diagram.png") });

  // Both pinned scroll scenes play their six phrases in order: at the middle of a phrase's animation-range window
  // (contain range of the scene: its top at the viewport top to its bottom at the viewport bottom) it is the most opaque.
  const scenes = await page.evaluate(() => [...document.querySelectorAll(".vae-flythrough")]
    .map((f) => [...f.querySelectorAll(".vae-fly-line")].map((p) => p.textContent.trim())));
  check("parallax.two-scenes-of-six-phrases", scenes.length === 2 && scenes.every((s) => s.length === 6), scenes);
  for (const k of scenes.keys()) {
    const order = [];
    for (const i of scenes[k].keys()) {
      order.push(await page.evaluate(async ([k, i]) => {
        const scene = document.querySelectorAll(".vae-flythrough .parallax")[k];
        const layers = [...scene.querySelectorAll(".vae-fly-line")].map((p) => p.parentElement);
        const [a, b] = layers[i].getAttribute("style").match(/contain (\d+)% contain (\d+)%/).slice(1).map(Number);
        const top = scene.getBoundingClientRect().top + scrollY;
        scrollTo({ top: top + ((a + b) / 200) * (scene.offsetHeight - innerHeight), behavior: "instant" });
        await new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)));
        const opacity = layers.map((l) => Number(getComputedStyle(l).opacity));
        return opacity.indexOf(Math.max(...opacity));
      }, [k, i]));
      if (i === 4) await page.screenshot({ path: join(out, "site", `scene-${k + 1}.png`) });
    }
    check(`parallax.scene-${k + 1}-plays-in-order`, order.join() === "0,1,2,3,4,5", order);
  }

  const expected = {
    "cmd-claude": "/plugin marketplace add kyr0/defuss-vae\n/plugin install defuss-vae@defuss-vae",
    "cmd-shell": "claude plugin marketplace add kyr0/defuss-vae\nclaude plugin install defuss-vae@defuss-vae",
    "cmd-skills": "npx skills add kyr0/defuss-vae --skill '*'",
    "cmd-gate": "git clone https://github.com/kyr0/defuss-vae ~/defuss-vae\npython3 ~/defuss-vae/plugin/scripts/vae.py gate --repo .",
  };
  const panelOf = { "cmd-claude": "claude", "cmd-shell": "claude", "cmd-skills": "skills", "cmd-gate": "skills" };
  for (const tab of ["claude", "codex", "skills"]) {
    await page.click(`#tab-${tab}`);
    const shown = await page.evaluate((t) => ["claude", "codex", "skills"].map((x) => ({
      x, visible: !document.getElementById(`panel-${x}`).hidden, selected: document.getElementById(`tab-${x}`).getAttribute("aria-selected"),
    })).every((s) => (s.x === t) === s.visible && (s.x === t) === (s.selected === "true")), tab);
    check(`tabs.${tab}-shows-only-its-panel`, shown);
    for (const [id, text] of Object.entries(expected).filter(([id]) => panelOf[id] === tab)) {
      await page.click(`[data-copy="${id}"]`);
      const copied = await page.evaluate(() => navigator.clipboard.readText());
      const label = await page.locator(`[data-copy="${id}"] span`).textContent();
      check(`copy.${id}`, copied === text && label === "Copied", { copied, label });
    }
  }
  await page.locator("#install").screenshot({ path: join(out, "site", "install.png") });

  await page.locator("footer").scrollIntoViewIfNeeded();
  await page.waitForFunction(() => [...document.images].every((i) => i.complete), null, { timeout: 15000 });
  const broken = await page.evaluate(() => [...document.images].filter((i) => i.naturalWidth === 0).map((i) => i.src));
  check("images.load", broken.length === 0, broken);
  await page.click(".fab-trigger");
  const top = await page.waitForFunction(() => scrollY < 50, null, { timeout: 10000 }).then(() => true, () => false);
  check("fab.scrolls-to-top", top);

  await page.click("label.swap");
  const dark = await page.evaluate(() => ({
    dark: document.documentElement.classList.contains("dark"), saved: localStorage.getItem("defuss-shadcn-theme"),
  }));
  check("theme.switch-turns-dark", dark.dark && dark.saved === '"dark"', dark);
  await page.reload({ waitUntil: "networkidle" });
  check("theme.choice-survives-reload", await page.evaluate(() => document.documentElement.classList.contains("dark")));
  check("load.desktop-clean", problems.length === 0, problems);
  await desktop.close();

  // The OS dark preference alone selects the dark palette.
  const darkContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "dark" });
  const darkPage = await darkContext.newPage();
  const darkProblems = watch(darkPage);
  await darkPage.goto(url, { waitUntil: "networkidle" });
  check("theme.follows-os-dark", await darkPage.evaluate(() => document.documentElement.classList.contains("dark")));
  await darkPage.screenshot({ path: join(out, "site", "desktop-dark.png") });
  check("load.dark-clean", darkProblems.length === 0, darkProblems);
  await darkContext.close();

  // Phone: layout fits the viewport, the menu sheet opens and a link in it closes it.
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const phonePage = await phone.newPage();
  const phoneProblems = watch(phonePage);
  await phonePage.goto(url, { waitUntil: "networkidle" });
  const outside = await phonePage.evaluate(sticksOut);
  check("layout.phone-fits-viewport", outside.length === 0, outside);
  await phonePage.screenshot({ path: join(out, "site", "phone.png") });
  await phonePage.click(".mk-header-menu");
  const opened = await phonePage.waitForFunction(() => document.getElementById("vae-menu").open, null, { timeout: 5000 }).then(() => true, () => false);
  check("menu.opens", opened);
  await phonePage.click('#vae-menu a[href="#install"]');
  const closed = await phonePage.waitForFunction(() => !document.getElementById("vae-menu").open, null, { timeout: 5000 }).then(() => true, () => false);
  check("menu.link-closes-it", closed);
  check("load.phone-clean", phoneProblems.length === 0, phoneProblems);
  await phone.close();
} finally {
  await browser.close();
  server.stop(true);
}

const failed = steps.filter((s) => !s.ok);
writeFileSync(join(out, "site-e2e.json"), `${JSON.stringify({ url: "docs/index.html", steps, failed: failed.length }, null, 2)}\n`);
console.log(`${steps.length - failed.length}/${steps.length} site checks passed`);
process.exit(failed.length ? 1 : 0);
