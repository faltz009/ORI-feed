#!/usr/bin/env python3
"""Build the attributed ORI report from the canonical feed history."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import timedelta
from pathlib import Path


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT))

from corpus import iter_records  # noqa: E402
from weather import ReferenceData, WeatherAnalyzer, parse_time  # noqa: E402


CONFIG_PATH = BASE / "config.json"
OUTPUT_DIR = BASE / "data"
DEFAULT_HISTORY = ROOT / "feed" / "history"
REFERENCE_DIR = BASE / "data" / "reference"
FEED_PATH = ROOT / "feed" / "feed.json"
ALIAS_WORD = re.compile(r"[a-z][a-z'\-]{2,}")


def arguments(config: dict) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=int(config.get("window_days", 30)), metavar="N"
    )
    parser.add_argument("--history", default=str(DEFAULT_HISTORY), metavar="DIR")
    return parser.parse_args()


def canonical_server_id(value: str | None) -> str | None:
    if not value:
        return None
    return value if ":" in value else f"discord:{value}"


def validate_output(report: dict) -> None:
    """Fail closed if a raw message field enters the durable report schema."""
    forbidden_keys = {
        "message",
        "content",
        "author",
        "username",
        "nickname",
        "user_id",
        "message_id",
        "message_url",
        "url",
        "attachments",
        "reply_to",
        "member_alias_tokens",
    }

    def visit(value, path="root"):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden_keys:
                    raise RuntimeError(f"forbidden durable field at {path}.{key}")
                visit(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(report)


def current_feed_metadata(feed: dict, server_id: str | None) -> tuple[dict, dict]:
    """Extract one server's collection coverage from the parsed rolling feed.

    ``feed`` is feed.json parsed once by main(); it contributes icons and
    coverage metadata only. Canonical history remains the message source.
    """
    bare_id = (server_id or "").removeprefix("discord:")
    server = next(
        (
            item
            for item in feed.get("servers", [])
            if str(item.get("id") or "") == bare_id
        ),
        {},
    )
    return server, {
        "collector_generated": feed.get("generated"),
        "channels_skipped_no_access": feed.get("channels_skipped_no_access"),
        "member_count": server.get("member_count"),
        **(server.get("coverage") or {}),
    }


def build_server_report(
    server_id: str,
    records: list[dict],
    config: dict,
    days: int,
    history: Path,
    reference: "ReferenceData",
    feed: dict,
) -> dict:
    """Reduce one server's canonical records into its report aggregate."""
    eligible = []
    aliases = set()
    for record in records:
        if record.get("is_bot"):
            continue
        if record.get("type") not in (None, 0, 19, "Default", "Reply"):
            continue
        observed_at = parse_time(record.get("timestamp"))
        if observed_at is None:
            continue
        display = record.get("user") or "?"
        aliases.update(ALIAS_WORD.findall(display.lower()))
        eligible.append((observed_at, record))
    eligible.sort(key=lambda item: (item[0], item[1].get("id") or ""))
    if not eligible:
        raise RuntimeError("no eligible human messages")

    latest = eligible[-1][0]
    current_start = latest - timedelta(days=days - 1)
    analyzer = WeatherAnalyzer(days, config)
    for observed_at, record in eligible:
        analyzer.observe(
            content=record.get("message") or "",
            timestamp=record.get("timestamp"),
            channel=record.get("channel") or "?",
            speaker=record.get("user_id") or f"display:{record.get('user') or '?'}",
            display_name=record.get("user") or "?",
            reactions=record.get("reactions") or [],
            attachments=record.get("attachments") or [],
            current=observed_at >= current_start,
        )

    feed_server, collector_coverage = current_feed_metadata(feed, server_id)
    aliases.update(feed_server.get("member_aliases") or [])
    latest_record = eligible[-1][1]
    server = {
        "id": server_id,
        "name": latest_record.get("server") or feed_server.get("name") or "Community",
        "icon": feed_server.get("icon"),
    }
    coverage = {
        "source": "canonical_feed_history",
        "history_path": str(history.relative_to(ROOT))
        if history.is_relative_to(ROOT) else str(history),
        "stored_records": len(records),
        "eligible_human_messages": len(eligible),
        **collector_coverage,
    }

    print(
        f"[{server['name']}] reducing {len(eligible):,} canonical messages; "
        f"current weather begins {current_start.date().isoformat()}…"
    )

    # Phrase discovery needs the complete first reduction. Replay only the
    # current window once the qualified bigram inventory is known so compound
    # occurrences replace—not duplicate—their component word observations.
    # Alias tokens are set once here, before any pass that filters on them.
    analyzer.member_alias_tokens = aliases
    analyzer.begin_semantic_pass(analyzer.phrase_rows(2, reference))
    for observed_at, record in eligible:
        if observed_at < current_start:
            continue
        analyzer.observe_semantic(
            content=record.get("message") or "",
            timestamp=record.get("timestamp"),
            channel=record.get("channel") or "?",
            speaker=record.get("user_id") or f"display:{record.get('user') or '?'}",
        )

    report = analyzer.finalize(reference, server=server, coverage=coverage)
    validate_output(report)
    return report


def write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )


def print_summary(report: dict, path: Path) -> None:
    print(f"[{report['server']['name']}] community aggregate -> {path}")
    print(
        "movement:",
        ", ".join(row["term"] for row in report["movement"]["rising"][:6])
        or report["movement"].get("status", "none"),
    )
    print(
        "circles:",
        "; ".join(circle["label"] for circle in report["conversation_circles"][:4])
        or "none",
    )


def main() -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    args = arguments(config)
    if args.days < 7:
        raise SystemExit("Memetic Weather requires a window of at least 7 days")
    history = Path(args.history)
    if not history.exists():
        raise SystemExit(
            f"canonical history not found at {history}; run ../feed.py first"
        )
    reference = ReferenceData.load(REFERENCE_DIR)
    # feed.json contributes only collection metadata (icons, coverage); parse
    # it once here rather than once per server.
    feed = json.loads(FEED_PATH.read_text(encoding="utf-8")) if FEED_PATH.exists() else {}
    # One report per server in the canonical corpus. config.guild_id chooses
    # which server appears first; it does not select a different code path or
    # output contract.
    by_server: dict[str, list[dict]] = {}
    unattributed = 0
    for record in iter_records(history):
        server_id = record.get("server_id")
        if not server_id:
            unattributed += 1
            continue
        by_server.setdefault(str(server_id), []).append(record)
    if unattributed:
        print(f"skipping {unattributed} canonical records without a server id")
    if not by_server:
        raise SystemExit("canonical history contains no server-attributed records")

    primary = canonical_server_id(config.get("guild_id"))
    if primary not in by_server:
        primary = max(by_server, key=lambda server_id: len(by_server[server_id]))
    order = [primary] + sorted(
        (server_id for server_id in by_server if server_id != primary),
        key=lambda server_id: -len(by_server[server_id]),
    )

    built = []
    for server_id in order:
        report = build_server_report(
            server_id, by_server[server_id], config, args.days, history, reference, feed
        )
        bare_id = server_id.split(":", 1)[-1]
        path = OUTPUT_DIR / f"weather-{bare_id}.json"
        built.append((server_id, path, report))

    # Write only after every server has reduced and passed the durable-output
    # validator. A broken server cannot leave a deceptively successful partial
    # multi-server build behind.
    index = []
    for server_id, path, report in built:
        write_report(path, report)
        print_summary(report, path)
        index.append({
            "id": server_id,
            "name": report["server"]["name"],
            "icon": report["server"]["icon"],
            "file": path.name,
            "messages": report["stats"]["messages"],
            "window": report["window"],
        })

    expected = {path.resolve() for _server_id, path, _report in built}
    for stale in OUTPUT_DIR.glob("weather*.json"):
        if stale.resolve() not in expected:
            stale.unlink()

    index_path = OUTPUT_DIR / "servers.json"
    index_path.write_text(
        json.dumps(
            {"generated": built[0][2].get("generated"), "servers": index},
            ensure_ascii=False, separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"server index ({len(index)}) -> {index_path}")


if __name__ == "__main__":
    main()
