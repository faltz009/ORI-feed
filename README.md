# ORI-feed

Get a feel for any Discord server. Add a bot, run one script, and you get the
last 24 hours (or week, or month) of the community as **one flat JSON** anyone
can analyze — plus a simple local page to read it, media included.

Built for the [Open Research Institute](https://openresearchinstitute.org);
works on any server.

## Quick start

**1. Get the code**

Click the green *Code* button → *Download ZIP* (or `git clone`), unzip anywhere.
You need Python 3 — nothing else, no packages to install.

**2. Make a Discord bot** (once, ~2 minutes)

1. Go to the [Discord developer portal](https://discord.com/developers/applications) → *New Application* → name it.
2. Open the **Bot** tab → enable **Message Content Intent** → *Save*.
3. Same tab → *Reset Token* → **copy the token** (this is the bot's password — keep it private).
4. Invite it to your server by opening this URL, with `YOUR_APP_ID` replaced by
   the *Application ID* from the *General Information* tab:

   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&scope=bot&permissions=66560
   ```

   `66560` = View Channels + Read Message History. Read-only — the bot can't
   post or manage anything.

**3. Hook the bot to the tool**

The token from step 2 is the only wiring there is. Two ways to connect it:

- **Locally:** `python3 feed.py --setup` — paste the token when asked; it's saved
  to `token.txt` (git-ignored, file mode 600) and used automatically from then on.
  One-off alternative: `DISCORD_TOKEN=… python3 feed.py`.
- **On GitHub (hosted):** add the token as an Actions secret named
  `DISCORD_TOKEN` (see *Keeping it updated* below). Never commit the token itself.

The bot reads every channel its roles allow. If your server has
permission-restricted channels that *should* be in the feed, give the bot (or a
role it holds) *View Channel* + *Read Message History* on them — whatever it
can't read is skipped automatically and counted in
`channels_skipped_no_access`.

**4. Run it**

```
python3 feed.py --setup     # first time: token + preferences
python3 feed.py             # pulls the messages
```

**5. Look at it**

```
python3 -m http.server 8000
```

Open http://localhost:8000 — channels on the left, messages on the right,
images and videos inline.

## Tuning the knobs

Three equivalent ways — pick whichever you like:

- **In the page:** click the ⚙ icon (top of the sidebar), pick your values,
  then *download config.json* into the ORI-feed folder — or copy the shown
  one-line command.
- **The wizard:** `python3 feed.py --setup` asks you everything.
- **By hand:** edit `config.json`.

| Knob | config.json | Default | Meaning |
|---|---|---|---|
| Window | `window_hours` | `24` | How far back to pull (`168` = a week — good for a first feel) |
| Media | `media` | `true` | Download images/videos locally (Discord's own links expire) |
| Media cap | `media_max_mb` | `50` | Skip files bigger than this |
| Snapshots | `snapshot` | `false` | Also keep a dated copy per run, `feed/daily/YYYY-MM-DD.json` |

Environment variables (`WINDOW_HOURS`, `MEDIA`, `MEDIA_MAX_MB`, `SNAPSHOT`)
override `config.json` for one-off runs.

## Keeping it updated automatically (optional)

**On your own machine** — add one cron line (`crontab -e`), e.g. daily at 07:00
with dated snapshots:

```
0 7 * * * cd /path/to/ORI-feed && SNAPSHOT=1 python3 feed.py >> feed.log 2>&1
```

**Hosted for free on GitHub** — fork this repo, then:
1. Repo *Settings → Secrets and variables → Actions* → add secret
   `DISCORD_TOKEN` = your bot token.
2. *Settings → Pages* → deploy from branch `main`, folder `/`.
3. Done. The included workflow (`.github/workflows/feed.yml`) pulls daily and
   your feed is public at `https://<you>.github.io/<repo>/` (viewer) and
   `…/feed/feed.json` (raw endpoint).

## The data format

One JSON object per message, flat list, sorted by time — designed so exports
from other platforms (Twitter, Slack, Signal…) can merge into the same stream:

```json
{
  "user": "Defender",
  "message": "the text content",
  "timestamp": "2026-07-10T13:48:23.433+00:00",
  "channel": "general",
  "thread": null,
  "reply_to": null,
  "id": "discord:1344305064818249788",
  "user_id": "discord:1221182134950301706",
  "url": "https://discord.com/channels/…",
  "attachments": [{"name": "image.png", "url": "…cdn link…",
                   "file": "feed/media/…", "bytes": 54241}],
  "platform": "discord",
  "server": "ORI 1.0 (2025)"
}
```

`id`/`user_id` are platform-prefixed (collision-free merging), `url` is the
permalink to the original message (provenance), `attachments[].file` is the
local copy (survives Discord's expiring CDN links). Wrapper fields:
`generated`, `window_hours`, `message_count`, `channels_skipped_no_access`.

## Privacy / consent

The bot can only read channels its role allows — **the bot's channel access IS
the boundary of the dataset.** Deny it a channel and that channel never enters
the feed. Mods control everything with native Discord permissions; there is no
separate config to audit.
