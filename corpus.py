"""Canonical storage for normalized community events.

The Discord collector emits one stable, platform-neutral record per message.
This module keeps those records in monthly JSONL partitions, de-duplicated by
the platform-prefixed message ID.  The rolling feed and the long report are
therefore two views of the same observations rather than separate collectors.

JSONL is intentional here: the corpus is append-friendly, inspectable without
special tools, and still small enough for the current research deployment.
The report never reads Discord directly and never writes raw content into its
own aggregate output.
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urlsplit


def partition_for(record: dict) -> str:
    """Return the UTC month partition for one normalized record."""
    timestamp = str(record.get("timestamp") or "")
    if len(timestamp) >= 7 and timestamp[4] == "-":
        return timestamp[:7]
    return "undated"


def _read_partition(path: Path) -> dict[str, dict]:
    records: dict[str, dict] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            message_id = record.get("id")
            if not message_id:
                raise ValueError(f"{path}:{line_number}: canonical record has no id")
            records[str(message_id)] = record
    return records


def _attachment_key(attachment: dict) -> str:
    """Stable identity for one attachment across collections.

    Discord CDN links carry rotating signature query parameters, so only the
    URL path (attachment ID + filename) identifies the same attachment twice.
    """
    return urlsplit(str(attachment.get("url") or "")).path


def _preserve_attachment_files(previous: dict, incoming: dict) -> None:
    """Carry local media paths forward when a re-observation lacks them.

    A MEDIA=0 collection re-emits attachments without the ``file`` field even
    though the downloaded copy on disk is still valid. The newest record wins
    everywhere else; forgetting the local path would orphan the file behind
    an expiring CDN link.
    """
    known = {
        _attachment_key(attachment): attachment["file"]
        for attachment in previous.get("attachments") or []
        if attachment.get("file")
    }
    if not known:
        return
    for attachment in incoming.get("attachments") or []:
        if not attachment.get("file"):
            local_path = known.get(_attachment_key(attachment))
            if local_path:
                attachment["file"] = local_path


def merge_records(directory: Path | str, incoming: Iterable[dict]) -> dict:
    """Merge records into monthly partitions and return a compact receipt.

    Existing IDs are replaced with the newest normalized representation. This
    lets reaction totals or attachment metadata improve on a later collection
    without duplicating the underlying message. The one exception is durable
    attachment enrichment: a local ``file`` path survives re-observations
    that lack it (see _preserve_attachment_files).
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in incoming:
        if not record.get("id"):
            raise ValueError("canonical record has no platform-prefixed id")
        grouped[partition_for(record)].append(record)

    added = updated = unchanged = 0
    touched = []
    for partition, batch in sorted(grouped.items()):
        path = directory / f"{partition}.jsonl"
        records = _read_partition(path)
        changed = False
        for record in batch:
            key = str(record["id"])
            previous = records.get(key)
            if previous is not None:
                # Enrich before comparing: a record whose only difference was
                # a missing local path counts as unchanged, so MEDIA=0 runs
                # do not rewrite otherwise-identical partitions.
                _preserve_attachment_files(previous, record)
            if previous is None:
                added += 1
                changed = True
            elif previous != record:
                updated += 1
                changed = True
            else:
                unchanged += 1
            records[key] = record
        if not changed:
            continue
        temporary = path.with_suffix(path.suffix + ".part")
        with temporary.open("w", encoding="utf-8") as handle:
            for record in sorted(
                records.values(),
                key=lambda item: (item.get("timestamp") or "", item.get("id") or ""),
            ):
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
        os.replace(temporary, path)
        touched.append(path.name)

    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "partitions": touched,
    }


def iter_records(
    directory: Path | str,
    *,
    server_id: str | None = None,
    server_name: str | None = None,
) -> Iterator[dict]:
    """Yield unique canonical records in timestamp order."""
    directory = Path(directory)
    seen = set()
    for path in sorted(directory.glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                record = json.loads(line)
                message_id = record.get("id")
                if not message_id:
                    raise ValueError(f"{path}:{line_number}: canonical record has no id")
                if message_id in seen:
                    continue
                if server_id and str(record.get("server_id") or "") != str(server_id):
                    continue
                if server_name and record.get("server") != server_name:
                    continue
                seen.add(message_id)
                yield record
