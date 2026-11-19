"""Radar de Nichos - coletor mensal.

Busca canais recentes que estao performando bem, por nicho e idioma,
usando a YouTube Data API v3 (chave gratuita). Gera:
  data/AAAA-MM.json  -> snapshot do mes
  site/data.js       -> todos os snapshots, lido pelo site

Uso:
  YT_API_KEY=xxxx python collector/collect.py
"""
import html, json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = "https://www.googleapis.com/youtube/v3/"
QUOTA = {"search": 100, "channels": 1, "playlistItems": 1, "videos": 1}


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


STOP = {
    "pt": {"como", "você", "não", "uma", "para", "isso", "por", "que", "é", "do", "da", "esse", "funciona"},
    "es": {"cómo", "como", "una", "para", "que", "por", "del", "los", "las", "es", "nadie", "funciona", "qué"},
    "en": {"the", "how", "you", "your", "what", "is", "a", "of", "and", "for", "to", "works", "why"},
    "de": {"der", "die", "das", "und", "wie", "ist", "nicht", "ein", "eine", "für", "mit", "zum"},
    "fr": {"le", "la", "les", "et", "comment", "est", "une", "des", "pour", "du", "ta", "vraiment"},
}


def guess_lang(text):
    """Palpite simples pelo titulo quando o video nao informa o idioma."""
    if any("\u0900" <= ch <= "\u097f" or "\u0600" <= ch <= "\u06ff" for ch in text):
        return "outro"
    words = {w.strip("?!.,:;()|#\"'¿¡").lower() for w in text.split()}
    scores = {lang: len(words & sw) for lang, sw in STOP.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] >= 2 else None


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def collect(yt, cfg, now):
    since = now - timedelta(days=cfg["lookback_days"])
    found = {}  # channel_id -> {niche, lang, format, top_video}

    # 1) buscar videos recentes mais vistos por nicho x idioma
    for niche in cfg["niches"]:
        for lang, qs in niche["queries"].items():
          meta = cfg["languages"][lang]
          for q in ([qs] if isinstance(qs, str) else qs):
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
                                    "sample_title": html.unescape(it["snippet"]["title"]),
                                    "sample_video": it["id"]["videoId"]}
              print(f"  busca {niche['id']}/{lang} '{q}': {len(res.get('items', []))} videos", flush=True)

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
        if vids < cfg.get("min_videos", 1) or vids > cfg["max_videos_per_channel"]:
            continue
        niche_cfg = next(n for n in cfg["niches"] if n["id"] == found[cid]["niche"])
        text = (c["snippet"]["title"] + " " + found[cid]["sample_title"]).lower()
        if any(term in text for term in niche_cfg.get("exclude", [])):
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
            "name": html.unescape(c["snippet"]["title"]),
            "url": f"https://www.youtube.com/channel/{cid}",
            "thumb": c["snippet"]["thumbnails"].get("medium", c["snippet"]["thumbnails"]["default"])["url"],
            "niche": info["niche"],
            "lang": info["lang"],
            "lang_source": "busca",
            "sample_id": info["sample_video"],
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

    # idioma real: idioma do audio/metadados do video em alta (a busca so indica o mercado)
    for group in chunks(out, 50):
        res = yt.get("videos", part="snippet", id=",".join(x["sample_id"] for x in group), maxResults=50)
        langs = {v["id"]: (v["snippet"].get("defaultAudioLanguage") or v["snippet"].get("defaultLanguage") or "")
                 for v in res.get("items", [])}
        for x in group:
            code = langs.get(x["sample_id"], "").split("-")[0].lower()
            if code and code != "zxx":
                x["lang"], x["lang_source"] = code, "audio"
            else:
                g = guess_lang(x["sample_title"] + " " + x["name"])
                if g:
                    x["lang"], x["lang_source"] = g, "titulo"
    if cfg.get("only_configured_languages"):
        out = [x for x in out if x["lang"] in cfg["languages"]]
    for x in out:
        x.pop("sample_id", None)

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
