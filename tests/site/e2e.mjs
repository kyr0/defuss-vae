// Browser e2e for the website in docs/: serves the folder over HTTP (ES modules do not run from file://) and drives
// the installed Google Chrome through Playwright, so CI needs no browser download. It covers the one page and each of
// its interactive parts: the skip link, navigation anchors, outbound links, the dark-mode switch, the phone menu, the gate
// demo tabs, the animated diagram, the measurement notes, the install tabs and their copy buttons, the scroll-to-top
// button, plus clean loading and layout at desktop and phone width. It then drives the review page the release zip writes
// for an edit run, opened from the link the CLI prints, as the human opens it: the agent's comments on their text, a
// text edit, a reply, a new comment, the quick ✅ / ❌ verdicts, the dashed applied card, the dark-mode switch, "Send to
// agent", the download, a reload, and the zip's CLI reading the response back
// from the review folder; then the agent's checked revision and its next review page. Evidence -> output/site-e2e.json
// and screenshots in output/site/.
import { spawnSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join, resolve, sep } from "node:path";
import { chromium } from "playwright-core";

const repo = resolve(import.meta.dir, "../..");
const root = join(repo, "docs");
const out = join(repo, "output");
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
let stopReviewServer = () => {};
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
  // The first Tab reaches the skip link, which shows itself on focus and jumps to the main content.
  await page.keyboard.press("Tab");
  const skip = await page.evaluate(() => ({
    focused: document.activeElement.classList.contains("vae-skip"), top: document.activeElement.getBoundingClientRect().top,
  }));
  await page.keyboard.press("Enter");
  const jumped = await page.waitForFunction(() => location.hash === "#main", null, { timeout: 5000 }).then(() => true, () => false);
  check("skip-link.shows-and-reaches-main", skip.focused && skip.top >= 0 && jumped, skip);
  const flip = await page.evaluate(() => {
    const rotate = document.querySelector(".vae-flip .text-rotate");
    return { lines: rotate ? rotate.querySelectorAll(":scope > span > span").length : 0, height: rotate ? rotate.getBoundingClientRect().height : 0 };
  });
  check("hero.flip-rotates-six-lines", flip.lines === 6 && flip.height > 0, flip);
  const refs = await page.evaluate(() => ({
    anchors: [...document.querySelectorAll('a[href^="#"]')].map((a) => a.getAttribute("href").slice(1)).filter((id) => !document.getElementById(id)),
    icons: [...document.querySelectorAll('use[href^="#"]')].map((u) => u.getAttribute("href").slice(1)).filter((id) => !document.getElementById(id)),
    navLinks: document.querySelectorAll(".mk-header-nav a").length,
  }));
  check("links.in-page-anchors-resolve", refs.anchors.length === 0, refs.anchors);
  check("links.icons-resolve", refs.icons.length === 0, refs.icons);
  check("nav.links", refs.navLinks === 4, refs.navLinks);

  // Every link that leaves the page reaches its target. Links to this repository's files are checked in the checkout:
  // GitHub shows the pushed main, so fetching them would pass for a file this change deletes and fail for one it adds,
  // and GitHub's file pages answer automated clients with 503 in bursts. The rest are opened in CloakBrowser, a Chromium
  // build patched against bot detection, since npm answers Chrome and plain fetches with a 403 Cloudflare challenge; one
  // retry absorbs a transient answer. CloakBrowser still meets that challenge at times (403 with
  // `cf-mitigated: challenge`; VERIFIED: a gate run on 2026-10-13 failed on it), and a challenge says nothing about the
  // page, so a link still challenged after the retry is listed as unchecked, never as dead. LinkedIn sends signed-out
  // visitors to its login wall (999 for real and made-up profiles alike), so its links are listed as unchecked. GitHub
  // Actions sets CI=true: there every outbound page stays unchecked and no browser is downloaded. The e2e never
  // downloads it either: `make setup` does. The pages load at once, one tab each with a minute to answer: the LSB and
  // FHS pages on refspecs.linuxfoundation.org answered after 6 to 30 s (VERIFIED: four requests on 2026-10-19, two gate
  // runs failed on its 30 s limit), and a page that timed out is not retried, since it already had the full minute.
  const targets = await page.evaluate(() => [...new Set([...document.querySelectorAll("a[href]")]
    .filter((a) => a.protocol.startsWith("http") && !a.getAttribute("href").startsWith("#")).map((a) => a.href.split("#")[0]))]);
  const ownFile = (t) => t.match(/^https:\/\/github\.com\/kyr0\/defuss-vae\/(?:blob|tree)\/main\/([^?]+)$/)?.[1];
  const missing = targets.map(ownFile).filter((p) => p && !existsSync(join(repo, decodeURIComponent(p))));
  check("links.repo-files-exist", missing.length === 0, missing);
  const external = targets.filter((t) => !ownFile(t));
  const unchecked = external.filter((t) => process.env.CI || new URL(t).hostname === "www.linkedin.com");
  const toCheck = external.filter((t) => !unchecked.includes(t));
  const transient = (s) => (typeof s !== "number" && !/Timeout \d+ms exceeded/.test(s)) || s >= 500 || s === 429;
  const dead = [];
  if (toCheck.length) {
    const { binaryInfo, launch } = await import("cloakbrowser");
    const installed = binaryInfo().installed;
    check("links.cloakbrowser-installed", installed, installed ? undefined : "run make setup");
    if (installed) {
      const cloak = await launch();
      try {
        const status = async (t) => {
          const tab = await cloak.newPage();
          try {
            return await tab.goto(t, { waitUntil: "domcontentloaded", timeout: 60000 }).then(
              (r) => (r.status() === 403 && r.headers()["cf-mitigated"] === "challenge" ? "challenge" : r.status()),
              (e) => e.message.split("\n")[0]);
          } finally {
            await tab.close();
          }
        };
        const answers = await Promise.all(toCheck.map(async (t) => {
          const s = await status(t);
          return [t, transient(s) ? await Bun.sleep(3000).then(() => status(t)) : s];
        }));
        for (const [t, s] of answers) {
          if (s === "challenge") unchecked.push(t);
          else if (!(s < 400)) dead.push(`${s} ${t}`);
        }
      } finally {
        await cloak.close();
      }
    }
  }
  check("links.external-pages-answer", dead.length === 0, { dead, unchecked });
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
  // The memory map, the review loop, the research run and the swarm map are static: once drawn, every relationship in
  // their lists has a wire.
  for (const id of ["memory-map", "review-loop", "research-run", "swarm-map"]) {
    await page.locator(`#${id}`).scrollIntoViewIfNeeded();
    const drawn = await page.waitForFunction((sel) => {
      const edges = document.querySelectorAll(`${sel} .diagram-edge`).length;
      const wires = document.querySelectorAll(`${sel} svg path`).length;
      return edges > 0 && wires >= edges && { edges, wires };
    }, `#${id}`, { timeout: 10000 }).then((h) => h.jsonValue(), () => null);
    check(`${id}.wires-drawn`, drawn !== null, drawn);
  }

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

  // The gate demo in the hero: each step selects its tab and shows only its own panel.
  const demoSteps = ["catch", "fix", "clear"];
  for (const step of demoSteps) {
    await page.click(`#demo-tab-${step}`);
    const shown = await page.evaluate(([s, all]) => all.every((x) =>
      (x === s) === !document.getElementById(`demo-${x}`).hidden
      && (x === s) === (document.getElementById(`demo-tab-${x}`).getAttribute("aria-selected") === "true")), [step, demoSteps]);
    check(`demo.${step}-shows-only-its-panel`, shown);
  }

  await page.locator("#lessons").screenshot({ path: join(out, "site", "lessons.png") });

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

  // the review editor's screenshot shows the site's active theme
  const shot = () => page.evaluate(() => [...document.querySelectorAll(".vae-shot img")]
    .filter((i) => getComputedStyle(i).display !== "none").map((i) => i.className).join());
  const lightShot = await shot();
  await page.click("label.swap");
  check("review-shot.follows-the-theme", lightShot === "vae-shot-light" && (await shot()) === "vae-shot-dark", [lightShot, await shot()]);
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
  // Under reduced motion no reveal transform contains positioned descendants, so one that escapes a scroll container
  // (a screen-reader span in a table caption's link did) widens the page past sticksOut; the page's width is the check.
  const still = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: "reduce" });
  const stillPage = await still.newPage();
  await stillPage.goto(url, { waitUntil: "networkidle" });
  const widths = await stillPage.evaluate(() => [document.documentElement.scrollWidth, innerWidth]);
  check("layout.phone-reduced-motion-no-sideways-scroll", widths[0] <= widths[1], widths);
  await still.close();

  // The review page: the release zip's CLI reviews a page in a fresh project (the agent's two findings), Chrome opens
  // the link it prints, the human edits a sentence, replies to one finding, comments on a table cell and sends.
  const { version } = JSON.parse(readFileSync(join(repo, "plugin", ".claude-plugin", "plugin.json"), "utf8"));
  const zip = `defuss-vae-${version}.zip`;
  const work = join(out, "review-e2e");
  rmSync(work, { recursive: true, force: true });
  mkdirSync(join(work, "proj"), { recursive: true });
  spawnSync("unzip", ["-q", join(repo, "output", zip), "-d", join(work, "plugin")]);
  const proj = join(work, "proj");
  spawnSync("git", ["init", "-q"], { cwd: proj });
  writeFileSync(join(proj, "guide.md"), "# Guide\n\nThe service is very fast. It answers quickly.\n\n## Use\n\n| Step | When |\n| --- | --- |\n| Run the check | daily |\n\n```mermaid\nflowchart LR\n  A --> B\n```\n");
  const vae = (...args) => spawnSync("python3", [join(work, "plugin", "scripts", "vae.py"), ...args], { cwd: proj, encoding: "utf8" });
  stopReviewServer = () => vae("edit", "--repo", proj, "serve", "--stop");
  const started = vae("edit", "--repo", proj, "start", "guide.md", "--model", "e2e");
  const run = /run=(\S+)/.exec(started.stdout)?.[1];
  const finding = (quote, rule, action, replacement) => [{ zitat: quote, vorkommen: 1, typ: "satz", umformulierung: replacement,
    befunde: [{ regel: rule, schwere: "wesentlich", sicherheit: "hoch", aktion: action, kommentar: `${rule}: a finding.`, belege: [] }] }];
  const parts = [finding("The service is very fast.", "S03", "umformulieren", "The service is fast."),
    finding("| Run the check | daily |", "P04", "pruefen", null)];
  const submitted = parts.map((value, i) => {
    const draft = join(proj, run, "draft", `000${i + 1}.json`);
    writeFileSync(draft, JSON.stringify(value));
    return vae("edit", "--repo", proj, "submit", "--run", run, "--part", String(i + 1), "--findings", draft, "--confidence", "high");
  });
  const pageUrl = /^PAGE: (http:\/\/127\.0\.0\.1:\d+\/[^ ]+\/) /m.exec(submitted[1].stdout)?.[1];
  const folder = /^RESPONSE: (\S+)\/response\.json /m.exec(submitted[1].stdout)?.[1];
  check("review.cli-links-the-page", submitted[1].stdout.includes("VERIFIED[edit.review]") && Boolean(pageUrl) && Boolean(folder)
    && existsSync(join(proj, folder, "index.html")), submitted.map((s) => s.stdout.split("\n").filter((l) => /^(ACCEPTED|REJECTED|PAGE|RESPONSE)/.test(l))));
  const applied = vae("edit", "--repo", proj, "apply", "--run", run);
  check("review.apply-rewrites-the-page", applied.stdout.includes("APPLIED 1/1") && applied.stdout.includes(`PAGE: ${pageUrl} `), applied.stdout.split("\n"));

  const review = await browser.newContext({ viewport: { width: 1400, height: 900 }, colorScheme: "light", acceptDownloads: true });
  const reviewPage = await review.newPage();
  const reviewProblems = watch(reviewPage);
  await reviewPage.goto(pageUrl, { waitUntil: "networkidle" });
  await reviewPage.waitForSelector(".doc-comments-card", { timeout: 30000 });
  const marks = await reviewPage.locator("mark.doc-comments-mark").allTextContents();
  const text = await reviewPage.evaluate(() => document.body.innerText);
  check("review.data-stays-in-its-script", !text.includes('"markdown"') && !text.includes('"comments"'), text.slice(0, 120));
  check("review.findings-mark-their-text", marks.join("|").includes("The service is very fast.") && marks.join("|").includes("Run the check"), marks);
  const dashed = await reviewPage.evaluate(() => [...document.querySelectorAll('.doc-comments-card[data-depth="0"]')]
    .map((c) => [c.dataset.id, c.hasAttribute("data-applied"), getComputedStyle(c).borderTopStyle]));
  check("review.applied-card-is-dashed", JSON.stringify(dashed) === JSON.stringify([["p1-1", true, "dashed"], ["p2-1", false, "solid"]]), dashed);
  // quick verdicts: ✅ then ❌ on the open finding leaves ❌; ✅ twice on the applied one takes it back
  const verdictOn = (id, v) => reviewPage.locator(`.doc-comments-card[data-id="${id}"] > .doc-comments-actions [data-vae-verdict="${v}"]`).click();
  await verdictOn("p2-1", "accept");
  await verdictOn("p2-1", "reject");
  await verdictOn("p1-1", "accept");
  await verdictOn("p1-1", "accept");
  const pressed = await reviewPage.evaluate(() => [...document.querySelectorAll('[data-vae-verdict][aria-pressed="true"]')]
    .map((b) => `${b.closest(".doc-comments-card").dataset.id}:${b.dataset.vaeVerdict}`));
  check("review.verdicts-replace-and-toggle", pressed.join() === "p2-1:reject", pressed);
  const card = reviewPage.locator(".doc-comments-card").first();
  await card.locator('button[data-doc-comments-action="reply"]').click();
  await card.locator("form.doc-comments-reply textarea").fill("Keep it: we measured 40 ms.");
  await card.locator('form.doc-comments-reply button[type="submit"]').click();
  await reviewPage.evaluate(() => {
    const cell = [...document.querySelectorAll("#de-editor .ce-block td, #de-editor .ce-block .tc-cell")].find((c) => c.textContent.trim() === "daily");
    const range = document.createRange();
    range.selectNodeContents(cell);
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  await reviewPage.click("#de-comment");
  await reviewPage.fill("#de-new-comment-body", "Hourly, not daily.");
  await reviewPage.click('#de-new-comment-form button[type="submit"]');
  check("review.counts-the-humans-comments", (await reviewPage.textContent("#de-count")) === "5 comments · 3 yours", await reviewPage.textContent("#de-count"));
  // the human edits the page: "quickly" becomes "in 40 ms", typed into the editor like any edit
  await reviewPage.evaluate(() => {
    const block = [...document.querySelectorAll("#de-editor .ce-paragraph")].find((p) => p.textContent.includes("It answers quickly."));
    const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node && !node.data.includes("quickly")) node = walker.nextNode();
    const range = document.createRange();
    range.setStart(node, node.data.indexOf("quickly"));
    range.setEnd(node, node.data.indexOf("quickly") + "quickly".length);
    block.focus();
    getSelection().removeAllRanges();
    getSelection().addRange(range);
  });
  await reviewPage.keyboard.type("in 40 ms");
  check("review.send-is-offered-over-http", await reviewPage.isVisible("#de-send"));
  await reviewPage.screenshot({ path: join(out, "site", "review.png") });
  const reviewTheme = () => reviewPage.evaluate(() => [document.documentElement.classList.contains("dark"), document.getElementById("de-theme").getAttribute("aria-pressed")].join());
  const light = await reviewTheme();
  await reviewPage.click("#de-theme");
  check("review.theme-switch-turns-dark", light === "false,false" && (await reviewTheme()) === "true,true", [light, await reviewTheme()]);
  await reviewPage.waitForFunction(() => document.getAnimations().length === 0);  // the colours ease over: shoot the end state
  await reviewPage.screenshot({ path: join(out, "site", "review-dark.png") });
  await reviewPage.click("#de-send");
  const sent = await reviewPage.waitForFunction(() => /Sent to the agent/.test(document.body.innerText), null, { timeout: 10000 }).then(() => true, () => false);
  check("review.send-saves-the-response", sent && existsSync(join(proj, folder, "response.json")));
  const back = vae("comments", folder, "--repo", proj);
  check("review.response-reads-back", back.status === 0 && /REPLY guide\.md:3 p1-1 .* by You: Keep it: we measured 40 ms\./.test(back.stdout)
    && /NEW guide\.md:9 \S+ "daily" by You: Hourly, not daily\./.test(back.stdout), back.stdout.split("\n"));
  check("review.verdict-reads-back-with-its-action", /^REJECT guide\.md:9 p2-1 .* → leave the text as it is$/m.test(back.stdout)
    && !/^(ACCEPT|ACCEPTED) .*p1-1/m.test(back.stdout) && !back.stdout.includes("by You: ✅") && !back.stdout.includes("by You: ❌"), back.stdout.split("\n"));
  check("review.only-the-edit-is-an-edit", back.stdout.includes("edits=1") && back.stdout.includes("@@ guide.md:3 @@")
    && back.stdout.includes("\n-The service is very fast. It answers quickly.\n+The service is very fast. It answers in 40 ms.\n"), back.stdout.split("\n"));
  const [download] = await Promise.all([reviewPage.waitForEvent("download"), reviewPage.click("#de-download")]);
  const saved = join(work, download.suggestedFilename());
  await download.saveAs(saved);
  const fromFile = vae("comments", saved, "--repo", proj);
  check("review.download-reads-back-the-same", saved.endsWith(".response.json") && fromFile.stdout === back.stdout, fromFile.stdout.split("\n").slice(0, 3));
  await reviewPage.reload({ waitUntil: "networkidle" });
  await reviewPage.waitForSelector(".doc-comments-card");
  await reviewPage.waitForFunction(() => document.querySelector("#de-editor").innerText.includes("It answers in 40 ms."), null, { timeout: 10000 }).catch(() => {});
  check("review.comments-survive-a-reload", (await reviewPage.textContent("#de-count")) === "5 comments · 3 yours");
  check("review.theme-survives-a-reload", (await reviewTheme()) === "true,true", await reviewTheme());
  const keptVerdict = await reviewPage.evaluate(() => [...document.querySelectorAll('[data-vae-verdict][aria-pressed="true"]')].length);
  check("review.verdict-survives-a-reload", keptVerdict === 1, keptVerdict);
  await reviewPage.click("#de-theme");
  const reloaded = await reviewPage.evaluate(() => ({ text: document.getElementById("de-editor").innerText.slice(0, 120),
    kept: Object.entries(localStorage).filter(([k]) => k.endsWith(":document")).map(([, v]) => JSON.parse(v).edited.slice(0, 80)) }));
  check("review.edits-survive-a-reload", reloaded.text.includes("It answers in 40 ms."), reloaded);
  check("review.marks-survive-a-reload", (await reviewPage.locator("mark.doc-comments-mark").allTextContents()).join("|").includes("daily"));
  check("load.review-clean", reviewProblems.length === 0, reviewProblems);
  check("review.response-is-json", JSON.parse(readFileSync(join(proj, folder, "response.json"), "utf8")).vae.kind === "edit");
  // The agent's revision: only what the human asked for (their edit, their comment, a chat request), checked by the
  // CLI against the page as the response found it, then the next review page in the same browser.
  const page1 = readFileSync(join(proj, "guide.md"), "utf8");
  writeFileSync(join(proj, "guide.md"), page1.replace("It answers quickly.", "It answers in 40 ms.").replace("| daily |", "| hourly |")
    + "\n## Limits\n\nThe check needs network access.\n");
  const notes = join(work, "notes.json");
  writeFileSync(notes, JSON.stringify([
    { on: "edits", quote: "It answers in 40 ms.", body: "Your edit, carried over." },
    { on: /^NEW guide\.md:9 (\S+) "daily"/m.exec(back.stdout)?.[1] ?? "?", quote: "| Run the check | hourly |", body: "Hourly, as you asked." },
    { on: "chat", said: "Add the limits.", quote: "## Limits\n\nThe check needs network access.\n", body: "Added." }]));
  const checked = vae("edit", "--repo", proj, "review", folder, "--notes", notes, "--check");
  const made = vae("edit", "--repo", proj, "review", folder, "--notes", notes);
  const nextUrl = /^PAGE: (http:\/\/127\.0\.0\.1:\d+\/[^ ]+\/) /m.exec(made.stdout)?.[1];
  check("revision.checked-and-linked", checked.stdout.includes("VERIFIED[revision]") && /-2-[0-9a-f]{8}\/$/.test(nextUrl ?? ""),
    `${checked.stdout}${made.stdout}`.split("\n").slice(0, 6));
  await reviewPage.goto(nextUrl, { waitUntil: "networkidle" });
  await reviewPage.waitForSelector(".doc-comments-card", { timeout: 30000 });
  const nextMarks = (await reviewPage.locator("mark.doc-comments-mark").allTextContents()).join("|");
  check("revision.notes-mark-their-text", ["in 40 ms", "hourly", "Limits"].every((t) => nextMarks.includes(t)), nextMarks);
  check("revision.starts-without-the-previous-comments", (await reviewPage.textContent("#de-count")) === "3 comments · 0 yours",
    await reviewPage.textContent("#de-count"));
  const dashedNotes = await reviewPage.locator(".doc-comments-card[data-applied]").count();
  check("revision.notes-on-made-changes-are-dashed", dashedNotes === 3, dashedNotes);
  check("load.revision-clean", reviewProblems.length === 0, reviewProblems);
  await review.close();
  const stopped = stopReviewServer();
  check("review.server-stops", stopped.stdout.trim() === "STOPPED the review server", stopped.stdout);
} finally {
  stopReviewServer();
  await browser.close();
  server.stop(true);
}

const failed = steps.filter((s) => !s.ok);
writeFileSync(join(out, "site-e2e.json"), `${JSON.stringify({ url: "docs/index.html", steps, failed: failed.length }, null, 2)}\n`);
console.log(`${steps.length - failed.length}/${steps.length} site checks passed`);
process.exit(failed.length ? 1 : 0);
