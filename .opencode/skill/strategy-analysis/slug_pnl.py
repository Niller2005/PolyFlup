"""Ground-truth PnL per market window from paper_fills (includes leg-outs the trades table misses).
Usage: uv run python .opencode/skill/strategy-analysis/slug_pnl.py [trades_paper.db]"""
import sqlite3, sys, collections, statistics as st
c=sqlite3.connect("file:%s?mode=ro" % (sys.argv[1] if len(sys.argv) > 1 else "trades_paper.db"), uri=True)
slug_of={}
for tok,slug in c.execute("select token_id,slug from paper_orders where slug is not null"): slug_of[tok]=slug
for tok,slug in c.execute("select token_id,slug from paper_positions"): slug_of.setdefault(tok,slug)
open_tok={t for (t,) in c.execute("select token_id from paper_positions where size>0.001")}
d=collections.defaultdict(lambda: dict(buy=collections.defaultdict(float),cash=0.0,fee=0.0,sold=0.0,redeem=0.0,tokens=set()))
miss=0
for ts,tok,side,price,size,fee,liq in c.execute("select ts,token_id,side,price,size,fee,liquidity from paper_fills order by id"):
    s=slug_of.get(tok)
    if not s: miss+=1; continue
    x=d[s]
    if side=="BUY": x["cash"]-=price*size; x["buy"][tok]+=size; x["tokens"].add(tok)
    elif side=="SELL": x["cash"]+=price*size-fee; x["sold"]+=size; x["fee"]+=fee
    else: x["cash"]+=price*size; x["redeem"]+=size
print("fills w/o slug",miss,"slugs",len(d))
cats=collections.defaultdict(list)
for s,x in d.items():
    if x["tokens"]&open_tok: continue
    nb=len(x["buy"])
    typ="pair(2 tokens)" if nb>=2 else "single-leg"
    if nb==1:
        typ+=" sold" if x["sold"]>0 else " held->redeem" if x["redeem"]>0 else " held->lost"
    mk="5m" if "-5m-" in s else "15m"
    cats[(typ,)].append(x["cash"]); cats[(typ,mk)].append(x["cash"])
for k,v in sorted(cats.items()):
    print(k,"n=%d total %.1f avg %.2f win%% %.0f"%(len(v),sum(v),st.mean(v),100*sum(1 for a in v if a>0)/len(v)))
print()
rows=[]
for s,x in d.items():
    if x["tokens"]&open_tok: continue
    rows.append((x["cash"],s,dict(x["buy"]),x["sold"],x["redeem"]))
rows.sort()
for cash,s,b,sold,red in rows[:12]: print("%.1f %s buys=%s sold=%.0f redeem=%.0f"%(cash,s,[round(v) for v in b.values()],sold,red))
print("top winners");
for cash,s,b,sold,red in rows[-5:]: print("%.1f %s buys=%s sold=%.0f redeem=%.0f"%(cash,s,[round(v) for v in b.values()],sold,red))
import itertools
vals=[r[0] for r in rows]; print("total closed %.1f n=%d; worst10 sum %.1f; rest %.1f"%(sum(vals),len(vals),sum(vals[:10]),sum(vals[10:])))
