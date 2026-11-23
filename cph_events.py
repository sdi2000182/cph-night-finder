#!/usr/bin/env python3
"""Find queer / techno / hyperpop / Arabic-influenced, free-or-cheap events in Copenhagen.

Usage: python3 cph_events.py [--days 14] [--max-price 100] [--all]
Sources: Dukop public iCal feed + Resident Advisor (unofficial GraphQL API, may break).
Instagram: prints hashtag links (no public API).
"""
import argparse, html, json, re, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UA = {"User-Agent": "Mozilla/5.0"}
DUKOP = "https://dukop.dk/en/feed/ical/"
RA = "https://ra.co/graphql"
RA_AREA = 402  # Copenhagen
CPH_TZ = ZoneInfo("Europe/Copenhagen")

TAGS = {
    "queer":  r"queer|lgbt|trans\b|t4t|lesb|dyke|gay|fag|nonbinary|non-binary|drag|pride|femme|flinta",
    "techno": r"techno|hyperpop|\brave\b|club ?night|dancefloor|dance floor|\bdjs?\b|b2b|gabber|ballroom|"
              r"\bparty\b|\bfest\b|fete|afterparty|bass music",
    "arabic": r"\b(arab|swana|mena\b|middle east|palestin|leban|syria|egypt|maghreb|morocc|algeri|tunis|"
              r"dabke|shaabi|ra[iï]\b|darbuka|oud\b|mahraganat|khaleeji|iraq|kurd|persian|iran)",
}
FREE = (r"free (entry|entrance|admission|event|party|of charge)|(entry|entrance|admission)\W+free|"
        r"\bgratis\b|donation|pay what you|pwyc|sliding scale|no entry fee|fri entré")
PRICE = r"(\d{2,4})\s*(?:kr|dkk|,-)|(?:kr\.?|dkk)\s*(\d{2,4})"
NOT_ENTRY = r"locker|garderobe|cloak|wardrobe|jacket|drink|beer|\bøl\b|cocktail|shot|merch|pant\b|deposit|food|mad\b"
EN = r"\b(the|and|with|join|us|we|you|will|for|our|night|this)\b"
DA = r"\b(og|med|vi|til|på|det|er|af|kom|vores|en|der)\b"
CPH = r"københavn|kbh|copenhagen|frederiksberg|valby|vanløse|amager|christiania|\b[12]\d{3}\b"


def get(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def dukop(now, end):
    text = re.sub(r"\r?\n[ \t]", "", get(DUKOP))  # unfold iCal lines
    for block in text.split("BEGIN:VEVENT")[1:]:
        ev = {}
        for line in block.splitlines():
            k, _, v = line.partition(":")
            ev[k.split(";")[0]] = v.replace("\\n", "\n").replace("\\,", ",").replace("\\;", ";")
        if not re.search(CPH, ev.get("LOCATION", "").lower()):
            continue
        utc = lambda s: s and datetime.strptime(s[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        yield dict(src="dukop", title=ev.get("SUMMARY", ""), desc=ev.get("DESCRIPTION", ""),
                   loc=ev.get("LOCATION", "").split("\n")[0], url=ev.get("URL", ""),
                   start=utc(ev.get("DTSTART")), stop=utc(ev.get("DTEND")))


def ra(now, end):
    q = """query($f: FilterInputDtoInput, $p: Int){ eventListings(filters:$f, pageSize:100, page:$p){
      totalResults data{ event{ title startTime endTime content cost contentUrl
      tickets(queryType:AVAILABLE){ title priceRetail }
      venue{name} genres{name} artists{name} } } } }"""
    page, seen = 1, 0
    while True:
        f = {"areas": {"eq": RA_AREA},
             "listingDate": {"gte": f"{now:%Y-%m-%d}", "lte": f"{end:%Y-%m-%d}"}}
        body = json.dumps({"query": q, "variables": {"f": f, "p": page}}).encode()
        res = json.loads(get(RA, body, {"Content-Type": "application/json",
                                        "Referer": "https://ra.co/events/dk/copenhagen"}))
        lst = res["data"]["eventListings"]
        for x in lst["data"]:
            e = x["event"]
            local = lambda s: s and datetime.fromisoformat(s[:19]).replace(tzinfo=CPH_TZ)
            extra = " ".join(g["name"] for g in e.get("genres") or []) + " " + \
                    " ".join(a["name"] for a in e.get("artists") or [])
            # real prices: RA ticket tiers (minus sold out) + the organiser's cost field
            prices = [round(t["priceRetail"]) for t in e.get("tickets") or []
                      if t.get("priceRetail") is not None and "sold out" not in (t.get("title") or "").lower()]
            prices += [int(n) for n in re.findall(r"\d+", e.get("cost") or "")]
            yield dict(src="ra", title=e["title"], desc=f"{e.get('content') or ''}\n{extra}", prices=prices,
                       loc=(e.get("venue") or {}).get("name", ""), url="https://ra.co" + e["contentUrl"],
                       start=local(e["startTime"]), stop=local(e.get("endTime")))
        seen += len(lst["data"])
        if not lst["data"] or seen >= lst["totalResults"]:
            return
        page += 1


def analyse(ev):
    blob = f"{ev['title']}\n{ev['desc']}".lower()
    tags = [t for t, rx in TAGS.items() if re.search(rx, blob)]
    if ev["src"] == "ra" and "techno" not in tags:
        tags.insert(0, "techno")  # everything on RA is a club/music event
    # RA ticket/cost prices + entry prices in the text (skipping locker/drink lines)
    prices = (ev.get("prices") or []) + [
        int(a or b) for line in blob.splitlines() if not re.search(NOT_ENTRY, line)
        for a, b in re.findall(PRICE, line)]
    # "free entry until 22:00, 75 kr after" -> not free for a late arrival, so prices win
    free = max(prices) == 0 if prices else bool(re.search(FREE, blob))
    lang = "EN" if len(re.findall(EN, blob)) >= len(re.findall(DA, blob)) else "DA"
    return tags, free, (min(prices), max(prices)) if prices else None, lang


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--max-price", type=int, default=100, help="DKK; events above this are hidden")
    ap.add_argument("--all", action="store_true", help="include Danish-only and single-tag matches")
    ap.add_argument("--any-time", action="store_true", help="include weekdays and early events")
    ap.add_argument("--html", metavar="FILE", help="write a web page (top picks + all cheap nights)")
    a = ap.parse_args()
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=a.days)

    hits = []
    for name, source in [("Dukop", dukop), ("RA", ra)]:
        try:
            events = list(source(now, end))
        except Exception as e:  # one source down shouldn't kill the other
            print(f"! {name} failed: {e}")
            continue
        for ev in events:
            if not ev["start"] or (ev["stop"] or ev["start"]) < now or ev["start"] > end:
                continue
            loc_start = ev["start"].astimezone(CPH_TZ)
            # Fri/Sat/Sun nights, starting 21:00+ (or after midnight = still the night before)
            late = loc_start.hour >= 21 or loc_start.hour < 6
            if not a.any_time and not (loc_start.weekday() >= 4 and late):
                continue
            tags, free, price, lang = analyse(ev)
            # filter on the highest price: arriving late usually means the late/all-night price
            if not tags or (price and price[1] > a.max_price and not free):
                continue
            # ponytail: keyword heuristic; pick = a party AND queer/arabic AND English.
            pick = bool("techno" in tags and set(tags) & {"queer", "arabic"} and lang == "EN")
            cost = ("FREE" if free else "price?" if not price else
                    f"{price[0]} kr" if price[0] == price[1] else f"{price[0]}–{price[1]} kr")
            hits.append(dict(ev=ev, tags=tags, cost=cost, lang=lang, pick=pick, when=loc_start))
    hits.sort(key=lambda h: h["when"])

    if a.html:
        open(a.html, "w").write(page(hits, a.days, now))
        print(f"wrote {a.html}: {sum(h['pick'] for h in hits)} picks, {len(hits)} total")
        return

    shown = [h for h in hits if a.all or h["pick"]]
    for h in shown:
        ev = h["ev"]
        print(f"{h['when']:%a %d %b %H:%M}  [{ev['src']}] [{h['cost']}] [{h['lang']}] [{','.join(h['tags'])}]")
        print(f"  {ev['title'].strip()}  @ {ev['loc']}")
        print(f"  {ev['url']}\n")
    print(f"{len(shown)} matches in the next {a.days} days.\n")
    print("Instagram (no public API, open in browser):")
    for tag in IG_TAGS:
        print(f"  #{tag}: https://www.instagram.com/explore/tags/{tag}/")


IG_TAGS = ["queercph", "queercopenhagen", "copenhagentechno", "cphrave", "swanacph"]


def page(hits, days, now):
    e = html.escape
    def card(h):
        ev = h["ev"]
        chips = "".join(f'<span class="chip {t}">{t}</span>' for t in h["tags"])
        return (f'<li><a href="{e(ev["url"])}"><time>{h["when"]:%a %d %b · %H:%M}</time>'
                f'<strong>{e(ev["title"].strip())}</strong><span class="loc">{e(ev["loc"])}</span>'
                f'<span class="meta"><span class="cost">{h["cost"]}</span>{chips}'
                f'<span class="src">{ev["src"]} · {h["lang"]}</span></span></a></li>')
    picks = "".join(card(h) for h in hits if h["pick"]) or "<li class=empty>Nothing matched this week.</li>"
    rest = "".join(card(h) for h in hits if not h["pick"]) or "<li class=empty>Nothing.</li>"
    ig = " ".join(f'<a href="https://www.instagram.com/explore/tags/{t}/">#{t}</a>' for t in IG_TAGS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>CPH Night Finder</title>
<style>
:root{{--bg:#f6f4f1;--fg:#1b1a19;--muted:#6b6660;--card:#fff;--line:#e3ded8;--queer:#b4237a;--arabic:#a15c00;--techno:#3b4cc0}}
@media (prefers-color-scheme:dark){{:root{{--bg:#121113;--fg:#eeeae6;--muted:#9a948d;--card:#1c1b1e;--line:#2e2c30;--queer:#ff7ac6;--arabic:#ffb347;--techno:#8f9bff}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.45 system-ui,sans-serif}}
main{{max-width:760px;margin:0 auto;padding:32px 16px 64px}}h1{{margin:0 0 4px;font-size:28px}}
h2{{margin:36px 0 12px;font-size:18px}}p{{color:var(--muted);margin:0}}a{{color:inherit}}
ul{{list-style:none;padding:0;margin:0;display:grid;gap:10px}}
li a{{display:grid;gap:2px;padding:14px 16px;background:var(--card);border:1px solid var(--line);border-radius:10px;text-decoration:none}}
li a:hover{{border-color:var(--muted)}}li a:focus-visible{{outline:2px solid var(--techno);outline-offset:2px}}
time,.loc,.src{{color:var(--muted);font-size:14px}}.meta{{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}}
.cost{{font-weight:600;font-size:14px}}.chip{{font-size:12px;padding:1px 8px;border-radius:99px;border:1px solid currentColor}}
.queer{{color:var(--queer)}}.arabic{{color:var(--arabic)}}.techno{{color:var(--techno)}}.src{{margin-left:auto}}
.empty{{color:var(--muted)}}.ig a{{margin-right:10px}}
</style></head><body><main>
<h1>CPH Night Finder</h1>
<p>Free or cheap (≤100 kr) Fri–Sun nights from 21:00, next {days} days. From Resident Advisor + Dukop.
Updated {now.astimezone(CPH_TZ):%a %d %b %H:%M}. Tags come from keywords, so check the listing.</p>
<h2>Top picks · queer / Arabic-influenced · English</h2><ul>{picks}</ul>
<h2>More cheap weekend nights</h2><ul>{rest}</ul>
<h2>Instagram</h2><p class="ig">{ig}</p>
</main></body></html>"""


if __name__ == "__main__":
    main()
