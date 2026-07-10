#!/usr/bin/env python3
"""Zero Feed — rolling-window public message feed for every server this bot is in.

Invite the bot (View Channels + Read Message History), run this script, and you
get a static JSON of the last WINDOW_HOURS of messages, one flat list, channel
as a property on each item. Channels the bot cannot read are skipped: the bot's
role IS the consent boundary.

Output:
  feed/feed.json              all servers combined, sorted by timestamp
  feed/<server-slug>.json     one file per server

No dependencies beyond the standard library.
Token: DISCORD_TOKEN env var (falls back to ~/.config/zero-bot/token).
"""
import json, os, re, sys, time, urllib.request, urllib.error

API = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
WINDOW_HOURS = float(os.environ.get("WINDOW_HOURS", 24))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "feed")

TOKEN = os.environ.get("DISCORD_TOKEN") or open(
    os.path.expanduser("~/.config/zero-bot/token")).read().strip()

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


def main():
    since = time.time() - WINDOW_HOURS * 3600
    after = snowflake_for(since)
    guilds, err = api("/users/@me/guilds")
    if guilds is None:
        sys.exit(f"cannot list guilds: HTTP {err}")

    os.makedirs(OUT, exist_ok=True)
    everything = []
    for g in guilds:
        gid, gname = g["id"], g["name"]
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
               "server": gname, "window_hours": WINDOW_HOURS,
               "message_count": len(records),
               "channels_skipped_no_access": skipped,
               "messages": records}
        path = os.path.join(OUT, slug(gname) + ".json")
        json.dump(doc, open(path, "w"), ensure_ascii=False, indent=1)
        print(f"[{gname}] {len(records)} messages, {skipped} private channels skipped -> {path}")
        everything += records

    everything.sort(key=lambda r: r["timestamp"] or "")
    json.dump({"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "window_hours": WINDOW_HOURS, "message_count": len(everything),
               "messages": everything},
              open(os.path.join(OUT, "feed.json"), "w"), ensure_ascii=False, indent=1)
    print(f"combined: {len(everything)} messages -> feed/feed.json")


if __name__ == "__main__":
    main()
