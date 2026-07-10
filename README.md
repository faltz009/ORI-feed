# Zero Feed

A public, static, daily feed of a Discord community's last 24 hours —
one flat JSON list anyone can analyze. Add the bot, get the feed. That's it.

**Endpoint:** `feed/feed.json` (all servers combined) and `feed/<server>.json` (per server), served by GitHub Pages. **Viewer:** `index.html` on the same Pages site.

## The format

One JSON object per message, sorted by timestamp:

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
  "url": "https://discord.com/channels/…/…/…",
  "attachments": [{"name": "image.png", "url": "…", "bytes": 54241}],
  "platform": "discord",
  "server": "ORI 1.0 (2025)"
}
```

Design notes:

- `id` / `user_id` are **platform-prefixed** so exports from other platforms
  (Twitter, Slack, Signal…) can merge into one stream without collisions.
- `url` is a permalink to the original message — every record carries its provenance.
- `channel` is a property on each item, not a file boundary (single flat list).
- Attachment URLs are Discord CDN links; they expire like the window does.
  Durable archiving is a separate concern from the live feed.
- Wrapper fields: `generated`, `window_hours`, `message_count`,
  `channels_skipped_no_access`.

## Consent model

The bot can only read what its role allows. Channels denied to the bot are
skipped automatically — **the bot's channel access IS the public-data boundary.**
Mods control the dataset with native Discord permissions; no config files.

## Run your own

1. Create a Discord application + bot, enable the *Message Content* intent,
   invite it with View Channels + Read Message History (`permissions=66560`).
2. Fork this repo. Add your bot token as the `DISCORD_TOKEN` Actions secret.
3. Enable GitHub Pages (deploy from branch, root).
4. The workflow pulls daily at 06:00 UTC (or run it manually from the Actions
   tab). `WINDOW_HOURS` env var changes the window.

Local run: `DISCORD_TOKEN=… python3 feed.py` — no dependencies beyond Python 3.
