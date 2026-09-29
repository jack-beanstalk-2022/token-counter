"""Self-contained HTML report.

No CDN, no network, no external fonts: the file is opened from disk and must render with
the machine offline.  Charts are inline SVG; interaction is a few hundred lines of vanilla
JS over an embedded JSON blob.  Under Clinical the chart marks are repainted by a small
WebGL2 layer (GL_JS) whose last pass draws them through a simulated water surface; the SVG
keeps the axes, text and tooltips, and keeps the marks too wherever WebGL2 is missing.

The page ships its styles over one markup (STYLES), cycled by a button in the top bar or the
`[` / `]` keys and remembered per browser.  A style never changes what the charts draw, only
the colours, type and paper they are drawn on.

The three charts share **one time axis and one viewport**.  The limit chart and the daily
chart are drawn over the same domain with the same margins, so a moment sits at the same x
in both and they can be read against each other; zooming or dragging either one moves both,
and recomposes the category pie over whatever range is in view.  Zoom is horizontal only --
a chart's value axis never changes, so heights stay comparable at every zoom level.

Every figure derived from inference rather than measurement carries a visible marker
(ARCHITECTURE.md section 7).
"""
import html
import json
import math
import os
import random
import time

# One geometry for every time chart.  The page re-derives the width from the panel at run
# time (one viewBox unit = one CSS pixel, so axis text is legible on a phone); these are the
# no-JS fallback values, and the proportions the margins are capped at.
CHART_W, CHART_L, CHART_R = 980, 62, 48
RL_H, RL_T, RL_B = 300, 18, 34
DAILY_H, DAILY_T, DAILY_B = 210, 18, 34

# The page styles, in the order the button cycles them; the first is the default.  The page
# reads this list from its payload, so it is written down once.
STYLES = [('clinical', 'Clinical'), ('matisse', 'Matisse')]

CSS = """
:root{
  --bg:#ffffff; --panel:#f7f8fa; --line:#e3e6ea; --fg:#14171a; --dim:#5b6570;
  --cached:#93b4f5; --uncached:#2563eb; --out:#10b981;
  --warn:#b45309; --warn-bg:#fef3c7;
  --c0:#2563eb;--c1:#7c3aed;--c2:#db2777;--c3:#ea580c;--c4:#ca8a04;--c5:#16a34a;
  --c6:#0891b2;--c7:#4f46e5;--c8:#9333ea;--c9:#e11d48;--c10:#65a30d;--c11:#0d9488;
  --c12:#a16207;--c13:#475569;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#0f1115; --panel:#171a21; --line:#272b33; --fg:#e6e9ee; --dim:#9aa4b2;
  --cached:#2b4270; --uncached:#60a5fa; --out:#34d399;
  --warn:#fbbf24; --warn-bg:#3b2f10;
  --c0:#60a5fa;--c1:#a78bfa;--c2:#f472b6;--c3:#fb923c;--c4:#fbbf24;--c5:#4ade80;
  --c6:#22d3ee;--c7:#818cf8;--c8:#c084fc;--c9:#fb7185;--c10:#a3e635;--c11:#2dd4bf;
  --c12:#d6b45b;--c13:#94a3b8;
}}
:root[data-theme="dark"]{
  --bg:#0f1115; --panel:#171a21; --line:#272b33; --fg:#e6e9ee; --dim:#9aa4b2;
  --cached:#2b4270; --uncached:#60a5fa; --out:#34d399;
  --warn:#fbbf24; --warn-bg:#3b2f10;
  --c0:#60a5fa;--c1:#a78bfa;--c2:#f472b6;--c3:#fb923c;--c4:#fbbf24;--c5:#4ade80;
  --c6:#22d3ee;--c7:#818cf8;--c8:#c084fc;--c9:#fb7185;--c10:#a3e635;--c11:#2dd4bf;
  --c12:#d6b45b;--c13:#94a3b8;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
  font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 80px}
.sub{color:var(--dim);font-size:13px;margin:0 0 6px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(168px,1fr));gap:10px;margin-top:18px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
.tile .v{font-size:23px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.tile .n{color:var(--dim);font-size:12px;margin-top:2px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-top:12px}
.legend{display:flex;flex-wrap:wrap;gap:12px;margin:8px 0 2px;font-size:12px;color:var(--dim)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
.legend b{color:var(--fg);font-variant-numeric:tabular-nums}
.legend [data-i]{transition:opacity .12s,color .12s}
.hi .legend [data-i]{opacity:.35}
.hi .legend [data-i].on{opacity:1;color:var(--fg);font-weight:600}
svg{display:block;width:100%;height:auto;overflow:visible}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
/* pan-y, not none: a vertical swipe still scrolls the page on a phone, while a horizontal
   drag and a two-finger pinch reach the chart instead of the browser. */
.chart{touch-action:pan-y;-webkit-user-select:none;user-select:none;
  -webkit-tap-highlight-color:transparent}
.chart.inplot{cursor:grab}
.chart.drag{cursor:grabbing}
.pie{flex:0 0 auto;width:240px;max-width:100%}
.pies{display:flex;flex-wrap:wrap;gap:24px 48px}
.pies>div{flex:1 1 380px;min-width:0}
@media(max-width:640px){.wrap{padding:18px 12px 60px} .tile .v{font-size:19px}}
"""

# The page's styles.  Every style is CSS over the same markup, keyed on `html[data-style]`,
# and every chart colour is a variable, so a style restyles the charts without redrawing
# them.  Nothing here is fetched: fonts are whatever the machine has, and every stack ends in
# a generic family.  Matisse's categorical palette (--c0..--c12, with --c13 a neutral for
# `other`) is checked for colour-vision separation against its own panel: neighbouring slots,
# and every pair among the first four, which is as many models as most corpora have.
# Clinical's predates that check and does not pass it (--c0 and --c1 converge under
# deuteranopia).
STYLE_CSS = r"""
/* ---- shared chrome: the style bar, the masthead, the switch ---------------------------- */
:root{--kicker:"Codex usage, recounted locally"}
.bar{position:sticky;top:0;z-index:20;display:flex;justify-content:space-between;align-items:center;
  gap:12px;padding:10px 16px;background:var(--bg);border-bottom:1px solid var(--line)}
.brand{font-weight:700;letter-spacing:.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* The style switch is one dot, drawn in the style it switches *to* -- so its colours are
   fixed here, not read from the page's variables, which belong to the style on screen. */
#stylebtn{display:grid;place-items:center;width:36px;height:36px;padding:0;border:0;
  background:none;cursor:pointer;border-radius:50%;-webkit-tap-highlight-color:transparent}
#stylebtn:focus-visible{outline:2px solid var(--uncached);outline-offset:2px}
.sdot{display:block;width:18px;height:18px;border-radius:50%;background:#8a8f98;
  transition:transform .15s ease}
#stylebtn:hover .sdot{transform:scale(1.15)}
/* Clinical: a crisp system-blue disc on a white panel ring with a hairline. */
#stylebtn[data-next="clinical"] .sdot{background:#2563eb;box-shadow:0 0 0 3px #fff,0 0 0 4px #c9ced6}
/* Matisse: a cobalt gouache cut-out, pinned slightly out of register over a sage sheet. */
#stylebtn[data-next="matisse"] .sdot{width:20px;height:19px;background:#394ca3;
  border-radius:60% 40% 55% 45%/55% 60% 40% 45%;box-shadow:3px 3px 0 #c9d4d2;transform:rotate(-8deg)}
#stylebtn[data-next="matisse"]:hover .sdot{transform:rotate(-8deg) scale(1.15)}
.mast{position:relative;padding:26px 0 6px}
.kicker{color:var(--dim);font-size:12px;letter-spacing:.08em;text-transform:uppercase}
.kicker::before{content:var(--kicker)}
.mast h1{margin:4px 0 6px;font-size:30px;line-height:1.05;letter-spacing:-.01em}
.dek{margin:0;color:var(--dim)}
.deco>*{display:none}
/* The switch fades through the page background rather than cutting. */
.wipe{position:fixed;inset:0;z-index:60;pointer-events:none;background:var(--bg)}
.wipe.go{display:block;animation:wipe .56s ease-in-out forwards}
@keyframes wipe{0%{opacity:0}45%,55%{opacity:1}100%{opacity:0}}
@media(max-width:640px){.brand{font-size:13px}}

/* ---- the WebGL layer: marks painted on a canvas behind each panel's content ----------- */
.glc{display:none;position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
[data-gl] .glc{display:block}
[data-gl] .panel{position:relative}
[data-gl] .panel>:not(.glc){position:relative}
/* The SVG marks stay in place, transparent, so their tooltips still answer the pointer. */
[data-gl] .mk{fill-opacity:0!important;stroke-opacity:0!important}
/* The headline numbers, repainted into the GL layer so the water reaches them: the canvas
   sits over the tiles, and the text under it keeps its place (and stays selectable and
   readable to assistive tech) but is not painted. */
.glt{display:none;position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:1}
[data-gl] .tiles{position:relative}
[data-gl] .glt{display:block}
[data-gl] .tiles.gltxt .tile>*{opacity:0}

/* ---- 1. CLINICAL: the base sheet above, light or dark with the system ----------------- */

/* ---- 2. MATISSE: papiers découpés -- gouache paper, cut with scissors, pinned up ------ */
/* Cream paper, a sage and a dusty-rose sheet torn behind the page, white brush dashes and an
   ink flower cut in one piece.  Gouache is matte, so nothing here is glossy: fills are flat,
   and the only depth is a second coloured sheet showing under the edge of a panel. */
:root[data-style="matisse"]{color-scheme:light;
  --bg:#f3efe6;--panel:#faf7f0;--line:#e0d8c8;--fg:#23252f;--dim:#6a655d;
  --cached:#c9d4d2;--uncached:#2f3a63;--out:#139688;--warn:#b4533e;--warn-bg:#f1dcd4;
  --c0:#394ca3;--c1:#bb5135;--c2:#139688;--c3:#a29015;--c4:#a82653;--c5:#1099bf;--c6:#732e7b;
  --c7:#66640c;--c8:#5571d8;--c9:#cb749e;--c10:#00673f;--c11:#c6784a;--c12:#87579d;--c13:#8f887c;
  --sage:#c9d4d2;--rose:#a8807b;--blush:#dcc0ba;--straw:#e9dfc8;--ink:#23252f;
  --serif:"Didot","Bodoni 72","Bodoni MT","Playfair Display","Libre Bodoni",Georgia,"Times New Roman",serif;
  --kicker:"Papiers d\00E9 coup\00E9 s \00B7  Codex usage, cut from local records"}
[data-style="matisse"] body{font:15px/1.55 "Avenir Next",Avenir,Futura,"Century Gothic","Gill Sans",
  "Trebuchet MS",system-ui,sans-serif}
[data-style="matisse"] .wrap{position:relative;z-index:1}
[data-style="matisse"] nav.bar{background:rgba(243,239,230,.86);border-bottom:2px solid var(--ink);
  backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px)}
[data-style="matisse"] .brand{font:italic 400 21px/1 var(--serif);letter-spacing:0}
[data-style="matisse"] .mast{padding:64px 0 40px;min-height:42vh}
[data-style="matisse"] .kicker{font:italic 400 17px/1.3 var(--serif);text-transform:none;letter-spacing:.01em;
  color:var(--fg)}
[data-style="matisse"] .mast h1{font:400 clamp(46px,8.4vw,104px)/.94 var(--serif);letter-spacing:-.02em;
  margin:12px 0 20px;max-width:8.5ch}
[data-style="matisse"] .dek{display:inline-block;background:var(--ink);color:var(--bg);padding:6px 14px 7px;
  font-size:13px;letter-spacing:.03em;transform:rotate(-1deg);
  border-radius:3px 14px 4px 12px/12px 4px 14px 3px}
/* The tiles are the cut-outs: one sheet of gouache each, trimmed by hand and pinned. */
[data-style="matisse"] .tiles{gap:18px;margin-top:8px}
[data-style="matisse"] .tile{position:relative;border:0;padding:18px 18px 18px 20px;background:var(--sage);
  border-radius:28px 12px 34px 16px/18px 30px 14px 26px;transform:rotate(-.7deg);
  transition:transform .25s cubic-bezier(.3,1.4,.5,1)}
[data-style="matisse"] .tile:nth-child(3n+2){background:var(--blush);transform:rotate(.6deg);
  border-radius:14px 32px 18px 28px/26px 12px 30px 16px}
[data-style="matisse"] .tile:nth-child(3n+3){background:var(--straw);transform:rotate(-.3deg);
  border-radius:34px 18px 26px 12px/14px 26px 18px 30px}
[data-style="matisse"] .tile:hover{transform:rotate(0) translateY(-3px)}
[data-style="matisse"] .tile::before{content:"";position:absolute;top:9px;right:13px;width:7px;height:7px;
  border-radius:50%;background:var(--ink)}
[data-style="matisse"] .tile .k{color:var(--fg);font-weight:600;font-size:10.5px;letter-spacing:.16em}
[data-style="matisse"] .tile .v{font:400 42px/1.05 var(--serif);letter-spacing:-.01em;margin-top:6px}
[data-style="matisse"] .tile .n{color:rgba(35,37,47,.78)}
/* Each panel is a paper sheet laid over a coloured one, a few millimetres out of register. */
[data-style="matisse"] .panel{border:0;padding:18px 20px;margin-top:26px;
  border-radius:6px 22px 8px 18px/18px 8px 22px 6px;box-shadow:-10px 10px 0 -2px var(--sage)}
[data-style="matisse"] .panel:nth-child(even){box-shadow:10px 10px 0 -2px var(--blush)}
[data-style="matisse"] .panel.pies{box-shadow:-10px 10px 0 -2px var(--straw)}
[data-style="matisse"] .legend{color:var(--dim)}
[data-style="matisse"] .legend i{width:12px;height:12px;border-radius:60% 40% 55% 45%/55% 60% 40% 45%}
[data-style="matisse"] .pie path{stroke-width:3;stroke-linejoin:round}
/* The area under the cumulative curve is a flat sage sheet, cut along the ink line. */
[data-style="matisse"] #rlchart path[fill-opacity]{fill:var(--sage);fill-opacity:1}
[data-style="matisse"] .deco .mz{display:block}
.mz{position:fixed;inset:0;z-index:0;overflow:hidden;pointer-events:none}
.mz svg{position:absolute;display:block;height:auto;overflow:visible}
.mz .sage{fill:var(--sage)} .mz .rose{fill:var(--rose)} .mz .ink{fill:var(--ink)}
.mz .stem{fill:none;stroke:var(--ink);stroke-width:7;stroke-linecap:round}
.mz .dash{stroke:#fff;stroke-width:11;stroke-linecap:round}
.mz-sage{left:-10vw;top:3vh;width:min(66vw,720px)}
.mz-rose{right:-9vw;bottom:-10vh;width:min(50vw,560px)}
.mz-dash1{right:8vw;top:10vh;width:min(36vw,360px)}
.mz-dash2{left:1vw;bottom:5vh;width:min(24vw,250px)}
.mz-flower{right:max(1vw,calc(50vw - 640px));top:8vh;width:auto!important;height:min(86vh,760px)!important;
  transform-origin:62% 100%;animation:sway 11s ease-in-out infinite alternate}
@keyframes sway{from{transform:rotate(-1.8deg)}to{transform:rotate(1.4deg)}}
@media(prefers-reduced-motion:reduce){.mz-flower{animation:none}}
@media(max-width:640px){
  .mz-flower{right:-24vw;top:12vh;height:48vh!important}
  .mz-sage{left:-30vw;width:96vw} .mz-rose{right:-30vw;width:84vw}
  .mz-dash1{width:44vw;right:-6vw;top:44vh}
  [data-style="matisse"] .mast{padding-top:40px;min-height:0}
  [data-style="matisse"] .kicker{max-width:64%}
  [data-style="matisse"] .tile .v{font-size:32px}}
"""

JS = """
const D = window.__TC__;
// Previews, model names and cwds come straight from rollout content; never hand them to
// innerHTML raw.
const esc = s => String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));

const big = n => n==null ? '--'
  : Math.abs(n)>=1e9 ? (n/1e9).toFixed(2)+'B'
  : Math.abs(n)>=1e6 ? (n/1e6).toFixed(1)+'M'
  : Math.abs(n)>=1e3 ? (n/1e3).toFixed(1)+'K' : String(n);
const when = t => new Date(t*1000).toLocaleString([], {month:'short', day:'numeric',
                                                      hour:'2-digit', minute:'2-digit'});
const day = t => new Date(t*1000).toLocaleDateString([], {month:'short', day:'numeric'});
const hm  = t => new Date(t*1000).toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
const clamp = (v,a,b) => v<a?a:(v>b?b:v);
const byId = id => document.getElementById(id);

const HOUR = 3600, DAY = 86400;

// ---- marks, for the WebGL layer -----------------------------------------------------
// Every chart also records what it drew as plain shapes -- areas, lines, rects, pies -- in its
// own SVG's units, keyed by the element it drew into.  Under a style that paints its marks
// in WebGL (GL_JS below) the SVG keeps the axes, the text and the tooltips, its marks go
// transparent, and these shapes are drawn instead.  Everywhere else nothing reads them.
const SCN = new Map();              // host -> {svg: () => element, vb: [w, h], clip, list}
const GLH = {dirty(){}};            // replaced by the WebGL layer once it is running
const varOf = f => (/var\((--[\w-]+)\)/.exec(f||'') || [])[1] || f;
function marks(host, svg, vb, clip, list){
  if(list) SCN.set(host, {svg, vb, clip, list}); else SCN.delete(host);
  GLH.dirty();
}

// ---- one viewport, three charts -----------------------------------------------------
// DOM is the whole range the report covers; VIEW is the slice currently drawn.  Every chart
// reads VIEW, so panning or zooming any one of them moves all three -- which is the reason
// the daily bars are rendered in unit-x rather than in pixels.
const G = D.geo || {};
const DOM = D.domain;
let VIEW = DOM ? [DOM[0], DOM[1]] : null;
// Deepest zoom: one day.  Content is placed at the hour its file opened, so below a day the
// composition pie stops resolving and mostly shows the gaps between sessions; a day is also
// the unit the daily chart is drawn in.  (A corpus shorter than a day is shown whole.)
const MIN_SPAN = 86400;

let W = G.w||980, L = G.l||62, RM = G.r||48, PLOT = W-L-RM;

const X   = t  => L + (t-VIEW[0])/(VIEW[1]-VIEW[0])*PLOT;
const Tat = vx => VIEW[0] + (vx-L)/PLOT*(VIEW[1]-VIEW[0]);

function measure(){
  // One viewBox unit = one CSS pixel.  A fixed 980-unit viewBox squeezed onto a 360px phone
  // renders 11px axis text at four; this keeps every label at the size it asks for.
  const host = document.querySelector('.chart');
  const w = host ? Math.round(host.getBoundingClientRect().width) : 0;
  W = Math.max(300, w || 980);
  L = Math.round(Math.min(G.l||62, W*0.17));
  RM = Math.round(Math.min(G.r||48, W*0.13));
  PLOT = W - L - RM;
}

// ---- shared ticks -------------------------------------------------------------------
// Both time charts draw the same ticks at the same x, which is what makes one readable
// against the other.  Day steps are anchored on the domain and stepped with setDate: ticks
// stay on local midnights across a clock change, and do not jump to neighbouring days while
// the chart is being dragged.
const PITCH = 64;                          // smallest gap between two tick labels, in px
                                           // -- a date at 11px is about 40 of them, and a
                                           // phone's plot is only ~230 wide
function ticks(){
  const span = VIEW[1]-VIEW[0];
  const fits = s => s/span*PLOT >= PITCH;
  const out = [];
  let step = 0;
  for(const s of [300, 600, 900, 1800, HOUR, 2*HOUR, 3*HOUR, 6*HOUR, 12*HOUR])
    if(fits(s)){ step = s; break; }
  if(step){
    const a = new Date(VIEW[0]*1000); a.setHours(0,0,0,0);
    let t = a.getTime()/1000;
    t += Math.ceil((VIEW[0]-t)/step)*step;
    for(; t<=VIEW[1] && out.length<64; t+=step) out.push([t, step]);
    return out;
  }
  let d = 364;
  for(const s of [1,2,3,7,14,28,91,182,364]) if(fits(s*DAY)){ d = s; break; }
  const c = new Date(DOM[0]*1000); c.setHours(0,0,0,0);
  const skip = Math.floor((VIEW[0]-c.getTime()/1000)/(d*DAY));
  if(skip > 0) c.setDate(c.getDate()+skip*d);
  for(let i=0; i<512; i++){
    const t = c.getTime()/1000;
    if(t > VIEW[1]) break;
    if(t >= VIEW[0]) out.push([t, d*DAY]);
    c.setDate(c.getDate()+d);
  }
  return out;
}

const tickLabel = (t, step) =>
  (step >= DAY || new Date(t*1000).getHours()===0) ? day(t) : hm(t);

/** Tick labels along the bottom, and -- unless `grid` is false -- a dashed guide up from each.
 *  The daily chart takes the labels only: its bars already mark the days. */
function axis(h, top, bot, tk, grid = true){
  let s = '';
  for(const [t, step] of tk){
    const xx = X(t);
    if(xx < L-0.5 || xx > W-RM+0.5) continue;
    if(grid) s += `<line x1="${xx.toFixed(1)}" y1="${top}" x2="${xx.toFixed(1)}" y2="${h-bot}" `+
         `stroke="var(--line)" stroke-width="1" stroke-dasharray="2 4"/>`;
    s += `<text x="${xx.toFixed(1)}" y="${h-bot+15}" text-anchor="middle" fill="var(--dim)" `+
         `font-size="11">${esc(tickLabel(t, step))}</text>`;
  }
  return s;
}

// ---- chart 1: cumulative tokens per weekly limit window ------------------------------
const RL = D.rate_limits || {};
const WINS = RL.windows || [];
// cum_points are [t, cumulative input, cumulative uncached, cumulative output].  Input and
// output are summed rather than drawn apart: output is under 1% of input, so a second curve
// would sit flat on the axis and say nothing.
const pick = p => p[1]+p[3];
// Fixed over the corpus, never over the viewport: zoom moves the time axis and leaves the
// value axis alone, so a curve keeps its height while the window slides under it.
let VMAX = 0;
WINS.forEach(w => (w.cum_points||[]).forEach(p => { VMAX = Math.max(VMAX, pick(p)); }));
VMAX = VMAX || 1;

function drawRL(tk){
  const host = byId('rlchart');
  if(!host) return;
  if(!WINS.length){
    host.innerHTML = '<p class="sub">No weekly-limit snapshots in range.</p>';
    marks(host, null);
    return;
  }
  const H = G.rl_h||300, T = G.rl_t||18, B = G.rl_b||34;
  const y  = v => H-B - (v/VMAX)*(H-B-T);
  const yp = p => H-B - (p/100)*(H-B-T);

  let s = `<svg viewBox="0 0 ${W} ${H}" data-h="${H}" data-t="${T}" data-b="${B}" role="img" `+
          `aria-label="cumulative tokens per weekly limit window">`;
  s += `<defs><clipPath id="tcclip-rl"><rect x="${L}" y="0" width="${PLOT}" height="${H}"/>`+
       `</clipPath></defs>`;
  // horizontal guides + left axis (measured) + right axis (reported)
  [0,.25,.5,.75,1].forEach(f=>{
    const yy = y(VMAX*f);
    s += `<line x1="${L}" y1="${yy.toFixed(1)}" x2="${W-RM}" y2="${yy.toFixed(1)}" stroke="var(--line)" stroke-width="1"/>`;
    s += `<text x="${L-8}" y="${(yy+4).toFixed(1)}" text-anchor="end" fill="var(--dim)" font-size="11">${big(Math.round(VMAX*f))}</text>`;
    s += `<text x="${W-RM+8}" y="${(yp(100*f)+4).toFixed(1)}" fill="var(--warn)" font-size="11">${Math.round(100*f)}%</text>`;
  });
  s += axis(H, T, B, tk);

  s += `<g clip-path="url(#tcclip-rl)">`;
  const mk = [];
  // Window boundaries carry the date they opened.  On a narrow screen, or zoomed out far
  // enough that three windows share fifty pixels, those labels collide into a smear -- so a
  // label is drawn only where there is room for it.  The boundary line is always drawn.
  let lastLbl = -1e9;
  WINS.forEach(w=>{
    const pts = w.cum_points||[], pcs = w.pct_points||[];
    const start = w.reset_at!=null ? w.reset_at : (pts.length?pts[0][0]:null);
    if(start==null) return;
    let hi = start;
    if(pts.length) hi = Math.max(hi, pts[pts.length-1][0]);
    if(pcs.length) hi = Math.max(hi, pcs[pcs.length-1][0]);
    if(hi < VIEW[0] || start > VIEW[1]) return;    // no part of this window is on screen
    // Reset boundary: the instant the replacement window was first reported.
    s += `<line x1="${X(start).toFixed(1)}" y1="${T}" x2="${X(start).toFixed(1)}" y2="${H-B}" `+
         `stroke="var(--dim)" stroke-dasharray="3 3" stroke-width="1"/>`;
    if(X(start) - lastLbl >= 46){
      lastLbl = X(start);
      s += `<text x="${(X(start)+3).toFixed(1)}" y="${T+10}" fill="var(--dim)" font-size="10">${esc(day(start))}</text>`;
    }
    if(pts.length){
      const line = [[X(start), y(0)]].concat(pts.map(p=>[X(p[0]), y(pick(p))]));
      mk.push({t:'area', pts:line, base:y(0), c:'--uncached', a:.16},
              {t:'line', pts:line, w:1.8, c:'--uncached'});
      const d = [`M ${X(start).toFixed(1)} ${y(0).toFixed(1)}`]
        .concat(pts.map(p=>`L ${X(p[0]).toFixed(1)} ${y(pick(p)).toFixed(1)}`));
      const last = pts[pts.length-1];
      s += `<path d="${d.join(' ')} L ${X(last[0]).toFixed(1)} ${y(0).toFixed(1)} Z" `+
           `fill="var(--uncached)" fill-opacity=".16" class="mk"/>`;
      s += `<path d="${d.join(' ')}" fill="none" stroke="var(--uncached)" stroke-width="1.8" class="mk">`+
           `<title>window opened ${esc(when(start))}\nreset quoted ${esc(w.resets_at_iso||'--')}\n`+
           `peak reported ${w.peak_pct==null?'--':w.peak_pct+'%'}\n`+
           `recorded input ${big(w.tokens.input)} over ${w.tokens.responses} responses\n`+
           `uncached ${big(w.tokens.uncached)} | output ${big(w.tokens.output)}`+
           (w.late_points ? `\n${w.late_points} later reading(s) not drawn: the next window `+
                            `had already opened` : '')+`</title></path>`;
    }
    if(pcs.length){
      const d = pcs.map((p,i)=>`${i?'L':'M'} ${X(p[0]).toFixed(1)} ${yp(p[1]).toFixed(1)}`);
      s += `<path d="${d.join(' ')}" fill="none" stroke="var(--warn)" stroke-width="1.4" stroke-dasharray="5 3" class="mk"/>`;
      mk.push({t:'line', pts:pcs.map(p=>[X(p[0]), yp(p[1])]), w:1.4, c:'--warn', dash:[5,3]});
    }
  });
  s += `</g>`;
  s += `<line x1="${L}" y1="${H-B}" x2="${W-RM}" y2="${H-B}" stroke="var(--line)"/>`;
  s += '</svg>';
  host.innerHTML = s;
  marks(host, ()=>host.querySelector('svg'), [W, H], [L, 0, PLOT, H], mk);
}

// ---- chart 2: daily recorded input ---------------------------------------------------
// The bars are rendered server-side in unit-x -- one unit is one local day -- so only the
// group transform changes here.  Nothing vertical is ever touched.
function drawDaily(tk){
  const host = byId('dailychart');
  if(!host) return;
  const svg = host.querySelector('svg');
  if(!svg) return;
  const H = +svg.getAttribute('data-h') || 210;
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  const clip = svg.querySelector('.clip');
  if(clip){ clip.setAttribute('x', L); clip.setAttribute('width', PLOT); }
  const mk = [];
  svg.querySelectorAll('g.bar').forEach(g=>{
    const a = +g.getAttribute('data-a'), b = +g.getAttribute('data-b');
    const x = X(a), sx = Math.max(X(b)-x, 0.001);
    g.setAttribute('transform', `translate(${x.toFixed(2)},0) scale(${sx.toFixed(5)},1)`);
    // The segments never change; they are read out of the markup once.
    if(!g.__mk) g.__mk = Array.from(g.querySelectorAll('rect.mk')).map(r=>
      [+r.getAttribute('y'), +r.getAttribute('height'), varOf(r.getAttribute('fill'))]);
    if(x + sx < L || x > W-RM) return;
    for(const [ry, rh, c] of g.__mk) mk.push({t:'rect', x:x+.04*sx, y:ry, w:.92*sx, h:rh, c});
  });
  const base = svg.querySelector('.base');
  if(base){ base.setAttribute('x1', L); base.setAttribute('x2', W-RM); }
  const peak = svg.querySelector('.peak');
  if(peak) peak.setAttribute('x', L);
  const ax = svg.querySelector('.ax');
  if(ax) ax.innerHTML = axis(H, +svg.getAttribute('data-t') || 18,
                                +svg.getAttribute('data-b') || 34, tk, false);
  marks(host, ()=>svg, [W, H], [L, 0, PLOT, H], mk);
}

// ---- chart 3: what filled the window -------------------------------------------------
// Tokenized content is deduplicated per rollout file, so the file is the finest unit its
// categories can honestly be placed on: a bucket carries the content of the files that
// opened inside it, and counts here when it overlaps the visible range.
const CATS = D.cats || {};
function drawPie(){
  const host = byId('catpie');
  if(!host) return;
  const series = CATS.series || [], bucket = CATS.bucket || 3600;
  const tot = {};
  let sum = 0;
  for(const row of series){
    const t = row[0], c = row[1];
    if(VIEW && (t+bucket <= VIEW[0] || t >= VIEW[1])) continue;
    for(const k in c){ tot[k] = (tot[k]||0) + c[k]; sum += c[k]; }
  }
  if(!sum){
    // An empty pie has two very different causes -- nothing tokenized at all, or nothing in
    // the range on screen -- and a reader cannot tell them apart from an empty panel.
    const msg = series.length ? 'No tokenized content in the visible range.'
                              : (CATS.note || 'No content in range.');
    host.innerHTML = emptyPie(msg);
    host.__shown = null;
    marks(host, null);
    return;
  }
  // Colour is the category's place in the corpus-wide order, so a slice keeps its colour as
  // the viewport moves and one pie can be read against the last.
  const order = CATS.order || Object.keys(tot);
  const rows = order.map((k,i)=>({k:k, v:tot[k]||0, fill:`var(--c${i%14})`}));
  pieTo(host, rows, sum, 'tokens in view', 'content composition by category');
}

// ---- chart 3b: which models took the input --------------------------------------------
// Recorded input by the charged model, from the same day buckets the daily bars draw, and in
// the same colours: a model past the daily chart's cap folds into `other` here as well.
const MODELS = D.models || {};
function drawModelPie(){
  const host = byId('modelpie');
  if(!host) return;
  const keys = MODELS.order || [], rank = {};
  keys.forEach((m,i)=>{ rank[m] = i; });
  const tot = {};
  let sum = 0;
  for(const row of (MODELS.days || [])){
    const a = row[0], b = row[1], c = row[2];
    if(VIEW && (b <= VIEW[0] || a >= VIEW[1])) continue;
    for(const m in c){
      const k = m in rank ? m : 'other';
      tot[k] = (tot[k]||0) + c[m]; sum += c[m];
    }
  }
  if(!sum){
    host.innerHTML = emptyPie('No recorded input in the visible range.');
    host.__shown = null;
    marks(host, null);
    return;
  }
  const rows = keys.concat(['other']).map(k=>({k:k, v:tot[k]||0,
    fill: k in rank ? `var(--c${rank[k]%14})` : 'var(--dim)'}));
  pieTo(host, rows, sum, 'recorded input in view', 'recorded input by model');
}

// ---- the pies follow the viewport, a beat behind ---------------------------------------
// A drag or a zoom redraws the time charts on every frame; recomposing the pies at that rate
// reads as flicker.  They wait until the viewport has been still for PIE_WAIT ms, then turn
// from the slices they show to the new ones.  A pie's rows are the same keys in the same
// order on every draw -- the corpus-wide order, zero-valued entries included -- so a slice
// can grow from nothing or shrink away rather than jump.
const REDUCE = (()=>{ try{ return matchMedia('(prefers-reduced-motion: reduce)').matches; }
                      catch(_){ return false; } })();
const PIE_WAIT = 180, PIE_MS = 520;
let pieTimer = 0, pieDrawn = false;

function schedulePies(){
  if(!pieDrawn){ pieDrawn = true; drawPie(); drawModelPie(); return; }   // first paint: now
  clearTimeout(pieTimer);
  pieTimer = setTimeout(()=>{ pieTimer = 0; drawPie(); drawModelPie(); }, PIE_WAIT);
}

/** Draw `rows` into `host`, turning from whatever fractions it shows now.  A newer call
 *  cancels an older tween mid-flight and starts from where that one had got to.  A frame
 *  without a timestamp (a stub DOM) lands on the end state at once. */
function pieTo(host, rows, sum, what, label){
  const to = rows.map(r=>r.v/sum);
  const from = host.__shown && host.__shown.length === to.length ? host.__shown : null;
  const tok = host.__tok = (host.__tok||0) + 1;
  if(!from || REDUCE){
    host.__shown = to;
    host.innerHTML = pie(rows, sum, what, label, to);
    pieMarks(host, rows, to);
    pieHover(host);
    return;
  }
  let t0 = null;
  const step = ts=>{
    if(host.__tok !== tok) return;                  // superseded by a newer range
    const k = typeof ts === 'number' ? Math.min(1, (ts - (t0 === null ? (t0 = ts) : t0))/PIE_MS) : 1;
    const e = 1 - Math.pow(1-k, 3);                 // ease out: fast start, soft landing
    host.__shown = from.map((f,i)=>f + (to[i]-f)*e);
    host.innerHTML = pie(rows, sum, what, label, host.__shown);
    pieMarks(host, rows, host.__shown);
    pieHover(host);
    if(k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/** Hovering a slice lights its legend line and dims the rest, in place of a tooltip: the
 *  legend already carries the name and the share.  The hovered index lives on the host, so
 *  it survives the pie being redrawn under the pointer while it turns to a new range. */
function pieHover(host){
  if(!host.addEventListener || !host.querySelectorAll                 // a stub DOM
     || !host.classList || typeof host.classList.toggle !== 'function') return;
  if(!host.__hov){
    host.__hov = true;
    const set = i=>{ if(host.__hi !== i){ host.__hi = i; pieHover(host); } };
    host.addEventListener('pointerover', e=>{
      const sl = e.target && e.target.closest && e.target.closest('.pie [data-i]');
      set(sl ? sl.getAttribute('data-i') : null);
    });
    host.addEventListener('pointerleave', ()=>set(null));
  }
  let lit = false;
  for(const el of host.querySelectorAll('.legend [data-i]')){
    const on = el.getAttribute('data-i') === host.__hi;
    el.classList.toggle('on', on);
    lit = lit || on;
  }
  host.classList.toggle('hi', lit);
}

/** The pie as marks: the same slices `pie` draws, from the same fractions. */
function pieMarks(host, rows, fr){
  const size = 240;
  marks(host, ()=>host.querySelector('.pie svg'), [size, size], null,
        [{t:'pie', cx:size/2, cy:size/2, r:size/2-4, sep:'--panel',
          slices:rows.map((r,i)=>[fr[i], varOf(r.fill)])}]);
}

/** An empty range keeps the pie's place -- a hollow ring and the reason -- so the panel does
 *  not collapse under the reader and spring back on the next range. */
function emptyPie(msg){
  const size = 240, r = size/2-4, c0 = size/2;
  return `<div class="row" style="gap:24px"><div class="pie"><svg viewBox="0 0 ${size} ${size}" `+
    `role="img" aria-label="${esc(msg)}"><circle cx="${c0}" cy="${c0}" r="${r-1}" fill="none" `+
    `stroke="var(--line)" stroke-width="2" stroke-dasharray="6 6"/></svg></div>`+
    `<p class="sub" style="max-width:220px">${esc(msg)}</p></div>`;
}

/** A pie and its legend.  `rows` are {k, v, fill} in draw order and `fr` the fraction of the
 *  circle each one takes -- its share, or a moment of a tween toward it.  The legend always
 *  reads the share; an entry that would read 0.0% keeps its slice but not its legend line. */
function pie(rows, sum, what, label, fr){
  const size = 240, r = size/2-4, c0 = size/2;
  let s = `<svg viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(label)}">`;
  let a = -Math.PI/2;                        // first slice starts at twelve o'clock
  for(let i=0; i<rows.length; i++){
    const row = rows[i], frac = fr ? fr[i] : row.v/sum;
    if(!(frac > 1e-6)) continue;
    if(frac >= 1-1e-12){
      // One entry holding everything: an arc whose ends coincide draws nothing.
      s += `<circle cx="${c0}" cy="${c0}" r="${r}" fill="${row.fill}" class="mk" data-i="${i}"/>`;
      break;
    }
    const b = a + frac*2*Math.PI;
    s += `<path d="M ${c0} ${c0} L ${(c0+r*Math.cos(a)).toFixed(2)} ${(c0+r*Math.sin(a)).toFixed(2)} `+
         `A ${r} ${r} 0 ${frac>0.5?1:0} 1 ${(c0+r*Math.cos(b)).toFixed(2)} ${(c0+r*Math.sin(b)).toFixed(2)} Z" `+
         `fill="${row.fill}" stroke="var(--panel)" stroke-width="1" class="mk" data-i="${i}"/>`;
    a = b;
  }
  s += '</svg>';
  const legend = rows.map((r,i)=>r.v/sum < 0.0005 ? '' :
    `<span data-i="${i}"><i style="background:${r.fill}"></i>${esc(r.k)} ${(100*r.v/sum).toFixed(1)}%</span>`).join('');
  return `<div class="row" style="gap:24px"><div class="pie">${s}</div>`+
    `<div class="legend" style="flex-direction:column;gap:6px">`+
    `<span><b>${big(sum)}</b>&nbsp;${what}</span>${legend}</div></div>`;
}

// ---- viewport ------------------------------------------------------------------------
let raf = 0;
function schedule(){
  if(raf) return;
  raf = requestAnimationFrame(()=>{ raf = 0; redraw(); });
}

function redraw(){
  const tk = VIEW ? ticks() : [];
  drawRL(tk);
  drawDaily(tk);
  schedulePies();
}

/** The view that puts `tAnchor` under `vxAnchor` at the given span, clamped to the domain. */
function spanned(span, tAnchor, vxAnchor){
  const full = DOM[1]-DOM[0];
  span = clamp(span, Math.min(MIN_SPAN, full), full);
  const v0 = clamp(tAnchor - (clamp(vxAnchor, L, W-RM)-L)/PLOT*span, DOM[0], DOM[1]-span);
  return [v0, v0+span];
}

/** Jump there at once: the hand is on the chart (a pinch), or a caller wants it now. */
function setSpan(span, tAnchor, vxAnchor){
  if(!VIEW) return;
  stopGlide();
  VIEW = spanned(span, tAnchor, vxAnchor);
  schedule();
}

// ---- zoom that glides -----------------------------------------------------------------
// A wheel notch or a double-click moves toward its view instead of jumping to it, easing
// out over about GLIDE_MS.  The move is a true zoom: the one moment that sits at the same x
// in the view it leaves and the view it reaches stays put the whole way, and the span
// changes geometrically, so every frame is the same factor closer.  Wheel turns that land
// mid-glide steer the goal, so a fast spin runs as one smooth zoom.  A drag or a pinch takes
// over at once; prefers-reduced-motion jumps.
const WHEEL_GAP = 250;                        // ms of wheel silence that ends a gesture
const GLIDE_MS = 90;                         // time constant: ~95% there in three of these
let GOAL = null, glideRaf = 0, glideT = 0;

function stopGlide(){ GOAL = null; }

function glideTo(v){
  if(!VIEW) return;
  if(REDUCE || typeof requestAnimationFrame !== 'function'){ VIEW = v; GOAL = null; schedule(); return; }
  GOAL = v;
  if(!glideRaf){ glideT = 0; glideRaf = requestAnimationFrame(glide); }
}

function glide(ts){
  glideRaf = 0;
  if(!GOAL) return;
  const dt = glideT && typeof ts === 'number' ? Math.min(64, ts - glideT) : 16;
  glideT = ts;
  const k = 1 - Math.exp(-dt/GLIDE_MS);
  const a0 = VIEW[0], s0 = VIEW[1]-VIEW[0], a1 = GOAL[0], s1 = GOAL[1]-GOAL[0];
  let a, s;
  if(Math.abs(s1 - s0) > s0*1e-6){
    const f = (a1*s0 - a0*s1)/(s0 - s1);      // the moment both views put at the same x
    s = s0*Math.pow(s1/s0, k);
    a = f - (f - a0)/s0*s;
  } else { s = s0; a = a0 + (a1 - a0)*k; }    // equal spans: a plain pan
  a = clamp(a, DOM[0], DOM[1]-s);
  VIEW = [a, a+s];
  if(Math.abs(s - s1) < s1*1e-3 && Math.abs(a - a1) < s1*5e-4){ VIEW = GOAL; GOAL = null; }
  redraw();
  if(GOAL) glideRaf = requestAnimationFrame(glide);
}

/** Zoom by `factor` about the moment under `vx`, gliding; turns compound on the goal. */
function zoomAt(factor, vx){
  if(!VIEW) return;
  const base = GOAL || VIEW;
  glideTo(spanned((base[1]-base[0])*factor, Tat(vx), vx));
}

function panPx(dvx){
  if(!VIEW) return;
  stopGlide();
  const span = VIEW[1]-VIEW[0];
  const v0 = clamp(VIEW[0] - dvx/PLOT*span, DOM[0], DOM[1]-span);
  VIEW = [v0, v0+span];
  schedule();
}

function reset(){
  if(!DOM) return;
  stopGlide();
  VIEW = [DOM[0], DOM[1]];
  schedule();
}

// One set of gestures, bound to each chart: wheel and trackpad on a desktop, drag and
// two-finger pinch on a touch screen, and a double-click back to the full range.
// Only the plot itself answers a mouse: inside the axes, from the left axis to the right one
// and from the top guide to the baseline.  The tick labels, the legend and the margins stay
// the page's, so a wheel turned over them scrolls it.  (Touch is not limited: pan-y already
// leaves a vertical swipe to the page anywhere on the chart.)
function inPlot(el, e){
  const svg = el.querySelector && el.querySelector('svg');
  if(!svg || !svg.getBoundingClientRect) return false;
  const r = svg.getBoundingClientRect();
  const H = +svg.getAttribute('data-h'), T = +svg.getAttribute('data-t'), B = +svg.getAttribute('data-b');
  if(!r.width || !r.height || !H) return false;
  const x = (e.clientX - r.left)/r.width*W, y = (e.clientY - r.top)/r.height*H;
  return x >= L && x <= W-RM && y >= T && y <= H-B;
}

// A wheel gesture -- events no more than WHEEL_GAP ms apart -- belongs wholly to the chart or
// wholly to the page, whichever it started on.  Kept for the whole page, not per chart: a
// page scroll carries a chart up under a still pointer, and that must go on scrolling.
const WHEEL = {at: -1e9, mode: null};

function bind(el){
  const pts = new Map();                     // live pointers, in viewBox x
  let pinch = null;
  const scale = () => W/(el.getBoundingClientRect().width || W);
  const vxOf = e => {
    const r = el.getBoundingClientRect();
    return (e.clientX - r.left)/(r.width || 1)*W;
  };

  el.addEventListener('wheel', e=>{
    if(!VIEW) return;
    const t = e.timeStamp || Date.now();
    if(t - WHEEL.at > WHEEL_GAP) WHEEL.mode = null;
    if(WHEEL.mode === 'page' || (WHEEL.mode !== 'zoom' && !inPlot(el, e))) return;
    if(Math.abs(e.deltaX) > Math.abs(e.deltaY)){          // trackpad swipe: pan
      WHEEL.mode = 'zoom';
      e.preventDefault();
      panPx(-e.deltaX*scale());
      return;
    }
    if(!e.deltaY) return;
    // Zoomed all the way out, scrolling down scrolls the page -- but a gesture that zoomed
    // out to the full range spends its momentum here, and the *next* one scrolls.
    const base = GOAL || VIEW;
    if(e.deltaY > 0 && base[1]-base[0] >= (DOM[1]-DOM[0])*(1-1e-9)){
      if(WHEEL.mode === 'zoom') e.preventDefault();
      return;
    }
    WHEEL.mode = 'zoom';
    e.preventDefault();
    const unit = e.deltaMode===1 ? 0.05 : (e.deltaMode===2 ? 0.8 : 0.002);
    const vx = clamp(vxOf(e), L, W-RM);
    zoomAt(1/Math.exp(-e.deltaY*unit), vx);
  }, {passive:false});

  el.addEventListener('pointerdown', e=>{
    if(!VIEW || (e.pointerType==='mouse' && e.button!==0)) return;
    if(e.pointerType !== 'touch' && !pts.size && !inPlot(el, e)) return;
    try{ el.setPointerCapture(e.pointerId); }catch(_){}
    pts.set(e.pointerId, vxOf(e));
    stopGlide();
    el.classList.add('drag');
    if(pts.size===2){
      const ids = Array.from(pts.keys());
      pinch = {ia:ids[0], ib:ids[1], ta:Tat(pts.get(ids[0])), tb:Tat(pts.get(ids[1]))};
    }
  });

  el.addEventListener('pointermove', e=>{
    if(!pts.has(e.pointerId)){ el.classList.toggle('inplot', inPlot(el, e)); return; }
    const prev = pts.get(e.pointerId), vx = vxOf(e);
    pts.set(e.pointerId, vx);
    e.preventDefault();
    if(pinch && pts.size>=2){
      const xa = pts.get(pinch.ia), xb = pts.get(pinch.ib);
      if(xa==null || xb==null) return;
      const dx = xb-xa, dt = pinch.tb-pinch.ta;
      // Ignore a pinch that has collapsed or crossed over: the span it implies is
      // meaningless, and a sign flip would turn the axis inside out.
      if(Math.abs(dx) < 4 || dx*dt <= 0) return;
      setSpan(dt*PLOT/dx, pinch.ta, xa);
      return;
    }
    panPx(vx - prev);
  });

  const lift = e=>{
    if(!pts.delete(e.pointerId)) return;
    if(pts.size < 2) pinch = null;
    if(!pts.size) el.classList.remove('drag');
  };
  el.addEventListener('pointerup', lift);
  el.addEventListener('pointercancel', lift);
  el.addEventListener('lostpointercapture', lift);
  el.addEventListener('pointerleave', ()=>el.classList.remove('inplot'));
  el.addEventListener('dblclick', e=>{
    if(!inPlot(el, e)) return;
    e.preventDefault();
    if(DOM) glideTo([DOM[0], DOM[1]]);
  });
}

function init(){
  if(!VIEW){
    drawPie();
    drawModelPie();
    return;
  }
  measure();
  ['rlchart','dailychart'].forEach(id=>{ const el = byId(id); if(el) bind(el); });
  addEventListener('wheel', e=>{                 // after the charts: whoever this one went to
    WHEEL.at = e.timeStamp || Date.now();
    if(!e.defaultPrevented) WHEEL.mode = 'page';
  }, {passive:true});
  let rt = 0;
  addEventListener('resize', ()=>{
    clearTimeout(rt);
    rt = setTimeout(()=>{ measure(); redraw(); }, 120);
  });
  redraw();
}
init();
"""

# The WebGL layer.  Under a style in GL_STYLES the chart marks -- the limit chart's area and
# curves, the daily bars, the pie slices -- are painted by WebGL2 instead of SVG: one canvas
# per panel, behind the panel's own content, drawing the shapes every chart records in SCN.
# The SVG stays: it carries the axes, the text and the tooltips, and only its marks go
# transparent.  No WebGL2, a context the browser takes back, or any other style, and the SVG
# marks are simply left visible -- the page reads the same with or without this layer.
#
# A frame is drawn multisampled into an offscreen buffer, and reaches the screen through one
# post pass, which draws it through the water (WAVE and SIM in GL_JS).
GL_STYLES = ['clinical']

GL_JS = r"""
// ---- the WebGL layer ------------------------------------------------------------------
// See GL_STYLES in render.py.  Nothing here runs without a real DOM and WebGL2, so the
// stub DOM in scripts/test_page.js never reaches it.
const GL_STYLES = D.gl_styles || [];
// The water: a height field simulated on a coarse grid over the viewport (see SIM below).
//   cell      grid spacing, CSS px -- larger is broader, smoother ripples
//   brush     radius of the disturbance a pointer drags through the water, CSS px
//   substeps  simulation steps per 1/60 s -- how fast a ripple travels
//   damp      energy kept per step -- how long a ripple lasts
//   visc      how much each step evens out velocity with its neighbours -- smooths chop
//   push      height a pointer adds per CSS px it travels (heights are clamped to +-1)
//   v0        pointer speed, px/ms, below which the water is left alone (reading, hovering)
//   slope     how steeply the surface tilts per unit of height difference
//   refract   CSS px the panel is displaced under a fully tilted surface
//   disp      chromatic split, as a fraction of the displacement
//   light     crest and trough lighting; 0 leaves only the refraction
const WAVE = {cell: 6, brush: 24, substeps: 2, damp: .955, visc: .0155, push: 1/45, v0: .3,
              slope: 22, refract: 36, disp: .25, light: 0};
const GLX = (()=>{
  if(typeof document === 'undefined' || !document.createElement || !document.querySelectorAll
     || typeof WebGL2RenderingContext === 'undefined') return null;
  const RT = document.documentElement;
  const layers = new Map();                 // panel -> its canvas and GL state
  let ok = true, on = false, pal = null, queued = false, loop = 0;

  // -- colour: the style's own variables, read once per style and theme ---------------
  function parse(v){
    let m = /^#([0-9a-f]{3,8})$/i.exec(v);
    if(m){
      let h = m[1];
      if(h.length < 5) h = h.split('').map(c=>c+c).join('');
      const n = i => parseInt(h.slice(i, i+2), 16)/255;
      return [n(0), n(2), n(4), h.length >= 8 ? n(6) : 1];
    }
    m = /rgba?\(([^)]+)\)/.exec(v);
    if(m){
      const p = m[1].split(/[\s,\/]+/).filter(Boolean).map(parseFloat);
      return [p[0]/255, p[1]/255, p[2]/255, p.length > 3 ? p[3] : 1];
    }
    return [.5, .5, .5, 1];
  }
  function rgba(name, a){                   // premultiplied, as the blend expects
    pal = pal || {};
    if(!(name in pal)) pal[name] = parse(getComputedStyle(RT).getPropertyValue(name).trim());
    const c = pal[name], al = c[3]*(a == null ? 1 : a);
    return [c[0]*al, c[1]*al, c[2]*al, al];
  }

  // -- geometry: triangles in the SVG's own units, six floats a vertex -----------------
  function tri(V, ax, ay, bx, by, cx, cy, c){
    V.push(ax, ay, c[0], c[1], c[2], c[3], bx, by, c[0], c[1], c[2], c[3],
           cx, cy, c[0], c[1], c[2], c[3]);
  }
  function line(V, pts, hw, c){             // segments as quads, bevelled where they meet
    let pn = null;
    for(let i = 1; i < pts.length; i++){
      const x0 = pts[i-1][0], y0 = pts[i-1][1], x1 = pts[i][0], y1 = pts[i][1];
      const l = Math.hypot(x1-x0, y1-y0);
      if(l < 1e-6) continue;
      const nx = -(y1-y0)/l*hw, ny = (x1-x0)/l*hw;
      tri(V, x0+nx, y0+ny, x1+nx, y1+ny, x1-nx, y1-ny, c);
      tri(V, x0+nx, y0+ny, x1-nx, y1-ny, x0-nx, y0-ny, c);
      if(pn){
        tri(V, x0, y0, x0+pn[0], y0+pn[1], x0+nx, y0+ny, c);
        tri(V, x0, y0, x0-pn[0], y0-pn[1], x0-nx, y0-ny, c);
      }
      pn = [nx, ny];
    }
  }
  function dashes(pts, pat){                // a polyline cut into its dashes, as SVG does
    const out = [];
    let k = 0, left = pat[0], cur = [pts[0]];
    for(let i = 1; i < pts.length; i++){
      let x0 = pts[i-1][0], y0 = pts[i-1][1];
      const x1 = pts[i][0], y1 = pts[i][1];
      let seg = Math.hypot(x1-x0, y1-y0);
      while(seg > left){
        const f = left/seg;
        x0 += (x1-x0)*f; y0 += (y1-y0)*f; seg -= left;
        if(k%2 === 0){ cur.push([x0, y0]); out.push(cur); }
        k++; left = pat[k%pat.length]; cur = [[x0, y0]];
      }
      left -= seg;
      if(k%2 === 0) cur.push([x1, y1]);
    }
    if(k%2 === 0 && cur.length > 1) out.push(cur);
    return out;
  }
  function geo(V, m){
    if(m.t === 'rect'){
      const c = rgba(m.c);
      tri(V, m.x, m.y, m.x+m.w, m.y, m.x+m.w, m.y+m.h, c);
      tri(V, m.x, m.y, m.x+m.w, m.y+m.h, m.x, m.y+m.h, c);
    } else if(m.t === 'area'){
      const c = rgba(m.c, m.a), p = m.pts;
      for(let i = 1; i < p.length; i++){
        tri(V, p[i-1][0], m.base, p[i-1][0], p[i-1][1], p[i][0], p[i][1], c);
        tri(V, p[i-1][0], m.base, p[i][0], p[i][1], p[i][0], m.base, c);
      }
    } else if(m.t === 'line'){
      const c = rgba(m.c, m.a);
      if(m.pts.length < 2) return;
      for(const run of (m.dash ? dashes(m.pts, m.dash) : [m.pts])) line(V, run, m.w/2, c);
    } else if(m.t === 'pie'){
      let a = -Math.PI/2;
      const seps = [];
      for(const [f, cv] of m.slices){
        if(!(f > 1e-6)) continue;
        const c = rgba(cv), b = a + f*2*Math.PI, n = Math.max(2, Math.ceil(f*160));
        for(let j = 0; j < n; j++){
          const u = a + (b-a)*j/n, w = a + (b-a)*(j+1)/n;
          tri(V, m.cx, m.cy, m.cx+m.r*Math.cos(u), m.cy+m.r*Math.sin(u),
                 m.cx+m.r*Math.cos(w), m.cy+m.r*Math.sin(w), c);
        }
        seps.push(a);
        a = b;
      }
      if(seps.length > 1){                  // the hairline the SVG strokes between slices
        const c = rgba(m.sep);
        for(const s of seps)
          line(V, [[m.cx, m.cy], [m.cx+m.r*Math.cos(s), m.cy+m.r*Math.sin(s)]], .5, c);
      }
    }
  }

  // -- programs --------------------------------------------------------------------------
  const MARK_VS = `#version 300 es
in vec2 p; in vec4 c;
uniform vec2 u_css, u_off; uniform float u_s;
out vec4 v_c;
void main(){
  vec2 q = (u_off + p*u_s)/u_css*2. - 1.;
  v_c = c; gl_Position = vec4(q.x, -q.y, 0., 1.);
}`;
  const MARK_FS = `#version 300 es
precision mediump float;
in vec4 v_c; out vec4 o;
void main(){ o = v_c; }`;
  const POST_VS = `#version 300 es
out vec2 v_uv;
void main(){
  vec2 p = vec2(float((gl_VertexID<<1)&2), float(gl_VertexID&2));
  v_uv = p; gl_Position = vec4(p*2. - 1., 0., 1.);
}`;
  // Water: the panel is seen through one simulated surface (SIM below).  Its height field
  // arrives as a texture over the viewport; the post pass reads the surface's slope where
  // this pixel is, and samples the panel that far away -- a refraction -- splitting red and
  // blue a little either side, so a moving edge fringes as it would through a lens.
  const f1 = x => (+x).toFixed(4);
  const POST_FS = `#version 300 es
precision highp float;
uniform sampler2D u_scene, u_wave; uniform vec2 u_res, u_wo, u_wn; uniform float u_dpr;
in vec2 v_uv; out vec4 o_fx;
vec4 scene(vec2 uv){ return texture(u_scene, uv); }
vec3 w_n;
vec2 water(vec2 uv){
  vec2 cl = u_wo + vec2(uv.x, 1. - uv.y)*u_res/u_dpr;       // this pixel, client CSS px
  vec2 g = (cl/${f1(WAVE.cell)} + .5)/u_wn, e = 1.5/u_wn;    // the grid: row 0 at the top
  float l = texture(u_wave, g - vec2(e.x, 0.)).r, r = texture(u_wave, g + vec2(e.x, 0.)).r;
  float t = texture(u_wave, g - vec2(0., e.y)).r, b = texture(u_wave, g + vec2(0., e.y)).r;
  w_n = normalize(vec3(vec2(r - l, t - b)/3.*${f1(WAVE.slope)}, 1.));
  return w_n.xy*${f1(WAVE.refract)}*u_dpr/u_res;
}
void main(){
  vec2 off = water(v_uv), uv = v_uv + off;
  vec4 c = scene(uv);
  if(${f1(WAVE.disp)} > 0. && dot(off, off) > 1e-10){
    vec2 d = off*${f1(WAVE.disp)};
    vec4 cr = scene(uv - d), cb = scene(uv + d);
    c = vec4(cr.r, c.g, cb.b, max(c.a, max(cr.a, cb.a)));   // still premultiplied
  }
  if(${f1(WAVE.light)} > 0.){
    vec3 L = normalize(vec3(.55, .65, 1.));
    float sp = pow(max(dot(w_n, normalize(L + vec3(0., 0., 1.))), 0.), 32.);
    float k = clamp((dot(w_n, L) - dot(vec3(0., 0., 1.), L))*.35 + sp*1.5, -1., 1.)*${f1(WAVE.light)}*.5;
    c = k > 0. ? c*(1. - k) + vec4(k) : c*(1. + k) + vec4(0., 0., 0., -k);
  }
  o_fx = c;
}`;

  function program(gl, vs, fs){
    const sh = (type, src)=>{
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if(!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, sh(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if(!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {};
    const n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for(let i = 0; i < n; i++){
      const nm = gl.getActiveUniform(p, i).name;
      u[nm] = gl.getUniformLocation(p, nm);
    }
    return {p, u};
  }

  /** The post pass in this layer, compiled on first use; null if it does not compile. */
  function postProg(Ly){
    if(Ly.pp === undefined){
      try{ Ly.pp = program(Ly.gl, POST_VS, POST_FS); }
      catch(e){ console.warn('token-counter: WebGL post pass did not compile\n'+e.message); Ly.pp = null; }
    }
    return Ly.pp;
  }

  function layer(panel){
    if(layers.has(panel)) return layers.get(panel);
    const cv = document.createElement('canvas');
    cv.className = 'glc';
    cv.setAttribute('aria-hidden', 'true');
    panel.insertBefore(cv, panel.firstChild);
    const gl = cv.getContext('webgl2', {alpha:true, premultipliedAlpha:true, antialias:false,
                                        depth:false, stencil:false});
    if(!gl){ cv.remove(); return null; }
    cv.addEventListener('webglcontextlost', e=>{ e.preventDefault(); ok = false; sync(); });
    let mark;
    try{ mark = program(gl, MARK_VS, MARK_FS); }
    catch(e){ console.warn('token-counter: WebGL marks did not compile\n'+e.message); cv.remove(); return null; }
    const Ly = {cv, gl, mark, pp: undefined, w:0, h:0,
                buf: gl.createBuffer(), vao: gl.createVertexArray(), post: gl.createVertexArray(),
                ms: gl.createFramebuffer(), rb: gl.createRenderbuffer(),
                res: gl.createFramebuffer(), tex: gl.createTexture(),
                samples: Math.min(4, gl.getParameter(gl.MAX_SAMPLES) || 0)};
    gl.bindVertexArray(Ly.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, Ly.buf);
    const pa = gl.getAttribLocation(mark.p, 'p'), ca = gl.getAttribLocation(mark.p, 'c');
    gl.enableVertexAttribArray(pa); gl.vertexAttribPointer(pa, 2, gl.FLOAT, false, 24, 0);
    gl.enableVertexAttribArray(ca); gl.vertexAttribPointer(ca, 4, gl.FLOAT, false, 24, 8);
    gl.bindVertexArray(null);
    layers.set(panel, Ly);
    return Ly;
  }

  function resize(Ly, w, h){
    const gl = Ly.gl;
    Ly.cv.width = Ly.w = w; Ly.cv.height = Ly.h = h;
    gl.bindRenderbuffer(gl.RENDERBUFFER, Ly.rb);
    gl.renderbufferStorageMultisample(gl.RENDERBUFFER, Ly.samples, gl.RGBA8, w, h);
    gl.bindFramebuffer(gl.FRAMEBUFFER, Ly.ms);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, Ly.rb);
    gl.bindTexture(gl.TEXTURE_2D, Ly.tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER, Ly.res);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, Ly.tex, 0);
  }

  // -- the water ----------------------------------------------------------------------------
  // A height field on a grid over the viewport, WAVE.cell CSS px a cell: each step, a cell's
  // velocity is pulled toward the mean height of its four neighbours, evened out a little with
  // theirs, and damped; the height follows the velocity.  A fast pointer drags a soft brush
  // through it.  One field serves every canvas, so a ripple crosses the tiles and the charts
  // as one surface; it is kept to the page as it scrolls, a whole cell at a time.  Steps run
  // only while there is motion in it, and stop when it has settled back to flat.
  const SIM = {w: 0, h: 0, H: null, V: null, H2: null, V2: null, live: false, ver: 0,
               sy: 0, acc: 0, t: 0};
  function simFit(){
    const w = Math.ceil(innerWidth/WAVE.cell) + 1, h = Math.ceil(innerHeight/WAVE.cell) + 1;
    if(w === SIM.w && h === SIM.h) return;
    SIM.w = w; SIM.h = h;
    for(const k of ['H', 'V', 'H2', 'V2']) SIM[k] = new Float32Array(w*h);
    SIM.live = false; SIM.ver++;
  }
  function simStep(){
    const {w, h, H, V, H2, V2} = SIM, keep = WAVE.damp, visc = WAVE.visc;
    let e = 0;
    for(let y = 0; y < h; y++){
      const up = (y > 0 ? y-1 : y)*w, dn = (y < h-1 ? y+1 : y)*w, row = y*w;
      for(let x = 0; x < w; x++){
        const i = row + x, lf = x > 0 ? i-1 : i, rt = x < w-1 ? i+1 : i;
        const mh = (H[lf] + H[rt] + H[up+x] + H[dn+x])*.25;
        const mv = (V[lf] + V[rt] + V[up+x] + V[dn+x])*.25;
        let v = V[i] + mh - H[i];
        v = (v + (mv - v)*visc)*keep;
        const hh = Math.max(-1, Math.min(1, (H[i] + v)*keep));
        V2[i] = v; H2[i] = hh;
        e = Math.max(e, Math.abs(hh) + Math.abs(v));
      }
    }
    SIM.H = H2; SIM.H2 = H; SIM.V = V2; SIM.V2 = V;
    return e;
  }
  /** Advance to the present, a fixed step at a time; false once the water is flat again. */
  function simRun(){
    const t = performance.now(), dt = Math.min(64, t - (SIM.t || t));
    SIM.t = t;
    let n = Math.round(dt/1000*60*WAVE.substeps) || 1, e = 1;
    while(n-- > 0) e = simStep();
    SIM.ver++;
    if(e < 2e-3){ SIM.H.fill(0); SIM.V.fill(0); SIM.live = false; }
    return SIM.live;
  }
  /** Push the surface down along a pointer's path from (x0, y0) to (x1, y1), client px. */
  function simStir(x0, y0, x1, y1, amt){
    simFit();
    const {w, h, H} = SIM, c = WAVE.cell, R = WAVE.brush;
    const dx = x1-x0, dy = y1-y0, L2 = dx*dx + dy*dy;
    const gx0 = Math.max(0, Math.floor((Math.min(x0, x1) - R)/c)), gx1 = Math.min(w-1, Math.ceil((Math.max(x0, x1) + R)/c));
    const gy0 = Math.max(0, Math.floor((Math.min(y0, y1) - R)/c)), gy1 = Math.min(h-1, Math.ceil((Math.max(y0, y1) + R)/c));
    for(let gy = gy0; gy <= gy1; gy++) for(let gx = gx0; gx <= gx1; gx++){
      const px = gx*c, py = gy*c;
      const f = L2 > 1e-6 ? Math.max(0, Math.min(1, ((px-x0)*dx + (py-y0)*dy)/L2)) : 0;
      const d = Math.hypot(px - (x0 + dx*f), py - (y0 + dy*f));
      if(d < R){
        const i = gy*w + gx;
        H[i] = Math.max(-1, Math.min(1, H[i] + Math.cos(d/R*Math.PI/2)*amt));
      }
    }
    if(!SIM.live){ SIM.live = true; SIM.t = 0; SIM.sy = scrollY; SIM.acc = 0; }
    SIM.ver++;
  }
  addEventListener('scroll', ()=>{                          // the water stays with the page
    const d = scrollY - SIM.sy;
    SIM.sy = scrollY;
    if(!SIM.live) return;
    SIM.acc += d;
    const n = Math.trunc(SIM.acc/WAVE.cell);
    if(!n) return;
    SIM.acc -= n*WAVE.cell;
    const {w, h} = SIM;
    for(const A of [SIM.H, SIM.V]){
      if(Math.abs(n) >= h){ A.fill(0); continue; }
      if(n > 0){ A.copyWithin(0, n*w); A.fill(0, (h-n)*w); }
      else { A.copyWithin(-n*w, 0, (h+n)*w); A.fill(0, 0, -n*w); }
    }
    SIM.ver++;
  }, {passive: true});

  /** The surface, handed to one canvas whose top-left is at client (bx, by). */
  function water(P, gl, T, bx, by){
    if(!P.u.u_wave) return;
    simFit();
    gl.activeTexture(gl.TEXTURE1);
    if(!T.wtex){
      T.wtex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, T.wtex);
      for(const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.LINEAR], [gl.TEXTURE_MAG_FILTER, gl.LINEAR],
                           [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]])
        gl.texParameteri(gl.TEXTURE_2D, k, v);
    } else gl.bindTexture(gl.TEXTURE_2D, T.wtex);
    if(T.wver !== SIM.ver){
      T.wver = SIM.ver;
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R16F, SIM.w, SIM.h, 0, gl.RED, gl.FLOAT, SIM.H);
    }
    gl.uniform1i(P.u.u_wave, 1);
    gl.uniform2f(P.u.u_wo, bx, by);
    gl.uniform2f(P.u.u_wn, SIM.w, SIM.h);
    gl.activeTexture(gl.TEXTURE0);
  }

  function paint(Ly, panel, groups){
    const gl = Ly.gl, dpr = Math.min(window.devicePixelRatio || 1, 2);
    const cw = panel.clientWidth, ch = panel.clientHeight;
    const pw = Math.max(1, Math.round(cw*dpr)), ph = Math.max(1, Math.round(ch*dpr));
    if(pw !== Ly.w || ph !== Ly.h) resize(Ly, pw, ph);
    const pr = panel.getBoundingClientRect();
    const bx = pr.left + panel.clientLeft, by = pr.top + panel.clientTop;

    // 1. the marks, multisampled
    gl.bindFramebuffer(gl.FRAMEBUFFER, Ly.ms);
    gl.viewport(0, 0, pw, ph);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.useProgram(Ly.mark.p);
    gl.bindVertexArray(Ly.vao);
    gl.bindBuffer(gl.ARRAY_BUFFER, Ly.buf);
    gl.uniform2f(Ly.mark.u.u_css, cw, ch);
    for(const g of groups){
      const svg = g.svg();
      if(!svg) continue;
      const r = svg.getBoundingClientRect();
      if(!r.width) continue;
      const V = [];
      for(const m of g.list) geo(V, m);
      if(!V.length) continue;
      const s = r.width/g.vb[0], ox = r.left - bx, oy = r.top - by;
      if(g.clip){
        const [x, y, w, h] = g.clip;
        gl.enable(gl.SCISSOR_TEST);
        gl.scissor(Math.round((ox + x*s)*dpr), Math.round(ph - (oy + (y+h)*s)*dpr),
                   Math.round(w*s*dpr), Math.round(h*s*dpr));
      } else gl.disable(gl.SCISSOR_TEST);
      gl.uniform2f(Ly.mark.u.u_off, ox, oy);
      gl.uniform1f(Ly.mark.u.u_s, s);
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(V), gl.STREAM_DRAW);
      gl.drawArrays(gl.TRIANGLES, 0, V.length/6);
    }
    gl.disable(gl.SCISSOR_TEST);

    // 2. resolved into a texture the post pass can sample
    gl.bindFramebuffer(gl.READ_FRAMEBUFFER, Ly.ms);
    gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, Ly.res);
    gl.blitFramebuffer(0, 0, pw, ph, 0, 0, pw, ph, gl.COLOR_BUFFER_BIT, gl.NEAREST);

    // 3. through the water, onto the canvas
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.disable(gl.BLEND);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const P = postProg(Ly);
    if(!P){ ok = false; sync(); return; }
    gl.useProgram(P.p);
    gl.bindVertexArray(Ly.post);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, Ly.tex);
    if(P.u.u_scene) gl.uniform1i(P.u.u_scene, 0);
    if(P.u.u_res) gl.uniform2f(P.u.u_res, pw, ph);
    if(P.u.u_dpr) gl.uniform1f(P.u.u_dpr, dpr);
    water(P, gl, Ly, bx, by);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  const animated = () => on && !REDUCE && SIM.live;
  function tickFrame(){
    loop = 0;
    if(SIM.live && on && !REDUCE) simRun();
    frame(true);
  }

  // -- the headline numbers: their text drawn into a texture, so ripples cross them too ---
  // Each glyph is placed where the browser laid it out (a Range per character), in the
  // element's own computed font, colour, letter-spacing and case; so wrapping, fallback
  // fonts and the theme all come out as the page drew them.  Redrawn only when the text,
  // the size or the style changes; between those, a ripple frame just re-samples it.
  const TX = {cv: null, gl: null, P: null, tex: null, vao: null, pad: null, stamp: ''};
  function textLayer(host){
    if(TX.cv) return TX.gl ? TX : null;
    TX.cv = document.createElement('canvas');
    TX.cv.className = 'glt';
    TX.cv.setAttribute('aria-hidden', 'true');
    host.insertBefore(TX.cv, host.firstChild);
    const gl = TX.cv.getContext('webgl2', {alpha:true, premultipliedAlpha:true, antialias:false,
                                           depth:false, stencil:false});
    try{ TX.P = gl && program(gl, POST_VS, POST_FS); }
    catch(e){ console.warn('token-counter: headline layer did not compile\n'+e.message); TX.P = null; }
    if(!gl || !TX.P){ TX.cv.remove(); return null; }
    TX.gl = gl; TX.tex = gl.createTexture(); TX.vao = gl.createVertexArray();
    TX.pad = document.createElement('canvas');
    return TX;
  }
  function paintText(){
    const host = document.querySelector('.tiles');
    if(!host) return;
    const L = textLayer(host);
    if(!L){ host.classList.remove('gltxt'); return; }
    const gl = L.gl, dpr = Math.min(window.devicePixelRatio || 1, 2);
    const hr = host.getBoundingClientRect();
    const pw = Math.max(1, Math.round(hr.width*dpr)), ph = Math.max(1, Math.round(hr.height*dpr));
    const els = host.querySelectorAll('.tile>*');
    const stamp = [pw, ph, RT.getAttribute('data-style'), matchMedia('(prefers-color-scheme: dark)').matches]
      .concat(Array.from(els, e=>e.textContent)).join('|');
    if(stamp !== L.stamp){
      L.stamp = stamp;
      const pad = L.pad;
      pad.width = pw; pad.height = ph;
      const x = pad.getContext('2d');
      x.scale(dpr, dpr);
      const rg = document.createRange();
      for(const el of els){
        const cs = getComputedStyle(el);
        x.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
        x.fillStyle = cs.color;
        x.textBaseline = 'alphabetic';
        const asc = x.measureText('Hg').fontBoundingBoxAscent || parseFloat(cs.fontSize)*.8;
        const up = cs.textTransform === 'uppercase';
        const walk = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for(let n = walk.nextNode(); n; n = walk.nextNode()){
          const t = n.nodeValue;
          for(let i = 0; i < t.length; i++){
            if(t[i] === ' ' || t[i] === '\n') continue;
            rg.setStart(n, i); rg.setEnd(n, i+1);
            const r = rg.getBoundingClientRect();
            if(!r.width) continue;
            x.fillText(up ? t[i].toUpperCase() : t[i], r.left - hr.left, r.top - hr.top + asc);
          }
        }
      }
      L.cv.width = pw; L.cv.height = ph;
      gl.bindTexture(gl.TEXTURE_2D, L.tex);
      gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, pad);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      host.classList.add('gltxt');
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, pw, ph);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(L.P.p);
    gl.bindVertexArray(L.vao);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_2D, L.tex);
    if(L.P.u.u_scene) gl.uniform1i(L.P.u.u_scene, 0);
    if(L.P.u.u_res) gl.uniform2f(L.P.u.u_res, pw, ph);
    if(L.P.u.u_dpr) gl.uniform1f(L.P.u.u_dpr, dpr);
    water(L.P, gl, L, hr.left, hr.top);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  }

  // A moving pointer stirs the water along its path, harder the faster it went; slower than
  // WAVE.v0 (reading, hovering a tooltip) it leaves the surface alone.  Not a touch, and not
  // a drag of the charts.
  let last = null;
  addEventListener('pointermove', e=>{
    if(!on || REDUCE || e.pointerType === 'touch' || e.buttons){ last = null; return; }
    const t = e.timeStamp || performance.now();
    if(last){
      const dt = Math.max(1, t - last.t), dist = Math.hypot(e.clientX - last.x, e.clientY - last.y);
      last.v = last.v*.6 + dist/dt*.4;
      if(last.v > WAVE.v0 && dist > 0){
        simStir(last.x, last.y, e.clientX, e.clientY,
                Math.min(.6, dist*WAVE.push*Math.min(1, (last.v - WAVE.v0)/WAVE.v0)));
        if(!loop) loop = requestAnimationFrame(tickFrame);
      }
      last.x = e.clientX; last.y = e.clientY; last.t = t;
    } else last = {x: e.clientX, y: e.clientY, t, v: 0};
  }, {passive: true});

  function frame(tick){
    queued = false;
    if(!on) return;
    const byPanel = new Map();
    for(const p of layers.keys()) byPanel.set(p, []);    // a panel left empty is cleared
    for(const [host, g] of SCN){
      const panel = g.svg && host.closest && host.closest('.panel');
      if(!panel) continue;
      if(!byPanel.has(panel)) byPanel.set(panel, []);
      byPanel.get(panel).push(g);
    }
    for(const [panel, groups] of byPanel){
      if(tick){                                           // a clock tick skips what is off screen
        const r = panel.getBoundingClientRect();
        if(r.bottom < 0 || r.top > innerHeight) continue;
      }
      const Ly = layer(panel);
      if(!Ly){ ok = false; sync(); return; }
      paint(Ly, panel, groups);
    }
    const th = document.querySelector('.tiles');
    if(!tick || !th || th.getBoundingClientRect().bottom > 0) paintText();
    if(animated() && !loop) loop = requestAnimationFrame(tickFrame);
  }

  // Drawn in the same task as the SVG it replaces -- a microtask, not a frame later -- so
  // the axes and the marks never part company during a drag.
  function request(){
    if(queued || !on) return;
    queued = true;
    (typeof queueMicrotask === 'function' ? queueMicrotask : f=>Promise.resolve().then(f))(()=>frame(false));
  }

  function sync(){
    on = ok && GL_STYLES.indexOf(RT.getAttribute('data-style')) >= 0;
    if(on) RT.setAttribute('data-gl', ''); else RT.removeAttribute('data-gl');
    pal = null;                                           // a new style is a new palette
    request();
  }

  try{ matchMedia('(prefers-color-scheme: dark)').addEventListener('change', ()=>{ pal = null; request(); }); }
  catch(_){}
  addEventListener('resize', ()=>{ simFit(); request(); });
  GLH.dirty = request;
  return {sync, request};
})();
"""


# The style switcher.  Kept apart from JS so the charts' script reads as it did; it runs after
# init(), touches the charts only through measure() and redraw(), and does nothing at all
# where there is no real DOM (scripts/test_page.js).
STYLE_JS = r"""
// ---- styles: one page, several readings -----------------------------------------------
const STYLES = D.styles || [['clinical','Clinical']];
const ROOT = document.documentElement;

let SI = 0;
function applyStyle(i){
  SI = (i % STYLES.length + STYLES.length) % STYLES.length;
  const id = STYLES[SI][0];
  ROOT.setAttribute('data-style', id);
  const btn = byId('stylebtn'), next = STYLES[(SI+1)%STYLES.length];
  if(btn){
    btn.setAttribute('data-next', next[0]);                   // the dot is drawn in `next`
    btn.setAttribute('aria-label', 'Switch to the ' + next[1] + ' style');
  }
  try{ localStorage.setItem('tc-style', id); }catch(_){}
  try{ history.replaceState(null, '', '#style=' + id); }catch(_){}
  if(GLX) GLX.sync();
  if(VIEW){ measure(); redraw(); }
}

function cycle(step){
  const w = document.querySelector('.wipe');
  if(!w || REDUCE){ applyStyle(SI + step); return; }
  w.classList.remove('go'); void w.offsetWidth; w.classList.add('go');
  setTimeout(()=>applyStyle(SI + step), 270);
  setTimeout(()=>w.classList.remove('go'), 620);
}

function initStyles(){
  if(!ROOT || !ROOT.setAttribute || !document.addEventListener) return;
  let id = null;
  const m = /style=([a-z]+)/.exec((typeof location !== 'undefined' && location.hash) || '');
  if(m) id = m[1];
  if(!id){ try{ id = localStorage.getItem('tc-style'); }catch(_){} }
  // A style this page no longer carries (a bookmark, or one remembered from an older
  // report) falls back to the first rather than to nothing.
  const i = STYLES.findIndex(s=>s[0] === id);
  applyStyle(i >= 0 ? i : 0);
  const btn = byId('stylebtn');
  if(btn) btn.addEventListener('click', e=>cycle(e.shiftKey ? -1 : 1));
  document.addEventListener('keydown', e=>{
    if(e.ctrlKey || e.metaKey || e.altKey) return;
    if(e.key === ']') cycle(1);
    else if(e.key === '[') cycle(-1);
  });
}
initStyles();
"""


def esc(s):
    return html.escape('' if s is None else str(s), quote=True)


def rel(seconds):
    """A duration as ``2d 3h``, for reset distances.  Sign is the caller's to phrase."""
    if seconds is None:
        return '&mdash;'
    s = abs(int(seconds))
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        return f'{d}d {h}h'
    if h:
        return f'{h}h {m}m'
    return f'{m}m'


def _script_json(obj):
    """JSON safe to embed inside a ``<script>`` element.

    The payload carries item previews taken verbatim from rollout content, so a tool output
    containing ``</script>`` would otherwise close the block and spill the rest of the data
    into the page as markup.  Escaping ``<``, ``>`` and ``&`` as unicode escapes is inert in
    JSON and cannot terminate the element.
    """
    return (json.dumps(obj, separators=(',', ':'), ensure_ascii=False)
            .replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
            .replace('\u2028', '\\u2028').replace('\u2029', '\\u2029'))


def big(n):
    if n is None:
        return '&mdash;'
    a = abs(n)
    for div, suf in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if a >= div:
            return f'{n/div:.2f}{suf}' if a < div * 100 else f'{n/div:.1f}{suf}'
    return f'{n:,}'


def pct(x, digits=1):
    return '&mdash;' if x is None else f'{100*x:.{digits}f}%'


def tile(k, v, note=''):
    n = f'<div class="n">{note}</div>' if note else ''
    return f'<div class="tile"><div class="k">{k}</div><div class="v">{v}</div>{n}</div>'



# Mirrors analyze.LEAD_MIN_GAP, formatted for prose.
DAILY_MODELS = 8        # models stacked in their own colour; the rest fold into `other`


def _domain(model):
    """The time span every chart on the page is drawn over, in unix seconds.

    One domain for all three charts: the limit chart and the daily chart then place a moment
    at the same x and can be read against each other, and the composition pie has a range to
    be recomposed over.  It covers the limit series, the daily buckets and the content
    buckets, so nothing the page can draw falls outside it.
    """
    lo = hi = None

    def seen(*times):
        nonlocal lo, hi
        for t in times:
            if t is None:
                continue
            lo = t if lo is None else min(lo, t)
            hi = t if hi is None else max(hi, t)

    rl = model.get('rate_limits') or {}
    if rl.get('available'):
        seen(rl.get('now'))
        for w in (rl.get('windows') or []):
            seen(w.get('reset_at'))
            for p in (w.get('cum_points') or []):
                seen(p[0])
            for p in (w.get('pct_points') or []):
                seen(p[0])
    for d in (model.get('daily') or []):
        seen(d.get('start'), d.get('end'))
    bucket = model.get('cat_bucket_s') or 3600
    for row in (model.get('cat_series') or []):
        # The bucket's *end* too: a bucket opening on the last instant of the range would
        # otherwise sit outside the page's own domain and drop out of the pie at full zoom.
        seen(row[0], row[0] + bucket)
    if lo is None:
        return None
    return [int(lo), int(max(hi, lo + 3600))]


def _daily_svg(daily, order, domain):
    """Daily recorded input, stacked by the model that was charged for it.

    `order` is the corpus-wide ranking, not the day's own: stacking in per-day order would
    reshuffle the colours from one bar to the next, and a model's colour would stop meaning
    anything across the chart.

    Bars are drawn in **unit x** -- one unit is the day, and the group's transform places and
    widens it -- so the page rescales the time axis on every zoom step without re-deriving
    any of this geometry, and without touching a height: what a bar's height means is the
    same at every zoom level.  The transform written here is the full-domain one, which is
    what a reader with JavaScript disabled is left with.
    """
    if not daily:
        return '<p class="sub">No data in range.</p>'
    dated = [d for d in daily if d.get('start') is not None and d.get('end') is not None]
    undated = [d for d in daily if d.get('start') is None or d.get('end') is None]
    if not dated:
        return '<p class="sub">No dated days in range.</p>'
    W, L, R, T, B = CHART_W, CHART_L, CHART_R, DAILY_T, DAILY_B
    t0, t1 = domain
    span = (t1 - t0) or 1
    px = lambda t: L + (t - t0) / span * (W - L - R)

    keys = [m for m in (order or []) if m][:DAILY_MODELS]
    rank = {m: i for i, m in enumerate(keys)}
    colour = lambda i: f'var(--c{i % 14})' if i < len(keys) else 'var(--dim)'
    mx = max(d['input'] for d in dated) or 1
    totals = {}
    parts = [f'<svg viewBox="0 0 {W} {DAILY_H}" data-h="{DAILY_H}" data-t="{T}" data-b="{B}" '
             f'role="img" aria-label="daily recorded input, stacked by model">',
             f'<defs><clipPath id="tcclip-daily"><rect class="clip" x="{L}" y="0" '
             f'width="{W-L-R}" height="{DAILY_H}"/></clipPath></defs>',
             '<g class="ax"></g>',
             '<g class="bars" clip-path="url(#tcclip-daily)">']
    for d in dated:
        a, b = d['start'], d['end']
        x, sx = px(a), max(px(b) - px(a), 0.001)
        parts.append(f'<g class="bar" data-a="{int(a)}" data-b="{int(b)}" '
                     f'transform="translate({x:.2f},0) scale({sx:.5f},1)">')
        ms = d.get('models') or {}
        segs, rest = [], d['input']
        for m, v in ms.items():
            if m in rank:
                segs.append((rank[m], m, v))
                rest -= v
        segs.sort()
        # Whatever the ranking does not name still has to be drawn, or the bar understates
        # the day: models past the cap, and any token the split did not account for.
        if rest > 0:
            segs.append((len(keys), 'other', rest))
        y, rows = DAILY_H - B, []
        for j, m, v in segs:
            sh = (v / mx) * (DAILY_H - B - T)
            y -= sh
            parts.append(f'<rect x="0.04" y="{y:.2f}" width="0.92" height="{sh:.2f}" '
                         f'fill="{colour(j)}" class="mk"></rect>')
            totals[m] = totals.get(m, 0) + v
            rows.append(f'{m} {v:,}')
        tip = (f'{d["date"]}\nrecorded {d["input"]:,}\ncached {d["cached"]:,}\n'
               f'uncached {d["uncached"]:,}\nresponses {d["responses"]:,}'
               + ('\n' + '\n'.join(rows) if rows else ''))
        parts.append(f'<rect x="0" y="{T}" width="1" height="{DAILY_H-T-B}" '
                     f'fill="transparent"><title>{esc(tip)}</title></rect>')
        parts.append('</g>')
    parts.append('</g>')
    parts.append(f'<line class="base" x1="{L}" y1="{DAILY_H-B}" x2="{W-R}" y2="{DAILY_H-B}" '
                 f'stroke="var(--line)"/>')
    parts.append(f'<text class="peak" x="{L}" y="{T-6}" fill="var(--dim)" font-size="11">'
                 f'peak {mx:,} tokens/day</text>')
    parts.append('</svg>')
    # An entry that would read 0.0% is noise in the legend; its tokens stay in the bars
    # and their tooltips.
    tot = sum(totals.values()) or 1
    legend = ' '.join(
        f'<span><i style="background:{colour(rank.get(m, len(keys)))}"></i>{esc(m)} '
        f'{100*v/tot:.1f}%</span>'
        for m, v in sorted(totals.items(), key=lambda kv: -kv[1]) if 100*v/tot >= 0.05)
    # A day the corpus never dated cannot be placed on a time axis.  It is named rather than
    # dropped in silence, because its tokens are in every total on the page.
    miss = ('' if not undated else
            f'<p class="sub">{len(undated)} undated day(s), '
            f'{sum(d["input"] for d in undated):,} recorded input, are not drawn.</p>')
    return ''.join(parts) + f'<div class="legend">{legend}</div>{miss}'


# ---- the Matisse collage ------------------------------------------------------------------
# Drawn once, here, from a fixed seed: the same torn edges every time the page opens, inline
# SVG so nothing is fetched, and hidden by CSS under every other style.

def _curve(pts):
    """A closed Catmull-Rom curve through `pts`, as an SVG path of cubic Beziers."""
    n = len(pts)
    d = [f'M{pts[0][0]:.1f} {pts[0][1]:.1f}']
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        d.append(f'C{p1[0] + (p2[0] - p0[0]) / 6:.1f} {p1[1] + (p2[1] - p0[1]) / 6:.1f} '
                 f'{p2[0] - (p3[0] - p1[0]) / 6:.1f} {p2[1] - (p3[1] - p1[1]) / 6:.1f} '
                 f'{p2[0]:.1f} {p2[1]:.1f}')
    return ''.join(d) + 'Z'


def _torn(rng, cx, cy, rx, ry, n=44, lump=.16, tear=.022):
    """A sheet torn into a rough oval: two slow lobes for the shape, a fine tremor for the edge."""
    f1, f2 = rng.uniform(0, 6.3), rng.uniform(0, 6.3)
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = 1 + lump * (.65 * math.sin(2 * a + f1) + .35 * math.sin(3 * a + f2)) + rng.uniform(-tear, tear)
        pts.append((cx + rx * r * math.cos(a), cy + ry * r * math.sin(a)))
    return _curve(pts)


def _leaf(x, y, deg, ln, w, slit=True):
    """An almond leaf from (x, y) pointing at `deg`, with the vein cut out of it."""
    d = (f'M0 0C{ln * .25:.1f} {-w:.1f} {ln * .7:.1f} {-w * .9:.1f} {ln:.1f} 0'
         f'C{ln * .7:.1f} {w * .9:.1f} {ln * .25:.1f} {w:.1f} 0 0Z')
    if slit:
        d += (f'M{ln * .34:.1f} {w * .08:.1f}C{ln * .5:.1f} {-w * .32:.1f} {ln * .7:.1f} {-w * .26:.1f} '
              f'{ln * .82:.1f} {-w * .03:.1f}C{ln * .66:.1f} {w * .06:.1f} {ln * .5:.1f} {w * .14:.1f} '
              f'{ln * .34:.1f} {w * .08:.1f}Z')
    return (f'<path class="ink" fill-rule="evenodd" transform="translate({x} {y}) rotate({deg})" '
            f'd="{d}"/>')


def _flower(rng):
    """Six pointed petals with seeds cut out of the heart, on a stem of cut leaves."""
    cx, cy, petals = 150, 150, 6
    turn = rng.uniform(0, 1)
    d = []
    for k in range(petals):
        a = 2 * math.pi * (k + turn) / petals
        tip = rng.uniform(88, 112)
        v0, v1 = a - math.pi / petals, a + math.pi / petals
        r0 = rng.uniform(30, 40)
        p = lambda ang, r: (cx + r * math.cos(ang), cy + r * math.sin(ang))
        start, t, c1, c2, end = (p(v0, r0), p(a + rng.uniform(-.08, .08), tip),
                                 p(a - .3, tip * .78), p(a + .3, tip * .78), p(v1, r0))
        if k == 0:
            d.append(f'M{start[0]:.1f} {start[1]:.1f}')
        d.append(f'Q{c1[0]:.1f} {c1[1]:.1f} {t[0]:.1f} {t[1]:.1f}Q{c2[0]:.1f} {c2[1]:.1f} {end[0]:.1f} {end[1]:.1f}')
    d.append('Z')
    for k in range(5):                         # the seeds: holes, so the sheet behind shows
        a = 2 * math.pi * (k + .35 + turn) / 5
        d.append(_torn(rng, cx + 19 * math.cos(a), cy + 19 * math.sin(a), 6, 4, n=8, lump=.2, tear=.08))
    head = f'<path class="ink" fill-rule="evenodd" d="{"".join(d)}"/>'
    stem = '<path class="stem" d="M162 196C176 262 170 322 196 392S214 520 262 640"/>'
    leaves = ''.join([
        _leaf(178, 300, -128, 96, 30), _leaf(186, 322, -52, 104, 32),
        _leaf(206, 452, -150, 110, 34), _leaf(214, 478, -36, 92, 28),
        _leaf(246, 590, -118, 70, 22, slit=False),
        _leaf(62, 64, -150, 34, 11, slit=False), _leaf(48, 92, 170, 30, 10, slit=False),
        _leaf(78, 40, -110, 28, 9, slit=False),
    ])
    return (f'<svg class="mz-flower" viewBox="-10 20 330 640" aria-hidden="true">'
            f'{head}{stem}{leaves}</svg>')


def _dashes(rng, rows, cols, cls):
    """White brush dashes, leaning the same way, in loose diagonal rows."""
    out, w, h = [], cols * 40 + rows * 16 + 60, rows * 44 + 60
    for r in range(rows):
        for c in range(cols):
            if rng.random() < .12:
                continue
            ln = rng.uniform(34, 56)
            x = 10 + c * 40 + r * 16 + rng.uniform(-5, 5)
            y = 20 + r * 44 + rng.uniform(-6, 6)
            a = math.radians(rng.uniform(56, 64))
            out.append(f'<line class="dash" x1="{x:.1f}" y1="{y + ln * math.sin(a):.1f}" '
                       f'x2="{x + ln * math.cos(a):.1f}" y2="{y:.1f}"/>')
    return f'<svg class="{cls}" viewBox="0 0 {w} {h}" aria-hidden="true">{"".join(out)}</svg>'


def _matisse():
    rng = random.Random(1947)                  # the year of Jazz
    return ('<div class="mz">'
            f'<svg class="mz-sage" viewBox="0 0 700 560"><path class="sage" d="{_torn(rng, 350, 280, 300, 230)}"/></svg>'
            f'<svg class="mz-rose" viewBox="0 0 560 460"><path class="rose" d="{_torn(rng, 280, 230, 240, 190)}"/></svg>'
            f'{_dashes(rng, 4, 7, "mz-dash1")}{_dashes(rng, 5, 4, "mz-dash2")}{_flower(rng)}'
            '</div>')


def render(model):
    """The page: six headline numbers and three charts over one shared, zoomable range.

    Everything else the model carries -- sessions, reconciliation, images, the window table,
    the data-quality counters and the disclosures that went with them -- is reported through
    `--json` and the stdout summary, not here.
    """
    t = model['totals']
    rl = model.get('rate_limits') or {}
    domain = _domain(model)

    # The weekly figure is the server's own percentage, not a token count of ours, and it
    # keeps that wording so a reader cannot take it for something this page measured.
    cur = rl.get('current') or {}
    wk_pct = cur.get('last_pct')
    wk_next, wk_now = cur.get('resets_at'), rl.get('now')
    wk_note = (f'resets in {rel(wk_next - wk_now)}'
               if (wk_now and wk_next and wk_next > wk_now) else 'reported by the server')

    sc = model.get('scope') or {}
    cat_note = None
    if sc.get('tokenizer_note'):
        cat_note = (f'Not counted: {sc["tokenizer_note"]} Every other figure on this page '
                    f'comes from the usage records and is unaffected.')
    elif sc.get('metrics_only'):
        cat_note = ('Not counted: this report was produced with --metrics-only, which reads '
                    'the usage records and does not tokenize anything.')

    # `sessions` arrives sorted by recorded input, the same measure as the first tile.  The
    # cwd is rollout content, so it is escaped like every other string from there.
    top = (model.get('sessions') or [None])[0]
    top_tile = []
    if top:
        where = os.path.basename((top.get('cwd') or '').rstrip('/\\'))
        top_note = ' &middot; '.join(html.escape(x) for x in
                                     (str(top['session_id'])[:8], where) if x)
        top_tile = [tile('Longest session', big(top['input']), top_note)]

    tiles = ''.join([
        tile('Recorded input', big(t['input']), f"{t['responses']:,} responses"),
        tile('Output', big(t['output']), f"{big(t['reasoning'])} reasoning"),
        tile('Cache hit', pct(t['cache_hit']), f"{big(t['cached'])} cached"),
        tile('Sessions', f"{t['sessions']:,}", f"{t['threads']:,} threads"),
    ] + top_tile + ([tile('Weekly limit used',
               '&mdash;' if wk_pct is None else f'{wk_pct:g}%', wk_note)]
         if rl.get('available') else []))

    if not rl.get('available'):
        rl_chart = (f'<div class="panel"><p class="sub">No rate-limit snapshots in range '
                    f'&mdash; {esc(rl.get("reason") or "none recorded")}.</p></div>')
    else:
        rl_chart = f"""<div class="panel">
  <div class="chart" id="rlchart"></div>
  <div class="legend">
    <span><i style="background:var(--uncached)"></i>cumulative tokens</span>
    <span><i style="background:var(--warn)"></i>weekly limit</span>
  </div>
</div>"""

    # Only the limit series, the content buckets and the shared geometry are read by the
    # page's JS; the deep-dive data it used to carry went out with the section that drew it.
    payload = _script_json({
        'geo': {'w': CHART_W, 'l': CHART_L, 'r': CHART_R,
                'rl_h': RL_H, 'rl_t': RL_T, 'rl_b': RL_B},
        'domain': domain,
        'styles': STYLES,
        'gl_styles': GL_STYLES,
        'rate_limits': {
            'now': rl.get('now'),
            'current': rl.get('current'),
            'windows': [{k: w[k] for k in
                         ('index', 'reset_at', 'reset_at_iso', 'resets_at', 'resets_at_iso',
                          'peak_pct', 'last_pct', 'tokens', 'pct_points', 'cum_points',
                          'late_points')}
                        for w in (rl.get('windows') or [])],
        },
        'cats': {
            'series': model.get('cat_series') or [],
            'bucket': model.get('cat_bucket_s') or 3600,
            'order': [c['category'] for c in (model.get('categories') or [])],
            'note': cat_note,
        },
        # The daily chart's own ranking and cap, so the model pie colours match its bars.
        'models': {
            'order': [m['model'] for m in model['models'] if m['model']][:DAILY_MODELS],
            'days': [[d['start'], d['end'], d.get('models') or {}] for d in model['daily']
                     if d.get('start') is not None and d.get('end') is not None],
        },
    })

    # The masthead's one line of context, written here so it reads the same with JS off.
    fmt = lambda ts: time.strftime('%b %d, %Y', time.localtime(ts))
    dek = ' &middot; '.join(x for x in (
        f'{fmt(domain[0])} &ndash; {fmt(domain[1])}' if domain else '',
        f"{t['sessions']:,} sessions", f"{t['responses']:,} responses") if x)
    first, nxt = STYLES[0], STYLES[1 % len(STYLES)]

    return f"""<!doctype html>
<html lang="en" data-style="{first[0]}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex Token Report</title>
<style>{CSS}{STYLE_CSS}</style></head><body>
<div class="deco" aria-hidden="true">{_matisse()}<div class="wipe"></div></div>
<nav class="bar"><div class="brand">tokenusage.dev</div><div><button id="stylebtn" type="button" data-next="{nxt[0]}" aria-label="Switch to the {esc(nxt[1])} style"><span class="sdot" aria-hidden="true"></span></button></div></nav>
<div class="wrap">

<header class="mast">
  <div class="kicker"></div>
  <h1>Codex Token Report</h1>
  <p class="dek">{dek}</p>
</header>

<div class="tiles">{tiles}</div>


{rl_chart}

<div class="panel"><div class="chart" id="dailychart">{_daily_svg(
    model['daily'], [m['model'] for m in model['models']], domain or [0, 1])}</div></div>

<div class="panel pies"><div id="catpie"></div><div id="modelpie"></div></div>

</div>
<script>window.__TC__ = {payload};</script>
<script>{JS}{GL_JS}{STYLE_JS}</script>
</body></html>
"""
