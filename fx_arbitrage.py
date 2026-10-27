"""
Simple Forex Arbitrage Finder + Visualizer with CSV loader.
Requires yfinance, networkx and matplotlib (see requirements.txt).
Features:
-Can also fetch live forex quotes from Yahoo Finance using yfinance.
-Supports both CSV input and live fetching.
-Can also visualize the forex graph and highlight arbitrage cycles using networkx + matplotlib.
Requirements:
-For CSV input: the script still imports all three packages at startup.
-For live fetching: install yfinance via `pip install yfinance` and internet access is required.
-For visualization: install networkx and matplotlib via `pip install networkx matplotlib`
"""
"""How to run this script:
1.Open a terminal.
2.Navigate to the directory containing this script and the quotes.csv file.
3.Run the script with the command:
python fx_arbitrage.py --csv quotes.csv --fee x --min-profit-pct y --draw --out graph.png --show
OR
python fx_arbitrage.py --live --fee x --min-profit-pct y --draw --out graph.png --show
--fee, --min-profit-pct, and --show are optional parameters.
4.You can adjust the --fee and --min-profit-pct parameters as needed (By default they are 0.0 and 1e-6 respectively).
5.The script will output whether an arbitrage opportunity was found, the cycle of trades, and the profit percentage and also draw the forex graph with highlighted cycle.
"""
""" quotes.csv file should contain the forex quotes in the following format:
eg;
pair,bid,ask
A/B,1.2345,1.2350
B/C,0.9876,0.9880
A/C,1.2222,1.2230
C/D,1.4567,1.4575
"""
import csv
import math
import argparse
from typing import List, Dict, Optional
from itertools import combinations
import yfinance as yf
import time
import networkx as nx
import matplotlib.pyplot as plt
TOL=1e-15
def parse_pair(pair: str):
    base, quote = pair.split('/')
    return base.strip(), quote.strip()
def build_edges_from_quotes(quotes: List[Dict], fee: float = 0.0):
    """ Returns (nodes_set, edges_list)
    edges_list contains tuples: (u, v, rate, side)
    where side is 'bid' for base->quote (selling base at bid)
    side is 'ask' for quote->base (selling quote at 1/ask)
    """
    nodes = set()
    edges = []  # (u, v, rate)
    multiplier = 1.0 - fee
    eps = 1e-18
    for q in quotes:
        pair = q.get("pair")
        if pair is None:
            continue
        base, quote = parse_pair(pair)
        try:
            bid = float(q["bid"])
            ask = float(q["ask"])
        except Exception as e:
            raise ValueError(f"Invalid bid/ask values for pair {pair}: {e}")
        nodes.add(base); nodes.add(quote)
        # base -> quote (sell base at bid)
        if bid > eps:
            rate_bq = bid * multiplier
            edges.append((base, quote, rate_bq,'bid'))
        # quote -> base (sell quote at 1/ask)
        if ask > eps:
            rate_qb = (1.0 / ask) * multiplier
            edges.append((quote, base, rate_qb,'ask'))
    return nodes, edges
def find_negative_cycle(nodes, edges, debug: bool = False) -> Optional[list]:
    """
    Returns a closed cycle (list of node names where cycle[0] == cycle[-1]) in forward trading order, or None if no negative cycle found.
    """
    # deterministic ordering
    node_list = sorted(list(nodes))
    idx = {n: i for i, n in enumerate(node_list)}
    N = len(node_list)
    if N == 0:
        return None
    bf_edges = []
    for u, v, rate, _side in edges:
        if rate is None or rate <= 0:
            continue
        ui, vi = idx[u], idx[v]
        w = -math.log(rate)
        bf_edges.append((ui, vi, w))
    # Standard Bellman-Ford relaxation
    dist = [0.0] * N
    parent = [-1] * N
    for _ in range(N - 1):
        updated = False
        for ui, vi, w in bf_edges:
            if dist[ui] + w < dist[vi] - TOL:
                dist[vi] = dist[ui] + w
                parent[vi] = ui
                updated = True
        if not updated:
            break
    # Find a cycle by checking for further relaxation
    for ui, vi, w in bf_edges:
        if dist[ui] + w < dist[vi] - TOL:
            # found negative cycle edge ui->vi; walk back to find a node on the cycle
            x = vi
            for _ in range(N):
                x = parent[x]
                if x == -1: break
            if x == -1:
                continue
            # collect cycle nodes (indices) by walking until we return to x
            cycle_idx = []
            cur = x
            while True:
                cycle_idx.append(cur)
                cur = parent[cur]
                if cur == x or cur == -1:
                    break
            # finalize: produce node names in forward order and close the loop
            cycle_idx.append(x)
            cycle_nodes = [node_list[i] for i in reversed(cycle_idx)]
            # Ensure closed: first == last
            if cycle_nodes[0] != cycle_nodes[-1]:
                cycle_nodes.append(cycle_nodes[0])
            return cycle_nodes
    return None
def compute_cycle_profit(cycle: List[str], edges: List[tuple]) -> Dict:
    # edges: (u,v,rate,side)
    # prefer the best (maximum) rate for a directed pair if duplicates exist
    rate_map = {}
    for (u,v,rate,side) in edges:
        if (u,v) not in rate_map or rate > rate_map[(u,v)]:
            rate_map[(u,v)] = rate
    amt = 1.0
    leg_rates = []
    for i in range(len(cycle)-1):
        u = cycle[i]; v = cycle[i+1]
        r = rate_map.get((u,v))
        if r is None: raise ValueError(f"No rate for leg {u}->{v} in provided edges.")
        leg_rates.append({"from":u, "to":v, "rate": r})
        amt *= r
    profit_pct = (amt - 1.0) * 100.0
    return {"start": cycle[0], "final_amount": amt, "profit_pct": profit_pct, "legs": leg_rates}
def find_arbitrage(quotes: List[Dict], fee: float = 0.0, min_profit_pct: float = 1e-8):
    nodes, edges = build_edges_from_quotes(quotes, fee=fee)
    cycle = find_negative_cycle(nodes, edges)
    if not cycle:
        return {"found": False, "reason": "no arbitrage cycle detected"}
    info = compute_cycle_profit(cycle, edges)
    if info["profit_pct"] < min_profit_pct*100.0 :
        return {"found": False, "reason": "profit below threshold", "profit_pct": info["profit_pct"]}
    return {"found": True, "cycle": cycle, **info}
# ---- CSV loader & convenience wrapper ----
def load_quotes_from_csv(path: str, pair_col: str = "pair", bid_col: str = "bid", ask_col: str = "ask", delimiter: str = ",") -> List[Dict]:
    """
    Load quotes from CSV file.
    Expects header with at least pair,bid,ask (case-sensitive by default).
    Returns list of dicts: {"pair":..., "bid":..., "ask":...}
    """
    # python fx_arbitrage.py --csv quotes.csv
    quotes = []
    with open(path, newline='') as f:
        reader = csv.DictReader(f, delimiter=delimiter)
        # check required columns
        headers = [h for h in reader.fieldnames] if reader.fieldnames else []
        if pair_col not in headers or bid_col not in headers or ask_col not in headers:
            raise ValueError(f"CSV missing required columns. Found headers: {headers}. Need '{pair_col}','{bid_col}','{ask_col}'")
        for row in reader:
            # skip empty lines
            if not row or not row.get(pair_col):
                continue
            entry = {
                "pair": row[pair_col].strip(),
                "bid": row[bid_col].strip(),
                "ask": row[ask_col].strip()
            }
            # allow numeric parsing errors to bubble up informatively
            try:
                entry["bid"] = float(entry["bid"])
                entry["ask"] = float(entry["ask"])
            except Exception as e:
                raise ValueError(f"Invalid numeric value in row {row}: {e}")
            quotes.append(entry)
    return quotes
def find_arbitrage_from_csv(path: str, fee: float = 0.0, min_profit_pct: float = 1e-8):
    quotes = load_quotes_from_csv(path)
    return find_arbitrage(quotes, fee=fee, min_profit_pct=min_profit_pct)
from matplotlib.patches import FancyArrowPatch
def visualize_graph(nodes, edges, cycle: Optional[List[str]] = None, out_file: str = "graph.png", show: bool = False, debug: bool = False):
    """
    Directed visualization that:
    - preserves all directed edges (MultiDiGraph),
    - draws A->B and B->A with opposite curvature so arrowheads don't overlap,
    - uses FancyArrowPatch so arrowheads reliably point to the destination node.
    Input `edges` must be a list of (u, v, rate, side) tuples.
    """
    if nx is None or plt is None:
        raise RuntimeError("Visualization requires networkx+matplotlib. Install: pip install networkx matplotlib")
    # Build MultiDiGraph (preserve parallel directed edges)
    G = nx.MultiDiGraph()
    for i, (u, v, rate, side) in enumerate(edges):
        key = f"{side}_{i}"
        G.add_edge(u, v, key=key, rate=rate, side=side)
    # Layout (fixed seed for reproducible positions)
    try:
        pos = nx.spring_layout(G, k=0.5, iterations=200, seed=42)
    except Exception:
        pos = nx.circular_layout(G)
    if debug:
        print("DEBUG: Nodes:", list(G.nodes()))
        print("DEBUG: Raw directed edges (u,v,rate,side):")
        for u, v, k, data in G.edges(keys=True, data=True):
            print("  ", (u, v, data.get('rate'), data.get('side')))
    fig, ax = plt.subplots(figsize=(12, 9))
    # Draw nodes & labels (highlight cycle nodes)
    cycle_set = set(cycle) if cycle else set()
    node_colors = ['orange' if n in cycle_set else 'lightgray' for n in G.nodes()]
    nx.draw_networkx_nodes(G, pos, node_size=900, node_color=node_colors, edgecolors='k', ax=ax)
    nx.draw_networkx_labels(G, pos, font_size=10, ax=ax)
    # Group edges by unordered pair so opposite directions can get opposite curvature
    pair_groups = {}  # frozenset({u,v}) -> list of (u,v,key,rate,side)
    for u, v, key, data in G.edges(keys=True, data=True):
        entry = (u, v, key, data.get('rate', 0.0), data.get('side', ''))
        k = frozenset({u, v})
        pair_groups.setdefault(k, []).append(entry)
    # Build cycle directed pairs for highlighting
    cycle_pairs = set()
    if cycle and len(cycle) >= 2:
        for i in range(len(cycle) - 1):
            cycle_pairs.add((cycle[i], cycle[i+1]))
    # Helper: radii generation for n parallel edges in same direction
    def radii_for_count(n, spacing=0.18):
        if n <= 0:
            return []
        if n == 1:
            return [spacing]
        # e.g., n=1 -> [spacing], n=2 -> [0.5*spacing,1.5*spacing], n=3 -> [.5,1.5,2.5]*spacing
        return [ (i + 0.5) * spacing for i in range(n) ]
    curve_labels = []  # (text, start, end, rad, t) for labels of curved edges
    # Draw edges per unordered pair; a->b get positive radii, b->a negative radii
    for fs, entries in pair_groups.items():
        nodes_list = sorted(list(fs))
        if len(nodes_list) != 2:
            # self-loop or single node group - draw straight arrows
            for (u, v, key, rate, side) in entries:
                x1, y1 = pos[u]; x2, y2 = pos[v]
                is_cycle = (u, v) in cycle_pairs
                linewidth = 3.0 if is_cycle else 1.2
                color = 'tab:orange' if is_cycle else 'tab:blue'
                arrow = FancyArrowPatch((x1, y1), (x2, y2),
                                        connectionstyle="arc3,rad=0.0",
                                        arrowstyle='-|>',
                                        mutation_scale=18,
                                        linewidth=linewidth,
                                        linestyle='solid' if side == 'bid' else 'dashed',
                                        color=color,
                                        shrinkA=12, shrinkB=8, zorder=2)
                ax.add_patch(arrow)
                # label
                mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                ax.text(mx, my, f"{rate:.6f}\n{side}", fontsize=8, ha='center', va='center', backgroundcolor='white', zorder=3)
            continue
        a, b = nodes_list[0], nodes_list[1]
        a_to_b = [e for e in entries if e[0] == a and e[1] == b]
        b_to_a = [e for e in entries if e[0] == b and e[1] == a]
        rad_ab = radii_for_count(len(a_to_b))
        rad_ba = radii_for_count(len(b_to_a))
        # draw a->b with positive radii (curve one way)
        for idx, (u, v, key, rate, side) in enumerate(a_to_b):
            rad = rad_ab[idx] if idx < len(rad_ab) else (0.18 * (idx+1))
            conn = f"arc3,rad={rad}"
            is_cycle = (u, v) in cycle_pairs
            linewidth = 3.0 if is_cycle else 1.2
            color = 'tab:orange' if is_cycle else 'tab:blue'
            x1, y1 = pos[u]; x2, y2 = pos[v]
            arrow = FancyArrowPatch((x1, y1), (x2, y2),
                                    connectionstyle=conn,
                                    arrowstyle='-|>',
                                    mutation_scale=18,
                                    linewidth=linewidth,
                                    linestyle='solid' if side == 'bid' else 'dashed',
                                    color=color,
                                    shrinkA=12, shrinkB=8, zorder=2)
            ax.add_patch(arrow)
            # label: moved onto the middle of its curve after the final layout (see below)
            lbl = ax.text((x1 + x2) / 2.0, (y1 + y2) / 2.0, f"{rate:.6f}\n{side}", fontsize=8,
                          ha='center', va='center', backgroundcolor='white', zorder=3)
            curve_labels.append((lbl, (x1, y1), (x2, y2), rad, 0.38 if idx == 0 else 0.5))
        # draw b->a with negative radii (curve opposite way)
        for idx, (u, v, key, rate, side) in enumerate(b_to_a):
            # b->a is drawn in the reverse direction, which already flips the side arc3 bulges to,
            # so a positive radius puts it on the opposite side from a->b
            rad = rad_ba[idx] if idx < len(rad_ba) else (0.18 * (idx+1))
            conn = f"arc3,rad={rad}"
            is_cycle = (u, v) in cycle_pairs
            linewidth = 3.0 if is_cycle else 1.2
            color = 'tab:orange' if is_cycle else 'tab:blue'
            x1, y1 = pos[u]; x2, y2 = pos[v]
            arrow = FancyArrowPatch((x1, y1), (x2, y2),
                                    connectionstyle=conn,
                                    arrowstyle='-|>',
                                    mutation_scale=18,
                                    linewidth=linewidth,
                                    linestyle='solid' if side == 'bid' else 'dashed',
                                    color=color,
                                    shrinkA=12, shrinkB=8, zorder=2)
            ax.add_patch(arrow)
            # label: moved onto the middle of its curve after the final layout (see below)
            lbl = ax.text((x1 + x2) / 2.0, (y1 + y2) / 2.0, f"{rate:.6f}\n{side}", fontsize=8,
                          ha='center', va='center', backgroundcolor='white', zorder=3)
            curve_labels.append((lbl, (x1, y1), (x2, y2), rad, 0.38 if idx == 0 else 0.5))
    ax.autoscale_view()
    ax.set_axis_off()
    plt.title('Directed Forex graph — bid edges solid, ask edges dashed; cycle highlighted')
    plt.tight_layout()
    # Put each curved-edge label on its curve. matplotlib's arc3 builds the control point in
    # display (pixel) space as midpoint + rad*(dy, -dx); the curve is a quadratic Bezier, so the
    # point at fraction t is (1-t)^2*start + 2t(1-t)*control + t^2*end.
    def place_labels(event=None):
        inv = ax.transData.inverted()
        for lbl, p1, p2, rad, t in curve_labels:
            X1, Y1 = ax.transData.transform(p1)
            X2, Y2 = ax.transData.transform(p2)
            cX = (X1 + X2) / 2.0 + rad * (Y2 - Y1)
            cY = (Y1 + Y2) / 2.0 - rad * (X2 - X1)
            wa, wb, wc = (1 - t) ** 2, 2 * t * (1 - t), t ** 2
            lbl.set_position(inv.transform((wa * X1 + wb * cX + wc * X2,
                                            wa * Y1 + wb * cY + wc * Y2)))
        if event is not None:
            fig.canvas.draw_idle()
    fig.canvas.draw()
    place_labels()
    fig.canvas.mpl_connect('resize_event', place_labels)  # keeps labels on the arrows in --show windows
    plt.savefig(out_file, dpi=200)
    print(f"Saved graph visualization to: {out_file}")
    if show:
        plt.show()
    plt.close(fig)
# --- dynamic ticker generation ---
# Choose the currencies you want (majors + some crosses)
# (we reuse the `currencies` list below for ticker generation)
# If you want ALL ordered pairs (N*(N-1) tickers) set ALL_PAIRS = True.
# If you only want pairs vs USD (common and much smaller), set ALL_PAIRS = False.
currencies = [
    "USD","EUR","GBP","JPY","AUD","CAD","CHF","NZD",
    "SGD","HKD","NOK","SEK","DKK","ZAR","INR","MXN","CNY","BRL","TRY"]
ALL_PAIRS = True
def make_yahoo_tickers(currency_list, all_pairs=True):
    tickers = []
    if all_pairs:
        for base in currency_list:
            for quote in currency_list:
                if base == quote:
                    continue
                # Yahoo ticker style: EURUSD=X
                tickers.append(f"{base}{quote}=X")
    else:
        # only base/USD and USD/quote pairs (fewer)
        for cur in currency_list:
            if cur == "USD":
                continue
            # cur/USD and USD/cur
            tickers.append(f"{cur}USD=X")
            tickers.append(f"USD{cur}=X")
    # remove duplicates and return
    return list(dict.fromkeys(tickers))
# Output file name (kept for compatibility if someone wants to write)
OUT_CSV = "real_data.csv"
# Synthetic spread fraction when real bid/ask aren't available (e.g. 0.0002 = 0.02%)
DEFAULT_SPREAD_FRACTION = 0.0002
def yf_symbol_to_pair(symbol: str) -> str:
    """Convert Yahoo symbol like 'EURUSD=X' to 'EUR/USD'"""
    sym = symbol.replace("=X", "")
    if len(sym) == 6:
        return sym[:3] + "/" + sym[3:]
    # fallback - just replace =X
    return sym
def safe_get_bid_ask(ticker_obj):
    """
    Return (bid, ask) if available; else (None, None).
    yfinance's .info may include 'bid' and 'ask' keys but not always.
    """
    info = {}
    try:
        info = ticker_obj.info or {}
    except Exception:
        # sometimes yfinance raises for certain tickers
        info = {}
    bid = info.get("bid")
    ask = info.get("ask")
    # Try other fields if explicit bid/ask missing
    if (bid is None or ask is None):
        # try regularMarketPrice or previousClose as a mid-price fallback
        mid = info.get("regularMarketPrice") or info.get("previousClose") or info.get("open")
        if mid is not None:
            # Use a small synthetic spread proportional to price
            spread = DEFAULT_SPREAD_FRACTION * float(mid)
            bid = float(mid) - spread/2
            ask = float(mid) + spread/2
        else:
            # give up
            return None, None
    return float(bid), float(ask)
def fetch_quotes(tickers: List[str]):
    rows = []
    for tk in tickers:
        try:
            t = yf.Ticker(tk)
        except Exception as e:
            print(f"Skipping {tk}: cannot create Ticker ({e})")
            continue
        pair = yf_symbol_to_pair(tk)
        bid, ask = safe_get_bid_ask(t)
        if bid is None or ask is None:
            print(f"Could not get price for {tk} / {pair} — skipping")
            continue
        rows.append({"pair": pair, "bid": bid, "ask": ask})
        # Be polite — avoid too-fast calls (yfinance may fetch each ticker)
        time.sleep(0.2)
    return rows
def write_csv(path: str, rows):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["pair", "bid", "ask"])
        writer.writeheader()
        for r in rows:
            writer.writerow({"pair": r["pair"], "bid": f"{r['bid']:.10f}", "ask": f"{r['ask']:.10f}"})
    print(f"Wrote {len(rows)} quotes to {path}")
# Precompute tickers from the currency list
TICKERS = make_yahoo_tickers(currencies, all_pairs=ALL_PAIRS)
# ---- CLI ----
def main():
    parser = argparse.ArgumentParser(description="Forex Arbitrage Finder + Visualizer")
    parser.add_argument("--csv", "-c", required=False, help="Path to quotes CSV (header: pair,bid,ask). If omitted, use --live to fetch from yfinance.")
    parser.add_argument("--live", "-l", action="store_true", help="Fetch live quotes from yfinance (mutually exclusive with --csv).")
    parser.add_argument("--fee", "-f", type=float, default=0.0, help="Per-trade multiplicative fee (e.g., 0.0002 for 0.02%)")
    parser.add_argument("--min-profit-pct", type=float, default=1e-6, help="Minimum profit percent to report (default tiny)")
    parser.add_argument("--draw", action='store_true', help="Draw and save graph visualization (requires networkx+matplotlib)")
    parser.add_argument("--out", type=str, default='graph.png', help="Output filename for visualization PNG")
    parser.add_argument("--show", action='store_true', help="Show the plot window after saving (may block on headless machines)")
    args = parser.parse_args()
    rows=[]
    # If CSV provided, use CSV path. If --live provided, fetch via yfinance.-li
    if args.csv:
        try:
            rows=load_quotes_from_csv(args.csv)
            result = find_arbitrage(rows, fee=args.fee, min_profit_pct=args.min_profit_pct)
        except Exception as e:
            print("Error:", e)
            return
    elif args.live:
        # fetch live quotes and run arbitrage check on those
        print("Fetching quotes from yfinance...")
        try:
            rows = fetch_quotes(TICKERS)
            if not rows:
                print("No quotes fetched. Check internet connection or yfinance availability.")
                return
            # rows is list of dicts with keys pair,bid,ask (floats)
            result = find_arbitrage(rows, fee=args.fee, min_profit_pct=args.min_profit_pct)
            out_path = OUT_CSV
            try:
                write_csv(out_path, rows)
                print(f"Fetched quotes written to: {out_path}")
            except Exception as e:
                print(f"Warning: failed to write fetched quotes to {out_path}: {e}")

        except Exception as e:
            print("Error while fetching or analyzing quotes:", e)
            return
    else:
        # User must explicitly pick one of the two modes; show exact commands expected.
        print("You must run the script with either --csv <path> or --live.")
        print("Examples:")
        print("  python fx_arbitrage.py --csv quotes.csv --fee 0.0001 --min-profit-pct 0.00001 --draw --out graph.png")
        print("  python fx_arbitrage.py --live --fee 0.0001 --min-profit-pct 0.00001 --draw --out graph.png")
        print("min-profit-pct is in fraction.")
        print("\nNote: --live will fetch quotes from yfinance. Make sure 'yfinance' is installed and you have internet.")
        return

    if not result["found"]:
        print("No arbitrage found.", result.get("reason"), result.get("profit_pct", ""))
    else:

        all_nodes, all_edges = build_edges_from_quotes(rows, fee=args.fee)
        print("Arbitrage found!")
        print("Cycle: " + " -> ".join(result["cycle"]))
        print(f"Start 1 {result['cycle'][0]} -> end {result['final_amount']:.10f} {result['cycle'][0]}")
        print(f"Profit %: {result['profit_pct']:.8f}")

        # Draw graph if requested (use the same nodes/edges used by the algorithm)
        print("Legs:")
        for leg in result["legs"]:
            print(f"  {leg['from']} -> {leg['to']}, rate={leg['rate']:.10f}")
    if args.draw:
        try:
            nodes, edges = build_edges_from_quotes(rows, fee=args.fee)
            cycle = result.get('cycle') if result.get('found') else None
            # If graph is huge, warn and still produce an output (it may be unreadable)
            if len(edges) > 100 or len(nodes) > 30:
                print(f"Warning: graph has {len(nodes)} nodes; visualization may be cluttered. Consider using a smaller currency set or CSV with fewer pairs.")
            visualize_graph(nodes, edges, cycle=cycle, out_file=args.out, show=args.show)
        except Exception as e:
            print("Failed to visualize graph:", e)
if __name__ == "__main__":
    main()
