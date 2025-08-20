import os, json

def get_pairs_path():
    pairs_file = os.getenv("PAIRS_FILE", "pairs.json")
    if not os.path.isabs(pairs_file):
        base_dir = os.path.dirname(os.path.abspath(__file__))
        pairs_file = os.path.join(base_dir, pairs_file)
    return pairs_file

def load_pairs():
    pairs_file = get_pairs_path()
    if not os.path.exists(pairs_file):
        return []
    try:
        with open(pairs_file, "r", encoding="utf-8") as f:
            pairs = json.load(f)
        return [(p["symbol"], p.get("timeframe", "5m")) for p in pairs]
    except Exception:
        return []

def save_pairs(pairs):
    pairs_file = get_pairs_path()
    try:
        with open(pairs_file, "w", encoding="utf-8") as f:
            json.dump([{"symbol": s, "timeframe": t} for s, t in pairs], f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[ERROR] Ошибка сохранения {pairs_file}: {e}")
