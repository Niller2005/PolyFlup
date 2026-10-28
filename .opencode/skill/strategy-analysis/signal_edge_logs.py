"""Confidence calibration from the master log + Binance klines (use before `evaluations` has data).
Usage: uv run python .opencode/skill/strategy-analysis/signal_edge_logs.py"""
import re, requests, collections, statistics as st, math
from datetime import datetime, timezone, timedelta
rx_ts=re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC\]")
rx_ab=re.compile(r"\[([A-Z]+(?:-5M)?)\] 🧪 A/B TEST: Using (BAYESIAN|ADDITIVE) confidence \(p_up=([\d.]+)\): ([\d.]+)%")
rx_bias=re.compile(r"\[([A-Z]+(?:-5M)?)\].*?(?:WAIT ZONE: |Trend Following: Confidence: [\d.]+% \(|⚠️  )?(?:\b(UP|DOWN)\b (?:pricing|is UNDERDOG|\()|WAIT ZONE: (UP|DOWN))")
evs=[]; pend={}
lines=open("logs/trades_paper.log",errors="replace").read().splitlines()
for i,line in enumerate(lines):
    m=rx_ts.match(line)
    if not m: continue
    ab=rx_ab.search(line)
    if ab:
        ts=datetime.strptime(m.group(1),"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        sym=ab.group(1)
        bias=None
        for l2 in lines[i+1:i+8]:
            if f"[{sym}]" in l2:
                mm=re.search(r"WAIT ZONE: (UP|DOWN)|\b(UP|DOWN) pricing|(UP|DOWN) is (?:UNDERDOG|FAVORITE)|Confidence: [\d.]+% \((UP|DOWN)\)",l2)
                if mm: bias=[g for g in mm.groups() if g][0]; break
        if bias: evs.append((ts,sym,ab.group(2),float(ab.group(3)),float(ab.group(4))/100,bias))
print("evaluations with bias:",len(evs))
assets={"BTC":"BTCUSDT","ETH":"ETHUSDT","SOL":"SOLUSDT","XRP":"XRPUSDT","DOGE":"DOGEUSDT"}
t0=min(e[0] for e in evs).timestamp()-1000; t1=max(e[0] for e in evs).timestamp()+1000
kl={}
for a,pair in assets.items():
    rows=[]; s=int(t0*1000)
    while s<t1*1000:
        r=requests.get("https://api.binance.com/api/v3/klines",params={"symbol":pair,"interval":"1m","startTime":s,"limit":1000},timeout=20).json()
        if not r: break
        rows+=r; s=r[-1][0]+60000
    kl[a]={int(x[0]//1000):(float(x[1]),float(x[4])) for x in rows}
def outcome(sym,ts):
    a=sym.split("-")[0]; w=300 if sym.endswith("5M") else 900
    ws=int(ts.timestamp()//w*w); we=ws+w
    if ws not in kl[a] or (we-60) not in kl[a] or we>time_now: return None,None
    return kl[a][we-60][1]>kl[a][ws][0], ts.timestamp()-ws
import time; time_now=time.time()-120
data=[]
for ts,sym,meth,pup,conf,bias in evs:
    r,tin=outcome(sym,ts)
    if r is None: continue
    price=pup if bias=="UP" else 1-pup   # market mid of the biased side
    win=(bias=="UP")==r
    data.append((conf,price,win,tin,sym,meth))
print("with outcome:",len(data))
def show(title,key,buckets):
    print(title)
    g=collections.defaultdict(list)
    for d in data: g[key(d)].append(d)
    for k in buckets:
        v=g.get(k,[])
        if len(v)<8: continue
        print("  %-10s n=%4d  hit %.3f  avg market price of that side %.3f  edge(hit-price) %+.3f"%(k,len(v),st.mean(x[2] for x in v),st.mean(x[1] for x in v),st.mean(x[2] for x in v)-st.mean(x[1] for x in v)))
show("by confidence",lambda d:"<.2" if d[0]<.2 else ".2-.4" if d[0]<.4 else ".4-.6" if d[0]<.6 else ".6-.8" if d[0]<.8 else ">=.8",["<.2",".2-.4",".4-.6",".6-.8",">=.8"])
show("by method",lambda d:d[5],["BAYESIAN","ADDITIVE"])
show("by market price of biased side",lambda d:"<.35" if d[1]<.35 else ".35-.5" if d[1]<.5 else ".5-.65" if d[1]<.65 else ".65-.8" if d[1]<.8 else ">=.8",["<.35",".35-.5",".5-.65",".65-.8",">=.8"])
hi=[d for d in data if d[0]>=.6]
print("high-confidence (>=.6) by price side:", {k:(len(v),round(st.mean(x[2] for x in v),3),round(st.mean(x[1] for x in v),3)) for k,v in {"price<.5":[d for d in hi if d[1]<.5],"price>=.5":[d for d in hi if d[1]>=.5]}.items() if v})
