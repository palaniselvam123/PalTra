// Renders every lesson in ./lessons (the Scalp page's beginner guides) to a PDF in frontend/public/guides and prints
// each one's id, file and page count; the HTML goes to ./out. Needs Playwright with Chromium on the NODE_PATH:
//   cd frontend && NODE_PATH=$(npm root -g) node scripts/guides/build.js [lessonId ...]
// Then set the page counts in frontend/src/lib/lessons.ts from out/summary.json.
const fs = require("fs");
const path = require("path");
const { chromium } = require("playwright");

const OUT_HTML = path.join(__dirname, "out");
const PDF_DIR = path.resolve(__dirname, "../../public/guides");

function pageCount(buf) {
  const s = buf.toString("latin1");
  const m = s.match(/\/Type\s*\/Page[^s]/g);
  return m ? m.length : 0;
}

(async () => {
  fs.mkdirSync(OUT_HTML, { recursive: true });
  const only = process.argv.slice(2);
  const files = fs.readdirSync(path.join(__dirname, "lessons")).filter((f) => f.endsWith(".js")).sort();
  const browser = await chromium.launch();
  const summary = [];
  for (const f of files) {
    const lesson = require(path.join(__dirname, "lessons", f));
    if (only.length && !only.includes(lesson.id)) continue;
    const html = lesson.html();
    const htmlPath = path.join(OUT_HTML, `${lesson.id}.html`);
    fs.writeFileSync(htmlPath, html);
    const page = await browser.newPage();
    await page.goto("file://" + htmlPath, { waitUntil: "load" });
    const pdfPath = path.join(PDF_DIR, lesson.file);
    const buf = await page.pdf({ path: pdfPath, format: "A4", printBackground: true, preferCSSPageSize: true });
    await page.close();
    const pages = pageCount(buf);
    summary.push({ id: lesson.id, file: lesson.file, pages, kb: Math.round(buf.length / 1024) });
    console.log(`${lesson.id}: ${pages} pages, ${Math.round(buf.length / 1024)} KB -> ${pdfPath}`);
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT_HTML, "summary.json"), JSON.stringify(summary, null, 2));
})();
