#!/usr/bin/env python3
"""Analyze midgame ADC/support proximity from archived Bronze Solo/Duo matches.

Only stdlib. One winner-vs-loser observation per MATCH, not per individual frame.
Results are observational, and Bronze III seeds do not certify all ten player ranks.
"""
import json
import math
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "bronze"
OUTPUT = DATA / "adc_analysis.json"
WINDOWS = ((15,25),(15,20),(20,25))
DISTANCES = (1500,2000,2500,3000,4000)
PRIMARY_DISTANCE = 2500
MIN_FRAMES = 3
RNG = random.Random(20261117)


def load_pairs():
    grouped = defaultdict(list)
    shards = sorted(DATA.glob("matches-??.json"))
    for file in shards:
        records = json.loads(file.read_text(encoding="utf-8"))
        for record in records:
            if record.get("matchId", "").startswith("EUW1_"):
                grouped[record["matchId"]].append(record)
    valid = []
    for mid, rows in sorted(grouped.items()):
        if len(rows) == 2 and {r.get("teamId") for r in rows} == {100,200} and \
                {r.get("win") for r in rows} == {True,False}:
            valid.append({"id":mid, "blue":next(x for x in rows if x["teamId"] == 100),
                          "red":next(x for x in rows if x["teamId"] == 200)})
    return valid, len(grouped), len(shards)


def exact_sign_p(positive, negative):
    n = positive + negative
    if not n:
        return None
    tail = sum(math.comb(n,k) for k in range(min(positive,negative)+1))
    return min(1.0, 2.0 * tail / (2 ** n))


def ci(samples, alpha=0.05):
    values = sorted(v for v in samples if v is not None and math.isfinite(v))
    if len(values) < 100:
        return None
    return [round(values[int(alpha/2 * (len(values)-1))],4),
            round(values[int((1-alpha/2) * (len(values)-1))],4)]


def summarize_pair_diffs(pairs, field, cutoff=None):
    winmore = losemore = ties = 0
    differences = []
    for p in pairs:
        a = p["blue"]; b=p["red"]
        if cutoff is not None and abs(a["teamGold15"]) > cutoff:
            continue
        if a.get(field) is None or b.get(field) is None:
            continue
        w,l = (a,b) if a["win"] else (b,a)
        delta = float(w[field]) - float(l[field])
        differences.append(delta)
        if abs(delta) < 1e-9: ties += 1
        elif delta > 0: winmore += 1
        else: losemore += 1
    if not differences:
        return None
    means=[]
    for _ in range(1600):
        sample=[differences[RNG.randrange(len(differences))] for i in differences]
        means.append(sum(sample)/len(sample))
    return {
        "matches":len(differences), "winnerMore":winmore,"loserMore":losemore,
        "ties":ties,"winnerMoreNonTieFraction":round(winmore / (winmore+losemore),4)
                          if winmore+losemore else None,
        "pairedDifferenceWinnerMinusLoser":round(sum(differences)/len(differences),4),
        "pairedDifference95pctBootstrapCI":ci(means),
        "twoSidedExactSignP":round(exact_sign_p(winmore,losemore),5),
    }


def group_rates(pairs):
    stats = {v:{"wins":0,"n":0,"gold15Sum":0} for v in ("low","medium","high")}
    for pair in pairs:
        for row in (pair["blue"],pair["red"]):
            if row.get("adcSupportNear") is None:
                continue
            fraction=row["adcSupportNear"]
            band="low" if fraction<0.4 else ("high" if fraction>=0.6 else "medium")
            stats[band]["n"]+=1
            stats[band]["wins"]+=int(row["win"])
            stats[band]["gold15Sum"]+=row["teamGold15"]
    out={}
    for band, x in stats.items():
        out[band]={"nTeams":x["n"],"wins":x["wins"],
                   "winRate":round(x["wins"]/x["n"],4) if x["n"] else None,
                   "meanTeamGoldLead15":round(x["gold15Sum"]/x["n"]) if x["n"] else None}
    return out


def bootstrap_rate_diff(pairs):
    def diff(sample):
        high_w=high_n=low_w=low_n=0
        for pair in sample:
            for row in (pair["blue"],pair["red"]):
                x=row["adcSupportNear"]
                if x>=0.6:
                    high_n+=1;high_w+=row["win"]
                elif x<0.4:
                    low_n+=1;low_w+=row["win"]
        return high_w/high_n-low_w/low_n if high_n and low_n else None
    return {"highMinusLowWinRate":round(diff(pairs),4),
            "bootstrap95pctCI":ci([diff([pairs[RNG.randrange(len(pairs))]
                                           for _ in pairs]) for _ in range(1600)])}


def is_fountain(x,y):
    if x is None or y is None:return True
    return (x<1700 and y<1700) or (x>13400 and y>13400)


def strict_proximity(pair,start,end,threshold):
    # Ignore observations when either participant is near fountain or killed
    # in the previous 70s, as opposed to interpreting death/recall as roaming.
    blue=pair["blue"]
    red=pair["red"]
    container=blue.get("timelineData") or red.get("timelineData")
    if not container:return None
    teams={}
    for p in container.get("participants",[]):
        if p.get("role") in ("BOTTOM","UTILITY"):
            teams.setdefault(p["team"],{})[p["role"]]=str(p["pid"])
    events=container.get("events",[])
    stats={}
    for tid in (100,200):
        pos=teams.get(tid,{})
        if not {"BOTTOM","UTILITY"}.issubset(pos):return None
        aid,sid=pos["BOTTOM"],pos["UTILITY"]
        eligible=close=0
        for frame in container.get("frames",[]):
            t=frame.get("t",0)
            if not (start*60000<=t<end*60000):continue
            f=frame.get("p",{})
            a=f.get(aid);s=f.get(sid)
            if not a or not s:continue
            if is_fountain(a[0],a[1]) or is_fountain(s[0],s[1]):continue
            dead=any(e.get("victim") in (int(aid),int(sid))
                     and e.get("t") is not None and 0 <= t-e["t"] <= 70000
                     for e in events)
            if dead:continue
            eligible+=1
            close+=((a[0]-s[0])**2+(a[1]-s[1])**2)<=threshold**2
        if eligible<MIN_FRAMES:return None
        stats[tid]={"fraction":close/eligible,"nFrames":eligible}
    return stats


def strict_analysis(pairs):
    results={}
    for start,end in WINDOWS:
        for radius in DISTANCES:
            comparable=[]
            for pair in pairs:
                obs=strict_proximity(pair,start,end,radius)
                if not obs:continue
                blue={**pair["blue"],"strict":obs[100]["fraction"]}
                red={**pair["red"],"strict":obs[200]["fraction"]}
                comparable.append({"blue":blue,"red":red})
            summary=summarize_pair_diffs(comparable,"strict")
            if summary:
                summary["meanEligibleFramesPerTeam"]=round(
                    sum(strict_proximity(p,start,end,radius)[100]["nFrames"]+
                        strict_proximity(p,start,end,radius)[200]["nFrames"]
                        for p in comparable)/(2*len(comparable)),2)
            results[f"{start}-{end}_r{radius}"]=summary
    return results


def normal_cdf(x):
    return 0.5*(1+math.erf(x/math.sqrt(2)))


def invert(matrix):
    n=len(matrix)
    a=[[float(v) for v in row]+[float(i==j) for j in range(n)]
       for i,row in enumerate(matrix)]
    for col in range(n):
        pivot=max(range(col,n),key=lambda i:abs(a[i][col]))
        if abs(a[pivot][col])<1e-10:raise ValueError("Singular Hessian")
        a[col],a[pivot]=a[pivot],a[col]
        norm=a[col][col]
        a[col]=[v/norm for v in a[col]]
        for i in range(n):
            if i==col:continue
            factor=a[i][col]
            a[i]=[x-factor*y for x,y in zip(a[i],a[col])]
    return [row[n:] for row in a]


def fit_logit(observations,features):
    # Matched-game directional logistic; no intercept, as swapping teams
    # swaps y and all differences. Not a causal model.
    n=features
    beta=[0.0]*n
    ridge=0.00001
    for _ in range(65):
        g=[0.0]*n
        h=[[0.0]*n for j in range(n)]
        for x,y in observations:
            dot=sum(b*xi for b,xi in zip(beta,x))
            prob=1/(1+math.exp(-max(-30,min(30,dot))))
            weight=prob*(1-prob)
            for j in range(n):
                g[j]+=x[j]*(y-prob)
                for k in range(n):h[j][k]+=x[j]*x[k]*weight
        for j in range(n):
            h[j][j]+=ridge
            g[j]-=ridge*beta[j]
        inverse=invert(h)
        step=[sum(inverse[j][k]*g[k] for k in range(n)) for j in range(n)]
        beta=[b+v for b,v in zip(beta,step)]
        if max(abs(v) for v in step)<1e-8:break
    # Recompute expected information at optimized coefficients.
    h=[[0.0]*n for j in range(n)]
    for x,y in observations:
        z=sum(b*xi for b,xi in zip(beta,x))
        p=1/(1+math.exp(-max(-30,min(30,z))))
        for j in range(n):
            for k in range(n):
                h[j][k]+=x[j]*x[k]*p*(1-p)
    for j in range(n):h[j][j]+=ridge
    cov=invert(h)
    out=[]
    for j in range(n):
        se=math.sqrt(max(0,cov[j][j]))
        z=beta[j]/se if se else 0
        out.append({"beta":round(beta[j],5),"se":round(se,5),
                    "oddsRatio":round(math.exp(max(-20,min(20,beta[j]))),4),
                    "OR95pctCI":[round(math.exp(max(-20,min(20,beta[j]-1.96*se))),4),
                                  round(math.exp(max(-20,min(20,beta[j]+1.96*se))),4)],
                    "waldP":round(2*(1-normal_cdf(abs(z))),5)})
    return out


def adjusted_models(pairs):
    # 1 unit of "support" or "other" is 10 percentage points more presence
    # for blue relative to red; 1 unit of "gold" = 1000 total team gold.
    observations=[]
    for pair in pairs:
        a,b=pair["blue"],pair["red"]
        if any(r.get(k) is None for r in (a,b)
               for k in ("adcSupportNear","adcOtherNear","teamGold15")):continue
        observations.append((
            [(a["adcSupportNear"]-b["adcSupportNear"])*10,
             a["teamGold15"]/1000,
             (a["adcOtherNear"]-b["adcOtherNear"])*10],
            int(a["win"])))
    res={"nMatches":len(observations)}
    if len(observations)<30:return res
    models=((("supportOnly",),(0,)),(("goldOnly",),(1,)),
            (("support","gold"),(0,1)),(("support","gold","otherAllies"),(0,1,2)))
    for labels,idxs in models:
        label="+".join(labels)
        projected=[([x[i] for i in idxs],y) for x,y in observations]
        try:
            params=fit_logit(projected,len(idxs))
            res[label]={k:v for k,v in zip(labels,params)}
        except (ValueError,OverflowError):
            res[label]={"error":"could not fit"}
    return res


def main():
    pairs,total,shards=load_pairs()
    if not pairs:raise RuntimeError("No paired winning and losing ADCs archived")
    observed=group_rates(pairs)
    analysis={
        "schemaVersion":1,
        "method":"observational paired-match comparison, Bronze-III seeded EUW Solo/Duo",
        "definition":{
            "queueId":420,
            "adc":"BOTTOM role (may include APC champions)",
            "mainWindow":"minutes 15-25",
            "distance":"Euclidean map coordinates <=2500",
            "highPresence":">=60% of eligible midgame frames",
            "lowPresence":"<40% of eligible midgame frames",
            "baseline":"Original collection filters ADC fountain and 45 seconds after ADC death, but not support deaths/fountain",
            "strict":"Sensitivity checks exclude both ADC and support when at fountain or <=70 seconds after death",
            "confounders":"Total team gold difference at minute 15, compared within the same match; not proof of causation",
        },
        "coverage":{
            "uniqueMatchesFound":total,"pairedMatches":len(pairs),
            "teamRows":2*len(pairs),"shards":shards,
            "matchesMissingPairedTeams":total-len(pairs),
            "dataQuality":"Seeded through currently Bronze III summoners; not all 10 players independently ranked"},
        "unadjusted":{
            "supportDistanceBands":observed,
            "highVsLow":bootstrap_rate_diff(pairs),
            "winnerVsLoserPaired":summarize_pair_diffs(pairs,"adcSupportNear"),
            "otherAlliesPaired":summarize_pair_diffs(pairs,"adcOtherNear"),
            "anyAllyPaired":summarize_pair_diffs(pairs,"adcAnyNear"),
        },
        "matchedGoldStrata":{
            f"absoluteTeamGoldLeadAt15<={c}":
            summarize_pair_diffs(pairs,"adcSupportNear",c)
            for c in (500,1000,2000,3000)
        },
        "adjustedDirectionalLogistic":adjusted_models(pairs),
        "sensitivityAnalysis":strict_analysis(pairs),
        "limitations":[
            "Associations are observational; teams may group because they are already winning",
            "Riot supplies roughly one player position per minute; not continuous movement",
            "Same person may appear in multiple matches; uncertainty here treats matches as independent",
            "Win/loss teams in the same match are paired, not two independent samples",
            "Champion composition, death/recall, objectives and vision may confound results",
            "Bronze sample is seeded by current rank; other participants are not validated Bronze",
        ]
    }
    OUTPUT.write_text(json.dumps(analysis,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"pairedMatches":len(pairs),
                      "winnerMoreSupport":analysis["unadjusted"]["winnerVsLoserPaired"],
                      "supportAdjustedForGold":analysis["adjustedDirectionalLogistic"].get("support+gold",{}),
                      "strict":analysis["sensitivityAnalysis"].get("15-25_r2500")},ensure_ascii=False))


if __name__=="__main__":
    main()
