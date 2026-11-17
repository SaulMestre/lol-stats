"""Observational Solo/Duo Bronze EUW cohort: ADC and fed-player ally proximity.

Caution: observational, Bronze-III seeded lobbies, not certified all-Bronze games.
"""
import json
import os
import random
import threading
import time
import urllib.parse
from collections import defaultdict
from pathlib import Path

from research_server import EUROPE, PLATFORM, request_json

DATA=Path("/tmp/bronze-cohort.json")
LOCK=threading.RLock()
STATUS={"phase":"idle","target":int(os.environ.get("BRONZE_MATCH_COUNT","500")),
        "matches":0,"seeds":0,"candidates":0,"errors":0,"skipped":0,"started":None,
        "finished":None,"lastError":None}
ROWS=[]
SEEN=set()
MAX_SEEDS=int(os.environ.get("BRONZE_SEED_COUNT","140"))
MID_START=15
MID_END=25
NEAR=2500
MIN_FRAMES=3
WINDOW_AFTER_DEATH_MS=45000
START_EPOCH=int(time.time())-45*86400

def frame_at(frames, minute):
    if not frames:return None
    f=min(frames,key=lambda x:abs(x.get("timestamp",0)-minute*60000))
    return f if abs(f.get("timestamp",0)-minute*60000)<=65000 else None

def point(frame,pid):
    v=frame.get("participantFrames",{}).get(str(pid),{}) if frame else {}
    return v.get("position") if isinstance(v.get("position"),dict) else None

def distance(a,b):
    if not a or not b:return None
    return ((a["x"]-b["x"])**2+(a["y"]-b["y"])**2)**0.5

def fountain(p):
    return p and ((p["x"]<1700 and p["y"]<1700) or
                  (p["x"]>13400 and p["y"]>13400))

def recently_dead(deaths,pid,t):
    return any(0<=t-x<=WINDOW_AFTER_DEATH_MS for x in deaths.get(pid,()))

def matchup_gold(frame,participant,foes):
    theirs=next((p for p in foes if p.get("teamPosition")==participant.get("teamPosition")),None)
    pf=frame.get("participantFrames",{})
    a=pf.get(str(participant["participantId"]),{})
    b=pf.get(str(theirs["participantId"]),{}) if theirs else {}
    if a.get("totalGold") is None or b.get("totalGold") is None:return None
    return a["totalGold"]-b["totalGold"]

def extract(match,timeline):
    info=match.get("info",{})
    if info.get("queueId")!=420:return []
    participants=info.get("participants",[])
    frames=timeline.get("info",{}).get("frames",[])
    f15=frame_at(frames,15)
    if not f15:return []
    pf15=f15.get("participantFrames",{})
    death_times=defaultdict(list)
    for f in frames:
        for e in f.get("events",[]):
            if e.get("type")=="CHAMPION_KILL" and e.get("victimId"):
                death_times[e["victimId"]].append(e.get("timestamp",f.get("timestamp",0)))
    # Anonymous minute-level positions and economy: no PUUID / summoner names.
    slim={"schema":1,"startMs":info.get("gameStartTimestamp"),
          "durationSec":info.get("gameDuration"),
          "participants":[{"pid":p["participantId"],"team":p["teamId"],
                           "role":p.get("teamPosition"),"champion":p.get("championName")}
                          for p in participants],
          "frames":[],"events":[]}
    for frame in frames:
        t=frame.get("timestamp",0)
        if 10*60000<=t<=32*60000:
            compact={}
            for p in participants:
                pf=frame.get("participantFrames",{}).get(str(p["participantId"]),{})
                xy=pf.get("position") or {}
                compact[str(p["participantId"])]=[
                    xy.get("x"),xy.get("y"),pf.get("totalGold"),pf.get("xp"),
                    pf.get("minionsKilled"),pf.get("jungleMinionsKilled"),
                    pf.get("level")]
            slim["frames"].append({"t":t,"p":compact})
        for event in frame.get("events",[]):
            if event.get("type")=="CHAMPION_KILL" and 10*60000<=event.get("timestamp",0)<=32*60000:
                slim["events"].append({"t":event.get("timestamp"),"victim":event.get("victimId"),
                    "killer":event.get("killerId"),"p":event.get("position")})
    rows=[]
    for team_id in (100,200):
        players=[p for p in participants if p["teamId"]==team_id]
        foes=[p for p in participants if p["teamId"]!=team_id]
        adcs=[p for p in players if p.get("teamPosition")=="BOTTOM"]
        supports=[p for p in players if p.get("teamPosition")=="UTILITY"]
        if len(adcs)!=1 or len(supports)!=1 or len(players)!=5:continue
        adc=adcs[0]; sup=supports[0]
        allies=[p for p in players if p["participantId"] not in
                (adc["participantId"],sup["participantId"])]
        def gold(p):
            return pf15.get(str(p["participantId"]),{}).get("totalGold")
        if any(gold(p) is None for p in participants):continue
        team_gold=sum(gold(p) for p in players)
        enemy_gold=sum(gold(p) for p in foes)
        leads=[(matchup_gold(f15,p,foes),p) for p in players]
        leads=[(lead,p) for lead,p in leads if lead is not None]
        if len(leads)!=5:continue
        fed_lead,fed=max(leads,key=lambda x:x[0])
        richest=max(players,key=gold)
        valid_adcs=[];valid_fed=[]
        for f in frames:
            t=f.get("timestamp",0)
            if not (MID_START*60000<=t<MID_END*60000):continue
            a=point(f,adc["participantId"])
            if a and not fountain(a) and not recently_dead(death_times,adc["participantId"],t):
                sup_d=distance(a,point(f,sup["participantId"]))
                other_d=[distance(a,point(f,p["participantId"])) for p in allies]
                if sup_d is not None and all(x is not None for x in other_d):
                    valid_adcs.append((sup_d<=NEAR,any(x<=NEAR for x in other_d),
                                       sum(x<=NEAR for x in other_d)+(sup_d<=NEAR)))
            fed_pos=point(f,fed["participantId"])
            if fed_pos and not fountain(fed_pos) and not recently_dead(death_times,fed["participantId"],t):
                fdist=[distance(fed_pos,point(f,p["participantId"])) for p in players
                       if p["participantId"]!=fed["participantId"]]
                if len(fdist)==4 and all(d is not None for d in fdist):
                    valid_fed.append((sum(d<=NEAR for d in fdist),any(d<=NEAR for d in fdist)))
        if len(valid_adcs)<MIN_FRAMES:continue
        adc_gold_diff=matchup_gold(f15,adc,foes)
        match_id=match.get("metadata",{}).get("matchId")
        rows.append({
            "matchId":match_id,"teamId":team_id,"win":bool(adc["win"]),
            "patch":info.get("gameVersion",""),"adcChamp":adc.get("championName"),
            "supportChamp":sup.get("championName"),"teamGold15":team_gold-enemy_gold,
            "adcGoldLead15":adc_gold_diff,
            "adcFrames":len(valid_adcs),
            "adcSupportNear":round(sum(q[0] for q in valid_adcs)/len(valid_adcs),4),
            "adcOtherNear":round(sum(q[1] for q in valid_adcs)/len(valid_adcs),4),
            "adcAnyNear":round(sum(q[0] or q[1] for q in valid_adcs)/len(valid_adcs),4),
            "adcAlliesMean":round(sum(q[2] for q in valid_adcs)/len(valid_adcs),4),
            "fedRole":fed.get("teamPosition"),"fedChamp":fed.get("championName"),
            "fedGoldLead15":fed_lead,"fedIsRichest":fed["participantId"]==richest["participantId"],
            "fedFrames":len(valid_fed),
            "fedAnyNear":round(sum(q[1] for q in valid_fed)/len(valid_fed),4) if valid_fed else None,
            "fedAlliesMean":round(sum(q[0] for q in valid_fed)/len(valid_fed),4) if valid_fed else None,
            "timelineData":slim if team_id==100 else None
        })
    return rows if len(rows)==2 else []

def band(value):
    if value is None:return "unknown"
    if value <.4:return "low (<40% frames)"
    if value >=.6:return "high (>=60% frames)"
    return "medium (40-60% frames)"

def gold_band(amount):
    if amount<-1000:return "behind >1k"
    if amount>1000:return "ahead >1k"
    return "even +/-1k"

def breakdown(rows,field):
    groups=defaultdict(list)
    for r in rows:
        v=r.get(field)
        if v is not None:groups[band(v)].append(r)
    return {b:{"n":len(v),"wins":sum(r["win"] for r in v),
               "winRate":round(sum(r["win"] for r in v)/len(v),3),
               "meanGold15":round(sum(r["teamGold15"] for r in v)/len(v))}
            for b,v in sorted(groups.items())}

def result(rows):
    if not rows:return {"nMatches":0}
    pairs=defaultdict(dict)
    for r in rows:pairs[r["matchId"]][r["win"]]=r
    same=[v for v in pairs.values() if True in v and False in v]
    def compare(field):
        higher=lower=tie=0
        for v in same:
            a=v[True].get(field);b=v[False].get(field)
            if a is None or b is None:continue
            if abs(a-b)<.0001:tie+=1
            elif a>b:higher+=1
            else:lower+=1
        return {"winnerHigher":higher,"winnerLower":lower,"tied":tie}
    fed=[r for r in rows if r["fedGoldLead15"]>=750 and r["fedAnyNear"] is not None
         and r["fedFrames"]>=MIN_FRAMES]
    strata={}
    for gold in ("behind >1k","even +/-1k","ahead >1k"):
        sub=[r for r in rows if gold_band(r["teamGold15"])==gold]
        strata[gold]={"n":len(sub),"support":breakdown(sub,"adcSupportNear"),
                      "otherAllies":breakdown(sub,"adcOtherNear"),
                      "fedAccompaniment":breakdown(
                        [r for r in sub if r in fed],"fedAnyNear")}
    return {
        "nMatches":len(same),"nTeamObservations":len(rows),
        "proximityDefinition":"euclidean distance <=2500; minutes 15-25; exclude fountain and 45s after deaths",
        "adcSupport":breakdown(rows,"adcSupportNear"),
        "adcOtherAllies":breakdown(rows,"adcOtherNear"),
        "adcAnyAlly":breakdown(rows,"adcAnyNear"),
        "winnerVsLoserSameMatch":{
            "support":compare("adcSupportNear"),
            "otherAllies":compare("adcOtherNear"),
            "anyAlly":compare("adcAnyNear"),
            "fed":compare("fedAnyNear")},
        "fedAt15":{"criterion":"largest team member gold advantage versus opponent of same role at minute 15, >=750 gold",
                   "nTeamObservations":len(fed),
                   "accompaniment":breakdown(fed,"fedAnyNear"),
                   "byRole":{role:{"n":len(sub),"support":breakdown(sub,"fedAnyNear")}
                             for role in ("TOP","JUNGLE","MIDDLE","BOTTOM","UTILITY")
                             if (sub:=[r for r in fed if r["fedRole"]==role])}},
        "byTeamGoldAdvantageAt15":strata,
        "meanFramesPerTeam":round(sum(r["adcFrames"] for r in rows)/len(rows),2)
    }

def public_export():
    with LOCK:
        return {"schemaVersion":1,"queueId":420,"region":"EUW",
                "status":dict(STATUS),"rows":list(ROWS)}


def public_status():
    with LOCK:return dict(STATUS)

def public_report():
    with LOCK:
        return {"status":dict(STATUS),"findings":result(ROWS),
                "notes":["Seed players currently in Bronze III; other lobby participants are not independently rank-verified.",
                         "Each match contributes one winning and one losing team; observations within match are not independent.",
                         "Midgame proximity is observational and does not establish causality.",
                         "A player being close may reflect winning map control rather than create victory.",
                         "Frames are sampled approximately once per minute, not continuous tracking."]}

def persist():
    with LOCK:
        data={"state":dict(STATUS),"rows":list(ROWS)}
    tmp=DATA.with_suffix(".tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False))
    tmp.replace(DATA)

def collect():
    if DATA.exists():
        try:
            existing=json.loads(DATA.read_text())
            saved=existing.get("rows",[])
            if isinstance(saved,list):
                with LOCK:
                    ROWS.extend(saved)
                    SEEN.update(r["matchId"] for r in saved if r.get("matchId"))
                    STATUS["matches"]=len(SEEN)
                print("Bronze checkpoint restored",len(SEEN),flush=True)
        except (ValueError,OSError,KeyError,TypeError):
            print("Bronze checkpoint restore unavailable",flush=True)
    with LOCK:STATUS.update(phase="selecting_bronze_players",started=int(time.time()))
    try:
        seeds=[]
        for division in ("III","II","IV"):
            for page in (1,2):
                try:
                    entries=request_json(PLATFORM,
                       f"/lol/league/v4/entries/RANKED_SOLO_5x5/BRONZE/{division}",{"page":page})
                    for entry in entries:
                        if entry.get("queueType","RANKED_SOLO_5x5")=="RANKED_SOLO_5x5":
                            seeds.append(entry)
                    print("Bronze seed discovery",division,page,"entries",len(entries),flush=True)
                    if len(seeds)>=MAX_SEEDS:break
                except Exception as exc:
                    with LOCK:STATUS["lastError"]="Seed discovery "+str(exc)[:100]
            if len(seeds)>=MAX_SEEDS:break
        if not seeds:raise RuntimeError("Bronze league endpoint yielded no entries")
        rng=random.Random(4202026)
        rng.shuffle(seeds)
        candidates=set()
        with LOCK:STATUS.update(phase="listing_ranked_matches")
        for i,entry in enumerate(seeds[:MAX_SEEDS],1):
            try:
                puuid=entry.get("puuid")
                if not puuid and entry.get("summonerId"):
                    summoner=request_json(PLATFORM,"/lol/summoner/v4/summoners/"+
                       urllib.parse.quote(str(entry["summonerId"]),safe=""))
                    puuid=summoner.get("puuid")
                if not puuid:continue
                ids=request_json(EUROPE,"/lol/match/v5/matches/by-puuid/"+
                     urllib.parse.quote(puuid,safe="")+"/ids",{
                     "queue":420,"startTime":START_EPOCH,"count":15})
                candidates.update(ids)
                with LOCK:STATUS.update(seeds=i,candidates=len(candidates))
                if len(candidates)>=STATUS["target"]*1.65:break
            except Exception as exc:
                with LOCK:
                    STATUS["errors"]+=1
                    STATUS["lastError"]="Seed lookup "+str(exc)[:100]
            if i%15==0:
                print("Bronze seed progress",json.dumps({"seeds":i,"candidates":len(candidates)}),flush=True)
        with LOCK:STATUS["phase"]="fetching_timelines"
        mids=sorted(candidates)
        rng.shuffle(mids)
        for mid in mids:
            with LOCK:
                if STATUS["matches"]>=STATUS["target"]:break
            if mid in SEEN:continue
            SEEN.add(mid)
            try:
                identifier=urllib.parse.quote(mid,safe="")
                match=request_json(EUROPE,"/lol/match/v5/matches/"+identifier)
                if match.get("info",{}).get("queueId")!=420:
                    with LOCK:STATUS["skipped"]+=1
                    continue
                timeline=request_json(EUROPE,"/lol/match/v5/matches/"+identifier+"/timeline")
                observations=extract(match,timeline)
                if len(observations)==2:
                    with LOCK:
                        ROWS.extend(observations)
                        STATUS["matches"]+=1
                else:
                    with LOCK:STATUS["skipped"]+=1
            except Exception as exc:
                with LOCK:
                    STATUS["errors"]+=1
                    STATUS["lastError"]="Match "+str(exc)[:120]
            with LOCK:n=STATUS["matches"]
            if n and n%10==0 and n!=getattr(collect,"last_logged",-1):
                collect.last_logged=n
                persist()
                with LOCK:
                    snap=result(ROWS)
                print("Bronze cohort progress",json.dumps({
                   "matches":n,"errors":STATUS["errors"],
                   "support":snap["adcSupport"],"others":snap["adcOtherAllies"],
                   "fed":snap["fedAt15"]["accompaniment"],
                   "paired":snap["winnerVsLoserSameMatch"]},ensure_ascii=False),flush=True)
        with LOCK:STATUS.update(phase="complete",finished=int(time.time()))
    except Exception as exc:
        with LOCK:STATUS.update(phase="failed",finished=int(time.time()),
                               lastError=str(exc)[:180],errors=STATUS["errors"]+1)
    persist()
    print("Bronze cohort finish",json.dumps(public_status(),ensure_ascii=False),flush=True)
