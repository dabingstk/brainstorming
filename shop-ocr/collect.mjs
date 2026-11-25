// 用 ego-browser 执行：ego-browser nodejs < collect.mjs
// 只做“打开页面 + 截一张图 + 记录截图区域”，不点按钮、不并发、不调内部接口。
const fs = await import('node:fs/promises');

// ego-browser 的脚本不继承 shell 环境变量，工作目录也不是 shell 目录，所以这里用绝对路径。
// 把它改成你机器上的实际目录（Windows 用正斜杠即可）。
const ROOT = 'C:/shop-ocr';

const cfg = JSON.parse(await fs.readFile(`${ROOT}/config.json`, 'utf8'));
const shotDir = `${ROOT}/shots`;
await fs.mkdir(shotDir, { recursive: true });

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const pngPath = `${shotDir}/${stamp}.png`;
const metaPath = `${shotDir}/${stamp}.json`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const task = await taskSpace(`${cfg.taskSpace}-${stamp}`);
const page = task.page('p1');

await page.goto(cfg.url);
if (cfg.waitFor) {
  await page.waitForSelector(cfg.waitFor, { timeout: 30000 });
}
// 给首屏异步渲染留时间，并带一点随机，避免固定节奏
await sleep(2000 + Math.floor(Math.random() * 2500));

let rect = null;
let viewport = null;

if (cfg.capture && cfg.capture.selector) {
  const info = await page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    el.scrollIntoView({ block: 'center', behavior: 'instant' });
    const r = el.getBoundingClientRect();
    return {
      x: r.left,
      y: r.top,
      width: r.width,
      height: r.height,
      vw: window.innerWidth,
      vh: window.innerHeight
    };
  }, cfg.capture.selector);

  if (info) {
    rect = { x: info.x, y: info.y, width: info.width, height: info.height };
    viewport = { width: info.vw, height: info.vh };
    await sleep(600);
  }
}

await page.screenshot({ path: pngPath });

const meta = {
  url: cfg.url,
  source: cfg.source,
  collected_at: new Date().toISOString(),
  png: pngPath,
  rect,
  viewport
};
await fs.writeFile(metaPath, JSON.stringify(meta, null, 2));

console.log(JSON.stringify({ ok: true, png: pngPath, meta: metaPath }));

await task.finish({ keep: [] });
