#!/usr/bin/env python3
"""Archive privacy-filtered Bronze SoloQ data exported by Render into GitHub.

Runs in GitHub Actions with the built-in GITHUB_TOKEN (only git push uses it).
Does not need the Riot API key or a personal access token.
Idempotent: immutable match IDs are merged into stable JSON shards.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data" / "bronze"
URL = os.getenv("COHORT_EXPORT_URL", "https://lol-stats-pre.onrender.com/bronze/export")
SHARDS = 32
# Only these research columns are allowed into the public repository.
FIELDS = {
    "matchId", "teamId", "win", "patch", "adcChamp", "supportChamp",
    "teamGold15", "adcGoldLead15", "adcFrames", "adcSupportNear",
    "adcOtherNear", "adcAnyNear", "adcAlliesMean",
    "fedRole", "fedChamp", "fedGoldLead15", "fedIsRichest",
    "fedFrames", "fedAnyNear", "fedAlliesMean", "timelineData",
}
FRAME_FIELDS = {"participants", "frames", "events", "durationSec", "startMs", "schema"}
SECRETS = {"apiKey", "riotToken", "puuid", "summonerId", "riotId", "gameName", "tagLine"}


def download() -> dict:
    req = urllib.request.Request(URL, headers={"User-Agent": "lol-stats-github-archiver/1.0",
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=110) as r:
        if r.status != 200:
            raise RuntimeError(f"Research export returned HTTP {r.status}")
        payload = r.read(35 * 1024 * 1024 + 1)
        if len(payload) > 35 * 1024 * 1024:
            raise RuntimeError("JSON export is larger than the 35 MiB safety limit")
    return json.loads(payload)


def sanitized(row: dict) -> dict:
    result = {k: v for k, v in row.items() if k in FIELDS}
    mid = result.get("matchId")
    if not isinstance(mid, str) or not mid.startswith("EUW1_"):
        raise ValueError("Unrecognized EUW match ID")
    if result.get("teamId") not in (100, 200) or not isinstance(result.get("win"), bool):
        raise ValueError(f"Malformed winner/team for {mid}")
    if any(k in json.dumps(result).lower() for k in ("rgapi-", "x-riot-token", "puuid")):
        raise ValueError("Unexpected sensitive data in export")
    timeline = result.get("timelineData")
    if timeline is not None:
        if not isinstance(timeline, dict):
            raise ValueError("Invalid timeline data")
        result["timelineData"] = {k: v for k, v in timeline.items() if k in FRAME_FIELDS}
    return result


def shard_name(mid: str) -> str:
    n = int(hashlib.sha256(mid.encode()).hexdigest()[:8], 16) % SHARDS
    return f"matches-{n:02d}.json"


def atomic_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    if path.exists() and path.read_text(encoding="utf-8") == text + "\n":
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> None:
    payload = download()
    if payload.get("queueId") != 420 or payload.get("schemaVersion") != 1:
        raise ValueError("Unexpected study schema or game queue (must be 420)")
    received = payload.get("rows")
    if not isinstance(received, list):
        raise ValueError("Export missing a 'rows' list")

    grouped: dict[str, dict[str, dict]] = {}
    for name in DEST.glob("matches-??.json"):
        rows = json.loads(name.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            raise ValueError(f"Invalid existing shard {name.name}")
        grouped[name.name] = {
            f'{r["matchId"]}:{r["teamId"]}': r for r in rows
        }

    new_rows = 0
    for untrusted in received:
        row = sanitized(untrusted)
        file_name = shard_name(row["matchId"])
        bucket = grouped.setdefault(file_name, {})
        key = f'{row["matchId"]}:{row["teamId"]}'
        old = bucket.get(key)
        # Do not overwrite a richer archived record with a truncated one.
        if old is None:
            bucket[key] = row
            new_rows += 1
        elif len(json.dumps(row)) > len(json.dumps(old)):
            bucket[key] = row

    all_matches = set()
    all_teams = 0
    for file_name, bucket in sorted(grouped.items()):
        values = sorted(bucket.values(), key=lambda x: (x["matchId"], x["teamId"]))
        atomic_json(DEST / file_name, values)
        all_teams += len(values)
        all_matches.update(r["matchId"] for r in values)

    manifest = {
        "schemaVersion": 1,
        "region": "EUW",
        "queueId": 420,
        "matchCount": len(all_matches),
        "teamObservations": all_teams,
        "shardCount": sum(bool(x) for x in grouped.values()),
        "source": "Riot MATCH-V5 timeline -> Render -> GitHub Actions",
        "notes": [
            "Bronze III seeded sample, not rank verification of every player",
            "Winner and loser observations from the same match must be paired",
            "Positions are periodic frames, not continuous motion",
            "No PUUID, Riot ID, or Riot API key is archived",
        ],
    }
    atomic_json(DEST / "manifest.json", manifest)
    status = payload.get("status") or {}
    print(json.dumps({
        "rows_this_snapshot": len(received),
        "new_team_rows": new_rows,
        "unique_matches_archived": len(all_matches),
        "source_phase": status.get("phase"),
        "source_progress": status.get("matches"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
