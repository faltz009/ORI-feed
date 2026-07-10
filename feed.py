#!/usr/bin/env python3
"""Zero Feed — rolling-window public message feed for every server this bot is in.

Invite the bot (View Channels + Read Message History), run this script, and you
get a static JSON of the last WINDOW_HOURS of messages, one flat list, channel
as a property on each item. Channels the bot cannot read are skipped: the bot's
role IS the consent boundary.

Output:
  feed/feed.json              all servers combined, sorted by timestamp
  feed/<server-slug>.json     one file per server
  feed/media/                 downloaded attachments (MEDIA=1, default)
  feed/daily/YYYY-MM-DD.json  timestamped snapshot of the combined feed (SNAPSHOT=1)

Knobs (environment variables):
  WINDOW_HOURS   how far back to pull (default 24; e.g. 168 = one week)
  MEDIA          download attachments locally, 1/0 (default 1 — CDN links expire)
  MEDIA_MAX_MB   per-file download cap in MB (default 50)
  SNAPSHOT       also write a dated copy under feed/daily/, 1/0 (default 0)

No dependencies beyond the standard library.
Token: DISCORD_TOKEN env var (falls back to ~/.config/zero-bot/token).
"""
import json, os, re, sys, time, urllib.request, urllib.error, urllib.parse

API = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "feed")
CONFIG_PATH = os.path.join(BASE, "config.json")
TOKEN_PATH = os.path.join(BASE, "token.txt")

try:
    CFG = json.load(open(CONFIG_PATH))
except Exception:
    CFG = {}

def knob(env, key, default, cast):
    """Priority: environment variable > config.json > default."""
    if os.environ.get(env) is not None:
        return cast(os.environ[env])
    if key in CFG:
        return cast(CFG[key])
    return default

_truthy = lambda v: str(v).lower() not in ("0", "false", "no", "n", "")
WINDOW_HOURS = knob("WINDOW_HOURS", "window_hours", 24, float)
MEDIA = knob("MEDIA", "media", True, _truthy)
MEDIA_MAX_MB = knob("MEDIA_MAX_MB", "media_max_mb", 50, float)
SNAPSHOT = knob("SNAPSHOT", "snapshot", False, _truthy)

def get_token():
    if os.environ.get("DISCORD_TOKEN"):
        return os.environ["DISCORD_TOKEN"].strip()
    for p in (TOKEN_PATH, os.path.expanduser("~/.config/zero-bot/token")):
        try:
            return open(p).read().strip()
        except OSError:
            continue
    sys.exit("No bot token found. Run:  python3 feed.py --setup")

TOKEN = None  # set in main() / setup()

MESSAGE_CHANNEL_TYPES = {0, 2, 5}          # text, voice (text-in-voice), announcement
THREAD_TYPES = {10, 11, 12}                # news / public / private threads


def api(path):
    """GET with rate-limit handling. Returns (json, None) or (None, http_status)."""
    while True:
        req = urllib.request.Request(API + path, headers={
            "Authorization": "Bot " + TOKEN, "User-Agent": "ZeroFeed/0.1"})
        try:
            return json.load(urllib.request.urlopen(req)), None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(float(e.headers.get("Retry-After", 1)) + 0.1)
                continue
            return None, e.code


def snowflake_for(unix_seconds):
    return (int(unix_seconds * 1000) - DISCORD_EPOCH_MS) << 22


def slug(name):
    s = re.sub(r"[^\w\s-]", "", name).strip().lower()
    return re.sub(r"[-\s]+", "-", s) or "server"


def normalize(m, guild_id, guild_name, channel_name, thread_name):
    rec = {
        "user": m["author"].get("global_name") or m["author"].get("username", "?"),
        "message": m.get("content", ""),
        "timestamp": m.get("timestamp"),
        "channel": channel_name,
        "thread": thread_name,
        "reply_to": ("discord:" + m["message_reference"]["message_id"])
                    if m.get("message_reference", {}).get("message_id") else None,
        "id": "discord:" + m["id"],
        "user_id": "discord:" + m["author"]["id"],
        "url": f"https://discord.com/channels/{guild_id}/{m['channel_id']}/{m['id']}",
        "attachments": [{"name": a.get("filename"), "url": a.get("url"),
                         "bytes": a.get("size")} for a in m.get("attachments", [])],
        "platform": "discord",
        "server": guild_name,
    }
    reactions = [{"emoji": r["emoji"].get("name"), "count": r["count"]}
                 for r in m.get("reactions", [])]
    if reactions:
        rec["reactions"] = reactions
    if m.get("type") not in (0, 19):       # 0 = default, 19 = reply
        rec["type"] = m.get("type")
    return rec


def pull_channel(ch_id, after_snowflake):
    """All messages in a channel after the snowflake, oldest→newest."""
    out, cursor = [], after_snowflake
    while True:
        batch, err = api(f"/channels/{ch_id}/messages?after={cursor}&limit=100")
        if err is not None:
            return out, err
        if not batch:
            return out, None
        batch.sort(key=lambda m: int(m["id"]))
        out.extend(batch)
        cursor = batch[-1]["id"]
        if len(batch) < 100:
            return out, None


def download_media(records):
    """Fetch every attachment into feed/media/, add a local 'file' path per item."""
    mdir = os.path.join(OUT, "media")
    os.makedirs(mdir, exist_ok=True)
    got = skipped_big = failed = 0
    for rec in records:
        for a in rec["attachments"]:
            url = a.get("url")
            if not url:
                continue
            # .../attachments/<channel>/<attachment_id>/<filename>?signed-params
            parts = urllib.parse.urlparse(url).path.rstrip("/").split("/")
            att_id, fname = (parts[-2], parts[-1]) if len(parts) >= 2 else ("x", parts[-1])
            fname = re.sub(r"[^\w.\-]", "_", urllib.parse.unquote(fname))[-80:]
            local = f"{att_id}-{fname}"
            dest = os.path.join(mdir, local)
            if not os.path.exists(dest):
                if (a.get("bytes") or 0) > MEDIA_MAX_MB * 1e6:
                    skipped_big += 1
                    continue
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "ZeroFeed/0.1"})
                    with urllib.request.urlopen(req) as r, open(dest + ".part", "wb") as f:
                        remaining = int(MEDIA_MAX_MB * 1e6)
                        while chunk := r.read(1 << 16):
                            remaining -= len(chunk)
                            if remaining < 0:
                                raise ValueError("over cap")
                            f.write(chunk)
                    os.replace(dest + ".part", dest)
                    got += 1
                except Exception:
                    failed += 1
                    try: os.remove(dest + ".part")
                    except OSError: pass
                    continue
            a["file"] = f"feed/media/{local}"
    print(f"media: {got} downloaded, {skipped_big} over size cap, {failed} failed")


def setup():
    """Interactive first-run wizard: writes token.txt + config.json."""
    print("ORI-feed setup — press Enter to keep the [default]\n")
    tok = input("Discord bot token (leave empty to keep current): ").strip()
    wh = input("How many hours back to pull? [24]: ").strip() or "24"
    media = (input("Download images/videos locally? y/n [y]: ").strip().lower() or "y") != "n"
    cap = input("Max size per media file, MB [50]: ").strip() or "50"
    snap = (input("Keep dated daily snapshots (for cron)? y/n [n]: ").strip().lower()) == "y"
    if tok:
        with open(TOKEN_PATH, "w") as f:
            f.write(tok)
        os.chmod(TOKEN_PATH, 0o600)
        print(f"token saved to {TOKEN_PATH} (kept out of git)")
    json.dump({"window_hours": float(wh), "media": media,
               "media_max_mb": float(cap), "snapshot": snap},
              open(CONFIG_PATH, "w"), indent=1)
    print(f"knobs saved to {CONFIG_PATH}\nNow run:  python3 feed.py")


def main():
    global TOKEN
    TOKEN = get_token()
    since = time.time() - WINDOW_HOURS * 3600
    after = snowflake_for(since)
    guilds, err = api("/users/@me/guilds")
    if guilds is None:
        sys.exit(f"cannot list guilds: HTTP {err}")

    os.makedirs(OUT, exist_ok=True)
    everything, per_server, server_meta = [], [], []
    for g in guilds:
        gid, gname = g["id"], g["name"]
        icon = (f"https://cdn.discordapp.com/icons/{gid}/{g['icon']}.png?size=128"
                if g.get("icon") else None)
        server_meta.append({"name": gname, "id": gid, "icon": icon})
        records, skipped = [], 0
        channels, err = api(f"/guilds/{gid}/channels")
        if channels is None:
            print(f"[{gname}] channel list failed: HTTP {err}"); continue
        names = {c["id"]: c["name"] for c in channels}

        targets = [(c["id"], c["name"], None)
                   for c in channels if c["type"] in MESSAGE_CHANNEL_TYPES]
        active, err = api(f"/guilds/{gid}/threads/active")
        if active:
            targets += [(t["id"], names.get(t.get("parent_id"), "?"), t["name"])
                        for t in active.get("threads", []) if t["type"] in THREAD_TYPES]

        for ch_id, ch_name, th_name in targets:
            msgs, err = pull_channel(ch_id, after)
            if err == 403:
                skipped += 1; continue
            if err is not None:
                print(f"[{gname}] #{ch_name}{'/' + th_name if th_name else ''}: HTTP {err}")
                continue
            records += [normalize(m, gid, gname, ch_name, th_name) for m in msgs]

        records.sort(key=lambda r: r["timestamp"] or "")
        doc = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "server": gname, "server_id": gid,
               "server_icon": icon, "window_hours": WINDOW_HOURS,
               "message_count": len(records),
               "channels_skipped_no_access": skipped,
               "messages": records}
        per_server.append((slug(gname), gname, skipped, doc))
        everything += records

    if MEDIA:
        download_media(everything)

    for sname, gname, skipped, doc in per_server:
        path = os.path.join(OUT, sname + ".json")
        json.dump(doc, open(path, "w"), ensure_ascii=False, indent=1)
        print(f"[{gname}] {doc['message_count']} messages, {skipped} private channels skipped -> {path}")

    everything.sort(key=lambda r: r["timestamp"] or "")
    combined = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "window_hours": WINDOW_HOURS, "message_count": len(everything),
                "servers": server_meta,
                "messages": everything}
    json.dump(combined, open(os.path.join(OUT, "feed.json"), "w"),
              ensure_ascii=False, indent=1)
    print(f"combined: {len(everything)} messages -> feed/feed.json")

    if SNAPSHOT:
        ddir = os.path.join(OUT, "daily")
        os.makedirs(ddir, exist_ok=True)
        spath = os.path.join(ddir, time.strftime("%Y-%m-%d", time.gmtime()) + ".json")
        json.dump(combined, open(spath, "w"), ensure_ascii=False, indent=1)
        print(f"snapshot -> {spath}")


if __name__ == "__main__":
    if "--setup" in sys.argv:
        setup()
    else:
        main()
