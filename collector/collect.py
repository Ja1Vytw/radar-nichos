"""Radar de Nichos - coletor mensal.

Busca canais recentes que estao performando bem, por nicho e idioma,
usando a YouTube Data API v3 (chave gratuita). Gera:
  data/AAAA-MM.json  -> snapshot do mes
  site/data.js       -> todos os snapshots, lido pelo site

Uso:
  YT_API_KEY=xxxx python collector/collect.py
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://www.googleapis.com/youtube/v3/"
QUOTA = {"search": 100, "channels": 1, "playlistItems": 1}


class YouTube:
    def __init__(self, key):
        self.key = key
        self.units = 0

    def get(self, endpoint, **params):
        params["key"] = self.key
        url = API + endpoint + "?" + urllib.parse.urlencode(params)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=30) as r:
                    self.units += QUOTA[endpoint]
                    return json.load(r)
            except urllib.error.HTTPError as e:
                body = e.read().decode(errors="ignore")
                if e.code == 403 and "quota" in body.lower():
                    raise SystemExit("Cota diaria da API esgotada. Rode de novo amanha.")
                if attempt == 2:
                    raise SystemExit(f"Erro {e.code} em {endpoint}: {body[:300]}")
                time.sleep(2 * (attempt + 1))


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def collect(yt, cfg, now):
    since = now - timedelta(days=cfg["lookback_days"])
    found = {}  # channel_id -> {niche, lang, format, top_video}

    # 1) buscar videos recentes mais vistos por nicho x idioma
    for niche in cfg["niches"]:
        for lang, q in niche["queries"].items():
            meta = cfg["languages"][lang]
            params = dict(part="snippet", type="video", q=q, order="viewCount",
                          publishedAfter=iso(since), maxResults=cfg["results_per_search"],
                          relevanceLanguage=lang, regionCode=meta["region"])
            dur = niche.get("duration") or ("short" if niche["format"] == "shorts" else "medium")
            if dur != "any":
                params["videoDuration"] = dur
            res = yt.get("search", **params)
            for it in res.get("items", []):
                cid = it["snippet"]["channelId"]
                if cid not in found:
                    found[cid] = {"niche": niche["id"], "lang": lang, "format": niche["format"],
                                  "sample_title": it["snippet"]["title"],
                                  "sample_video": it["id"]["videoId"]}
            print(f"  busca {niche['id']}/{lang}: {len(res.get('items', []))} videos", flush=True)

    # 2) dados dos canais (50 por chamada)
    channels = {}
    for group in chunks(list(found), 50):
        res = yt.get("channels", part="snippet,statistics,contentDetails", id=",".join(group), maxResults=50)
        for c in res.get("items", []):
            channels[c["id"]] = c

    # 3) data do PRIMEIRO video (nao a criacao do canal) para canais pequenos
    out = []
    for cid, c in channels.items():
        st = c.get("statistics", {})
        vids = int(st.get("videoCount", 0) or 0)
        if vids == 0 or vids > cfg["max_videos_per_channel"]:
            continue
        uploads = c["contentDetails"]["relatedPlaylists"]["uploads"]
        dates, token = [], None
        while True:
            p = dict(part="contentDetails", playlistId=uploads, maxResults=50)
            if token: p["pageToken"] = token
            res = yt.get("playlistItems", **p)
            dates += [parse(i["contentDetails"]["videoPublishedAt"]) for i in res.get("items", [])
                      if i.get("contentDetails", {}).get("videoPublishedAt")]
            token = res.get("nextPageToken")
            if not token: break
        if not dates:
            continue
        first = min(dates)
        age = max((now - first).days, 1)
        if age > cfg["max_channel_age_days"]:
            continue
        views = int(st.get("viewCount", 0) or 0)
        avg = views / vids
        info = found[cid]
        min_avg = cfg["min_avg_views_shorts"] if info["format"] == "shorts" else cfg["min_avg_views_long"]
        if avg < min_avg:
            continue
        subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0) or 0)
        out.append({
            "id": cid,
            "name": c["snippet"]["title"],
            "url": f"https://www.youtube.com/channel/{cid}",
            "thumb": c["snippet"]["thumbnails"].get("medium", c["snippet"]["thumbnails"]["default"])["url"],
            "niche": info["niche"],
            "lang": info["lang"],
            "format": info["format"],
            "first_video": first.date().isoformat(),
            "age_days": age,
            "videos": vids,
            "views": views,
            "subs": subs,
            "avg_views": round(avg),
            "views_per_day": round(views / age),
            "sample_title": info["sample_title"],
            "sample_url": f"https://www.youtube.com/watch?v={info['sample_video']}",
        })

    # nota de 0 a 100: views/dia relativo ao melhor do mesmo formato
    for fmt in ("long", "shorts"):
        group = [x for x in out if x["format"] == fmt]
        top = max((x["views_per_day"] for x in group), default=1) or 1
        for x in group:
            x["score"] = round(100 * (x["views_per_day"] / top) ** 0.5)
    out.sort(key=lambda x: -x["score"])
    return out


def build_site_data(cfg):
    data_dir = os.path.join(ROOT, "data")
    months = []
    for f in sorted(os.listdir(data_dir)):
        if f.endswith(".json") and f[:4].isdigit():
            months.append(json.load(open(os.path.join(data_dir, f), encoding="utf-8")))
    months.sort(key=lambda m: m["month"], reverse=True)
    payload = {"niches": {n["id"]: n["name"] for n in cfg["niches"]},
               "languages": {k: v["label"] for k, v in cfg["languages"].items()},
               "months": months}
    with open(os.path.join(ROOT, "site", "data.js"), "w", encoding="utf-8") as fh:
        fh.write("window.NICHE_DATA = " + json.dumps(payload, ensure_ascii=False) + ";\n")


def main():
    cfg = json.load(open(os.path.join(ROOT, "collector", "config.json"), encoding="utf-8"))
    if "--rebuild" in sys.argv:
        build_site_data(cfg); print("site/data.js regenerado"); return
    key = os.environ.get("YT_API_KEY")
    if not key:
        raise SystemExit("Defina a variavel YT_API_KEY com sua chave da YouTube Data API.")
    now = datetime.now(timezone.utc)
    yt = YouTube(key)
    print("Coletando...", flush=True)
    channels = collect(yt, cfg, now)
    snap = {"month": now.strftime("%Y-%m"), "updated_at": now.isoformat(timespec="minutes"),
            "quota_used": yt.units, "channels": channels}
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", f"{snap['month']}.json"), "w", encoding="utf-8") as fh:
        json.dump(snap, fh, ensure_ascii=False, indent=1)
    build_site_data(cfg)
    print(f"OK: {len(channels)} canais, {yt.units} unidades de cota usadas.")


if __name__ == "__main__":
    main()
