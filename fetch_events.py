Builds events.json: upcoming biotech catalysts for public companies.

Sources (all free):
  1. ClinicalTrials.gov v2 API: Phase 3 industry trials with a primary completion
     date in the next 180 days (a proxy for when topline results may come).
  2. SEC EDGAR full-text search: recent 8-Ks that mention a PDUFA / target action date.
  3. events_manual.json (optional): events you add by hand, same fields as below.

Set SEC_USER_AGENT to "Your Name your@email.com" (SEC requires it).
"""
import datetime as dt
import json
import os
import re
import time

import requests

UA = {"User-Agent": os.environ.get("SEC_USER_AGENT", "BiotechCalendar contact@example.com")}
TODAY = dt.date.today()
END = TODAY + dt.timedelta(days=180)
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
SUFFIX = r"\b(inc|incorporated|corp|corporation|ltd|limited|plc|co|company|llc|sa|ag|nv|the|holdings)\b"


def norm(name):
    name = re.sub(r"/.*", "", name.lower())
    name = re.sub(r"[^a-z0-9 ]", " ", name)
    name = re.sub(SUFFIX, " ", name)
    return " ".join(name.split())


def ticker_map():
    data = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=30).json()
    return {norm(v["title"]): (v["ticker"], v["title"]) for v in data.values()}


def trial_events(tmap):
    events, token = [], None
    while True:
        params = {
            "filter.advanced": (
                f"AREA[PrimaryCompletionDate]RANGE[{TODAY},{END}] "
                "AND AREA[Phase]PHASE3 AND AREA[LeadSponsorClass]INDUSTRY"
            ),
            "pageSize": 1000,
        }
        if token:
            params["pageToken"] = token
        r = requests.get("https://clinicaltrials.gov/api/v2/studies", params=params, timeout=60).json()
        for s in r.get("studies", []):
            p = s["protocolSection"]
            sponsor = p["sponsorCollaboratorsModule"]["leadSponsor"]["name"]
            hit = tmap.get(norm(sponsor))
            if not hit:
                continue  # not a listed company we can match
            d = p["statusModule"]["primaryCompletionDateStruct"]["date"]
            exact = len(d) == 10
            events.append({
                "ticker": hit[0],
                "company": hit[1].title(),
                "type": "Trial data",
                "date": d if exact else d + "-01",
                "precision": "day" if exact else "month",
                "title": p["identificationModule"].get("briefTitle", ""),
                "source": "https://clinicaltrials.gov/study/" + p["identificationModule"]["nctId"],
                "note": "Primary completion date. Results are usually announced weeks to months later.",
            })
        token = r.get("nextPageToken")
        if not token:
            return events


def pdufa_events():
    params = {
        "q": '"PDUFA" "target action date"',
        "forms": "8-K",
        "dateRange": "custom",
        "startdt": str(TODAY - dt.timedelta(days=45)),
        "enddt": str(TODAY),
    }
    r = requests.get("https://efts.sec.gov/LATEST/search-index", params=params, headers=UA, timeout=30).json()
    events, seen = [], set()
    pattern = re.compile(rf"(?:PDUFA|target action date)[^.]{{0,160}}?((?:{MONTHS}) \d{{1,2}}, \d{{4}})", re.I)
    for h in r.get("hits", {}).get("hits", [])[:100]:
        try:
            adsh, fname = h["_id"].split(":")
            src = h["_source"]
            cik = int(src["ciks"][0])
            url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh.replace('-', '')}/{fname}"
            text = re.sub(r"<[^>]+>", " ", requests.get(url, headers=UA, timeout=30).text)
            text = " ".join(text.split())
            name = src["display_names"][0]
            tick = re.search(r"\(([A-Z.\-]+)(?:, [A-Z.\-]+)*\)\s+\(CIK", name)
            for m in pattern.finditer(text):
                date = dt.datetime.strptime(m.group(1).title(), "%B %d, %Y").date()
                key = (cik, date)
                if date >= TODAY and key not in seen:
                    seen.add(key)
                    events.append({
                        "ticker": tick.group(1) if tick else "",
                        "company": re.sub(r"\s*\(.*", "", name),
                        "type": "FDA decision",
                        "date": str(date),
                        "precision": "day",
                        "title": text[max(0, m.start() - 80):m.end()][:240],
                        "source": url,
                        "note": "From an 8-K filing. Check the filing for the drug and indication.",
                    })
        except Exception as e:  # keep going if one filing fails
            print("skip filing:", e)
        time.sleep(0.2)  # stay under SEC rate limits
    return events


def main():
    events = pdufa_events() + trial_events(ticker_map())
    if os.path.exists("events_manual.json"):
        events += json.load(open("events_manual.json"))
    events.sort(key=lambda e: e["date"])
    json.dump({"updated": dt.datetime.utcnow().isoformat() + "Z", "events": events},
              open("events.json", "w"), indent=1)
    print(len(events), "events written")


if __name__ == "__main__":
    main() 

    
