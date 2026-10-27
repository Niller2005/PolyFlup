"""Leg-out / completion statistics per symbol, combined price, fill timing from the master paper log.
Usage: uv run python .opencode/skill/strategy-analysis/pair_outcomes.py [logs/trades_paper.log]"""
import re, collections, statistics as st, glob, sys
from datetime import datetime
rx_ts=re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) UTC\]")
rx_sym=re.compile(r"\[([A-Z]+(?:-5M)?)\]")
rx_place=re.compile(r"Placing ATOMIC entry\+hedge: (UP|DOWN) ([\d.]+) @ \$([\d.]+) \+ (UP|DOWN) [\d.]+ @ \$([\d.]+)")
rx_sell=re.compile(r"Emergency sell filled(?: instantly)?: ([\d.]+) @ \$([\d.]+)")
rx_paper_sell=re.compile(r"PAPER SELL ([\d.]+) @ \$([\d.]+) \(taker, fee \$([\d.]+)\)")
rx_crit=re.compile(r"CRITICAL: (Entry|UP|DOWN) filled \(([\d.]+)\) but (?:hedge|UP|DOWN) timed out")
pairs=[]; cur={}
files=sys.argv[1:] or ["logs/trades_paper.log"]
for f in files:
  for line in open(f,errors="replace"):
    m=rx_ts.match(line)
    if not m: continue
    ts=datetime.strptime(m.group(1),"%Y-%m-%d %H:%M:%S")
    s=rx_sym.search(line[22:]); 
    if not s: continue
    sym=s.group(1)
    p=rx_place.search(line)
    if p:
      if sym in cur and cur[sym].get("out") is None: cur[sym]["out"]="lost-track"; 
      cur[sym]=dict(sym=sym,ts=ts,entry_side=p.group(1),size=float(p.group(2)),e=float(p.group(3)),h=float(p.group(5)),out=None,sold=None,fee=0)
      pairs.append(cur[sym]); continue
    c=cur.get(sym)
    if not c: continue
    if "Both orders filled" in line: c["out"]="both"
    elif "TIMEOUT: Both orders not filled" in line: c["out"]="timeout"
    elif c["out"]=="timeout":
      cr=rx_crit.search(line)
      if cr: c["filled_leg"]="entry" if cr.group(1)=="Entry" else "hedge"
      if "HOLDING" in line and "despite" in line: c["resolution"]="hold"
      if "emergency selling" in line or "selling both" in line: c["resolution"]="sell"
      ps=rx_sell.search(line)
      if ps and c.get("sold") is None: c["sold"]=float(ps.group(2)); c["fee"]=0.07*c["sold"]*(1-c["sold"])*c["size"]
      if "Cannot emergency sell" in line: c["resolution"]="orphan"
    if "Trade skipped - atomic" in line or "Trade #" in line and "placed successfully" in line: pass
n=len(pairs); cnt=collections.Counter(p["out"] for p in pairs)
print("pairs",n,dict(cnt))
done=[p for p in pairs if p["out"] in("both","timeout")]
to=[p for p in done if p["out"]=="timeout"]
print("leg-out rate %.1f%% of %d"%(100*len(to)/len(done),len(done)))
# by symbol
for key,f in (("sym",lambda p:p["sym"]),("market",lambda p:"5m" if p["sym"].endswith("5M") else "15m")):
  g=collections.defaultdict(lambda:[0,0])
  for p in done: g[f(p)][0]+=1; g[f(p)][1]+= p["out"]=="timeout"
  print(key,{k:"%d/%d=%.0f%%"%(v[1],v[0],100*v[1]/v[0]) for k,v in sorted(g.items())})
# by combined
def comb(p): return round(p["e"]+p["h"],2)
g=collections.defaultdict(lambda:[0,0])
for p in done: g[comb(p)][0]+=1; g[comb(p)][1]+=p["out"]=="timeout"
print("by combined",{k:"%d/%d=%.0f%%"%(v[1],v[0],100*v[1]/v[0]) for k,v in sorted(g.items())})
# by favourite price: max(e,h)
def bucket(x): return "<.35" if x<.35 else "<.5" if x<.5 else "<.65" if x<.65 else ">=.65"
g=collections.defaultdict(lambda:[0,0])
for p in done: b=bucket(max(p["e"],p["h"])); g[b][0]+=1; g[b][1]+=p["out"]=="timeout"
print("by fav leg price",{k:"%d/%d=%.0f%%"%(v[1],v[0],100*v[1]/v[0]) for k,v in sorted(g.items())})
# which leg fills when it times out: favourite (higher price) or underdog?
fav=und=0; loss=[]
for p in to:
  fl=p.get("filled_leg")
  if not fl: continue
  price=p["e"] if fl=="entry" else p["h"]; other=p["h"] if fl=="entry" else p["e"]
  if price>other: fav+=1
  elif price<other: und+=1
print("timeout: filled leg was higher-priced",fav,"lower-priced",und)
# loss on sell
ls=[(p["sold"]-(p["e"] if p.get("filled_leg")=="entry" else p["h"]))*p["size"]-p["fee"] for p in to if p.get("sold") and p.get("resolution")=="sell" and p.get("filled_leg")]
print("sold leg-outs n=%d total %.1f avg %.2f median %.2f"%(len(ls),sum(ls),st.mean(ls) if ls else 0,st.median(ls) if ls else 0))
res=collections.Counter(p.get("resolution","?") for p in to); print("resolution",dict(res))
# expected pair profit when both fill
edge=[(1-comb(p))*p["size"] for p in done if p["out"]=="both"]
print("both-fill pairs n=%d total edge %.1f avg %.2f"%(len(edge),sum(edge),st.mean(edge)))
print("NET from pairs (edge+leg-out sells) = %.1f"%(sum(edge)+sum(ls)))
# by hour
g=collections.defaultdict(lambda:[0,0])
for p in done: g[p["ts"].hour][0]+=1; g[p["ts"].hour][1]+=p["out"]=="timeout"
print("by hour",{k:"%d/%d"%(v[1],v[0]) for k,v in sorted(g.items())})
# by seconds into window (5m windows only)
g=collections.defaultdict(lambda:[0,0])
for p in done:
  w=300 if p["sym"].endswith("5M") else 900
  e=(p["ts"].hour*3600+p["ts"].minute*60+p["ts"].second)%w
  b=int(e//(w/5)); g[(w,b)][0]+=1; g[(w,b)][1]+=p["out"]=="timeout"
print("by window fifth",{k:"%d/%d"%(v[1],v[0]) for k,v in sorted(g.items())})
print()
rx_lr=re.compile(r"Leg-risk EV/sh: taker-hedge ([-+\d.]+)¢ \(ask \$([\d.]+)\) \| flatten ([-+\d.]+)¢ \| hold ([-+\d.]+)¢")
# attach first leg-risk per pair
cur={}; first={}
for f in files:
  for line in open(f,errors="replace"):
    m=rx_ts.match(line)
    if not m: continue
    s=rx_sym.search(line[22:])
    if not s: continue
    sym=s.group(1)
    if rx_place.search(line): cur[sym]=len([1 for _ in first])+id(object()); key=(sym,line[:21]); cur[sym]=key; continue
    lr=rx_lr.search(line)
    if lr and sym in cur and cur[sym] not in first: first[cur[sym]]=tuple(float(x) for x in lr.groups())
print("pairs with leg-risk line:",len(first))
import statistics
fl=[v[2] for v in first.values()]; ho=[v[3] for v in first.values()]; th=[v[0] for v in first.values()]
print("leg-risk at first check: flatten mean %.1f¢, hold mean %.1f¢, taker-hedge mean %.1f¢"%(statistics.mean(fl),statistics.mean(ho),statistics.mean(th)))
print("realised sold loss per share (timeouts, sold): mean %.1f¢"%(100*statistics.mean([ (p["sold"]-(p["e"] if p.get("filled_leg")=="entry" else p["h"])) for p in to if p.get("sold") and p.get("resolution")=="sell" and p.get("filled_leg")])))
