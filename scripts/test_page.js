// The page's own JavaScript, run against a stub DOM.
//
//     node scripts/test_page.js            # renders its own fixture through Python
//     node scripts/test_page.js page.html  # or checks a report you already have
//
// The three charts share one time axis and one viewport (ARCHITECTURE.md section 7), and
// none of that is visible from the Python side: it lives in the script embedded in the
// page.  This loads that script, drives it the way a reader would, and asserts the two time
// charts stay in step with each other and with the composition pie.  Node only -- no npm,
// no browser, no network.
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');

const REPO = path.dirname(__dirname);

function fixture() {
  const out = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'tcpage-')), 'page.html');
  const py = [
    'import sys, io',
    `sys.path.insert(0, r'${path.join(REPO, 'scripts')}')`,
    'import test_pipeline as tp',
    `io.open(r'${out}', 'w', encoding='utf-8').write(tp.page_fixture())`,
  ].join('\n');
  execFileSync(process.env.PYTHON || 'python', ['-c', py], { cwd: REPO, stdio: 'inherit' });
  return out;
}

const file = process.argv[2] || fixture();
const html = fs.readFileSync(file, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (scripts.length !== 2) throw new Error(`expected 2 scripts in the page, got ${scripts.length}`);

// ---- a DOM with just enough in it ---------------------------------------------------
const WIDTH = 1148;                        // the panel width the charts are measured at

function node(attrs = {}) {
  return {
    attrs,
    innerHTML: '',
    classList: { add() {}, remove() {} },
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    addEventListener() {},
    setPointerCapture() {},
    getBoundingClientRect() { return { left: 0, top: 0, width: WIDTH, height: 300 }; },
    querySelector(sel) { return (this.kids || []).find(k => k.sel === sel) || null; },
    querySelectorAll(sel) { return (this.kids || []).filter(k => k.sel === sel); },
  };
}

// The daily bars are rendered by Python; the page only ever moves them.
const dailyHtml = html.slice(html.indexOf('id="dailychart"'));
const bars = [...dailyHtml.matchAll(/<g class="bar" data-a="(\d+)" data-b="(\d+)"/g)]
  .map(m => Object.assign(node({ 'data-a': m[1], 'data-b': m[2] }), { sel: 'g.bar' }));
if (!bars.length) throw new Error('no daily bars in the page');

const clip = Object.assign(node(), { sel: '.clip' });
const base = Object.assign(node(), { sel: '.base' });
const peak = Object.assign(node(), { sel: '.peak' });
const ax = Object.assign(node(), { sel: '.ax' });
const dailySvg = Object.assign(node({ 'data-h': '210', 'data-t': '18', 'data-b': '34' }),
  { sel: 'svg', kids: [...bars, clip, base, peak, ax] });

const rlHost = node();
const dailyHost = Object.assign(node(), { kids: [dailySvg] });
const pieHost = node();
const modelHost = node();
const els = { rlchart: rlHost, dailychart: dailyHost, catpie: pieHost, modelpie: modelHost };

const raf = [];
const ctx = {
  console,
  requestAnimationFrame(fn) { raf.push(fn); return raf.length; },
  clearTimeout() {}, setTimeout() {}, addEventListener() {},
  document: {
    getElementById: id => els[id] || null,
    querySelector: sel => (sel === '.chart' ? rlHost : null),
  },
};
ctx.window = ctx;
ctx.globalThis = ctx;
vm.createContext(ctx);
scripts.forEach(s => vm.runInContext(s, ctx));

const run = code => vm.runInContext(code, ctx);
const flush = () => { while (raf.length) raf.shift()(); };

let bad = 0;
const check = (name, ok, detail) => {
  console.log((ok ? '[PASS] ' : '[FAIL] ') + name + (ok ? '' : '\n        ' + detail));
  if (!ok) bad++;
};

const ticksOf = svg => [...svg.matchAll(
  /<text x="([\d.]+)" y="[\d.]+" text-anchor="middle" fill="var\(--dim\)" font-size="11">([^<]*)</g)]
  .map(m => m[1] + '=' + m[2]);
const transforms = () => bars.map(b => b.getAttribute('transform'));
const pieTotal = () => (pieHost.innerHTML.match(/<b>([^<]+)<\/b>/) || [])[1] || null;
const modelTotal = () => (modelHost.innerHTML.match(/<b>([^<]+)<\/b>/) || [])[1] || null;

flush();                                   // the page drew itself once, at full extent

const DOM = run('DOM'), W = run('W'), L = run('L'), RM = run('RM');
check('the page measures the panel it is in', W === WIDTH && L <= 62 && RM <= 48,
      `W=${W} L=${L} RM=${RM}`);

// 1. one axis: the same ticks, at the same x, in both time charts
const t1 = ticksOf(rlHost.innerHTML), t2 = ticksOf(ax.innerHTML);
check('both charts draw the same ticks at the same x',
      t1.length >= 2 && JSON.stringify(t1) === JSON.stringify(t2),
      JSON.stringify([t1, t2]));

// 2. the same instant lands on the same x in both charts
const a0 = +bars[0].getAttribute('data-a');
const xOfDay = +transforms()[0].match(/translate\(([-\d.]+),/)[1];
check('a day bar starts where the limit chart puts that instant',
      Math.abs(xOfDay - run(`X(${a0})`)) < 0.01, `${xOfDay} vs ${run(`X(${a0})`)}`);
check('the plot area is the same in both charts',
      +clip.getAttribute('x') === L && +clip.getAttribute('width') === W - L - RM
      && +base.getAttribute('x1') === L && +base.getAttribute('x2') === W - RM,
      `clip=${clip.getAttribute('x')}/${clip.getAttribute('width')} base=${base.getAttribute('x1')}..${base.getAttribute('x2')}`);

const fullPie = pieTotal();
check('the pie sums the whole range at full extent', !!fullPie, String(fullPie));

// The model pie is the daily bars regrouped: same total, same colour per model.
const fullModel = modelTotal();
const dailyTotal = run('(D.models.days||[]).reduce((s,r)=>s+Object.values(r[2]).reduce((a,b)=>a+b,0),0)');
check('the model pie sums every day the daily chart draws',
      fullModel === run(`big(${dailyTotal})`), `${fullModel} vs ${dailyTotal}`);
const firstModel = run('D.models.order[0]');
const barHasC0 = /<rect x="0.04"[^>]*fill="var\(--c0\)"/.test(dailyHtml);
check('a model keeps its daily-chart colour in the pie',
      !!firstModel && barHasC0
      && modelHost.innerHTML.includes(`background:var(--c0)"></i>${firstModel} `),
      `${firstModel} barHasC0=${barHasC0}`);

// 3. zoom: horizontal only, both charts, pie included
const before = { rl: rlHost.innerHTML, bars: transforms() };
const mid = (DOM[0] + DOM[1]) / 2;
run(`setSpan((VIEW[1]-VIEW[0])/8, ${mid}, L+PLOT/2)`);
flush();
const view = run('VIEW');
check('zooming shrinks the visible span',
      Math.abs((view[1] - view[0]) - (DOM[1] - DOM[0]) / 8) < 1, `span=${view[1] - view[0]}`);
const axisOf = s => (s.match(/text-anchor="end"[^>]*>([^<]+)</g) || []).join('|');
check('the value axis is untouched by zooming',
      axisOf(before.rl) === axisOf(rlHost.innerHTML),
      `${axisOf(before.rl)}  vs  ${axisOf(rlHost.innerHTML)}`);
check('zooming one chart moved the other', transforms()[0] !== before.bars[0],
      transforms()[0]);
check('the day bars got wider, not taller',
      +transforms()[0].match(/scale\(([\d.]+),1\)/)[1]
      > +before.bars[0].match(/scale\(([\d.]+),1\)/)[1]
      && !/scale\([\d.]+,[^1]/.test(transforms()[0]), transforms()[0]);
const emptyPie = pieHost.innerHTML;
run(`setSpan((DOM[1]-DOM[0])/8, DOM[0], L)`);            // over the content, not a quiet gap
flush();
check('the pie recomposes for the visible range',
      !!pieTotal() && pieTotal() !== fullPie && emptyPie !== pieHost.innerHTML,
      `${fullPie} -> ${pieTotal()}`);
const t1z = ticksOf(rlHost.innerHTML), t2z = ticksOf(ax.innerHTML);
check('the two charts still share ticks when zoomed',
      t1z.length >= 1 && JSON.stringify(t1z) === JSON.stringify(t2z),
      JSON.stringify([t1z, t2z]));

// 4. drag moves the range by exactly the distance dragged
const v0 = run('VIEW').slice();
run('panPx(-100)');
flush();
const v1 = run('VIEW');
const expect = (v0[1] - v0[0]) * 100 / run('PLOT');
check('dragging moves the range by the distance dragged',
      Math.abs((v1[0] - v0[0]) - expect) < 1
      && Math.abs((v1[1] - v1[0]) - (v0[1] - v0[0])) < 1,
      `moved ${v1[0] - v0[0]}, expected ${expect}`);

// 5. the viewport cannot leave the range it was given
run('panPx(-1e9)'); flush();
check('panning stops at the end of the range', Math.abs(run('VIEW')[1] - DOM[1]) < 1,
      String(run('VIEW')));
run('panPx(1e9)'); flush();
check('panning stops at the start of the range', Math.abs(run('VIEW')[0] - DOM[0]) < 1,
      String(run('VIEW')));
run('setSpan(1, VIEW[0], L)'); flush();
check('zoom has a floor',
      Math.abs((run('VIEW')[1] - run('VIEW')[0]) - run('MIN_SPAN')) < 1, String(run('VIEW')));
run('setSpan(1e12, VIEW[0], L)'); flush();
check('zooming out stops at the full range',
      Math.abs(run('VIEW')[0] - DOM[0]) < 1 && Math.abs(run('VIEW')[1] - DOM[1]) < 1,
      String(run('VIEW')));
check('the pie is whole again at full extent', pieTotal() === fullPie,
      `${pieTotal()} vs ${fullPie}`);
check('the model pie is whole again at full extent', modelTotal() === fullModel,
      `${modelTotal()} vs ${fullModel}`);

// 6. a window with nothing in it says so, rather than drawing an empty pie
run('VIEW = [DOM[0], DOM[0]+600]'); run('redraw()');
check('an empty slice is explained, not left blank',
      /No tokenized content in the visible range|<b>/.test(pieHost.innerHTML),
      pieHost.innerHTML.slice(0, 160));

// 7. a phone: the geometry is re-derived, and the labels keep their size
run(`document.querySelector = () => ({ getBoundingClientRect: () => ({left:0,top:0,width:334,height:210}) })`);
run('measure(); reset();'); flush();
check('the charts re-measure for a narrow screen',
      run('W') === 334 && run('L') < 62 && run('PLOT') > 200,
      `W=${run('W')} L=${run('L')} RM=${run('RM')} PLOT=${run('PLOT')}`);
const tm1 = ticksOf(rlHost.innerHTML), tm2 = ticksOf(ax.innerHTML);
check('a phone still gets readable ticks, the same ones in both charts',
      tm1.length >= 2 && JSON.stringify(tm1) === JSON.stringify(tm2), JSON.stringify([tm1, tm2]));
check('nothing is drawn outside the narrow plot area',
      tm1.every(t => +t.split('=')[0] >= run('L') - 0.5
                     && +t.split('=')[0] <= run('W') - run('RM') + 0.5), JSON.stringify(tm1));

console.log(bad ? `\n${bad} FAILED` : `\n${'all page checks passed'}`);
process.exit(bad ? 1 : 0);
