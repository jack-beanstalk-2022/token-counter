"""Self-contained HTML report.

No CDN, no network, no external fonts: the file is opened from disk and must render with
the machine offline.  Charts are inline SVG; interaction is a few hundred lines of vanilla
JS over an embedded JSON blob.

The page ships nine styles over one markup (STYLES), cycled by a button in the top bar or the
`[` / `]` keys and remembered per browser.  Two of them draw a WebGL scene behind the panels
from the same payload the charts read -- a city of daily towers and a spiral of content
categories -- with no library: the shaders and the matrix math are inline below.

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
import os
import time

# One geometry for every time chart.  The page re-derives the width from the panel at run
# time (one viewBox unit = one CSS pixel, so axis text is legible on a phone); these are the
# no-JS fallback values, and the proportions the margins are capped at.
CHART_W, CHART_L, CHART_R = 980, 62, 48
RL_H, RL_T, RL_B = 300, 18, 34
DAILY_H, DAILY_T, DAILY_B = 210, 18, 34

# The page styles, in the order the button cycles them; the first is the default.  The page
# reads this list from its payload, so it is written down once.
STYLES = [('brutal', 'Brutal'), ('phosphor', 'Phosphor'), ('broadsheet', 'Broadsheet'),
          ('swiss', 'Swiss'), ('blueprint', 'Blueprint'), ('outrun', 'Outrun'),
          ('city', 'Tokenopolis'), ('galaxy', 'Nebula'), ('clinical', 'Clinical')]

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

# Nine opinions about one page.  Every style is CSS over the same markup, keyed on
# `html[data-style]`; two of them ("city", "galaxy") also light a WebGL scene behind the
# panels, built from the same payload the charts read.  Nothing here is fetched: fonts are
# whatever the machine has, and every stack ends in a generic family.
STYLE_CSS = r"""
/* ---- shared chrome: the style bar, the masthead, the decorations --------------------- */
:root{--sky:#0f1115;--kicker:"Codex usage, recounted locally"}
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
.caption,.tblock{display:none}
#stage{position:fixed;inset:0;width:100%;height:100%;display:none;z-index:0;pointer-events:none}
.deco>*{display:none}
.wrap,.bar{position:relative;z-index:1}
.bar{position:sticky}
.wipe{position:fixed;inset:0;z-index:60;pointer-events:none;
  background:repeating-linear-gradient(115deg,var(--fg) 0 28px,var(--uncached) 28px 56px)}
.wipe.go{display:block;animation:wipe .56s cubic-bezier(.7,0,.3,1) forwards}
@keyframes wipe{0%{clip-path:inset(0 100% 0 0)}45%,55%{clip-path:inset(0 0 0 0)}100%{clip-path:inset(0 0 0 100%)}}
@media(max-width:640px){#stylebtn .sw-l,.keys{display:none} .brand{font-size:13px}}

/* ---- 1. BRUTAL: hard edges, primary ink, nothing rounded ----------------------------- */
:root[data-style="brutal"]{
  --bg:#f3efe6;--panel:#ffffff;--line:#000000;--fg:#000000;--dim:#333333;
  --cached:#9ad0ff;--uncached:#0047ff;--out:#00c853;--warn:#ff2e00;--warn-bg:#ffe500;
  --c0:#0047ff;--c1:#ff2e88;--c2:#ffb800;--c3:#00b37e;--c4:#7a00ff;--c5:#ff5a00;--c6:#00a3c4;
  --c7:#1a1a1a;--c8:#b0005a;--c9:#6b8f00;--c10:#ff8fb1;--c11:#4d4dff;--c12:#8c6d00;--c13:#777777;
  --kicker:"Raw numbers. No rounded corners."}
[data-style="brutal"] body{font:15px/1.45 "Courier New",Courier,monospace}
[data-style="brutal"] .bar{background:#ffe500;border-bottom:4px solid #000}
[data-style="brutal"] .brand{font:900 18px/1 "Arial Black",Impact,sans-serif;text-transform:uppercase}
[data-style="brutal"] #stylebtn{border:3px solid #000;border-radius:0;background:#000;color:#ffe500;
  box-shadow:5px 5px 0 #ff2e88;font-weight:700;text-transform:uppercase}
[data-style="brutal"] #stylebtn:active{transform:translate(5px,5px);box-shadow:none}
[data-style="brutal"] .kicker{display:inline-block;background:#000;color:#fff;padding:5px 12px;
  font-weight:700;transform:rotate(-2deg);letter-spacing:.04em}
[data-style="brutal"] .mast h1{font:900 clamp(46px,11vw,132px)/.84 "Arial Black",Impact,sans-serif;
  text-transform:uppercase;letter-spacing:-.05em;margin:18px 0 12px;text-shadow:7px 7px 0 #ff2e88}
[data-style="brutal"] .dek{color:#000;font-weight:700;background:#fff;display:inline-block;
  border:3px solid #000;padding:4px 10px}
[data-style="brutal"] .tiles{gap:18px;margin-top:28px}
[data-style="brutal"] .tile,[data-style="brutal"] .panel{border:3px solid #000;border-radius:0;
  box-shadow:8px 8px 0 #000}
[data-style="brutal"] .tile{transition:transform .08s,box-shadow .08s;animation:slam .42s both cubic-bezier(.2,1.6,.4,1)}
[data-style="brutal"] .tile:hover{transform:translate(-4px,-4px);box-shadow:12px 12px 0 #000}
[data-style="brutal"] .tile:nth-child(4n+1){background:#ffe500}
[data-style="brutal"] .tile:nth-child(4n+2){background:#ff9ccf}
[data-style="brutal"] .tile:nth-child(4n+3){background:#7df9ff}
[data-style="brutal"] .tile:nth-child(4n+4){background:#b6ff5c}
[data-style="brutal"] .tile:nth-child(2){animation-delay:.05s}
[data-style="brutal"] .tile:nth-child(3){animation-delay:.1s}
[data-style="brutal"] .tile:nth-child(4){animation-delay:.15s}
[data-style="brutal"] .tile:nth-child(5){animation-delay:.2s}
[data-style="brutal"] .tile:nth-child(6){animation-delay:.25s}
[data-style="brutal"] .tile .k{color:#000;font-weight:700}
[data-style="brutal"] .tile .n{color:#000}
[data-style="brutal"] .tile .v{font:900 38px/1.05 "Arial Black",Impact,sans-serif;letter-spacing:-.03em}
[data-style="brutal"] .panel{margin-top:28px}
[data-style="brutal"] .legend{color:#000}
@keyframes slam{0%{transform:translateY(-40px) rotate(-4deg);opacity:0}100%{transform:none;opacity:1}}

/* ---- 2. PHOSPHOR: a green CRT, scanlines and all ------------------------------------- */
:root[data-style="phosphor"]{
  --bg:#020a04;--panel:rgba(0,40,12,.35);--line:#11622b;--fg:#39ff7a;--dim:#22b457;
  --cached:#0e6b2e;--uncached:#39ff7a;--out:#ffcc33;--warn:#ffb000;--warn-bg:#2a1d00;
  --c0:#39ff7a;--c1:#ffb000;--c2:#00e5ff;--c3:#b8ff3d;--c4:#ff6a3d;--c5:#1f9e4a;--c6:#ffe066;
  --c7:#6affc1;--c8:#c08a00;--c9:#9dff9d;--c10:#0bbf8a;--c11:#ffd29d;--c12:#5c8a2e;--c13:#2f6b3f;
  --kicker:"> codex-tokens --report --since=epoch"}
[data-style="phosphor"] body{font:14px/1.55 Consolas,"Lucida Console",Menlo,monospace;
  text-shadow:0 0 5px rgba(57,255,122,.55);
  background:radial-gradient(ellipse at 50% 40%,#073516 0%,#020a04 72%) fixed}
[data-style="phosphor"] .scan{display:block;position:fixed;inset:0;pointer-events:none;z-index:30;
  background:repeating-linear-gradient(to bottom,rgba(0,0,0,.32) 0 1px,transparent 1px 3px);
  box-shadow:inset 0 0 200px rgba(0,0,0,.95);animation:flick 5s infinite}
[data-style="phosphor"] .wrap{animation:crt .7s cubic-bezier(.2,.8,.2,1)}
[data-style="phosphor"] .bar{background:#020a04;border-bottom:1px dashed var(--line)}
[data-style="phosphor"] .brand::before{content:"root@codex:~$ "}
[data-style="phosphor"] #stylebtn{border:1px solid var(--fg);border-radius:0;background:transparent;
  color:var(--fg);text-transform:uppercase}
[data-style="phosphor"] #stylebtn:hover{background:var(--fg);color:#020a04;text-shadow:none}
[data-style="phosphor"] .kicker{color:var(--fg);text-transform:none;font-size:14px;letter-spacing:0}
[data-style="phosphor"] .kicker::after{content:"\2588";animation:blink 1s steps(1) infinite;margin-left:4px}
[data-style="phosphor"] .mast h1{font-size:clamp(26px,5vw,44px);text-transform:uppercase;letter-spacing:.24em;
  margin:18px 0 4px}
[data-style="phosphor"] .mast h1::before{content:"## "}
[data-style="phosphor"] .dek::before{content:"// "}
[data-style="phosphor"] .tile,[data-style="phosphor"] .panel{border:1px dashed var(--line);border-radius:0;background:var(--panel)}
[data-style="phosphor"] .tile .k::before{content:"$ "}
[data-style="phosphor"] .tile .v{font-weight:400;letter-spacing:.04em}
[data-style="phosphor"] .tile .v::after{content:"_";animation:blink 1.2s steps(1) infinite}
[data-style="phosphor"] svg{filter:drop-shadow(0 0 2px rgba(57,255,122,.55))}
@keyframes flick{0%,100%{opacity:.92}47%{opacity:.86}48%{opacity:.97}50%{opacity:.8}53%{opacity:.94}}
@keyframes blink{50%{opacity:0}}
@keyframes crt{0%{transform:scaleY(.004);filter:brightness(6)}55%{transform:scaleY(1);filter:brightness(2)}100%{filter:none}}

/* ---- 3. BROADSHEET: a newspaper of record -------------------------------------------- */
:root[data-style="broadsheet"]{
  --bg:#f1ead9;--panel:transparent;--line:#1b1b1b;--fg:#161411;--dim:#5a5247;
  --cached:#a9a39a;--uncached:#161411;--out:#8b1e1e;--warn:#9e1b1b;--warn-bg:#e8dcc0;
  --c0:#161411;--c1:#9e1b1b;--c2:#8a8170;--c3:#27466b;--c4:#b08d3c;--c5:#3f5b3a;--c6:#c2b8a3;
  --c7:#5d2a42;--c8:#7c4a1e;--c9:#4a4a4a;--c10:#d8cbad;--c11:#44616f;--c12:#8a7a55;--c13:#9a948a;
  --kicker:"Late City Edition \00B7  All the tokens fit to print"}
[data-style="broadsheet"] body{font:16px/1.55 Georgia,"Times New Roman",serif;counter-reset:fig;
  background-image:radial-gradient(rgba(60,40,10,.06) 1px,transparent 1.2px);background-size:4px 4px}
[data-style="broadsheet"] .bar{border-bottom:3px double var(--line)}
[data-style="broadsheet"] .brand{font-style:italic;font-weight:400}
[data-style="broadsheet"] #stylebtn{border:1px solid var(--line);border-radius:0;background:none;
  font-variant:small-caps;letter-spacing:.06em;font-size:14px}
[data-style="broadsheet"] .mast{text-align:center;border-bottom:5px double var(--line);padding:22px 0 14px}
[data-style="broadsheet"] .kicker{color:var(--fg);font:700 11px/1 Georgia,serif;letter-spacing:.3em;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:7px 0;display:block}
[data-style="broadsheet"] .mast h1{font:400 clamp(42px,9vw,104px)/1.02 "Old English Text MT","UnifrakturMaguntia",
  "Engravers Old English BT","Goudy Text MT",Georgia,serif;margin:16px 0 8px;letter-spacing:0}
[data-style="broadsheet"] .dek{font-style:italic;color:var(--fg)}
[data-style="broadsheet"] .tiles{gap:0;border-bottom:1px solid var(--line);margin-top:0}
[data-style="broadsheet"] .tile{background:none;border:0;border-right:1px solid var(--line);border-radius:0;padding:16px 18px}
[data-style="broadsheet"] .tile:last-child{border-right:0}
[data-style="broadsheet"] .tile .k{font-variant:small-caps;text-transform:none;letter-spacing:.1em;
  color:var(--fg);font-weight:700;font-size:14px}
[data-style="broadsheet"] .tile .v{font:700 36px/1.1 Georgia,serif}
[data-style="broadsheet"] .tile .n{font-style:italic}
[data-style="broadsheet"] .panel{background:none;border:0;border-top:3px solid var(--line);border-radius:0;
  padding:12px 0 18px;margin-top:22px;counter-increment:fig}
[data-style="broadsheet"] .panel::before{content:"Fig. " counter(fig) ".";display:block;font-style:italic;
  font-weight:700;margin-bottom:6px}
[data-style="broadsheet"] .pies>div{border-left:1px solid var(--line);padding-left:18px}

/* ---- 4. SWISS: grid, red, and very large numbers ------------------------------------- */
:root[data-style="swiss"]{
  --bg:#ffffff;--panel:#ffffff;--line:#111111;--fg:#111111;--dim:#6b6b6b;
  --cached:#cfcfcf;--uncached:#e3000f;--out:#111111;--warn:#e3000f;--warn-bg:#ffe3e3;
  --c0:#e3000f;--c1:#111111;--c2:#8a8a8a;--c3:#ff7a00;--c4:#0050a0;--c5:#c9c9c9;--c6:#7a0008;
  --c7:#4d4d4d;--c8:#ff9aa0;--c9:#0a8f5a;--c10:#ffcc00;--c11:#003366;--c12:#a0a0a0;--c13:#d8d8d8;
  --kicker:"Bericht \2014  Report \2014  Rapport \2014  Nr. 01"}
[data-style="swiss"] body{font:15px/1.4 "Helvetica Neue",Helvetica,Arial,sans-serif}
[data-style="swiss"] .bar{border-bottom:2px solid #111}
[data-style="swiss"] .brand{text-transform:lowercase;letter-spacing:-.02em;font-size:17px}
[data-style="swiss"] #stylebtn{border-radius:0;background:#e3000f;color:#fff;border:0;font-weight:700;padding:9px 16px}
[data-style="swiss"] .mast{border-top:14px solid #e3000f;margin-top:18px;padding-top:18px;overflow:hidden}
[data-style="swiss"] .mast::after{content:"";position:absolute;right:-60px;top:24px;width:260px;height:260px;
  border-radius:50%;background:#e3000f;z-index:-1}
[data-style="swiss"] .kicker{color:#111;font-weight:700;letter-spacing:0;text-transform:none}
[data-style="swiss"] .mast h1{font:700 clamp(52px,11vw,150px)/.86 "Helvetica Neue",Helvetica,Arial,sans-serif;
  letter-spacing:-.06em;text-transform:lowercase;margin:22px 0 18px;max-width:9ch;animation:slide .6s both cubic-bezier(.2,.8,.2,1)}
[data-style="swiss"] .mast h1::after{content:".";color:#e3000f}
[data-style="swiss"] .dek{color:#111;font-weight:700}
[data-style="swiss"] .tiles{gap:0 24px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-top:36px}
[data-style="swiss"] .tile{border:0;border-top:2px solid #111;border-radius:0;background:none;padding:10px 0 20px}
[data-style="swiss"] .tile .v{font-size:clamp(32px,3.6vw,46px);font-weight:700;letter-spacing:-.05em;line-height:1.05}
[data-style="swiss"] .tile:first-child .v{color:#e3000f}
[data-style="swiss"] .tile .k{text-transform:lowercase;letter-spacing:0;font-size:13px;color:#111;font-weight:700}
[data-style="swiss"] .panel{border:0;border-top:2px solid #111;border-radius:0;background:none;padding:14px 0;margin-top:26px}
@keyframes slide{0%{transform:translateX(-30px);opacity:0}100%{transform:none;opacity:1}}

/* ---- 5. BLUEPRINT: drafted, dimensioned, title-blocked ------------------------------- */
:root[data-style="blueprint"]{
  --bg:#0f3d7a;--panel:rgba(15,61,122,.72);--line:rgba(220,235,255,.55);--fg:#eaf3ff;--dim:#a9c6ee;
  --cached:#6fa3e6;--uncached:#ffffff;--out:#ffe45c;--warn:#ffe45c;--warn-bg:rgba(255,228,92,.12);
  --c0:#ffffff;--c1:#ffe45c;--c2:#7fd7ff;--c3:#ff9e7a;--c4:#b3ffcf;--c5:#c9b3ff;--c6:#9fb8d9;
  --c7:#ffc2e0;--c8:#5ab0ff;--c9:#e0ff7a;--c10:#ffd9a0;--c11:#7affea;--c12:#d0d0d0;--c13:#7a93b8;
  --kicker:"DWG. NO. TC-001 \00B7  REV. A \00B7  DO NOT SCALE"}
[data-style="blueprint"] body{font:14px/1.5 "Courier New",Consolas,monospace;background-color:#0f3d7a;
  background-image:linear-gradient(rgba(255,255,255,.13) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.13) 1px,transparent 1px),
    linear-gradient(rgba(255,255,255,.05) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,255,255,.05) 1px,transparent 1px);
  background-size:100px 100px,100px 100px,20px 20px,20px 20px;background-attachment:fixed}
[data-style="blueprint"] .bar{background:rgba(15,61,122,.92);border-bottom:2px solid var(--fg)}
[data-style="blueprint"] .brand{text-transform:uppercase;letter-spacing:.2em}
[data-style="blueprint"] #stylebtn{border:1px dashed var(--fg);border-radius:0;background:none;
  text-transform:uppercase;letter-spacing:.14em}
[data-style="blueprint"] .mast{padding:30px 0 14px;min-height:170px}
[data-style="blueprint"] .kicker{color:var(--fg);letter-spacing:.2em}
[data-style="blueprint"] .mast h1{font:700 clamp(24px,4.4vw,40px)/1.15 "Courier New",monospace;
  letter-spacing:.28em;text-transform:uppercase;max-width:62%}
[data-style="blueprint"] .tblock{display:grid;grid-template-columns:auto auto;position:absolute;right:0;top:26px;
  border:2px solid var(--fg);margin:0;font-size:11px;text-transform:uppercase;background:var(--bg)}
[data-style="blueprint"] .tblock>div{display:contents}
[data-style="blueprint"] .tblock dt,[data-style="blueprint"] .tblock dd{margin:0;padding:3px 10px;border:1px solid var(--line)}
[data-style="blueprint"] .tblock dt{color:var(--dim)}
[data-style="blueprint"] .tile,[data-style="blueprint"] .panel{border:1px solid var(--line);border-radius:0;
  position:relative;background:var(--panel)}
[data-style="blueprint"] .tile::before,[data-style="blueprint"] .panel::before,
[data-style="blueprint"] .tile::after,[data-style="blueprint"] .panel::after{content:"";position:absolute;
  width:14px;height:14px;border:0 solid var(--fg)}
[data-style="blueprint"] .tile::before,[data-style="blueprint"] .panel::before{left:-6px;top:-6px;border-width:2px 0 0 2px}
[data-style="blueprint"] .tile::after,[data-style="blueprint"] .panel::after{right:-6px;bottom:-6px;border-width:0 2px 2px 0}
[data-style="blueprint"] .tile .k{display:flex;align-items:center;gap:6px;color:var(--fg)}
[data-style="blueprint"] .tile .k::before{content:"\25C2";color:var(--dim)}
[data-style="blueprint"] .tile .k::after{content:"";flex:1;height:1px;background:var(--dim);margin-right:-2px}
[data-style="blueprint"] .tile .v{font-weight:400;letter-spacing:.06em}
@media(max-width:760px){[data-style="blueprint"] .tblock{position:static;margin-top:14px;display:inline-grid}
  [data-style="blueprint"] .mast h1{max-width:none}}

/* ---- 6. OUTRUN: sunset, chrome, and a grid that never ends --------------------------- */
:root[data-style="outrun"]{
  --bg:#12002b;--panel:rgba(22,0,48,.74);--line:rgba(255,64,200,.45);--fg:#fff1ff;--dim:#caa2ea;
  --cached:#6b2fa8;--uncached:#ff2fd0;--out:#27f3ff;--warn:#ffd23f;--warn-bg:rgba(255,210,63,.12);
  --c0:#ff2fd0;--c1:#27f3ff;--c2:#ffd23f;--c3:#ff6b3d;--c4:#9d5cff;--c5:#3dff9e;--c6:#ff8fe8;
  --c7:#4d7cff;--c8:#ffb86b;--c9:#b3fffb;--c10:#ff4d6d;--c11:#c6ff3d;--c12:#e0b3ff;--c13:#8d7bb0;
  --kicker:"Token Drive '86"}
[data-style="outrun"] body{font:15px/1.5 "Trebuchet MS","Segoe UI",sans-serif;
  background:linear-gradient(#07001a 0%,#24004a 34%,#7a1680 56%,#ff5e62 66%,#12002b 66.1%) fixed}
[data-style="outrun"] .sun{display:block;position:fixed;left:50%;bottom:30vh;width:min(62vw,540px);aspect-ratio:1;
  transform:translateX(-50%);border-radius:50%;z-index:0;
  background:linear-gradient(#fff36b 8%,#ff9d3d 45%,#ff2fd0 88%);
  -webkit-mask-image:linear-gradient(#000 52%,transparent 52% 55%,#000 55% 63%,transparent 63% 67%,#000 67% 74%,
    transparent 74% 80%,#000 80% 86%,transparent 86% 94%,#000 94%);
  mask-image:linear-gradient(#000 52%,transparent 52% 55%,#000 55% 63%,transparent 63% 67%,#000 67% 74%,
    transparent 74% 80%,#000 80% 86%,transparent 86% 94%,#000 94%);
  filter:drop-shadow(0 0 60px rgba(255,60,190,.8))}
[data-style="outrun"] .floor{display:block;position:fixed;left:0;right:0;bottom:0;height:34vh;perspective:240px;
  overflow:hidden;z-index:0;background:linear-gradient(#1b0036,#05000f);
  box-shadow:0 -2px 30px 4px rgba(255,47,208,.75)}
[data-style="outrun"] .plane{position:absolute;left:-60%;right:-60%;top:0;height:260%;transform-origin:50% 0;
  transform:rotateX(76deg);
  background-image:linear-gradient(rgba(255,47,208,.95) 2px,transparent 2px),
    linear-gradient(90deg,rgba(255,47,208,.95) 2px,transparent 2px);
  background-size:64px 64px;animation:drive .9s linear infinite}
[data-style="outrun"] .bar{background:rgba(11,0,32,.72);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line)}
[data-style="outrun"] .brand{font-style:italic;color:#27f3ff;text-shadow:0 0 10px #27f3ff}
[data-style="outrun"] #stylebtn{background:linear-gradient(90deg,#ff2fd0,#27f3ff);color:#12002b;border:0;
  font-weight:800;box-shadow:0 0 18px rgba(255,47,208,.75)}
[data-style="outrun"] #stylebtn .sw-l,[data-style="outrun"] #stylebtn #styleidx{opacity:.8}
[data-style="outrun"] .mast{text-align:center;padding:9vh 0 20vh}
[data-style="outrun"] .kicker{font:italic 400 clamp(28px,5vw,46px)/1 "Brush Script MT","Segoe Script",cursive;
  color:#ff2fd0;text-transform:none;letter-spacing:0;text-shadow:0 0 14px #ff2fd0,0 0 2px #fff;
  display:inline-block;transform:rotate(-7deg) translateY(18px);position:relative;z-index:2}
[data-style="outrun"] .mast h1{font:900 italic clamp(44px,10vw,124px)/.92 "Arial Black",Impact,sans-serif;
  text-transform:uppercase;letter-spacing:-.02em;transform:skewX(-9deg);
  background:linear-gradient(#f4fbff 0%,#9adcff 44%,#20124d 50%,#ff9ae6 53%,#ffffff 100%);
  -webkit-background-clip:text;background-clip:text;color:transparent;-webkit-text-stroke:1px rgba(255,255,255,.55);
  filter:drop-shadow(0 0 22px rgba(255,47,208,.65))}
[data-style="outrun"] .dek{color:#27f3ff;letter-spacing:.2em;text-transform:uppercase;font-size:12px;
  text-shadow:0 0 8px #27f3ff}
[data-style="outrun"] .tile,[data-style="outrun"] .panel{background:var(--panel);border:1px solid var(--line);
  border-radius:4px;backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);
  box-shadow:0 0 22px rgba(255,47,208,.3),inset 0 0 22px rgba(39,243,255,.07)}
[data-style="outrun"] .tile .v{color:#27f3ff;text-shadow:0 0 12px rgba(39,243,255,.85);font-style:italic}
@keyframes drive{to{background-position:0 64px}}

/* ---- 7/8. TOKENOPOLIS and NEBULA: glass over a live WebGL scene ---------------------- */
:root[data-style="city"]{
  --bg:#05060d;--panel:rgba(12,14,32,.56);--line:rgba(140,170,255,.2);--fg:#eef2ff;--dim:#9aa6d4;
  --cached:#2d3f8a;--uncached:#5ee1ff;--out:#ff4fd8;--warn:#ffc857;--warn-bg:rgba(255,200,87,.12);
  --c0:#5ee1ff;--c1:#ff4fd8;--c2:#ffc857;--c3:#7c5cff;--c4:#43ff9e;--c5:#ff7a45;--c6:#4f8bff;
  --c7:#f4ff5e;--c8:#ff8fb3;--c9:#3dd6c6;--c10:#c38bff;--c11:#9dff4f;--c12:#ffa6f0;--c13:#8a93b8;
  --sky:#060817;--grid:#3b6cff;--kicker:"Tokenopolis"}
:root[data-style="galaxy"]{
  --bg:#02010a;--panel:rgba(14,8,34,.5);--line:rgba(190,160,255,.2);--fg:#f3eeff;--dim:#a99cc9;
  --cached:#3a2a7a;--uncached:#b18cff;--out:#ffb86b;--warn:#ffd37a;--warn-bg:rgba(255,211,122,.12);
  --c0:#7fb2ff;--c1:#ff7ac6;--c2:#ffd37a;--c3:#8affd1;--c4:#c29bff;--c5:#ff9466;--c6:#66e0ff;
  --c7:#f7ff8a;--c8:#ff6f91;--c9:#9dffa0;--c10:#d6b3ff;--c11:#ffb3e6;--c12:#9ab8ff;--c13:#8c86a8;
  --sky:#02010a;--grid:#ffe3b0;--kicker:"Nebula"}
[data-style="city"] body,[data-style="galaxy"] body{font:14px/1.5 "Segoe UI",system-ui,-apple-system,sans-serif}
[data-style="city"] #stage,[data-style="galaxy"] #stage{display:block}
[data-style="city"] body{background:radial-gradient(ellipse at 50% 90%,#16204d,#05060d 70%) fixed}
[data-style="galaxy"] body{background:radial-gradient(ellipse at 50% 50%,#1d0f3d,#02010a 70%) fixed}
[data-style="city"] .mast,[data-style="galaxy"] .mast{min-height:64vh;display:flex;flex-direction:column;
  justify-content:flex-end;padding-bottom:22px;text-shadow:0 2px 24px rgba(0,0,0,.9);pointer-events:none}
[data-style="galaxy"] .mast{align-items:center;text-align:center;justify-content:center;min-height:78vh}
[data-style="galaxy"] .mast::before{content:"";position:absolute;left:50%;top:50%;width:min(900px,100%);height:340px;
  transform:translate(-50%,-50%);background:radial-gradient(closest-side,rgba(2,1,10,.72),transparent);z-index:-1}
[data-style="city"] .kicker,[data-style="galaxy"] .kicker{letter-spacing:.7em;color:var(--c0);font-weight:700;font-size:13px}
[data-style="galaxy"] .kicker{color:var(--c1)}
[data-style="city"] .mast h1,[data-style="galaxy"] .mast h1{font:200 clamp(40px,7.5vw,92px)/1 "Segoe UI Light",
  "Helvetica Neue",system-ui,sans-serif;letter-spacing:.01em;margin:10px 0}
[data-style="city"] .cap-city,[data-style="galaxy"] .cap-galaxy{display:block;color:var(--dim);font-size:12px;
  margin:10px 0 0;max-width:560px}
.nogl .cap-city::after,.nogl .cap-galaxy::after{content:" (WebGL is unavailable here, so the scene is off.)"}
[data-style="city"] .bar,[data-style="galaxy"] .bar{background:rgba(5,6,13,.35);border-bottom:1px solid var(--line);
  backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px)}
[data-style="city"] #stylebtn,[data-style="galaxy"] #stylebtn{background:rgba(255,255,255,.06);
  border:1px solid color-mix(in srgb,var(--c0) 60%,transparent);box-shadow:0 0 22px color-mix(in srgb,var(--c0) 35%,transparent)}
[data-style="city"] .tile,[data-style="city"] .panel,
[data-style="galaxy"] .tile,[data-style="galaxy"] .panel{background:var(--panel);border:1px solid var(--line);
  border-radius:16px;backdrop-filter:blur(16px) saturate(140%);-webkit-backdrop-filter:blur(16px) saturate(140%)}
[data-style="city"] .tile .v,[data-style="galaxy"] .tile .v{font-weight:300;font-size:28px}
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

# The style switcher and the two WebGL scenes.  Kept apart from JS so the charts' script
# reads as it did; it runs after init(), touches the charts only through measure() and
# redraw(), and does nothing at all where there is no real DOM (scripts/test_page.js).
STYLE_JS = r"""
// ---- styles: one page, nine opinions --------------------------------------------------
const STYLES = D.styles || [['clinical','Clinical']];
const ROOT = document.documentElement;
const REDUCE = (()=>{ try{ return matchMedia('(prefers-reduced-motion: reduce)').matches; }
                      catch(_){ return false; } })();

function hexRGB(s){
  s = String(s||'').trim();
  if(s[0]!=='#' || s.length<7) return [0.6, 0.6, 0.7];
  return [1,3,5].map(i => parseInt(s.slice(i,i+2), 16)/255);
}
function cssVar(n){ try{ return getComputedStyle(ROOT).getPropertyValue(n); }catch(_){ return ''; } }
function palette(){ const p = []; for(let i=0;i<14;i++) p.push(hexRGB(cssVar('--c'+i))); return p; }

// A deterministic generator, so a scene is the same scene every time the page opens.
function prng(seed){
  return ()=>{ seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    let t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0)/4294967296; };
}

// ---- the stage: tiny WebGL, no library ------------------------------------------------
// The scenes are drawn from the same payload as the charts and read the same VIEW: whatever
// the time charts have in range is lit, and everything else dims.
const Stage = (()=>{
  const cv = byId('stage');
  if(!cv || !cv.getContext) return null;
  let gl = null, ok = null, kind = null, raf = 0, born = 0, scene = null;
  let mx = 0, my = 0, tmx = 0, tmy = 0, sc = 0;
  const FOV = 50*Math.PI/180;
  const rel = t => (t - (DOM ? DOM[0] : 0))/HOUR;

  function context(){
    if(ok !== null) return ok;
    try{ gl = cv.getContext('webgl', {antialias:true, alpha:false})
              || cv.getContext('experimental-webgl'); }catch(_){ gl = null; }
    ok = !!gl;
    if(!ok) ROOT.classList.add('nogl');
    return ok;
  }
  function program(vs, fs){
    const mk = (type, src)=>{
      const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s);
      if(!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const p = gl.createProgram();
    gl.attachShader(p, mk(gl.VERTEX_SHADER, vs));
    gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(p);
    if(!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
    const u = {};
    p.u = n => (n in u) ? u[n] : (u[n] = gl.getUniformLocation(p, n));
    return p;
  }
  function upload(p, data, spec){
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW);
    const stride = spec.reduce((s, a)=>s+a[1], 0);
    return {buf, n: data.length/stride, bind(){
      gl.bindBuffer(gl.ARRAY_BUFFER, buf);
      for(let i=0;i<8;i++) gl.disableVertexAttribArray(i);
      let off = 0;
      for(const [name, k] of spec){
        const l = gl.getAttribLocation(p, name);
        if(l >= 0){ gl.enableVertexAttribArray(l);
                    gl.vertexAttribPointer(l, k, gl.FLOAT, false, stride*4, off*4); }
        off += k;
      }
    }};
  }

  // column-major 4x4
  const mul = (a, b)=>{ const o = new Float32Array(16);
    for(let i=0;i<4;i++) for(let j=0;j<4;j++){ let s = 0;
      for(let k=0;k<4;k++) s += a[k*4+j]*b[i*4+k]; o[i*4+j] = s; } return o; };
  const persp = (f, asp, n, fr)=>{ const t = 1/Math.tan(f/2), nf = 1/(n-fr);
    return new Float32Array([t/asp,0,0,0, 0,t,0,0, 0,0,(fr+n)*nf,-1, 0,0,2*fr*n*nf,0]); };
  const sub = (a, b)=>[a[0]-b[0], a[1]-b[1], a[2]-b[2]];
  const cross = (a, b)=>[a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]];
  const norm = a=>{ const l = Math.hypot(a[0], a[1], a[2]) || 1; return [a[0]/l, a[1]/l, a[2]/l]; };
  const dot = (a, b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
  function look(e, c){
    const z = norm(sub(e, c)), x = norm(cross([0,1,0], z)), y = cross(z, x);
    return new Float32Array([x[0],y[0],z[0],0, x[1],y[1],z[1],0, x[2],y[2],z[2],0,
                             -dot(x,e), -dot(y,e), -dot(z,e), 1]);
  }

  // ---- Tokenopolis: one tower per day, one row per week, stacked by model -----------
  const CITY_VS = `
attribute vec3 aP; attribute vec3 aN; attribute vec3 aC; attribute vec4 aX; attribute vec2 aU;
uniform mat4 uM; uniform vec2 uV; uniform float uG;
varying vec3 vN; varying vec3 vC; varying vec3 vW; varying vec2 vU; varying float vOn; varying float vK;
void main(){
  float g = clamp(uG*1.7 - aX.w*0.7, 0.0, 1.0);
  g = g*g*(3.0 - 2.0*g);
  vec3 p = vec3(aP.x, aP.y*g, aP.z);
  vOn = (aX.y > uV.x && aX.x < uV.y) ? 1.0 : 0.0;
  vN = aN; vC = aC; vW = p; vU = aU; vK = aX.z;
  gl_Position = uM*vec4(p, 1.0);
}`;
  const CITY_FS = `
precision mediump float;
varying vec3 vN; varying vec3 vC; varying vec3 vW; varying vec2 vU; varying float vOn; varying float vK;
uniform vec3 uSky; uniform vec3 uGrid; uniform vec3 uEye; uniform float uFog; uniform float uT; uniform float uCell;
float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7)))*43758.5453); }
void main(){
  vec3 col;
  if(vK > 0.5 && vK < 1.5){
    vec2 q = abs(fract(vW.xz/uCell) - 0.5);
    float line = 1.0 - smoothstep(0.0, 0.03, min(q.x, q.y));
    float pulse = 0.5 + 0.5*sin(uT*1.4 - length(vW.xz)*0.45);
    col = uSky*1.3 + uGrid*line*(0.25 + 0.55*pulse);
  } else {
    vec3 n = normalize(vN);
    float dif = max(dot(n, normalize(vec3(0.35, 0.9, 0.25))), 0.0);
    float rim = pow(1.0 - abs(dot(n, normalize(uEye - vW))), 3.0);
    vec3 base = vC*(0.2 + 0.5*dif) + vC*rim*0.6;
    if(vK > 1.5){
      vec2 e = abs(vU - 0.5);
      base = vC*0.5 + vC*smoothstep(0.4, 0.5, max(e.x, e.y))*1.4;
    } else {
      vec2 c = vec2(vU.x*4.0, vU.y*5.0);
      vec2 f = fract(c);
      float win = step(0.22, f.x)*step(f.x, 0.78)*step(0.28, f.y)*step(f.y, 0.72);
      float lit = step(0.4, hash(floor(c) + floor(vW.xz*3.0)));
      float tw = 0.7 + 0.3*sin(uT*2.0 + hash(floor(c))*40.0);
      base += win*lit*tw*mix(vec3(1.0, 0.93, 0.75), vC, 0.35)*0.95*vOn;
    }
    if(vOn < 0.5) base = mix(vec3(dot(base, vec3(0.3, 0.59, 0.11))), base, 0.25)*0.32;
    col = base;
  }
  float fog = 1.0 - exp(-length(vW - uEye)*uFog);
  gl_FragColor = vec4(mix(col, uSky, fog), 1.0);
}`;

  function cityMesh(){
    const S = 1.4, HW = 0.5;
    const pal = palette(), dim = hexRGB(cssVar('--c13'));
    const keys = MODELS.order || [], rank = {};
    keys.forEach((m, i)=>{ rank[m] = i; });
    const days = (MODELS.days || []).map(r=>{
      let s = 0; for(const m in r[2]) s += r[2][m];
      return {a: r[0], b: r[1], m: r[2], s};
    }).sort((p, q)=>p.a-q.a);
    const v = [];
    const vert = (p, n, c, x, u)=>v.push(p[0],p[1],p[2], n[0],n[1],n[2], c[0],c[1],c[2],
                                         x[0],x[1],x[2],x[3], u[0],u[1]);
    const quad = (ps, n, c, x, us)=>[0,1,2,0,2,3].forEach(i=>vert(ps[i], n, c, x, us[i]));
    let rows = 1, mxs = 0;
    if(days.length){
      const mid = t=>{ const d = new Date(t*1000); d.setHours(0,0,0,0); return d; };
      const first = mid(days[0].a);
      const mon = new Date(first); mon.setDate(first.getDate() - (first.getDay()+6)%7);
      days.forEach(d=>{
        const dd = mid(d.a);
        const n = Math.round((dd - mon)/864e5);
        d.col = (dd.getDay()+6)%7; d.row = Math.floor(n/7);
        rows = Math.max(rows, d.row+1); mxs = Math.max(mxs, d.s);
      });
    }
    const zo = Math.floor((rows-1)/2);
    const HMAX = clamp(Math.max(rows, 7)*S*0.5, 5, 18);
    const rnd = prng(11);
    for(const d of days){
      if(!d.s) continue;
      const cx = (d.col-3)*S, cz = (d.row-zo)*S;
      const x0 = cx-HW, x1 = cx+HW, z0 = cz-HW, z1 = cz+HW;
      const segs = [];
      let rest = d.s;
      for(const m in d.m) if(m in rank){ segs.push([rank[m], d.m[m]]); rest -= d.m[m]; }
      segs.sort((p, q)=>p[0]-q[0]);
      if(rest > 0) segs.push([99, rest]);
      const st = 0.75*d.row/rows + 0.25*rnd();
      const X = k=>[rel(d.a), rel(d.b), k, st];
      let y = 0;
      segs.forEach(([r, val], j)=>{
        const c = r < 99 ? pal[r%14] : dim;
        const y1 = y + val/(mxs||1)*HMAX;
        const U = [[0,y],[1,y],[1,y1],[0,y1]];
        quad([[x1,y,z1],[x1,y,z0],[x1,y1,z0],[x1,y1,z1]], [1,0,0], c, X(0), U);
        quad([[x0,y,z0],[x0,y,z1],[x0,y1,z1],[x0,y1,z0]], [-1,0,0], c, X(0), U);
        quad([[x0,y,z1],[x1,y,z1],[x1,y1,z1],[x0,y1,z1]], [0,0,1], c, X(0), U);
        quad([[x1,y,z0],[x0,y,z0],[x0,y1,z0],[x1,y1,z0]], [0,0,-1], c, X(0), U);
        if(j === segs.length-1)
          quad([[x0,y1,z0],[x1,y1,z0],[x1,y1,z1],[x0,y1,z1]], [0,1,0], c, X(2),
               [[0,0],[1,0],[1,1],[0,1]]);
        y = y1;
      });
    }
    const G = Math.max(rows, 7)*S*3;
    quad([[-G,0,-G],[G,0,-G],[G,0,G],[-G,0,G]], [0,1,0], [0,0,0], [-1e9, 1e9, 1, 0],
         [[0,0],[1,0],[1,1],[0,1]]);
    return {data: new Float32Array(v), R: Math.max(rows*S, 7*S), H: HMAX, S,
            cz: ((rows-1)/2 - zo)*S};
  }

  // ---- Nebula: one arm per content category, time running outward from the core -------
  const GAL_VS = `
attribute vec3 aP; attribute vec3 aC; attribute vec4 aX;
uniform mat4 uM; uniform vec2 uV; uniform float uPx; uniform float uG; uniform float uT;
varying vec3 vC;
void main(){
  float on = (aX.y > uV.x && aX.x < uV.y) ? 1.0 : 0.0;
  float bg = step(0.5, aX.w);
  vec3 p = aP;
  float g = clamp(uG*1.5 - length(p.xz)*0.035, 0.0, 1.0);
  g = g*g*(3.0 - 2.0*g);
  float a = (1.0 - g)*2.6*(1.0 - bg);
  float c = cos(a), s = sin(a);
  p.xz = mat2(c, -s, s, c)*p.xz*mix(1.0, g, 1.0 - bg);
  vec4 q = uM*vec4(p, 1.0);
  gl_Position = q;
  float tw = 0.75 + 0.25*sin(uT*2.5 + aP.x*13.0 + aP.z*7.0);
  float k = mix(mix(0.55, 1.0, on), tw, bg);
  gl_PointSize = clamp(aX.z*uPx/q.w*k, 1.0, 42.0);
  vC = aC*mix(mix(0.16, 1.0, on), tw, bg);
}`;
  const GAL_FS = `
precision mediump float;
varying vec3 vC;
void main(){
  vec2 d = gl_PointCoord - 0.5;
  float r2 = dot(d, d)*4.0;
  if(r2 > 1.0) discard;
  gl_FragColor = vec4(vC*exp(-r2*3.2), 1.0);
}`;

  function galaxyMesh(){
    let rows = CATS.series || [], order = CATS.order || [], bucket = CATS.bucket || 3600;
    if(!rows.length){                 // nothing tokenized: spin the models instead
      rows = (MODELS.days || []).map(r=>[r[0], r[2]]); order = MODELS.order || []; bucket = DAY;
    }
    const pal = palette(), idx = {};
    order.forEach((k, i)=>{ idx[k] = i; });
    const K = Math.max(1, Math.min(order.length || 1, 14));
    const span = DOM ? Math.max(1, DOM[1]-DOM[0]) : 1;
    const rnd = prng(7);
    const gs = ()=>(rnd()+rnd()+rnd()-1.5)*2;
    let plan = 0;
    for(const r of rows) for(const k in r[1]) plan += 1 + Math.floor(Math.log2(1 + r[1][k]));
    const scale = Math.min(1, 40000/(plan || 1));
    const R = 11, v = [];
    const star = (x, y, z, c, t0, t1, size, bg)=>v.push(x, y, z, c[0], c[1], c[2], t0, t1, size, bg);
    for(const r of rows){
      const t = r[0], p = DOM ? clamp((t + bucket/2 - DOM[0])/span, 0, 1) : 0.5;
      const t0 = rel(t), t1 = rel(t + bucket);
      for(const k in r[1]){
        const n = r[1][k];
        if(!(n > 0)) continue;
        const i = (k in idx) ? idx[k] : 13;
        const c = pal[i%14].map(x=>x*0.42);
        const cnt = Math.max(1, Math.round((1 + Math.floor(Math.log2(1 + n)))*scale));
        const spread = 0.06 + 0.015*Math.log10(n + 1);
        for(let j=0;j<cnt;j++){
          const rr = 1.2 + p*R + gs()*0.22;
          const th = 2*Math.PI*(i%K)/K + p*2.3*Math.PI + gs()*spread;
          star(Math.cos(th)*rr, gs()*0.16*(1.3 - 0.7*p), Math.sin(th)*rr, c, t0, t1,
               0.035 + 0.02*Math.log10(n + 1)*rnd(), 0);
        }
      }
    }
    const warm = hexRGB(cssVar('--grid'));
    for(let j=0;j<2600;j++){         // the core: always lit, it belongs to no range
      const rr = Math.abs(gs())*0.9;
      const th = rnd()*2*Math.PI;
      const b = 0.05 + 0.1*rnd();
      star(Math.cos(th)*rr, gs()*0.28*Math.max(0, 1 - rr/2), Math.sin(th)*rr,
           warm.map(x=>x*b), -1e9, 1e9, 0.05 + 0.05*rnd(), 0);
    }
    for(let j=0;j<2400;j++){         // the sky behind it
      const u = rnd()*2 - 1, th = rnd()*2*Math.PI, rr = 80 + rnd()*60, q = Math.sqrt(1 - u*u);
      const b = 0.25 + 0.6*rnd()*rnd();
      star(Math.cos(th)*q*rr, u*rr, Math.sin(th)*q*rr, [b*0.9, b*0.92, b], -1e9, 1e9,
           0.18 + 0.3*rnd(), 1);
    }
    return {data: new Float32Array(v), R};
  }

  function build(k){
    if(k === 'city'){
      const m = cityMesh(), p = program(CITY_VS, CITY_FS);
      return Object.assign(m, {p, k, mesh: upload(p, m.data,
        [['aP',3], ['aN',3], ['aC',3], ['aX',4], ['aU',2]])});
    }
    const m = galaxyMesh(), p = program(GAL_VS, GAL_FS);
    return Object.assign(m, {p, k, mesh: upload(p, m.data, [['aP',3], ['aC',3], ['aX',4]])});
  }

  function frame(now){
    raf = requestAnimationFrame(frame);
    if(document.hidden || !scene) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.round(innerWidth*dpr), h = Math.round(innerHeight*dpr);
    if(cv.width !== w || cv.height !== h){ cv.width = w; cv.height = h; }
    gl.viewport(0, 0, w, h);
    const T = REDUCE ? 0 : now/1000;
    const G = REDUCE ? 1 : Math.min(1, (now - born)/2200);
    const max = Math.max(1, document.documentElement.scrollHeight - innerHeight);
    sc += (clamp(scrollY/max, 0, 1) - sc)*0.08;
    mx += (tmx - mx)*0.05; my += (tmy - my)*0.05;
    const sky = hexRGB(cssVar('--sky'));
    gl.clearColor(sky[0], sky[1], sky[2], 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    const asp = w/h, wide = Math.pow(Math.max(1, 1.3/asp), 0.7);
    const V = VIEW ? [rel(VIEW[0]), rel(VIEW[1])] : [-1e9, 1e9];
    const p = scene.p;
    gl.useProgram(p);
    scene.mesh.bind();
    let eye, tgt;
    if(scene.k === 'city'){
      const az = 0.7 + T*0.045 + mx*0.6, el = 0.3 + sc*0.85 - my*0.12;
      const rad = (scene.R*0.9 + 7)*wide*(1.15 - sc*0.25);
      tgt = [0, scene.H*0.18, scene.cz];
      eye = [tgt[0] + Math.cos(az)*Math.cos(el)*rad, tgt[1] + Math.sin(el)*rad,
             tgt[2] + Math.sin(az)*Math.cos(el)*rad];
      gl.enable(gl.DEPTH_TEST); gl.disable(gl.BLEND);
      gl.uniform3fv(p.u('uSky'), sky);
      gl.uniform3fv(p.u('uGrid'), hexRGB(cssVar('--grid')));
      gl.uniform3fv(p.u('uEye'), eye);
      gl.uniform1f(p.u('uFog'), 0.9/(rad*2.2));
      gl.uniform1f(p.u('uCell'), scene.S);
    } else {
      const az = T*0.03 + mx*0.7, el = 1.05 - sc*0.8 - my*0.15;
      const rad = scene.R*2.5*wide;
      tgt = [0, 0, 0];
      eye = [Math.cos(az)*Math.cos(el)*rad, Math.sin(el)*rad, Math.sin(az)*Math.cos(el)*rad];
      gl.disable(gl.DEPTH_TEST); gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
      gl.uniform1f(p.u('uPx'), h/(2*Math.tan(FOV/2)));
    }
    // The city is lifted in frame so it stands above the masthead rather than behind it.
    const P = persp(FOV, asp, 0.1, 500);
    if(scene.k === 'city') P[9] = -0.32;          // off-axis: NDC y moves up by this much
    gl.uniformMatrix4fv(p.u('uM'), false, mul(P, look(eye, tgt)));
    gl.uniform2f(p.u('uV'), V[0], V[1]);
    gl.uniform1f(p.u('uG'), G);
    gl.uniform1f(p.u('uT'), T);
    gl.drawArrays(scene.k === 'city' ? gl.TRIANGLES : gl.POINTS, 0, scene.mesh.n);
  }

  addEventListener('pointermove', e=>{
    if(e.pointerType !== 'mouse') return;
    tmx = e.clientX/innerWidth*2 - 1; tmy = e.clientY/innerHeight*2 - 1;
  });

  return {
    start(k){
      if(!context()) return;
      try{
        if(scene && scene.mesh) gl.deleteBuffer(scene.mesh.buf);
        scene = build(k); kind = k; born = performance.now();
        if(!raf) raf = requestAnimationFrame(frame);
      }catch(err){
        scene = null; ROOT.classList.add('nogl');
        console.warn('token-counter: scene off', err);
      }
    },
    stop(){
      if(raf) cancelAnimationFrame(raf);
      raf = 0; kind = null;
      if(scene && scene.mesh && gl) gl.deleteBuffer(scene.mesh.buf);
      scene = null;
    },
  };
})();

// ---- the switch -----------------------------------------------------------------------
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
  if(Stage){ if(id === 'city' || id === 'galaxy') Stage.start(id); else Stage.stop(); }
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
<canvas id="stage" aria-hidden="true"></canvas>
<div class="deco" aria-hidden="true"><div class="sun"></div><div class="floor"><div class="plane"></div></div><div class="scan"></div><div class="wipe"></div></div>
<nav class="bar"><div class="brand">token-counter</div><div><span class="keys">[ ]</span><button id="stylebtn" type="button" aria-label="Cycle the page style"><span class="sw-l">Style</span><b id="stylename">{first[1]}</b><span id="styleidx">1/{len(STYLES)}</span><span class="sw-go" aria-hidden="true">&#8635;</span></button></div></nav>
<div class="wrap">

<header class="mast">
  <div class="kicker"></div>
  <h1>Codex Token Report</h1>
  <p class="dek">{dek}</p>
  <p class="caption cap-city">One tower per day, one row per week, Monday at the left; each tower is stacked by
    the model charged, in the daily chart's colours. Towers in the charts' visible range are lit &mdash; drag
    or zoom a chart below to scan the city. Scroll to lift the camera.</p>
  <p class="caption cap-galaxy">One spiral arm per content category, in the composition pie's colours; time runs
    outward from the core, and each hour of content adds stars in proportion to the log of its tokens.
    Stars outside the charts' visible range fade.</p>
  <dl class="tblock"><div><dt>Project</dt><dd>Codex usage</dd></div><div><dt>Drawn by</dt><dd>token-counter</dd></div>
    <div><dt>Date</dt><dd>{time.strftime('%Y-%m-%d')}</dd></div><div><dt>Scale</dt><dd>1 tok : 1 tok</dd></div>
    <div><dt>Sheet</dt><dd>1 of 1</dd></div></dl>
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
