"""Deltakura stylesheet, from the token system in ops/design/site_redesign_v1.md.

Colour tokens (light default, dark via prefers-color-scheme):
  漆喰 shikkui  page background        墨 sumi       text, strong lines
  なまこ namako secondary text, icons  薄墨 usuzumi  faint / deleted state
  青磁 seiji    data marks, links      朱印 shuin    ONLY the 保管済 stamp and one CTA

Type: 明朝 display (page titles and the hero line only), gothic body, a
monospace data face with tabular figures for every number. Scale 13 / 16 /
20 / 28 / 44 px, weights 400 and 700.

No webfont, no external asset, no inline style anywhere (CSP style-src 'self'):
every width a chart needs is a class (.w0 .. .w100) generated below.
"""

from __future__ import annotations

_WIDTHS = "".join(f".w{i}{{width:{i}%}}" for i in range(101))

CSS = """/* Deltakura. One stylesheet; no webfont, no external asset, no tracker. */
:root{
  color-scheme:light dark;
  --shikkui:#F2F3F0; --sumi:#1D2227; --namako:#3C4852; --usuzumi:#8B9298;
  --seiji:#3E7F74; --shuin:#C23B2A;
  /* derived neutrals and states */
  --surface:#FAFBF9; --rule:#D3D8D5; --rule-soft:#E3E6E3; --wash:#E4EDEA;
  --on-shuin:#FFFFFF; --focus:#3E7F74;
  --h1:#E4EDEA; --h2:#CCE0DB; --h3:#A9CEC6; --h4:#7FB5AA; --h5:#559A8D; --h6:#2F6A60;
  --h-ink:#1D2227; --h5-ink:#1D2227; --h-ink-strong:#FFFFFF;
  --display:"Hiragino Mincho ProN","Yu Mincho","YuMincho","BIZ UDPMincho",serif;
  --body:"Hiragino Sans","Yu Gothic UI","Yu Gothic","BIZ UDPGothic","Meiryo",system-ui,sans-serif;
  --data:ui-monospace,"Cascadia Mono","Consolas","SF Mono","BIZ UDGothic",monospace;
  --r:6px;
}
@media (prefers-color-scheme:dark){
  :root{
    --shikkui:#15181B; --sumi:#E7E9E6; --namako:#A9B4BD; --usuzumi:#6E767D;
    --seiji:#6FB3A6; --shuin:#E0634F;
    --surface:#1B1F23; --rule:#343B41; --rule-soft:#262B30; --wash:#1E2A28;
    --on-shuin:#15181B; --focus:#6FB3A6;
    --h1:#1D2826; --h2:#223A35; --h3:#2B5049; --h4:#3A6C63; --h5:#529287; --h6:#6FB3A6;
    --h-ink:#E7E9E6; --h5-ink:#15181B; --h-ink-strong:#15181B;
  }
}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0; background:var(--shikkui); color:var(--sumi); font-family:var(--body);
  font-size:16px; line-height:1.8; font-weight:400; overflow-wrap:anywhere}
:lang(ja){word-break:auto-phrase; line-break:strict}
.wrap{max-width:1080px; margin:0 auto; padding:0 16px}
@media(min-width:720px){.wrap{padding:0 32px}}
a{color:inherit; text-decoration:underline; text-decoration-color:var(--seiji);
  text-decoration-thickness:2px; text-underline-offset:4px}
a:hover{text-decoration-thickness:3px}
:focus-visible{outline:2px solid var(--focus); outline-offset:3px; border-radius:2px}
.num,.data{font-family:var(--data); font-variant-numeric:tabular-nums; letter-spacing:0}
.vh{position:absolute!important; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); white-space:nowrap}
.ic{width:24px; height:24px; flex:none; color:var(--namako)}
b,strong,h1,h2,h3{font-weight:700}

/* header */
header.site{border-bottom:1px solid var(--rule)}
header.site .wrap{display:flex; flex-wrap:wrap; align-items:center; gap:8px 24px; padding-top:12px; padding-bottom:12px}
.brand{display:inline-flex; align-items:center; gap:8px; text-decoration:none; color:var(--sumi); font-weight:700; font-size:16px; line-height:1.2}
.brand .ic{color:var(--sumi)}
.brand small{font-weight:400; font-size:13px; color:var(--namako); margin-left:2px}
nav.site{display:flex; flex-wrap:wrap; gap:4px 20px; font-size:13px; margin-left:auto}
nav.site a{text-decoration:none; color:var(--namako); padding:6px 0}
nav.site a:hover{color:var(--sumi)}
nav.site a[aria-current]{color:var(--sumi); text-decoration:underline; text-decoration-color:var(--seiji); text-decoration-thickness:2px; text-underline-offset:6px}
nav.site .lang{border:1px solid var(--rule); border-radius:var(--r); padding:5px 10px}
@media(max-width:719px){nav.site{margin-left:0; width:100%; gap:2px 16px}}

main{padding:40px 0 72px}
.crumb{font-size:13px; color:var(--namako); margin:0 0 8px}
h1.title{font-family:var(--display); font-size:28px; line-height:1.4; margin:0 0 8px; letter-spacing:.02em}
.sub{font-size:16px; color:var(--namako); margin:0 0 24px}
h2{font-size:20px; line-height:1.5; margin:0 0 16px; display:flex; align-items:center; gap:10px}
h3{font-size:16px; line-height:1.5; margin:0}
p{margin:0 0 12px}
section.sec{margin-top:64px}
.fine{font-size:13px; color:var(--namako); line-height:1.7}
.fine .ic{width:16px; height:16px; vertical-align:-3px; margin-right:4px}

/* hero */
.hero h1{font-family:var(--display); font-weight:700; font-size:28px; line-height:1.35; letter-spacing:.04em; margin:8px 0 12px}
.hero .sub{font-size:16px; margin-bottom:28px}
@media(min-width:720px){.hero h1{font-size:44px; margin-top:16px} .hero .sub{font-size:20px}}

/* buttons */
.btn,button.intent{display:inline-flex; align-items:center; justify-content:center; gap:8px; min-height:44px;
  padding:0 18px; border:1.5px solid var(--sumi); border-radius:var(--r); background:transparent;
  color:var(--sumi); font:inherit; font-size:16px; font-weight:700; line-height:1.2; text-decoration:none; cursor:pointer}
.btn:hover,button.intent:hover{background:var(--wash); color:var(--sumi)}
.btn.primary{background:var(--shuin); border-color:var(--shuin); color:var(--on-shuin)}
.btn.primary:hover{filter:brightness(1.07); background:var(--shuin); color:var(--on-shuin)}
button.intent[disabled]{border-color:var(--rule); color:var(--namako); background:transparent; cursor:default}
.act{display:flex; flex-wrap:wrap; align-items:center; gap:8px 16px; margin-top:16px}

/* the 40-day strip */
.strip{margin:0; padding:20px 16px 16px; background:var(--surface); border:1px solid var(--rule); border-radius:var(--r)}
@media(min-width:720px){.strip{padding:28px 28px 20px}}
.st-grid{display:grid; grid-template-columns:1fr; gap:4px 20px; align-items:center}
@media(min-width:720px){.st-grid{grid-template-columns:150px 1fr 118px}}
.st-lab-row{display:flex; align-items:center; gap:10px; line-height:1.35; margin-top:8px}
.st-lab-row b{display:block; font-size:16px}
.st-lab-row small{display:block; font-size:13px; color:var(--namako)}
.st-kura-lab .ic{color:var(--seiji)}
.st-plot svg{display:block; overflow:visible}
.st-end{display:flex; justify-content:flex-end}
@media(max-width:719px){.st-end:empty{display:none} .st-end{justify-content:flex-start; margin-top:4px}}
.c-src{fill:var(--namako)}
.c-next{fill:var(--namako); stroke:var(--usuzumi); stroke-width:1; stroke-dasharray:2 2}
.c-next.f1{fill-opacity:.12}.c-next.f2{fill-opacity:.28}.c-next.f3{fill-opacity:.45}.c-next.f4{fill-opacity:.62}.c-next.f5{fill-opacity:.8}
.c-gone{fill:none; stroke:var(--usuzumi); stroke-width:1; stroke-dasharray:3 2}
.c-kura{fill:var(--seiji)}
.c-kura:hover{fill:var(--sumi)}
.st-br{stroke:var(--usuzumi); stroke-width:1}
.st-lab{font-family:var(--body); font-size:13px; fill:var(--namako)}
.st-bl{paint-order:stroke; stroke:var(--surface); stroke-width:6px; stroke-linejoin:round}
.st-date{font-family:var(--data); font-size:13px; fill:var(--namako)}
.stamp{display:inline-flex; flex-direction:column; align-items:center; justify-content:center;
  color:var(--shuin); border:2px solid var(--shuin); border-radius:4px; padding:6px 10px 5px;
  transform:rotate(-4deg); line-height:1.15; box-shadow:inset 0 0 0 2px var(--surface), inset 0 0 0 3px var(--shuin)}
.stamp-k{font-family:var(--display); font-weight:700; font-size:20px; letter-spacing:.12em}
.stamp-n{font-family:var(--data); font-variant-numeric:tabular-nums; font-weight:700; font-size:13px}
.stamp-n small{font-family:var(--body); font-weight:700}
.strip figcaption{margin-top:16px; padding-top:12px; border-top:1px solid var(--rule-soft); font-size:13px; color:var(--namako)}
.st-cap{display:block; color:var(--sumi); font-size:16px; margin-bottom:6px}
.st-legend{list-style:none; margin:0; padding:0; display:flex; flex-wrap:wrap; gap:4px 18px}
.st-legend li{display:inline-flex; align-items:center; gap:6px}
.sw{display:inline-block; width:10px; height:14px; border-radius:1px}
.sw-src{background:var(--namako)}
.sw-next{border:1px dashed var(--usuzumi); background:var(--rule-soft)}
.sw-gone{border:1px dashed var(--usuzumi)}
.sw-kura{background:var(--seiji)}
@media (prefers-reduced-motion:no-preference){
  .st-kura svg{animation:reveal 1.1s cubic-bezier(.3,.7,.3,1) both}
  .stamp{animation:press .32s 1.05s ease-out both}
}
@keyframes reveal{from{clip-path:inset(0 100% 0 0)}to{clip-path:inset(0 0 0 0)}}
@keyframes press{from{opacity:0; transform:rotate(-4deg) scale(1.35)}to{opacity:1; transform:rotate(-4deg) scale(1)}}

/* facts */
.facts{list-style:none; margin:24px 0 0; padding:0; display:grid; grid-template-columns:1fr; gap:12px}
@media(min-width:720px){.facts{grid-template-columns:repeat(3,1fr); gap:24px}}
.facts li{display:flex; align-items:center; gap:12px}
.facts .v{display:block; font-family:var(--data); font-variant-numeric:tabular-nums; font-weight:700; font-size:20px; line-height:1.3}
.facts .k{display:block; font-size:13px; color:var(--namako); line-height:1.4}
@media(min-width:720px){.facts .v{font-size:28px}}

/* cards */
.cards{display:grid; grid-template-columns:1fr; gap:16px}
@media(min-width:720px){.cards{grid-template-columns:repeat(3,1fr)}}
.card{display:flex; flex-direction:column; gap:12px; background:var(--surface); border:1px solid var(--rule); border-radius:var(--r); padding:20px}
.card-h{display:flex; align-items:center; gap:10px}
.card p{margin:0}
.card .grow{flex:1}
.card .act{margin-top:0}
.card.soon{background:transparent; border-style:dashed}
.mini{display:block; width:100%; height:64px}
.mini.hm{height:88px}
.sp rect{fill:var(--seiji)}
.hm .h0{fill:var(--rule-soft)}
.soon-stamp{display:inline-block; align-self:flex-start; font-family:var(--display); font-weight:700; font-size:20px;
  letter-spacing:.14em; color:var(--namako); border:2px dashed var(--usuzumi); border-radius:4px; padding:4px 12px; transform:rotate(-3deg)}

/* flow (a true sequence) */
.flow{list-style:none; margin:0; padding:0; display:grid; grid-template-columns:1fr; gap:0}
.flow li{position:relative; display:flex; align-items:center; gap:12px; padding:14px 16px;
  background:var(--surface); border:1px solid var(--rule); border-radius:var(--r)}
.flow li + li{margin-top:28px}
.flow li + li::before{content:""; position:absolute; left:28px; top:-22px; width:2px; height:16px; background:var(--usuzumi)}
.flow li + li::after{content:""; position:absolute; left:24px; top:-10px; border:5px solid transparent; border-top:6px solid var(--usuzumi)}
.flow .ic{color:var(--seiji)}
.flow b{display:block; font-size:16px; line-height:1.4}
.flow small{display:block; font-size:13px; color:var(--namako); line-height:1.4}
.flow .out{flex-wrap:wrap}
.chips{display:flex; flex-wrap:wrap; gap:6px; width:100%}
.chip{display:inline-flex; align-items:center; gap:6px; font-size:13px; line-height:1.4; border:1px solid var(--rule); border-radius:var(--r); padding:4px 10px; background:var(--shikkui)}
.chip .ic{width:16px; height:16px}
@media(min-width:900px){
  .flow{grid-template-columns:repeat(4,1fr) 1.35fr; gap:0 28px; align-items:stretch}
  .flow li{flex-direction:column; align-items:flex-start; text-align:left}
  .flow li + li{margin-top:0}
  .flow li + li::before{left:-22px; top:50%; width:16px; height:2px}
  .flow li + li::after{left:-10px; top:calc(50% - 4px); border:5px solid transparent; border-left:6px solid var(--usuzumi)}
}

/* しないこと tiles */
.tiles{list-style:none; margin:0; padding:0; display:grid; grid-template-columns:repeat(2,1fr); gap:12px}
@media(min-width:720px){.tiles{grid-template-columns:repeat(5,1fr)}}
.tiles.t3{grid-template-columns:repeat(2,1fr)}
@media(min-width:720px){.tiles.t3{grid-template-columns:repeat(3,1fr)}}
.tiles li{display:flex; flex-direction:column; gap:8px; padding:16px; border:1px solid var(--rule); border-radius:var(--r); font-size:13px; line-height:1.6}
.tiles li b{font-size:16px; line-height:1.4}
.tiles .ic{width:28px; height:28px}

/* developer doors */
.doors{list-style:none; margin:0; padding:0; display:grid; grid-template-columns:1fr; gap:12px}
@media(min-width:720px){.doors{grid-template-columns:repeat(3,1fr)}}
.doors li{display:flex; gap:12px; align-items:flex-start}
.doors a{font-weight:700}
.doors small{display:block; font-size:13px; color:var(--namako); line-height:1.5}
.doors code,.mono{font-family:var(--data); font-size:13px}

.operator{display:flex; flex-wrap:wrap; gap:6px 20px; align-items:center; font-size:16px}

/* labels (data caveats) */
.labels{list-style:none; margin:12px 0 0; padding:0; display:flex; flex-wrap:wrap; gap:6px}
.labels li{font-size:13px; line-height:1.5; border:1px solid var(--rule); border-left:3px solid var(--seiji); border-radius:2px; padding:3px 10px; background:var(--surface)}

/* numbers row on data pages */
.kpis{list-style:none; margin:20px 0 0; padding:0; display:grid; grid-template-columns:repeat(2,1fr); gap:12px 24px}
@media(min-width:720px){.kpis{grid-template-columns:repeat(4,1fr)}}
.kpis .v{display:block; font-family:var(--data); font-variant-numeric:tabular-nums; font-weight:700; font-size:20px; line-height:1.3}
.kpis .k{display:block; font-size:13px; color:var(--namako)}
@media(min-width:720px){.kpis .v{font-size:28px}}

/* figures */
figure.fig{margin:0; padding:20px 16px 12px; background:var(--surface); border:1px solid var(--rule); border-radius:var(--r)}
figure.fig svg{display:block; overflow:visible}
figure.fig figcaption{font-size:13px; color:var(--namako); margin-top:8px}
.bx-wh,.bx-tk{stroke:var(--namako); stroke-width:1.5}
.bx-ax{stroke:var(--rule); stroke-width:1}
.bx-box{fill:var(--wash); stroke:var(--seiji); stroke-width:2}
.bx-med{stroke:var(--sumi); stroke-width:3}
.bx-ml{font-family:var(--data); font-weight:700; font-size:13px; fill:var(--sumi)}
.bx-tl{font-family:var(--data); font-size:13px; fill:var(--namako)}
.five{list-style:none; margin:12px 0 0; padding:0; display:grid; grid-template-columns:repeat(auto-fit,minmax(120px,1fr)); gap:6px 16px}
.five .k{display:block; font-size:13px; color:var(--namako)}
.five .v{font-family:var(--data); font-variant-numeric:tabular-nums; font-size:16px}
.dc-bar{fill:var(--seiji)}
.dc-bar:hover{fill:var(--sumi)}
.dc-grid{stroke:var(--rule); stroke-width:1; stroke-dasharray:2 3}
.dc-base{stroke:var(--namako); stroke-width:1}
.dc-yl,.dc-xl{font-family:var(--data); font-size:13px; fill:var(--namako)}
.dc-sub{font-family:var(--body)}

/* horizontal bars */
.hbars{list-style:none; margin:0; padding:0; display:grid; gap:6px}
.hbars li{display:grid; grid-template-columns:minmax(5.5em,9em) 1fr auto; align-items:center; gap:10px; font-size:13px; line-height:1.4}
@media(min-width:720px){.hbars li{grid-template-columns:12em 1fr 8em; font-size:16px}}
.hb-bar{display:block; height:12px; background:var(--rule-soft); border-radius:2px; overflow:hidden}
.hb-fill{display:block; height:100%; background:var(--seiji)}
.hb-v{font-family:var(--data); font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap}
.hb-v small{display:inline-block; min-width:4.2em; color:var(--namako); font-size:13px; margin-left:6px}
.two{display:grid; grid-template-columns:1fr; gap:32px}
@media(min-width:900px){.two{grid-template-columns:1fr 1fr}}
.two h3{margin-bottom:12px}

/* tables */
.tablewrap{overflow-x:auto; border:1px solid var(--rule); border-radius:var(--r); background:var(--surface); margin:12px 0}
table{border-collapse:collapse; width:100%; font-size:13px}
caption{text-align:left; padding:10px 12px; color:var(--namako); font-size:13px; border-bottom:1px solid var(--rule)}
th,td{padding:8px 10px; text-align:left; border-bottom:1px solid var(--rule-soft); white-space:nowrap}
thead th{font-weight:700; color:var(--namako); background:var(--surface)}
tbody tr:last-child td,tbody tr:last-child th{border-bottom:0}
td.n,th.n{text-align:right; font-family:var(--data); font-variant-numeric:tabular-nums}
code{font-family:var(--data); font-size:.92em}

/* heatmap */
table.heat{font-size:13px}
table.heat th[scope=row]{position:sticky; left:0; background:var(--surface); font-weight:700; z-index:1; white-space:normal; min-width:9em; max-width:13em; line-height:1.4}
table.heat thead th{font-family:var(--data); font-weight:400; text-align:right}
table.heat td{padding:0; border:1px solid var(--surface); text-align:right; font-family:var(--data); font-variant-numeric:tabular-nums}
table.heat td a,table.heat td span.x{display:block; padding:8px 8px; min-width:4.2em; text-decoration:none; color:inherit}
table.heat td a:hover{text-decoration:underline; text-decoration-color:currentColor}
table.heat td a:focus-visible{outline-offset:-3px}
table.heat .tot{padding:6px 10px; min-width:8em; font-weight:700}
table.heat tfoot td,table.heat tfoot th{font-weight:700; padding:8px; border-top:1px solid var(--rule); background:var(--surface)}
.tbar{display:block; height:4px; margin-top:4px; background:var(--rule-soft); border-radius:2px; overflow:hidden}
.tbar i{display:block; height:100%; background:var(--seiji)}
.h0{background:transparent; color:var(--usuzumi)}
.h1{background:var(--h1); color:var(--h-ink)} .h2{background:var(--h2); color:var(--h-ink)}
.h3{background:var(--h3); color:var(--h-ink)} .h4{background:var(--h4); color:var(--h-ink)}
.h5{background:var(--h5); color:var(--h5-ink)} .h6{background:var(--h6); color:var(--h-ink-strong)}
.hm .h1{fill:var(--h1)} .hm .h2{fill:var(--h2)} .hm .h3{fill:var(--h3)} .hm .h4{fill:var(--h4)} .hm .h5{fill:var(--h5)} .hm .h6{fill:var(--h6)}
.scale{list-style:none; margin:8px 0 0; padding:0; display:flex; flex-wrap:wrap; gap:0; font-size:13px; font-family:var(--data)}
.scale li{padding:2px 8px; min-width:4.5em; text-align:center}

/* year chips */
.yearnav{display:flex; flex-wrap:wrap; gap:4px; margin:16px 0 0}
.yearnav a,.yearnav span{font-family:var(--data); font-size:13px; border:1px solid var(--rule); border-radius:4px; padding:3px 8px; text-decoration:none; color:var(--namako); background:var(--surface)}
.yearnav a:hover{border-color:var(--seiji); color:var(--sumi)}
.yearnav span[aria-current]{background:var(--sumi); border-color:var(--sumi); color:var(--shikkui)}

/* details (privacy facts, long tables) */
details{border-top:1px solid var(--rule)}
details:last-of-type{border-bottom:1px solid var(--rule)}
summary{cursor:pointer; list-style:none; padding:14px 0; font-weight:700; display:flex; align-items:center; gap:10px}
summary::-webkit-details-marker{display:none}
summary::after{content:""; margin-left:auto; width:8px; height:8px; border-right:1.5px solid var(--namako); border-bottom:1.5px solid var(--namako); transform:rotate(45deg); transition:transform .15s}
details[open] summary::after{transform:rotate(225deg)}
details > :not(summary){margin-left:0}
.facts-list{margin:0 0 16px; padding-left:1.2em}
.facts-list li{margin:4px 0}
details .tablewrap{margin-top:0}

/* rhythm between blocks */
.cards + .fine{margin-top:12px}
section.sec + .fine,details + .fine{margin-top:32px}
.doors + details{margin-top:28px}
h1.title + .tiles{margin-top:24px}
.labels + details{margin-top:20px}
.act + .fine{margin-top:8px}
h1.lone{padding:48px 0 96px; margin:0}

/* footer */
footer.site{border-top:1px solid var(--rule); padding:28px 0 40px; font-size:13px; color:var(--namako); line-height:1.7}
footer.site a{color:var(--namako)}
footer.site a:hover{color:var(--sumi)}
.attrib{margin:0 0 16px; padding:12px 14px; border:1px solid var(--rule); border-radius:var(--r); overflow-wrap:anywhere}
.attrib b{display:block; color:var(--sumi)}
.foot-links{display:flex; flex-wrap:wrap; gap:4px 18px; margin:8px 0}
.skip{position:absolute; left:-9999px}
.skip:focus{position:static; display:inline-block; padding:8px 12px; background:var(--sumi); color:var(--shikkui)}

/* stacked daily bars (articles): celadon for the main series, then neutrals */
.sd-a{fill:var(--seiji)} .sd-b{fill:var(--h3)} .sd-c{fill:var(--namako)} .sd-d{fill:var(--usuzumi)} .sd-e{fill:var(--h5)} .sd-f{fill:var(--rule)}
.dc rect[class^="sd-"]{stroke:var(--surface); stroke-width:.5}
.sw.sd-a{background:var(--seiji)} .sw.sd-b{background:var(--h3)} .sw.sd-c{background:var(--namako)} .sw.sd-d{background:var(--usuzumi)} .sw.sd-e{background:var(--h5)} .sw.sd-f{background:var(--rule)}
.sd-mean{stroke:var(--sumi); stroke-width:1; stroke-dasharray:5 3}
.sd-ml{font-family:var(--data); font-size:13px; fill:var(--sumi); paint-order:stroke; stroke:var(--surface); stroke-width:4px; stroke-linejoin:round}
.sd-legend{margin-top:10px; font-size:13px}

/* articles: a quiet reading column; the figures are the loud part */
.post{max-width:720px}
.post-meta{display:flex; flex-wrap:wrap; align-items:center; gap:4px 16px; font-size:13px; color:var(--namako); margin:4px 0 24px}
.post-meta .ic{width:18px; height:18px; color:var(--seiji)}
.post-meta time{font-family:var(--data); font-variant-numeric:tabular-nums; color:var(--sumi)}
.lede{font-size:20px; line-height:1.7; margin:0 0 40px; padding:2px 0 2px 16px; border-left:3px solid var(--seiji)}
.prose{line-height:1.9}
.prose > p{margin:0 0 20px}
.prose h2{font-size:20px; margin:48px 0 16px; padding-top:20px; border-top:1px solid var(--rule)}
.prose h3{font-size:16px; margin:32px 0 12px}
.prose h4{font-size:16px; margin:24px 0 8px; color:var(--namako)}
.prose ul,.prose ol{margin:0 0 20px; padding-left:1.4em}
.prose li{margin:4px 0}
.prose li > ul,.prose li > ol{margin:4px 0 0}
.prose blockquote{margin:0 0 20px; padding:2px 16px; border-left:3px solid var(--rule); color:var(--namako)}
.prose blockquote p{margin:0 0 8px}
.prose pre{overflow-x:auto; margin:0 0 20px; padding:12px 14px; background:var(--surface); border:1px solid var(--rule); border-radius:var(--r); font-size:13px; line-height:1.6}
.prose hr{border:0; border-top:1px solid var(--rule); margin:40px 0}
.prose .tablewrap{margin:0 0 24px}
.prose td.c,.prose th.c{text-align:center}
.prose figure.chart,.prose .strip,.prose .chart{margin:8px 0 32px}
.prose .chart .tablewrap{margin:0}
.prose figure.chart details{margin-top:12px}
.fig-src{display:block; margin-top:2px; font-size:13px; color:var(--namako)}
.post-end{margin-top:56px}
.post-end h2{font-size:16px}
.src-list{margin:0; padding-left:1.2em; font-size:13px; line-height:1.7; overflow-wrap:anywhere}
.post-act{margin-top:40px; padding-top:24px; border-top:1px solid var(--rule)}
.post-act .act{margin-top:0}
.post-act .btn .ic{width:18px; height:18px; color:currentColor}
.post-act .fine{margin:6px 0 0}

/* article lists: a ledger of dated entries */
.posts{list-style:none; margin:0; padding:0; border-top:1px solid var(--rule)}
.posts li{display:grid; grid-template-columns:1fr; gap:2px 24px; padding:16px 0; border-bottom:1px solid var(--rule-soft)}
@media(min-width:720px){.posts li{grid-template-columns:7.5em 1fr}}
.posts time{font-family:var(--data); font-variant-numeric:tabular-nums; font-size:13px; color:var(--namako); padding-top:3px}
.posts a{font-weight:700; line-height:1.5}
.posts p{grid-column:-2; margin:0; font-size:13px; color:var(--namako); line-height:1.6}
.posts.row{display:grid; grid-template-columns:1fr; gap:16px; border-top:0}
@media(min-width:720px){.posts.row{grid-template-columns:repeat(3,1fr); gap:24px}}
.posts.row li{grid-template-columns:1fr; align-content:start; padding:14px 0 0; border-bottom:0; border-top:2px solid var(--sumi)}
.posts.row p{grid-column:auto}
.more{margin:16px 0 0; font-size:13px}
.lone-line{margin:24px 0}

/* subscribe: URL slips, one click selects the whole address */
.subs{list-style:none; margin:24px 0 24px; padding:0; display:grid; grid-template-columns:1fr; gap:16px}
@media(min-width:720px){.subs{grid-template-columns:repeat(2,1fr)}}
.subs > li{display:flex; flex-direction:column; gap:12px; padding:20px; background:var(--surface); border:1px solid var(--rule); border-radius:var(--r)}
.sub-h{display:flex; align-items:center; gap:10px}
.sub-h .ic{width:28px; height:28px; color:var(--seiji)}
.sub-h h2{margin:0; font-size:20px}
.subs p{margin:0}
.urls{margin:0; display:grid; gap:10px}
.urls div{display:grid; gap:2px}
.urls dt{font-size:13px; color:var(--namako)}
.urls dd{margin:0}
code.url,pre.url{display:block; margin:0; font-family:var(--data); font-size:13px; line-height:1.5; padding:8px 10px;
  border:1px dashed var(--usuzumi); border-radius:4px; background:var(--shikkui); color:var(--sumi);
  -webkit-user-select:all; user-select:all; overflow-wrap:anywhere; word-break:break-all; cursor:text}
pre.url{white-space:pre-wrap}
pre.url code{font-size:inherit}
.state{align-self:flex-start; font-size:13px; color:var(--namako); border:1px dashed var(--usuzumi); border-radius:4px; padding:2px 10px}

@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{animation:none!important; transition:none!important}
}
""" + _WIDTHS + "\n"
