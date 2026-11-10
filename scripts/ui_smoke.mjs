// Headless DOM smoke test for the static DealRadar client.
// Requires: npm install jsdom (dev-only). Run against a live local API:
//   node scripts/ui_smoke.mjs http://127.0.0.1:8013
import { readFile } from "node:fs/promises";
import { JSDOM } from "jsdom";

const base = process.argv[2] || "http://127.0.0.1:8013";
const html = await readFile(new URL("../web/index.html", import.meta.url), "utf8");
const js = await readFile(new URL("../web/app.js", import.meta.url), "utf8");

const dom = new JSDOM(html, { url: base + "/", runScripts: "outside-only", pretendToBeVisual: true });
const { window } = dom;
window.fetch = (input, init) => fetch(new URL(input, base), init);
if (!window.crypto?.randomUUID) {
  Object.defineProperty(window.crypto, "randomUUID", {
    value: () => "12345678-1234-4234-8234-123456789012",
  });
}
window.HTMLElement.prototype.scrollIntoView = () => {};
window.eval(js);

const $ = (sel) => window.document.querySelector(sel);
const text = (sel) => $(sel)?.textContent?.trim() || "";
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const assert = (condition, message) => {
  if (!condition) throw new Error("FAIL: " + message);
  console.log("ok - " + message);
};

$("#postal").value = "N9A 6K9";
$("#query").value = "chicken breast";
$("#searchForm").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));

for (let i = 0; i < 60 && !$(".card"); i += 1) await wait(500);
assert(window.document.querySelectorAll(".card").length > 0, "search renders product cards");
assert(text(".card .price").startsWith("$"), "price is formatted as CAD");
assert($("#sourceStatus").textContent.includes("flipp"), "source status is visible");
assert(!$("#formError").hidden === false, "no form error on a valid full postal code");

const compare = $("[data-compare]");
compare.click();
await wait(250);
assert(!$("#comparePanel").hidden, "comparison panel opens");
assert(window.document.querySelectorAll("#compareTable tbody tr").length >= 1, "comparison table has rows");

const watch = $("[data-watch]");
const itemId = watch.dataset.watch;
watch.click();
await wait(800);
assert(JSON.parse(window.localStorage.getItem("dr_watch")).some((w) => w.item_id === itemId), "watchlist persists locally");

$("#postal").value = "N9V";
$("#query").value = "milk";
$("#searchForm").dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
await wait(300);
assert(text("#formError").includes("full postal code"), "FSA-only search is rejected with a clear reason");

console.log("UI smoke test passed");
