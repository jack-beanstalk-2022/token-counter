"""Self-contained HTML report.

No CDN, no network, no external fonts: the file is opened from disk and must render with
the machine offline.  Charts are inline SVG; interaction is a few hundred lines of vanilla
JS over an embedded JSON blob.

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
svg{display:block;width:100%;height:auto;overflow:visible}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
/* pan-y, not none: a vertical swipe still scrolls the page on a phone, while a horizontal
   drag and a two-finger pinch reach the chart instead of the browser. */
.chart{touch-action:pan-y;cursor:grab;-webkit-user-select:none;user-select:none;
  -webkit-tap-highlight-color:transparent}
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
#stylebtn{font:inherit;font-size:13px;cursor:pointer;display:inline-flex;align-items:center;gap:10px;
  padding:7px 14px;border:1px solid var(--line);background:var(--panel);color:var(--fg);
  border-radius:999px;white-space:nowrap;transition:transform .12s,box-shadow .12s}
#stylebtn:hover{transform:translateY(-1px)}
#stylebtn:focus-visible{outline:2px solid var(--uncached);outline-offset:2px}
#stylebtn .sw-l{opacity:.7;text-transform:uppercase;font-size:11px;letter-spacing:.1em}
#stylebtn #styleidx{opacity:.6;font-variant-numeric:tabular-nums}
#stylebtn .sw-go{font-size:15px;line-height:1}
.keys{color:var(--dim);font-size:11px;margin-right:8px}
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
@media(max-width:640px){#stylebtn .sw-l,.keys{display:none} .brand{font-size:13px}}

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
[data-style="matisse"] #stylebtn{background:var(--ink);color:var(--bg);border:0;
  border-radius:22px 9px 18px 12px/12px 18px 9px 22px}
[data-style="matisse"] #stylebtn:hover{transform:rotate(-2deg)}
[data-style="matisse"] .keys{color:var(--fg)}
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

// ---- one viewport, three charts -----------------------------------------------------
// DOM is the whole range the report covers; VIEW is the slice currently drawn.  Every chart
// reads VIEW, so panning or zooming any one of them moves all three -- which is the reason
// the daily bars are rendered in unit-x rather than in pixels.
const G = D.geo || {};
const DOM = D.domain;
let VIEW = DOM ? [DOM[0], DOM[1]] : null;
// Deepest zoom: a thousandth of the corpus, floored at ten minutes.  It bounds how far
// off-screen a path coordinate can land as much as it bounds the zoom.
const MIN_SPAN = DOM ? Math.max(600, (DOM[1]-DOM[0])/1000) : 600;

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

function axis(h, top, bot, tk){
  let s = '';
  for(const [t, step] of tk){
    const xx = X(t);
    if(xx < L-0.5 || xx > W-RM+0.5) continue;
    s += `<line x1="${xx.toFixed(1)}" y1="${top}" x2="${xx.toFixed(1)}" y2="${h-bot}" `+
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
  if(!WINS.length){ host.innerHTML = '<p class="sub">No weekly-limit snapshots in range.</p>'; return; }
  const H = G.rl_h||300, T = G.rl_t||18, B = G.rl_b||34;
  const y  = v => H-B - (v/VMAX)*(H-B-T);
  const yp = p => H-B - (p/100)*(H-B-T);

  let s = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="cumulative tokens per weekly limit window">`;
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
      const d = [`M ${X(start).toFixed(1)} ${y(0).toFixed(1)}`]
        .concat(pts.map(p=>`L ${X(p[0]).toFixed(1)} ${y(pick(p)).toFixed(1)}`));
      const last = pts[pts.length-1];
      s += `<path d="${d.join(' ')} L ${X(last[0]).toFixed(1)} ${y(0).toFixed(1)} Z" `+
           `fill="var(--uncached)" fill-opacity=".16"/>`;
      s += `<path d="${d.join(' ')}" fill="none" stroke="var(--uncached)" stroke-width="1.8">`+
           `<title>window opened ${esc(when(start))}\nreset quoted ${esc(w.resets_at_iso||'--')}\n`+
           `peak reported ${w.peak_pct==null?'--':w.peak_pct+'%'}\n`+
           `recorded input ${big(w.tokens.input)} over ${w.tokens.responses} responses\n`+
           `uncached ${big(w.tokens.uncached)} | output ${big(w.tokens.output)}`+
           (w.late_points ? `\n${w.late_points} later reading(s) not drawn: the next window `+
                            `had already opened` : '')+`</title></path>`;
    }
    if(pcs.length){
      const d = pcs.map((p,i)=>`${i?'L':'M'} ${X(p[0]).toFixed(1)} ${yp(p[1]).toFixed(1)}`);
      s += `<path d="${d.join(' ')}" fill="none" stroke="var(--warn)" stroke-width="1.4" stroke-dasharray="5 3"/>`;
    }
  });
  s += `</g>`;
  s += `<line x1="${L}" y1="${H-B}" x2="${W-RM}" y2="${H-B}" stroke="var(--line)"/>`;
  s += '</svg>';
  host.innerHTML = s;
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
  svg.querySelectorAll('g.bar').forEach(g=>{
    const a = +g.getAttribute('data-a'), b = +g.getAttribute('data-b');
    const x = X(a), sx = Math.max(X(b)-x, 0.001);
    g.setAttribute('transform', `translate(${x.toFixed(2)},0) scale(${sx.toFixed(5)},1)`);
  });
  const base = svg.querySelector('.base');
  if(base){ base.setAttribute('x1', L); base.setAttribute('x2', W-RM); }
  const peak = svg.querySelector('.peak');
  if(peak) peak.setAttribute('x', L);
  const ax = svg.querySelector('.ax');
  if(ax) ax.innerHTML = axis(H, +svg.getAttribute('data-t') || 18,
                                +svg.getAttribute('data-b') || 34, tk);
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
    host.innerHTML = `<p class="sub">${esc(msg)}</p>`;
    return;
  }
  // Colour is the category's place in the corpus-wide order, so a slice keeps its colour as
  // the viewport moves and one pie can be read against the last.
  const order = CATS.order || Object.keys(tot);
  const rows = order.map((k,i)=>({k:k, v:tot[k]||0, fill:`var(--c${i%14})`})).filter(r=>r.v>0);
  host.innerHTML = pie(rows, sum, 'tokens in view', 'content composition by category');
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
  if(!sum){ host.innerHTML = '<p class="sub">No recorded input in the visible range.</p>'; return; }
  const rows = keys.concat(['other']).map(k=>({k:k, v:tot[k]||0,
    fill: k in rank ? `var(--c${rank[k]%14})` : 'var(--dim)'})).filter(r=>r.v>0);
  host.innerHTML = pie(rows, sum, 'recorded input in view', 'recorded input by model');
}

/** A pie and its legend.  `rows` are {k, v, fill} in draw order; an entry that would read
 *  0.0% keeps its slice but not its legend line. */
function pie(rows, sum, what, label){
  const size = 240, r = size/2-4, c0 = size/2;
  let s = `<svg viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(label)}">`;
  let a = -Math.PI/2;                        // first slice starts at twelve o'clock
  for(const row of rows){
    const frac = row.v/sum;
    const tip = `${row.k}: ${row.v.toLocaleString()} tokens (${(100*frac).toFixed(1)}%)`;
    if(frac >= 1-1e-12){
      // One entry holding everything: an arc whose ends coincide draws nothing.
      s += `<circle cx="${c0}" cy="${c0}" r="${r}" fill="${row.fill}"><title>${esc(tip)}</title></circle>`;
      break;
    }
    const b = a + frac*2*Math.PI;
    s += `<path d="M ${c0} ${c0} L ${(c0+r*Math.cos(a)).toFixed(2)} ${(c0+r*Math.sin(a)).toFixed(2)} `+
         `A ${r} ${r} 0 ${frac>0.5?1:0} 1 ${(c0+r*Math.cos(b)).toFixed(2)} ${(c0+r*Math.sin(b)).toFixed(2)} Z" `+
         `fill="${row.fill}" stroke="var(--panel)" stroke-width="1"><title>${esc(tip)}</title></path>`;
    a = b;
  }
  s += '</svg>';
  const legend = rows.filter(r=>r.v/sum >= 0.0005).map(r=>
    `<span><i style="background:${r.fill}"></i>${esc(r.k)} ${(100*r.v/sum).toFixed(1)}%</span>`).join('');
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
  drawPie();
  drawModelPie();
}

/** Put `tAnchor` under `vxAnchor` at the given span, clamped to the domain. */
function setSpan(span, tAnchor, vxAnchor){
  if(!VIEW) return;
  const full = DOM[1]-DOM[0];
  span = clamp(span, Math.min(MIN_SPAN, full), full);
  const v0 = clamp(tAnchor - (clamp(vxAnchor, L, W-RM)-L)/PLOT*span, DOM[0], DOM[1]-span);
  VIEW = [v0, v0+span];
  schedule();
}

function panPx(dvx){
  if(!VIEW) return;
  const span = VIEW[1]-VIEW[0];
  const v0 = clamp(VIEW[0] - dvx/PLOT*span, DOM[0], DOM[1]-span);
  VIEW = [v0, v0+span];
  schedule();
}

function reset(){
  if(!DOM) return;
  VIEW = [DOM[0], DOM[1]];
  schedule();
}

// One set of gestures, bound to each chart: wheel and trackpad on a desktop, drag and
// two-finger pinch on a touch screen, and a double-click back to the full range.
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
    if(Math.abs(e.deltaX) > Math.abs(e.deltaY)){          // trackpad swipe: pan
      e.preventDefault();
      panPx(-e.deltaX*scale());
      return;
    }
    if(!e.deltaY) return;
    e.preventDefault();
    const unit = e.deltaMode===1 ? 0.05 : (e.deltaMode===2 ? 0.8 : 0.002);
    const vx = clamp(vxOf(e), L, W-RM);
    setSpan((VIEW[1]-VIEW[0])/Math.exp(-e.deltaY*unit), Tat(vx), vx);
  }, {passive:false});

  el.addEventListener('pointerdown', e=>{
    if(!VIEW || (e.pointerType==='mouse' && e.button!==0)) return;
    try{ el.setPointerCapture(e.pointerId); }catch(_){}
    pts.set(e.pointerId, vxOf(e));
    el.classList.add('drag');
    if(pts.size===2){
      const ids = Array.from(pts.keys());
      pinch = {ia:ids[0], ib:ids[1], ta:Tat(pts.get(ids[0])), tb:Tat(pts.get(ids[1]))};
    }
  });

  el.addEventListener('pointermove', e=>{
    if(!pts.has(e.pointerId)) return;
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
  el.addEventListener('dblclick', e=>{ e.preventDefault(); reset(); });
}

function init(){
  if(!VIEW){
    drawPie();
    drawModelPie();
    return;
  }
  measure();
  ['rlchart','dailychart'].forEach(id=>{ const el = byId(id); if(el) bind(el); });
  let rt = 0;
  addEventListener('resize', ()=>{
    clearTimeout(rt);
    rt = setTimeout(()=>{ measure(); redraw(); }, 120);
  });
  redraw();
}
init();
"""

# The style switcher.  Kept apart from JS so the charts' script reads as it did; it runs after
# init(), touches the charts only through measure() and redraw(), and does nothing at all
# where there is no real DOM (scripts/test_page.js).
STYLE_JS = r"""
// ---- styles: one page, several readings -----------------------------------------------
const STYLES = D.styles || [['clinical','Clinical']];
const ROOT = document.documentElement;
const REDUCE = (()=>{ try{ return matchMedia('(prefers-reduced-motion: reduce)').matches; }
                      catch(_){ return false; } })();

let SI = 0;
function applyStyle(i){
  SI = (i % STYLES.length + STYLES.length) % STYLES.length;
  const id = STYLES[SI][0];
  ROOT.setAttribute('data-style', id);
  const nm = byId('stylename'), ix = byId('styleidx'), btn = byId('stylebtn');
  if(nm) nm.textContent = STYLES[SI][1];
  if(ix) ix.textContent = (SI+1) + '/' + STYLES.length;
  if(btn) btn.title = 'Next: ' + STYLES[(SI+1)%STYLES.length][1] + '  (shift-click or [ for previous)';
  try{ localStorage.setItem('tc-style', id); }catch(_){}
  try{ history.replaceState(null, '', '#style=' + id); }catch(_){}
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
                         f'fill="{colour(j)}"></rect>')
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
    first = STYLES[0]

    return f"""<!doctype html>
<html lang="en" data-style="{first[0]}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Codex Token Report</title>
<style>{CSS}{STYLE_CSS}</style></head><body>
<div class="deco" aria-hidden="true">{_matisse()}<div class="wipe"></div></div>
<nav class="bar"><div class="brand">token-counter</div><div><span class="keys">[ ]</span><button id="stylebtn" type="button" aria-label="Cycle the page style"><span class="sw-l">Style</span><b id="stylename">{first[1]}</b><span id="styleidx">1/{len(STYLES)}</span><span class="sw-go" aria-hidden="true">&#8635;</span></button></div></nav>
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
<script>{JS}{STYLE_JS}</script>
</body></html>
"""
