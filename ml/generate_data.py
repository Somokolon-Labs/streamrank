"""Build the demo catalog and simulate a behavioural log.

Real product photography (fetched once by ``scripts/fetch_images.py``) is paired
with generated titles, prices and latent taste factors. Users are given hidden
preference vectors and then browse; the resulting log is what the models learn
from, so offline metrics measure a genuine signal rather than noise.

    python ml/generate_data.py --users 1500 --sessions 4
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "data" / "catalog_images.json"
CATALOG_OUT = ROOT / "data" / "catalog.json"
LOG_OUT = ROOT / "data" / "interactions.csv"

LATENT_DIM = 16

CATEGORY_SPEC = {
    "audio": {"brands": ["Aurelia", "Kestrel", "Nomad Audio"], "nouns": ["Studio Headphones", "Wireless Earbuds", "Monitor Set"], "price": (79, 0.45)},
    "computing": {"brands": ["Lumen", "Vertex", "Corewave"], "nouns": ["Ultrabook", "Mechanical Keyboard", "Desk Dock"], "price": (420, 0.5)},
    "wearables": {"brands": ["Pulse", "Northline", "Arc"], "nouns": ["Fitness Watch", "Sleep Tracker", "Smart Band"], "price": (150, 0.4)},
    "photography": {"brands": ["Silverlight", "Optera", "Halide"], "nouns": ["Mirrorless Body", "Prime Lens", "Travel Tripod"], "price": (520, 0.55)},
    "home": {"brands": ["Casa Norte", "Loomly", "Terra"], "nouns": ["Linen Throw", "Ceramic Vase", "Floor Lamp"], "price": (65, 0.5)},
    "kitchen": {"brands": ["Copperline", "Brew Lab", "Sable"], "nouns": ["Pour-over Kettle", "Espresso Grinder", "Chef Knife"], "price": (110, 0.45)},
    "fitness": {"brands": ["Stride", "Kinetic", "Hexa"], "nouns": ["Running Shoe", "Training Mat", "Resistance Set"], "price": (95, 0.4)},
    "outdoor": {"brands": ["Ridgeway", "Tundra", "Basalt"], "nouns": ["Trail Backpack", "Insulated Flask", "Rain Shell"], "price": (135, 0.45)},
    "fashion": {"brands": ["Atelier Nord", "Muse", "Ferro"], "nouns": ["Leather Tote", "Wool Coat", "Canvas Sneaker"], "price": (180, 0.6)},
    "beauty": {"brands": ["Verda", "Ilume", "Botanica"], "nouns": ["Serum", "Cleansing Oil", "Day Cream"], "price": (45, 0.4)},
    "gaming": {"brands": ["Voltcraft", "Pixelworks", "Rhea"], "nouns": ["Controller", "Headset", "Capture Card"], "price": (85, 0.45)},
    "books": {"brands": ["Folio", "Marginalia", "Press North"], "nouns": ["Design Anthology", "Systems Reader", "Essay Collection"], "price": (28, 0.35)},
}

QUALIFIERS = ["Mk II", "Pro", "Classic", "Lite", "Signature", "Edition 3", "Compact", "Studio"]
EVENT_WEIGHTS = {"view": 1.0, "click": 3.0, "add_to_cart": 5.0, "purchase": 8.0}


def build_catalog(rng: random.Random, np_rng: np.random.Generator) -> list[dict]:
    if not IMAGES.exists():
        raise SystemExit(f"missing {IMAGES} - run scripts/fetch_images.py first")
    images = json.loads(IMAGES.read_text(encoding="utf-8"))

    prototypes = {
        category: np_rng.normal(0, 1, LATENT_DIM) for category in CATEGORY_SPEC
    }
    items: list[dict] = []
    index = 0
    for category, photos in images.items():
        spec = CATEGORY_SPEC.get(category)
        if not spec:
            continue
        base_price, spread = spec["price"]
        for photo in photos:
            index += 1
            brand = rng.choice(spec["brands"])
            noun = rng.choice(spec["nouns"])
            title = f"{brand} {noun}"
            if rng.random() < 0.55:
                title += f" {rng.choice(QUALIFIERS)}"
            price = round(float(base_price * math.exp(np_rng.normal(0, spread))), 2)
            latent = prototypes[category] + np_rng.normal(0, 0.55, LATENT_DIM)
            items.append(
                {
                    "id": f"itm_{index:04d}",
                    "title": title,
                    "brand": brand,
                    "category": category,
                    "price": max(9.0, min(2400.0, price)),
                    "rating": round(float(np.clip(np_rng.normal(4.25, 0.35), 2.8, 5.0)), 2),
                    "tags": sorted(rng.sample([noun.lower(), category, brand.lower().split()[0], "bestseller", "new"], k=3)),
                    "image_url": photo["url"],
                    "image_credit": photo["photographer"],
                    "alt_text": photo["alt"],
                    "latent": [round(float(v), 5) for v in latent],
                }
            )
    return items


def simulate(items: list[dict], users: int, sessions: int, rng: random.Random, np_rng: np.random.Generator) -> list[dict]:
    latents = np.array([item["latent"] for item in items], dtype=np.float32)
    latents /= np.linalg.norm(latents, axis=1, keepdims=True) + 1e-9
    categories = [item["category"] for item in items]
    prices = np.array([item["price"] for item in items], dtype=np.float32)
    ratings = np.array([item["rating"] for item in items], dtype=np.float32)
    price_z = (np.log(prices) - np.log(prices).mean()) / (np.log(prices).std() + 1e-9)

    # A long tail of intrinsic popularity, so "popularity" is a real baseline.
    popularity = np_rng.pareto(1.4, len(items)) + 0.4
    popularity /= popularity.sum()

    now = time.time()
    horizon_s = 30 * 24 * 3600
    log: list[dict] = []

    for user_index in range(users):
        user_id = f"usr_{user_index:05d}"
        # Each user mixes one or two category prototypes plus personal noise.
        picks = rng.sample(sorted(set(categories)), k=rng.choice([1, 1, 2]))
        taste = np.zeros(LATENT_DIM, dtype=np.float32)
        for category in picks:
            members = [i for i, c in enumerate(categories) if c == category]
            taste += latents[members].mean(axis=0)
        taste += np_rng.normal(0, 0.35, LATENT_DIM).astype(np.float32)
        taste /= np.linalg.norm(taste) + 1e-9
        price_sensitivity = float(np_rng.normal(-0.35, 0.3))

        for session_index in range(max(1, int(np_rng.poisson(sessions) or 1))):
            session_id = f"ses_{user_index:05d}_{session_index}"
            session_start = now - rng.random() * horizon_s
            impressions = rng.randint(6, 18)
            shown = np_rng.choice(len(items), size=impressions, replace=False, p=popularity)
            clock = session_start
            for position, item_index in enumerate(shown):
                clock += rng.uniform(4, 40)
                affinity = float(latents[item_index] @ taste) * 3.2
                utility = affinity + price_sensitivity * float(price_z[item_index]) + 0.45 * (float(ratings[item_index]) - 4.0)
                p_click = 1 / (1 + math.exp(-(utility - 1.15 - 0.06 * position)))
                log.append(
                    {
                        "user_id": user_id,
                        "session_id": session_id,
                        "item_id": items[item_index]["id"],
                        "event": "view",
                        "position": position,
                        "ts": round(clock, 3),
                    }
                )
                if rng.random() < p_click:
                    clock += rng.uniform(1, 6)
                    log.append({"user_id": user_id, "session_id": session_id, "item_id": items[item_index]["id"], "event": "click", "position": position, "ts": round(clock, 3)})
                    if rng.random() < 0.22:
                        clock += rng.uniform(2, 30)
                        log.append({"user_id": user_id, "session_id": session_id, "item_id": items[item_index]["id"], "event": "add_to_cart", "position": position, "ts": round(clock, 3)})
                        if rng.random() < 0.45:
                            clock += rng.uniform(5, 60)
                            log.append({"user_id": user_id, "session_id": session_id, "item_id": items[item_index]["id"], "event": "purchase", "position": position, "ts": round(clock, 3)})

    log.sort(key=lambda row: row["ts"])
    return log


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the StreamRank demo dataset")
    parser.add_argument("--users", type=int, default=1500)
    parser.add_argument("--sessions", type=int, default=3)
    parser.add_argument("--seed", type=int, default=23)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    np_rng = np.random.default_rng(args.seed)

    items = build_catalog(rng, np_rng)
    CATALOG_OUT.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_OUT.write_text(json.dumps(items, indent=2), encoding="utf-8")

    log = simulate(items, args.users, args.sessions, rng, np_rng)
    with LOG_OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["user_id", "session_id", "item_id", "event", "position", "ts"])
        writer.writeheader()
        writer.writerows(log)

    counts: dict[str, int] = {}
    for row in log:
        counts[row["event"]] = counts.get(row["event"], 0) + 1
    print(f"catalog: {len(items)} items -> {CATALOG_OUT}")
    print(f"log:     {len(log)} interactions -> {LOG_OUT}")
    print("         " + " · ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    print(f"         click-through rate {counts.get('click', 0) / max(1, counts.get('view', 1)):.3f}")


if __name__ == "__main__":
    main()
