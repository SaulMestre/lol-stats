"""Riot SoloQ EUW ADC research collector. Restricted to named Riot ID, queue 420.

Starts one controlled collection run on boot; publishes anonymized aggregate metrics,
never keys, raw participant identifiers or unrestricted Riot proxy endpoints.
"""
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EUROPE = "https://europe.api.riotgames.com"
PLATFORM = "https://euw1.api.riotgames.com"
AGENT = "lol-stats-research/0.2 (Riot soloqueue statistical research)"
OUT = Path("/tmp/lol-stats-report.json")
STATE = {"phase":"not_started","done":0,"target":int(os.environ.get("MATCH_COUNT","50")),
         "errors":[],"started":None,"completed":None}
REPORT = {"metadata":{"queueId":420,"region":"EUW","mode":"Ranked Solo/Duo"},
          "matches":[],"aggregate":{}}
LOCK=threading.Lock()
LAST_CALL=[0.0]

def request_json(host, endpoint, params=None):
    token=os.environ.get("RIOT_API_KEY","")
    if not token:
        raise RuntimeError("RIOT_API_KEY is not configured")
    qs=("?"+urllib.parse.urlencode(params)) if params else ""
    url=host+endpoint+qs
    for attempt in range(9):
        # Rate-limit ourselves to avoid common dev-key quotas.
        delay=max(0,1.45-(time.monotonic()-LAST_CALL[0]))
        if delay: time.sleep(delay)
        LAST_CALL[0]=time.monotonic()
        request=urllib.request.Request(url, headers={
            "X-Riot-Token":token,"User-Agent":AGENT,"Accept":"application/json"})
        try:
            with urllib.request.urlopen(request,timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (429,500,502,503,504):
                try: wait=max(3,float(exc.headers.get("Retry-After","3")))
                except ValueError: wait=3
                time.sleep(min(60,wait+attempt*2))
                continue
            raise RuntimeError("Riot returned HTTP "+str(exc.code)) from None
        except (urllib.error.URLError,TimeoutError):
            time.sleep(min(30,3*(attempt+1)))
    raise RuntimeError("Riot API retry limit exceeded")

def zone(pos):
    if not pos: return "unknown"
    x,y=pos.get("x",0),pos.get("y",0)
    # Broad, approximate regions only; not high-fidelity terrain polygons.
    if x<6000 and y<6000: return "blue-side lower quadrant"
    if x>9000 and y>9000: return "red-side upper quadrant"
    if abs(x-y)<2600 and 4500<x<11000: return "central corridor / river"
    return "outer lane / jungle"

def dist(a,b):
    if not a or not b: return None
    return round(math.hypot(a["x"]-b["x"],a["y"]-b["y"]))

def frame_near(frames, timestamp):
    if not frames: return None
    f=min(frames,key=lambda it:abs(it.get("timestamp",0)-timestamp))
    return f if abs(f.get("timestamp",0)-timestamp)<=75000 else None

def parse_match(match,tl,puuid):
    info=match["info"]
    if info.get("queueId")!=420: return None
    players=info["participants"]
    me=next((p for p in players if p.get("puuid")==puuid),None)
    if not me or me.get("teamPosition")!="BOTTOM": return None
    pid=me["participantId"]; team=me["teamId"]
    support=next((p for p in players if p["teamId"]==team and p.get("teamPosition")=="UTILITY"),None)
    enemy=next((p for p in players if p["teamId"]!=team and p.get("teamPosition")=="BOTTOM"),None)
    frames=tl.get("info",{}).get("frames",[])
    def pf(frame,p):
        if not frame or not p:return None
        return frame.get("participantFrames",{}).get(str(p["participantId"]))
    def frame_at(minutes):
        f=frame_near(frames,minutes*60000)
        return f if f and abs(f["timestamp"]-minutes*60000)<=65000 else None
    marks={}
    for minute in (10,15,20):
        f=frame_at(minute); a=pf(f,me); b=pf(f,enemy)
        if a:
            marks[str(minute)]={
                "cs":a.get("minionsKilled",0)+a.get("jungleMinionsKilled",0),
                "gold":a.get("totalGold"),"xp":a.get("xp"),
                "goldDiff":a.get("totalGold",0)-b.get("totalGold",0) if b else None,
                "csDiff":a.get("minionsKilled",0)-b.get("minionsKilled",0) if b else None
            }
    positions=[]
    isolated=0
    deaths=[]
    for f in frames:
        t=f.get("timestamp",0)
        a=pf(f,me); b=pf(f,support)
        if not a or not a.get("position"):continue
        positions.append({
            "minute":round(t/60000,1),
            "adc":a["position"],
            "support":b.get("position") if b else None,
            "distance":dist(a["position"],b.get("position")) if b else None,
            "adcCs":a.get("minionsKilled",0),"adcGold":a.get("totalGold",0)
        })
    for f in frames:
        for event in f.get("events",[]):
            if event.get("type")!="CHAMPION_KILL" or event.get("victimId")!=pid:continue
            t=event.get("timestamp",f.get("timestamp",0))
            nearest=frame_near(frames,t)
            a=pf(nearest,me)
            anchor=event.get("position") or (a.get("position") if a else None)
            allies=[]
            for player in players:
                if player["teamId"]!=team or player["participantId"]==pid: continue
                target=pf(nearest,player)
                delta=dist(anchor,target.get("position") if target else None)
                if delta is not None: allies.append({"role":player.get("teamPosition"),"distance":delta})
            support_d=next((ally["distance"] for ally in allies if ally["role"]=="UTILITY"),None)
            deaths.append({
                "minute":round(t/60000,2),"position":anchor,
                "zoneApprox":zone(anchor),"alliesWithin2000":sum(a["distance"]<2000 for a in allies),
                "closestAllyDistance":min((a["distance"] for a in allies),default=None),
                "supportDistance":support_d,
                "snapshotOffsetSec":round(abs(nearest["timestamp"]-t)/1000,1) if nearest else None
            })
    eligible=[p for p in positions if p["minute"]<=14.5 and p["minute"]>=2
              and p["distance"] is not None]
    team_damage=sum(p.get("totalDamageDealtToChampions",0) for p in players if p["teamId"]==team)
    duration=info.get("gameDuration",0)
    if duration>100000: duration=duration/1000 # older millisecond representation
    duration=max(duration,1)
    return {
        "matchId":match["metadata"]["matchId"],
        "dateEpochMs":info.get("gameStartTimestamp"),
        "patch":info.get("gameVersion",""),
        "champion":me["championName"],"support":support["championName"] if support else None,
        "win":me["win"],"durationMin":round(duration/60,2),
        "kills":me["kills"],"deaths":me["deaths"],"assists":me["assists"],
        "cs":me.get("totalMinionsKilled",0)+me.get("neutralMinionsKilled",0),
        "csPerMin":round((me.get("totalMinionsKilled",0)+me.get("neutralMinionsKilled",0))*60/duration,2),
        "damagePerMin":round(me.get("totalDamageDealtToChampions",0)*60/duration,1),
        "damageShare":round(me.get("totalDamageDealtToChampions",0)/team_damage,4) if team_damage else None,
        "goldPerMin":round(me.get("goldEarned",0)*60/duration,1),
        "killParticipation":round((me["kills"]+me["assists"])/max(1,sum(p["kills"] for p in players if p["teamId"]==team)),3),
        "timelineMarks":marks,
        "deathsTimeline":deaths,
        "supportDistanceEarly":round(sum(p["distance"] for p in eligible)/len(eligible)) if eligible else None,
        "framesEarlyTotal":len(eligible),
        "framesEarlyFar2500":sum(p["distance"]>2500 for p in eligible),
        "positions":positions
    }

def summary(matches):
    if not matches:return {}
    keys=("kills","deaths","assists","csPerMin","damagePerMin","damageShare",
          "goldPerMin","killParticipation","supportDistanceEarly")
    def mean(rows,key):
        vals=[row[key] for row in rows if row.get(key) is not None]
        return round(sum(vals)/len(vals),3) if vals else None
    def metrics(rows):
        return {"n":len(rows),"wins":sum(r["win"] for r in rows),
                "winrate":round(sum(r["win"] for r in rows)/len(rows),3),
                "means":{k:mean(rows,k) for k in keys},
                "totalTimelineDeaths":sum(len(r["deathsTimeline"]) for r in rows),
                "earlyFrames":sum(r["framesEarlyTotal"] for r in rows),
                "earlyFar2500Frames":sum(r["framesEarlyFar2500"] for r in rows)}
    output={"all":metrics(matches),"byChampion":{},"bySupport":{}}
    for key,field in (("byChampion","champion"),("bySupport","support")):
        for value in sorted(set(r[field] for r in matches if r[field])):
            output[key][value]=metrics([r for r in matches if r[field]==value])
    return output

def persist():
    with LOCK:
        OUT.write_text(json.dumps({"state":STATE,"report":REPORT},ensure_ascii=False))

def collect():
    with LOCK: STATE.update(phase="identifying",started=int(time.time()))
    try:
        name_tag=os.environ.get("RIOT_ID","eissen4#EUW").split("#",1)
        if len(name_tag)!=2:raise RuntimeError("Invalid RIOT_ID")
        name,tag=[urllib.parse.quote(x,safe="") for x in name_tag]
        account=request_json(EUROPE,f"/riot/account/v1/accounts/by-riot-id/{name}/{tag}")
        puuid=account["puuid"]
        with LOCK:STATE["phase"]="listing_matches"
        ids=request_json(EUROPE,"/lol/match/v5/matches/by-puuid/"+
            urllib.parse.quote(puuid,safe="")+"/ids",
            {"queue":420,"start":0,"count":min(100,max(1,STATE["target"]))})
        with LOCK: STATE.update(phase="collecting",target=len(ids))
        for i,mid in enumerate(ids,1):
            try:
                safeid=urllib.parse.quote(mid,safe="")
                match=request_json(EUROPE,f"/lol/match/v5/matches/{safeid}")
                if match["info"].get("queueId")==420:
                    tl=request_json(EUROPE,f"/lol/match/v5/matches/{safeid}/timeline")
                    parsed=parse_match(match,tl,puuid)
                    if parsed:
                        with LOCK:
                            REPORT["matches"].append(parsed)
                            REPORT["aggregate"]=summary(REPORT["matches"])
            except Exception as exc:
                with LOCK:STATE["errors"].append({"matchIndex":i,"error":str(exc)[:150]})
            with LOCK: STATE["done"]=i
            if i%5==0:
                persist()
                with LOCK:
                    print("Riot collection progress: "+json.dumps({"done":STATE["done"],"stored":len(REPORT["matches"]),"errors":len(STATE["errors"]),"aggregate":REPORT["aggregate"].get("all",{})}),flush=True)
        with LOCK:STATE.update(phase="complete",completed=int(time.time()))
    except Exception as exc:
        with LOCK:
            STATE.update(phase="failed",completed=int(time.time()))
            STATE["errors"].append({"error":str(exc)[:200]})
    persist()
    with LOCK:
        print(f"Riot collection: phase={STATE['phase']} matches={len(REPORT['matches'])} "
              f"errors={len(STATE['errors'])}",flush=True)

class Handler(BaseHTTPRequestHandler):
    def do_HEAD(self):
        if self.path.split("?")[0] not in ("/","/health","/status","/report"):
            return self.send_error(404)
        self.send_response(200);self.end_headers()
    def do_GET(self):
        path=self.path.split("?")[0]
        if path in ("/","/health"):
            payload={"ok":True,"project":"LoL SoloQ Research","source":"Riot MATCH-V5"}
        elif path=="/status":
            with LOCK:payload=dict(STATE)
        elif path=="/report":
            with LOCK:payload={
                "state":dict(STATE),
                "metadata":REPORT["metadata"],
                "aggregate":REPORT["aggregate"],
                "matches":[{k:v for k,v in row.items() if k!="positions"}
                           for row in REPORT["matches"]]}
        else:
            self.send_error(404);return
        data=json.dumps(payload,ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)))
        self.end_headers()
        self.wfile.write(data)

if __name__=="__main__":
    if OUT.exists():
        try:
            obj=json.loads(OUT.read_text())
            REPORT.update(obj.get("report",{}))
        except (ValueError,OSError):pass
    threading.Thread(target=collect,daemon=True,name="riot-collector").start()
    ThreadingHTTPServer(("0.0.0.0",int(os.environ.get("PORT","10000"))),Handler).serve_forever()
