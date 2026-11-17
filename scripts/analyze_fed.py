#!/usr/bin/env python3
"""Bronze EUW: is a player's minute-15 gold lead better converted into a win when allies stay nearby?

One player per team, selected BEFORE measuring 15–25 minute proximity.
Results are observational, matched by match, and exploratory. Stdlib only.
"""
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from analyze_adc import load_pairs, fit_logit, exact_sign_p, ci

DATA = Path(__file__).resolve().parents[1] / "data" / "bronze"
OUT = DATA / "fed_analysis.json"
RNG = random.Random(20261117)
ROLES = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
MIN_LEAD = 750
NEAR = 2500


def eligible(row, min_lead=MIN_LEAD):
    return (row.get("fedGoldLead15") is not None
            and row["fedGoldLead15"] >= min_lead
            and row.get("fedFrames", 0) >= 3
            and row.get("fedAnyNear") is not None)


def band(value):
    return "low" if value < 0.4 else ("medium" if value < 0.6 else "high")


def table(rows):
    out = {}
    for k in ("low", "medium", "high"):
        group = [r for r in rows if band(r["fedAnyNear"]) == k]
        out[k] = {
            "teams": len(group),
            "wins": sum(bool(r["win"]) for r in group),
            "winRate": round(sum(bool(r["win"]) for r in group) / len(group), 4) if group else None,
            "meanTeamGold15": round(sum(r["teamGold15"] for r in group) / len(group)) if group else None,
            "meanFedLead15": round(sum(r["fedGoldLead15"] for r in group) / len(group)) if group else None,
            "meanNearbyTeammates": round(sum(r["fedAlliesMean"] for r in group) / len(group), 3) if group else None,
            "roleCounts": {role: sum(r["fedRole"] == role for r in group) for role in ROLES}
        }
    return out


def group_bootstrap(pairs, threshold=MIN_LEAD):
    selected = [(a,b) for a,b in ((p["blue"],p["red"]) for p in pairs)]
    def calc(ps):
        high_w=high_n=low_w=low_n=0
        for blue,red in ps:
            for row in (blue, red):
                if not eligible(row, threshold):
                    continue
                b=band(row["fedAnyNear"])
                if b=="high":
                    high_n+=1
                    high_w+=bool(row["win"])
                if b=="low":
                    low_n+=1
                    low_w+=bool(row["win"])
        return high_w/high_n-low_w/low_n if high_n and low_n else None
    point=calc(selected)
    bootstrap=[calc([selected[RNG.randrange(len(selected))] for _ in selected])
               for _ in range(1400)]
    return {"highMinusLowWinRate":round(point,4) if point is not None else None,
            "bootstrapped95pctCI":ci(bootstrap), "resamplingUnit":"match"}


def pairs_with_two_leaders(pairs, threshold=MIN_LEAD):
    return [p for p in pairs if eligible(p["blue"], threshold)
            and eligible(p["red"], threshold)]


def direct(pairs, field="fedAnyNear"):
    pos=neg=tie=0; diffs=[]
    for pair in pairs:
        a,b=pair["blue"],pair["red"]
        if a.get(field) is None or b.get(field) is None:
            continue
        winner,loser=(a,b) if a["win"] else (b,a)
        delta=winner[field]-loser[field]
        diffs.append(delta)
        if delta>1e-9:pos+=1
        elif delta<-1e-9:neg+=1
        else:tie+=1
    if not diffs:return None
    boot=[sum(diffs[RNG.randrange(len(diffs))] for _ in diffs)/len(diffs)
          for _ in range(1400)]
    return {"matchedGames":len(diffs),"winnerMoreAccompanied":pos,
            "loserMoreAccompanied":neg,"equalPresence":tie,
            "winnerShareAmongNonTies":round(pos/(pos+neg),4) if pos+neg else None,
            "twoSidedExactSignP":round(exact_sign_p(pos,neg),5),
            "meanWinnerMinusLoserPresence":round(sum(diffs)/len(diffs),4),
            "pairedBootstrap95pctCI":ci(boot)}


def directional_model(pairs):
    valid=pairs_with_two_leaders(pairs)
    obs=[]
    for p in valid:
        a,b=p["blue"],p["red"]
        obs.append((
            [(a["fedAnyNear"]-b["fedAnyNear"])*10,
             a["teamGold15"]/1000,
             (a["fedGoldLead15"]-b["fedGoldLead15"])/1000],
            int(a["win"])
        ))
    models={"nMatchedGames":len(obs),
            "units":{"accompaniment":"10 percentage point difference in fraction of midgame snapshots with at least one ally near",
                     "teamGold":"1000 gold more team advantage at minute 15",
                     "fedLead":"1000 gold more lead over opposing same-role player at minute 15"}}
    if len(obs)<50:return models
    for name,indices in (("proximity_only",(0,)),("plus_team_gold",(0,1)),
                         ("plus_team_gold_and_fed_lead",(0,1,2))):
        try:
            results=fit_logit([([x[i] for i in indices],y) for x,y in obs],len(indices))
            models[name]={["proximityPer10pp","teamGoldPer1000","fedLeadPer1000"][i]:v
                          for i,v in zip(indices,results)}
        except (ArithmeticError,ValueError,OverflowError) as exc:
            models[name]={"error":type(exc).__name__}
    return models


def sample_strata(rows):
    strata={}
    for name,criterion in (
        ("teamBehindMoreThan1000", lambda r:r["teamGold15"] < -1000),
        ("teamWithin1000", lambda r:abs(r["teamGold15"])<=1000),
        ("teamAheadMoreThan1000", lambda r:r["teamGold15"] > 1000),
        ("fedLead750to1499", lambda r:750<=r["fedGoldLead15"]<1500),
        ("fedLead1500plus", lambda r:r["fedGoldLead15"]>=1500),
        ("fedIsTeamRichest", lambda r:r.get("fedIsRichest") is True),
        ("fedIsNotTeamRichest", lambda r:r.get("fedIsRichest") is False)
    ):
        selection=[r for r in rows if criterion(r)]
        strata[name]={"nTeams":len(selection),"presence":table(selection)}
    return strata


def early_death(events, pid, t, ms=70000):
    return any(e.get("victim")==pid and e.get("t") is not None
               and 0 <= t-e["t"]<=ms for e in events)


def team_frame_proximity(container, row, start, end, radius):
    members=[p for p in container.get("participants",[])
             if p.get("team")==row["teamId"]]
    fed=next((p for p in members
              if p.get("role")==row.get("fedRole") and p.get("champion")==row.get("fedChamp")),None)
    if not fed or len(members)!=5:return None
    fid=str(fed["pid"])
    allies=[str(p["pid"]) for p in members if p["pid"]!=fed["pid"]]
    e=container.get("events",[])
    near=near2=frames=alliesSum=0
    for f in container.get("frames",[]):
        t=f.get("t",0)
        if not (start*60000<=t<end*60000):continue
        arr=f.get("p",{}).get(fid)
        if not arr or arr[0] is None or arr[1] is None:continue
        x,y=arr[:2]
        if ((x<1700 and y<1700) or (x>13400 and y>13400)
            or early_death(e, fed["pid"],t)):
            continue
        count=0
        for pid in allies:
            q=f.get("p",{}).get(pid)
            if not q or q[0] is None or q[1] is None:continue
            if (q[0]-x)**2+(q[1]-y)**2<=radius*radius:
                count+=1
        frames+=1
        near+=count>=1
        near2+=count>=2
        alliesSum+=count
    if frames<3:return None
    return {"any":near/frames,"twoPlus":near2/frames,
            "alliesMean":alliesSum/frames,"frames":frames}


def sensitivity(pairs):
    out={}
    for a,b in ((15,20),(20,25),(15,25)):
        for radius in (1500,2500,4000):
            matched=[]
            unpaired=[]
            for p in pairs:
                container=p["blue"].get("timelineData") or p["red"].get("timelineData")
                if not container:continue
                enriched={}
                for key in ("blue","red"):
                    r=p[key]
                    if not eligible(r): continue
                    value=team_frame_proximity(container,r,a,b,radius)
                    if value:
                        rec={**r,"near":value["any"],
                             "near2":value["twoPlus"],
                             "numNear":value["alliesMean"]}
                        enriched[key]=rec
                        unpaired.append(rec)
                if len(enriched)==2:
                    matched.append(enriched)
            def table_metric(group,field):
                return [{"category":k,"n":len(q),"winRate":round(sum(r["win"] for r in q)/len(q),4)
                          if q else None} for k in ("low","medium","high")
                        for q in [[r for r in group if band(r[field])==k]]]
            out[f"{a}-{b}_radius{radius}"]={
                "fedTeamObservations":len(unpaired),
                "bothFedWithinSameMatch":len(matched),
                "proximityAndWins":table_metric(unpaired,"near"),
                "matchedPair":direct(matched,"near"),
                "twoOrMoreAlliesMatched":direct(matched,"near2"),
                "meanAlliesMatched":direct(matched,"numNear"),
            }
    return out


def main():
    pairs,total,shards=load_pairs()
    rows=[r for p in pairs for r in (p["blue"],p["red"]) if eligible(r)]
    both=pairs_with_two_leaders(pairs)
    main_result={
        "schemaVersion":1,
        "question":"Does staying near the largest gold-advantaged teammate at minute 15 correlate with winning or prevent a throw?",
        "sample":{
            "archivedMatches":len(pairs),
            "twoTeamsPerMatch":len(pairs)*2,
            "teamsWithFedLeader":len(rows),
            "gamesBothTeamsHaveFedLeader":len(both),
            "archiveShards":shards,
        },
        "definitions":{
            "rank":"Bronze III seed, not all other lobby ranks verified",
            "queueId":420,
            "fedSelection":"team member with largest gold difference vs opposite role, at minute 15; ahead by >=750 gold",
            "proximity":"at least one other teammate within <=2500 map units; snapshot frequency ~1 minute",
            "time":"minutes 15 inclusive to 25 exclusive",
            "lowPresence":"<40% of valid snapshots",
            "mediumPresence":"40-60% of valid snapshots",
            "highPresence":">=60% of valid snapshots",
            "outcome":"team wins the match, NOT whether fed player remains fed",
        },
        "main":{
            "winRatesByPresence":table(rows),
            "highMinusLowClusterBootstrap":group_bootstrap(pairs),
            "bothFedSameMatch":direct(both),
            "pairedMeanNumberOfNearbyAllies":direct(both,"fedAlliesMean"),
            "pairedAdjustedDirectionalLogit":directional_model(pairs),
        },
        "byRole":{
            role:{"teams":sum(r["fedRole"]==role for r in rows),
                  "table":table([r for r in rows if r["fedRole"]==role])}
            for role in ROLES
        },
        "stratified":sample_strata(rows),
        "sensitivity":sensitivity(pairs),
        "limitations":[
            "Observational association, cannot show that proximity causes wins",
            "The strongest-by-gold-difference player is not always strongest team win condition",
            "TOP and ADC naturally have different clustering behaviour; interpret per role",
            "Position snapshots are ~1 minute apart; a gank/fight may be missed",
            "We select the fed player at 15 min, then measure later to reduce reverse timing",
            "Gold lead at 15 may already reflect team quality, and later grouping can follow map control",
            "Repeated players across games can produce nonindependent observations",
            "Riot Bronze III seed does not mean all ten participants are individually Bronze"
        ]
    }
    OUT.write_text(json.dumps(main_result,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"sample":main_result["sample"],
                      "mainRates":main_result["main"]["winRatesByPresence"],
                      "paired":main_result["main"]["bothFedSameMatch"],
                      "adjusted":main_result["main"]["pairedAdjustedDirectionalLogit"],
                      "sensitivity":main_result["sensitivity"].get("15-25_radius2500")},ensure_ascii=False))


if __name__=="__main__":
    main()
