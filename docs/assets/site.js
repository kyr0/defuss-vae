// The page's own glue: the dark-mode switch and the copy buttons. Every other behavior (sheet menu, tabs, diagram,
// parallax, scroll-to-top) comes from the defuss-shadcn components, wired by their data attributes in all.min.js.
// WHY two listeners in a plain module rather than a framework app: docs/ is served as-is without a build step, and the
// components already own their states, so the page adds only what no component does.
// VERIFIED: tests/site/e2e.mjs drives both in Chrome (theme.switch-turns-dark, theme.choice-survives-reload, copy.*).
const theme = document.getElementById("theme-toggle");

const setTheme = (dark) => {
  document.documentElement.classList.toggle("dark", dark);
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
  theme.checked = dark;
  // Same key and shape as the defuss-shadcn Dark Mode guide, so the choice carries across defuss pages.
  try { localStorage.setItem("defuss-shadcn-theme", JSON.stringify(dark ? "dark" : "light")); } catch {}
};
theme.checked = document.documentElement.classList.contains("dark");
theme.addEventListener("change", () => setTheme(theme.checked));

// Each copy button names its terminal (data-copy); only command lines are copied, never output or prompts.
for (const button of document.querySelectorAll("[data-copy]")) {
  const label = button.querySelector("span");
  const status = document.getElementById(`${button.dataset.copy}-status`);
  button.addEventListener("click", async () => {
    const lines = [...document.querySelectorAll(`#${button.dataset.copy} pre[data-prefix]:not([data-tone]) code`)];
    try {
      await navigator.clipboard.writeText(lines.map((code) => code.textContent).join("\n"));
      status.textContent = "Copied";
    } catch {
      status.textContent = "Copy failed";
    }
    label.textContent = status.textContent;
    setTimeout(() => { label.textContent = "Copy"; }, 2000);
  });
}
