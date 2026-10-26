// Print the bilingual HTML (对照 mode, all <details> open) to an A4 landscape PDF.
const { chromium } = require('playwright');
(async () => {
  const [src, out] = process.argv.slice(2);
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1400, height: 1000 } });
  await page.goto('file://' + src, { waitUntil: 'load', timeout: 180000 });
  await page.evaluate(async () => {
    document.body.setAttribute('data-mode', 'both');
    document.querySelectorAll('details').forEach(d => d.open = true);
    document.querySelectorAll('img[loading="lazy"]').forEach(i => i.loading = 'eager');
    await document.fonts.ready;
    await Promise.all([...document.images].map(i => i.complete ? 0 : new Promise(r => { i.onload = i.onerror = r; })));
  });
  await page.emulateMedia({ media: 'print' });
  await page.pdf({ path: out, format: 'A4', landscape: true, printBackground: true,
                   margin: { top: '12mm', bottom: '14mm', left: '14mm', right: '14mm' },
                   displayHeaderFooter: true, headerTemplate: '<span></span>',
                   footerTemplate: '<div style="font-size:8px;width:100%;text-align:center;color:#888">临床检验技术的系统化调查 · 中日对照 · <span class="pageNumber"></span> / <span class="totalPages"></span></div>' });
  await browser.close();
})();
