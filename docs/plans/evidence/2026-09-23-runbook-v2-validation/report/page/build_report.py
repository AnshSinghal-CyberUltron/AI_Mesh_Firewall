"""Build the Runbook v2 Verdict page from report_data.json + the v2.1 Part 0 markdown sources.

Usage: python3 build_report.py [--allow-pending]
Refuses to build while any [BENCH RESULT] placeholder or 'PENDING' value remains, unless --allow-pending.
"""
import html
import json
import os
import re
import sys

import charts
import mdparse as md

HERE = os.path.dirname(os.path.abspath(__file__))
D = json.load(open(os.path.join(HERE, "report_data.json"), encoding="utf-8"))
CSS = open(os.path.join(HERE, "style.css"), encoding="utf-8").read()
OUT = os.path.join(HERE, "runbook-v2-verdict.html")

if "--allow-pending" not in sys.argv:
    blob = json.dumps(D)
    assert md.placeholders() == 0, f"{md.placeholders()} [BENCH RESULT] placeholders remain in the v2.1 sources"
    assert "PENDING" not in blob, "report_data.json still has PENDING values"

e = html.escape
STAMP = {"MET": "ok", "NOT MET": "bad", "MET WITH CORRECTIONS": "warn", "PARTLY MET": "warn", "UNMEASURED": "na",
         "CONFIRMED": "ok", "REFUTED": "bad", "PARTIAL": "warn", "HELD": "ok", "PENDING": "na",
         "NEEDS CORRECTION": "warn"}


def stamp(label, cls=None, big=False):
    c = cls or STAMP.get(label, "acc")
    return f'<span class="stamp {c}{" big" if big else ""}">{e(label)}</span>'


def section(sid, eyebrow, title, lede, body):
    lede_html = f"<p>{lede}</p>" if lede else ""
    return (f'<section id="{sid}" aria-labelledby="{sid}-h"><div class="sec-head"><span class="eyebrow">{e(eyebrow)}</span>'
            f'<h2 id="{sid}-h">{e(title)}</h2>{lede_html}</div>{body}</section>')


# ---------- header + verdict ----------
head, bullets = md.verdict()
vb = "".join(f"<li>{md.inline(b)}</li>" for b in bullets)
meta = "".join(f"<span>{k} <b>{e(v)}</b></span>" for k, v in D["meta"])
header = f"""
<header class="top">
  <span class="eyebrow">AI Mesh Firewall · backend rewrite runbook v2 · validation {e(D["date"])}</span>
  <h1>{e(D["headline"])}</h1>
  <p class="muted">{D["lede"]}</p>
  <div class="meta">{meta}</div>
  <div class="verdict">
    <div class="answer">{stamp(D["verdict_stamp"], "bad", True)}<span class="display">{md.inline(head.strip("*"))}</span></div>
    <ul>{vb}</ul>
  </div>
</header>"""

# ---------- objective scorecard ----------
rows = "".join(
    f'<div class="row"><h3>{e(r["goal"])}</h3><div>{stamp(r["status"])}</div><div class="ev">{r["evidence"]}</div></div>'
    for r in D["scorecard"])
score = section("objective", "The objective, scored", "Your target, measured against v1 and the v2 prototype",
                D["scorecard_lede"], f'<div class="score">{rows}</div>')

# ---------- v1 vs v2 comparison ----------
cmp_rows = "".join(
    "<tr>" + f"<td>{e(r[0])}<span class=\"note\">{e(r[1])}</span></td>" + "".join(f'<td class="num">{e(c)}</td>' for c in r[2:]) + "</tr>"
    for r in D["compare"]["rows"])
cmp_head = "".join(f'<th{" class=num" if i else ""}>{e(h)}</th>' for i, h in enumerate(D["compare"]["head"]))
compare = section("compare", "Same harness, same profile", "v1 against the v2 prototype", D["compare"]["lede"],
                  f'<div class="compare"><table><thead><tr>{cmp_head}</tr></thead><tbody>{cmp_rows}</tbody></table></div>')

# ---------- charts ----------
figs = []
for fig in D["figures"]:
    if fig["kind"] == "line":
        series = [dict(s, points=[tuple(p) for p in s["points"]]) for s in fig["series"]]
        svg = charts.line_chart(series, x_label=fig["x_label"], y_label=fig["y_label"], x_max=fig["x_max"], y_max=fig["y_max"],
                                threshold=fig.get("threshold"), threshold_label=fig.get("threshold_label", ""),
                                x_ticks=fig.get("x_ticks"), y_ticks=fig.get("y_ticks"), desc=fig["title"])
        legend = ('<div class="legend-note"><span><span class="dot solid"></span>passes the infra gate (≤ 0.1% errors)</span>'
                  '<span><span class="dot hollow"></span>fails it</span></div>')
    else:
        svg = charts.bar_chart(fig["bars"], value_label=fig["value_label"], v_max=fig["v_max"], desc=fig["title"],
                               unit_fmt=fig.get("unit_fmt", "{:g}"))
        legend = ""
    figs.append(f'<figure class="figure"><div class="fig-title">{e(fig["title"])}</div><div class="chart-scroll">{svg}</div>'
                f'{legend}<figcaption>{fig["caption"]}</figcaption></figure>')
figures = section("figures", "Load ladders", "Where the budget goes", D["figures_lede"], '<div class="figs">' + "".join(figs) + "</div>")

# ---------- measured facts (§0.2) ----------
frows = "".join(f"<tr><td>{md.inline(r['q'])}</td><td>{md.inline(r['claim'])}</td><td>{md.inline(r['measured'])}</td>"
                f"<td>{md.inline(r['src'])}</td></tr>" for r in md.facts())
facts = section("facts", "Runbook v2.1 §0.2", "Measured facts that replace the plan's estimates", D["facts_lede"],
                '<div class="compare"><table class="facts"><thead><tr><th>Quantity</th><th>v2 runbook / its source</th>'
                f'<th>Measured (conditions)</th><th>Lane</th></tr></thead><tbody>{frows}</tbody></table></div>')

# ---------- corrections register (§0.3) ----------
reg = md.register()
counts = {k: sum(1 for r in reg if r["impact"] == k) for k in ("CRITICAL", "HIGH", "MEDIUM", "LOW")}
chips = "".join(f'<button type="button" class="chip" id="f-{k.lower()}" data-impact="{k}" aria-pressed="{"true" if k in ("CRITICAL", "HIGH") else "false"}">'
                f'{k.title()}<span class="n">{n}</span></button>' for k, n in counts.items())
items = []
for r in reg:
    plain = re.sub(r"\*\*|`", "", r["proved"])
    gist = re.split(r"(?<=[.;])\s", plain, maxsplit=1)[0].rstrip(";")
    if len(gist) > 190:
        gist = gist[:187].rsplit(" ", 1)[0] + "…"
    items.append(
        f'<details data-impact="{r["impact"]}" data-text="{e((r["id"] + " " + r["where"] + " " + r["proved"] + " " + r["fix"]).lower())}">'
        f'<summary><span class="cid">{e(r["id"])}</span>{stamp(r["impact"], r["impact"])}<span class="gist">{e(gist)}</span>'
        f'<span class="where">fix before {e(r["before"])}</span></summary>'
        f'<div class="body"><div><h4>Where</h4><p>{md.inline(r["where"])}</p><h4>What validation proved</h4><p>{md.inline(r["proved"])}</p></div>'
        f'<div><h4>Correction in v2.1</h4><p>{md.inline(r["fix"])}</p><h4>Fix before</h4><p>{md.inline(r["before"])}</p></div></div></details>')
legend = md.inline(md.register_legend())
register_html = (f'<p class="small muted">{legend}</p><div class="filters" role="group" aria-label="Filter by impact">{chips}'
                 f'<input class="search" id="reg-search" type="search" placeholder="Filter by card, section or word (e.g. GW12)" aria-label="Filter corrections">'
                 f'<span class="count" id="reg-count" aria-live="polite"></span></div><div class="reg" id="reg">{"".join(items)}</div>')
register_sec = section("register", "Runbook v2.1 §0.3", f"{len(reg)} corrections, each proven by execution", D["register_lede"], register_html)

# ---------- what held ----------
held = "".join(f"<div>{stamp(h[0])}<span>{h[1]}</span></div>" for h in D["held"])
held_sec = section("held", "Survived contradiction", "What the plan got right", D["held_lede"], f'<div class="held">{held}</div>')

# ---------- new cards ----------
cards = "".join(f'<div class="card"><span class="eyebrow">New card</span><h3>{e(c["id"])} — {e(c["title"])}</h3>'
                f'<p class="muted" style="margin-top:6px">{md.inline(c["objective"])}</p><p>{md.inline(c["why"])}</p></div>' for c in md.new_cards())
cards_sec = section("cards", "Runbook v2.1 §0.4–0.5", "Two new cards and a corrected task order", D["cards_lede"], f'<div class="cards">{cards}</div>')

# ---------- method ----------
kv = "".join(f"<dt>{e(k)}</dt><dd>{v}</dd>" for k, v in D["method"]) if isinstance(D["method"], list) else ""
method_sec = section("method", "How the numbers were produced", "Method, instrument and evidence", D["method_lede"], f'<dl class="kv">{kv}</dl>')

# ---------- risks ----------
risks = "".join(f"<li>{r}</li>" for r in D["risks"])
risk_sec = section("risks", "Still open", "Risks the plan must carry forward", "", f'<ul class="risks">{risks}</ul>')

script = """
<script>
(function(){
  var chips=[].slice.call(document.querySelectorAll('.chip[data-impact]'));
  var rows=[].slice.call(document.querySelectorAll('#reg details'));
  var q=document.getElementById('reg-search'), out=document.getElementById('reg-count');
  function apply(){
    var on=chips.filter(function(c){return c.getAttribute('aria-pressed')==='true';}).map(function(c){return c.dataset.impact;});
    var term=(q.value||'').trim().toLowerCase(), n=0;
    rows.forEach(function(r){var ok=on.indexOf(r.dataset.impact)>=0&&(!term||r.dataset.text.indexOf(term)>=0);r.hidden=!ok;if(ok)n++;});
    out.textContent=n+' of '+rows.length+' shown';
  }
  chips.forEach(function(c){c.addEventListener('click',function(){c.setAttribute('aria-pressed',c.getAttribute('aria-pressed')==='true'?'false':'true');apply();});});
  q.addEventListener('input',apply); apply();
})();
</script>"""

fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@75..100,500..800'
         '&family=IBM+Plex+Mono:wght@400;500;600&family=Source+Sans+3:ital,wght@0,400;0,600;0,700;1,400&display=swap">')
page = (f'<title>Runbook v2 Verdict</title>\n<meta name="description" content="{e(D["description"])}">\n{fonts}\n<style>{CSS}</style>\n'
        f'<div class="wrap">{header}{score}{compare}{figures}{facts}{register_sec}{held_sec}{cards_sec}{method_sec}{risk_sec}'
        f'<footer>{D["footer"]}</footer></div>{script}\n')
open(OUT, "w", encoding="utf-8").write(page)
print(f"wrote {OUT} ({len(page):,} bytes); register {counts}; facts {len(md.facts())}; placeholders {md.placeholders()}")
