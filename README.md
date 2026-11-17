# LoL Solo/Duo ADC Research

Data research for **eissen4#EUW** using Riot's official MATCH-V5 endpoint.

- **GitHub**: this repo
- **Render**: https://lol-stats-pre.onrender.com
- `/health`: health check
- `/status`: current extraction progress and errors
- `/report`: aggregated statistics and per-match analyses (public, **no API keys**)

Only Ranked Solo/Duo (`queueId=420`) is eligible. Flex, ARAM, normal and Clash are excluded. Matches are further filtered to `teamPosition=BOTTOM` for ADC metrics.

The collector starts when the Render web process starts. It attempts up to `MATCH_COUNT` (default 50) of the most recent Solo/Duo matches, obeys rate limits, downloads match and timeline JSON in memory, and extracts:

- ADC stats: wins, KDA, damage/min, damage share, gold/min, CS/min, participation
- Timeline marks at 10, 15, and 20 minutes: CS, gold, XP and opposing bot differences
- Approximate ADC–support distance and frame counts during minutes 2–14.5
- Every ADC death event with its location, the closest sampled allied positions, and the **sampling offset** (so no frame is misrepresented as exact)
- Per-champion and per-support groups in `aggregate`

The /report endpoint omits full position series to avoid unnecessary exposure of detailed maps; the background process writes the complete derived report to `/tmp/lol-stats-report.json` on Render, which is **ephemeral** (not persistent storage).

## Configuration

The existing Render service has environment variables:
- `RIOT_API_KEY`: secret, never commit or log
- `RIOT_ID`: `eissen4#EUW`
- `MATCH_COUNT`: optional, default 50

The Render configuration currently runs `python riot_probe.py`, which delegates to `research_server.py`. Auto-deploy is off; a deployment must be triggered manually after code changes.

## Important limitations

- Riot timeline positions are periodic snapshots, not continuous tracking.
- Relative distances represent straight-line coordinates in map units, not pathfinding distance.
- Death coordinates may be event coordinates, but ally locations are snapshots at nearby times. Always inspect `snapshotOffsetSec`.
- A support being far away does not itself indicate a poor rotation.
- Patches, opponent skill, wave position and enemy vision require additional analysis before causal conclusions.
- **No reference sample from other Bronze players has been collected by this script yet**. Do not label the player's average as a Bronze baseline.

## Next research steps

1. Validate full 50-game run and extract per-champion, per-support figures.
2. Collect a deduplicated Bronze III EUW ranked match cohort using league and match IDs.
3. Join champion pairs Caitlyn–Lux and Miss Fortune–Zyra.
4. Compare winning/losing teams with matching patch and rank criteria; avoid conditioning only on winners.
5. Add persistent storage before scaling to 500+ matches, rather than relying on Render's ephemeral filesystem.

## Bronze EUW cohort experiment

The separate `bronze_cohort.py` module samples **unique** recent Ranked Solo/Duo (queue 420) matches discovered from players currently listed in Bronze III, with Bronze II/IV as fallbacks. It does **not** independently verify every other lobby participant's rank at match time; interpret as *Bronze-III-seeded matches*, not an exact all-Bronze population.

In each match, the collector compares both teams' ADCs and their allies using official MATCH-V5 minute frames:
- Midgame **minute 15–25**, team winning/losing outcomes.
- Ally near = Euclidean distance at most 2,500 map units. Frames in fountain or shortly after a death are excluded.
- ADC proximity to **support** separately from ADC proximity to the **other three teammates**.
- The most fed teammate is selected **at minute 15** as highest positive gold lead over the opposing player in the same role, with a minimum +750 gold lead for fed-player subgroup analysis.
- Breakdowns by team gold advantage at minute 15 to identify confounding.
- Direct within-match comparison of the winning ADC versus the losing ADC.

Live endpoints:
- `/bronze/status`: source collection progress.
- `/bronze/report`: aggregate pilot/cohort results and limitations (no secrets or player identities).

Controls: `RESEARCH_MODE=bronze`, `BRONZE_MATCH_COUNT` (sample size), `BRONZE_SEED_COUNT` (number of potential ranked seeds). Logs every ten validated matches.

**Observational study:** statistics do not prove that proximity causes winning. Winners may group because they already have map control. The unit of analysis for significance is the match, not each minute frame or each team independently. The timeline is only an approximately minute-spaced series. All write outputs use Render's ephemeral `/tmp` storage.
