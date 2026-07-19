#!/usr/bin/env python3
"""Zero Feed — message collector for every server this bot is in.

Invite the bot (View Channels + Read Message History), run this script, and you
get a static JSON of the last WINDOW_HOURS of messages, one flat list, channel
as a property on each item. Channels the bot cannot read are skipped: its role
defines the observable channel boundary. Storage and publication policy remain
separate deployment decisions.

Output:
  feed/feed.json              all servers, one flat list, sorted by timestamp
  feed/history/YYYY-MM.jsonl  canonical de-duplicated event history
  feed/media/                 downloaded attachments (MEDIA=1, default)
  feed/daily/YYYY-MM-DD.json  timestamped snapshot of the feed (SNAPSHOT=1)

Knobs (environment variables):
  WINDOW_HOURS   how far back to pull (default 24; e.g. 168 = one week)
  MEDIA          download attachments locally, 1/0 (default 1 — CDN links expire)
  MEDIA_MAX_MB   per-file download cap in MB (default 50)
  SNAPSHOT       also write a dated copy under feed/daily/, 1/0 (default 0)

No dependencies beyond the standard library.
Token: DISCORD_TOKEN env var (falls back to ~/.config/zero-bot/token).

One-time corpus backfill:
  python3 feed.py --all-history
  python3 feed.py --all-history --guild SERVER_ID
"""
import argparse, json, os, re, sys, time, urllib.request, urllib.error, urllib.parse
from datetime import datetime

from corpus import merge_records

API = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1420070400000
BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "feed")
HISTORY = os.path.join(OUT, "history")
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
THREAD_PARENT_TYPES = {0, 5, 15, 16}       # text, announcement, forum, media
ALIAS_WORD = re.compile(r"[a-z][a-z'\-]{2,}")

# The feed is intentionally publishable, so credentials must not cross the
# collection boundary even when somebody pastes one into Discord. These are
# high-confidence token formats, not a general prose filter: normal message
# text remains literal and inspectable.
CREDENTIAL_PATTERNS = tuple(re.compile(pattern) for pattern in (
    r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_-]{20,}",
    r"(?<![A-Za-z0-9_])gh[oprsu]_[A-Za-z0-9]{20,}",
    r"(?<![A-Za-z0-9_-])xox[baprs]-[A-Za-z0-9-]{10,}",
    r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{30,}",
    r"(?<![A-Za-z0-9_-])[MN][A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{20,}",
))
CREDENTIAL_REDACTION = "[credential redacted]"


def redact_credentials(text):
    """Remove recognizable API credentials before canonical persistence."""
    redacted = text or ""
    for pattern in CREDENTIAL_PATTERNS:
        redacted = pattern.sub(CREDENTIAL_REDACTION, redacted)
    return redacted


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


def member_aliases(guild_id):
    """Fetch roster-name tokens used to keep names out of language features."""
    aliases, count, after = set(), 0, "0"
    while True:
        members, err = api(f"/guilds/{guild_id}/members?limit=1000&after={after}")
        if err is not None:
            return [], None, err
        if not members:
            return sorted(aliases), count, None
        count += len(members)
        for member in members:
            user = member.get("user") or {}
            for name in (member.get("nick"), user.get("global_name"), user.get("username")):
                if name:
                    aliases.update(ALIAS_WORD.findall(name.lower()))
        if len(members) < 1000:
            return sorted(aliases), count, None
        after = str((members[-1].get("user") or {}).get("id") or after)


def resolve_member_mentions(content, mentions):
    """Render Discord user snowflakes as the names readers see in Discord.

    Discord keeps ``<@user-id>`` in message content and supplies the matching
    user objects separately.  The normalized feed is a human-readable record,
    so join those two parts while the complete API message is still available.
    Unknown mention IDs remain untouched and the viewer can render a generic
    fallback without inventing an identity.
    """
    names = {}
    for mention in mentions or []:
        user_id = str(mention.get("id") or "")
        member = mention.get("member") or {}
        name = (
            member.get("nick")
            or mention.get("global_name")
            or mention.get("username")
        )
        if user_id and name:
            names[user_id] = name

    def replace(match):
        name = names.get(match.group(1))
        return f"@{name}" if name else match.group(0)

    return re.sub(r"<@!?(\d+)>", replace, content or "")


def normalize(
    m,
    guild_id,
    guild_name,
    channel_id,
    channel_name,
    thread_id,
    thread_name,
):
    rec = {
        "user": m["author"].get("global_name") or m["author"].get("username", "?"),
        "message": redact_credentials(resolve_member_mentions(
            m.get("content", ""), m.get("mentions", [])
        )),
        "timestamp": m.get("timestamp"),
        "channel": channel_name,
        "channel_id": "discord:" + channel_id,
        "thread": thread_name,
        "thread_id": "discord:" + thread_id if thread_id else None,
        "reply_to": ("discord:" + m["message_reference"]["message_id"])
                    if m.get("message_reference", {}).get("message_id") else None,
        "id": "discord:" + m["id"],
        "user_id": "discord:" + m["author"]["id"],
        "is_bot": bool(m["author"].get("bot")),
        "url": f"https://discord.com/channels/{guild_id}/{m['channel_id']}/{m['id']}",
        "attachments": [{"name": a.get("filename"), "url": a.get("url"),
                         "bytes": a.get("size")} for a in m.get("attachments", [])],
        "platform": "discord",
        "server": guild_name,
        "server_id": "discord:" + guild_id,
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


def archived_threads(channel_id, visibility, stop_before=None):
    """Enumerate archived threads with Discord's native pagination.

    visibility is ``public``, ``private``, or ``joined_private``. The private
    route requires Manage Threads; joined_private is the read-only fallback
    and only returns private threads the bot was explicitly added to.

    ``stop_before`` (unix seconds) bounds the walk to the collection window:
    a thread archived before that moment cannot contain in-window messages,
    so it is neither returned nor pulled from later. Discord orders these
    pages newest-archived first, which lets the walk stop at the first
    out-of-window thread instead of enumerating the entire archive.
    """
    if visibility == "public":
        route = f"/channels/{channel_id}/threads/archived/public"
        cursor_kind = "timestamp"
    elif visibility == "private":
        route = f"/channels/{channel_id}/threads/archived/private"
        cursor_kind = "timestamp"
    elif visibility == "joined_private":
        route = f"/channels/{channel_id}/users/@me/threads/archived/private"
        cursor_kind = "snowflake"
    else:
        raise ValueError(f"unknown archived-thread visibility: {visibility}")

    found, before = [], None
    while True:
        query = {"limit": 100}
        if before:
            query["before"] = before
        data, err = api(route + "?" + urllib.parse.urlencode(query))
        if err is not None:
            return found, err
        batch = data.get("threads", [])
        for thread in batch:
            if stop_before:
                stamp = (thread.get("thread_metadata") or {}).get("archive_timestamp")
                try:
                    archived_at = datetime.fromisoformat(
                        (stamp or "").replace("Z", "+00:00")
                    ).timestamp()
                except ValueError:
                    archived_at = None  # malformed stamp: keep the thread
                if archived_at is not None and archived_at < stop_before:
                    if cursor_kind == "timestamp":
                        # Newest-archived first: this thread and everything
                        # after it are out of window; the walk is complete.
                        return found, None
                    # The joined-private route pages by thread ID, not archive
                    # time, so later pages can still hold in-window threads:
                    # drop this one but keep walking.
                    continue
            found.append(thread)
        if not data.get("has_more") or not batch:
            return found, None
        last = batch[-1]
        before = ((last.get("thread_metadata") or {}).get("archive_timestamp")
                  if cursor_kind == "timestamp" else last["id"])


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


def main(all_history=False, guild_filter=None):
    global TOKEN
    TOKEN = get_token()
    since = 0 if all_history else time.time() - WINDOW_HOURS * 3600
    after = 0 if all_history else snowflake_for(since)
    guilds, err = api("/users/@me/guilds")
    if guilds is None:
        sys.exit(f"cannot list guilds: HTTP {err}")

    os.makedirs(OUT, exist_ok=True)
    everything, server_meta, skipped_total = [], [], 0
    for g in guilds:
        gid, gname = g["id"], g["name"]
        if guild_filter and gid not in guild_filter:
            continue
        icon = (f"https://cdn.discordapp.com/icons/{gid}/{g['icon']}.png?size=128"
                if g.get("icon") else None)
        aliases, member_count, member_error = member_aliases(gid)
        if member_error is not None:
            print(f"[{gname}] member roster unavailable: HTTP {member_error}")
        records, skipped = [], 0
        channels, err = api(f"/guilds/{gid}/channels")
        if channels is None:
            print(f"[{gname}] channel list failed: HTTP {err}"); continue
        names = {c["id"]: c["name"] for c in channels}

        # target ID is where messages are read; channel ID remains the stable
        # parent channel when the target is a thread.
        targets = [(c["id"], c["id"], c["name"], None)
                   for c in channels if c["type"] in MESSAGE_CHANNEL_TYPES]
        public_archived = []
        private_archived = []
        private_scope = "all_accessible"
        for parent in (c for c in channels if c["type"] in THREAD_PARENT_TYPES):
            public, err = archived_threads(
                parent["id"], "public", stop_before=None if all_history else since
            )
            if err not in (None, 403, 404):
                print(f"[{gname}] #{parent['name']} public archive: HTTP {err}")
            public_archived.extend(public)

            # Private threads only exist under ordinary text channels. First
            # ask for the complete accessible set; if Discord denies Manage
            # Threads, fall back to private threads the bot explicitly joined.
            if parent["type"] == 0:
                private, private_err = archived_threads(
                    parent["id"], "private", stop_before=None if all_history else since
                )
                if private_err == 403:
                    private_scope = "joined_only"
                    private, private_err = archived_threads(
                        parent["id"], "joined_private",
                        stop_before=None if all_history else since,
                    )
                if private_err not in (None, 403, 404):
                    print(f"[{gname}] #{parent['name']} private archive: HTTP {private_err}")
                private_archived.extend(private)

        active, err = api(f"/guilds/{gid}/threads/active")
        if active:
            targets += [
                (t["id"], t.get("parent_id") or t["id"], names.get(t.get("parent_id"), "?"), t["name"])
                        for t in active.get("threads", []) if t["type"] in THREAD_TYPES]
        targets += [
            (
                thread["id"],
                thread.get("parent_id") or thread["id"],
                names.get(thread.get("parent_id"), "?"),
                thread["name"],
            )
            for thread in public_archived + private_archived
            if thread.get("type") in THREAD_TYPES
        ]

        # A thread cannot be both active and archived, but de-duplication here
        # also protects against inconsistent API pages during state changes.
        unique_targets = []
        target_ids = set()
        for target in targets:
            if target[0] not in target_ids:
                unique_targets.append(target)
                target_ids.add(target[0])
        targets = unique_targets

        for target_id, channel_id, ch_name, th_name in targets:
            msgs, err = pull_channel(target_id, after)
            if err == 403:
                skipped += 1; continue
            if err is not None:
                print(f"[{gname}] #{ch_name}{'/' + th_name if th_name else ''}: HTTP {err}")
                continue
            records += [
                normalize(
                    m,
                    gid,
                    gname,
                    channel_id,
                    ch_name,
                    target_id if th_name else None,
                    th_name,
                )
                for m in msgs
            ]

        print(f"[{gname}] {len(records)} messages, {skipped} private channels skipped")
        print(
            f"[{gname}] thread coverage: {len(active.get('threads', [])) if active else 0} active, "
            f"{len(public_archived)} public archived, {len(private_archived)} private archived "
            f"({private_scope})"
        )
        server_meta.append({
            "name": gname,
            "id": gid,
            "icon": icon,
            "member_count": member_count,
            "member_aliases": aliases,
            "coverage": {
                "targets": len(targets),
                "public_archived_threads": len(public_archived),
                "private_archived_threads": len(private_archived),
                "private_archived_scope": private_scope,
            },
        })
        skipped_total += skipped
        everything += records

    if MEDIA:
        download_media(everything)

    everything.sort(key=lambda r: r["timestamp"] or "")
    receipt = merge_records(HISTORY, everything)
    print(
        "history: "
        f"{receipt['added']} added, {receipt['updated']} updated, "
        f"{receipt['unchanged']} unchanged -> feed/history/"
    )
    if all_history:
        # A backfill extends the canonical corpus; it must not turn the public
        # rolling feed into a many-megabyte historical dump.
        print("backfill complete; feed/feed.json remains the rolling view")
        return
    # Only windowed runs reach this point; --all-history returned above.
    combined = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "window_hours": WINDOW_HOURS,
                "all_history": False,
                "message_count": len(everything),
                "channels_skipped_no_access": skipped_total,
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
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--all-history", action="store_true",
                        help="pull every accessible historical message")
    parser.add_argument("--guild", action="append", default=[], metavar="SERVER_ID",
                        help="limit collection to one server ID; may be repeated")
    args = parser.parse_args()
    if args.setup:
        setup()
    else:
        main(all_history=args.all_history, guild_filter=set(args.guild))
