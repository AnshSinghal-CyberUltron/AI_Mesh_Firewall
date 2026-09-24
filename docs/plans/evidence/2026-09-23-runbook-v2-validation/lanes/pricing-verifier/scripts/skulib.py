"""Load saved Cloud Billing Catalog SKU pages and extract prices. Read-only over raw/."""
import glob, json, os
from decimal import Decimal

RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raw")

def load(service=None):
    out = []
    pat = f"skus_{service}_p*.json" if service else "skus_*_p*.json"
    for f in sorted(glob.glob(os.path.join(RAW, pat))):
        svc = os.path.basename(f)[5:].rsplit("_p", 1)[0]
        for s in json.load(open(f)).get("skus", []):
            s["_svc"] = svc
            s["_file"] = os.path.basename(f)
            out.append(s)
    return out

def money(u):
    return Decimal(str(u.get("units", "0") or "0")) + Decimal(int(u.get("nanos", 0) or 0)) / Decimal(10**9)

def tiers(sku):
    pi = sku["pricingInfo"][0]
    pe = pi["pricingExpression"]
    return [(Decimal(str(t.get("startUsageAmount", 0))), money(t["unitPrice"])) for t in pe["tieredRates"]]

def unit(sku):
    pe = sku["pricingInfo"][0]["pricingExpression"]
    return pe["usageUnit"], pe.get("usageUnitDescription"), pe.get("displayQuantity")

def eff(sku):
    return sku["pricingInfo"][0].get("effectiveTime")

def row(sku):
    u = unit(sku)
    return {
        "svc": sku["_svc"], "file": sku["_file"], "skuId": sku["skuId"], "description": sku["description"],
        "category": sku["category"], "serviceRegions": sku.get("serviceRegions"),
        "geoTaxonomy": sku.get("geoTaxonomy"),
        "usageUnit": u[0], "usageUnitDescription": u[1],
        "tieredRates": [{"startUsageAmount": str(a), "usd": str(p)} for a, p in tiers(sku)],
        "effectiveTime": eff(sku),
        "summary": sku["pricingInfo"][0].get("summary", ""),
    }

def first_price(sku):
    """Price of the first non-zero tier (or 0)."""
    t = tiers(sku)
    return t[0][1] if t else None
