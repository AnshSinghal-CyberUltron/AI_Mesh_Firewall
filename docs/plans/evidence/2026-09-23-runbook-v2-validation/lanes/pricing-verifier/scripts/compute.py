"""Build prices.json, the on-demand recompute of the 2026-09-02 cost matrix, and the fleet table.

Every price is looked up by skuId in the saved Cloud Billing Catalog pages under ../raw
(fetched by curl; see ../raw/FETCHED_AT_UTC.txt). Machine specs come from
../raw/machine_types_asia-south1.json (gcloud compute machine-types list).
Run:  python3 compute.py > ../compute_output.txt
"""
import json, math, os, itertools
from decimal import Decimal as D, ROUND_HALF_UP

import skulib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.dirname(HERE)
RAW = os.path.join(OUT, "raw")
H = D(730)                       # hours per month used for every $/month figure
GIB = D(2) ** 30

ALL = {s["skuId"]: s for s in skulib.load()}


def c2(x):
    return float(D(x).quantize(D("0.01"), ROUND_HALF_UP))


def sku(sku_id, must_contain=None, region=None):
    s = ALL[sku_id]
    if must_contain:
        assert must_contain in s["description"], (sku_id, s["description"], must_contain)
    if region:
        assert region in (s.get("serviceRegions") or []), (sku_id, s.get("serviceRegions"))
    return s


def price(sku_id, **kw):
    """First chargeable rate (skips a leading free-allowance tier). Banded SKUs are costed from TIERS, not this."""
    t = skulib.tiers(sku(sku_id, **kw))
    for _, p in t:
        if p > 0:
            return p
    return D(0)


def banded(gib, tiers):
    gib = D(gib); tot = D(0)
    for i, (start, p) in enumerate(tiers):
        end = tiers[i + 1][0] if i + 1 < len(tiers) else None
        if gib <= start:
            break
        span = (min(gib, end) if end is not None else gib) - start
        tot += span * p
    return tot


def banded_inverse(usd, tiers):
    """GiB of banded egress that `usd` buys."""
    usd = D(usd); gib = D(0)
    for i, (start, p) in enumerate(tiers):
        end = tiers[i + 1][0] if i + 1 < len(tiers) else None
        width = (end - start) if end is not None else None
        if p == 0:
            gib = end if end is not None else gib
            continue
        cost_full = width * p if width is not None else None
        if cost_full is not None and usd >= cost_full:
            usd -= cost_full; gib = end
        else:
            return gib + usd / p
    return gib


def sku_record(sku_id, key):
    s = ALL[sku_id]; pi = s["pricingInfo"][0]; pe = pi["pricingExpression"]
    return {
        "key": key, "skuId": sku_id, "service": s["category"]["serviceDisplayName"],
        "description": s["description"], "usageType": s["category"]["usageType"],
        "serviceRegions": s.get("serviceRegions"),
        "usageUnit": pe["usageUnit"], "usageUnitDescription": pe.get("usageUnitDescription"),
        "baseUnit": pe.get("baseUnit"), "baseUnitConversionFactor": pe.get("baseUnitConversionFactor"),
        "tieredRates_usd": [{"startUsageAmount": str(a), "unitPrice": str(p)} for a, p in skulib.tiers(s)],
        "aggregationInfo": pi.get("aggregationInfo"), "effectiveTime": pi.get("effectiveTime"),
        "raw_file": s["_file"],
    }


# ---------------------------------------------------------------- SKU registry
M = "asia-south1"
REG = [
    # compute on-demand, Mumbai
    ("g2_core_h", "A6EC-9503-C0CD", "G2 Instance Core running in Mumbai", M),
    ("g2_ram_gib_h", "5C70-9568-3192", "G2 Instance Ram running in Mumbai", M),
    ("l4_gpu_h", "AE59-12DA-20E3", "Nvidia L4 GPU running in Mumbai", M),
    ("c4_core_h", "70F1-D1A1-608C", "C4 Instance Core running in Mumbai", M),
    ("c4_ram_gib_h", "BC60-A7D5-6A2C", "C4 Instance Ram running in Mumbai", M),
    ("c4d_core_h", "D1E1-0367-825A", "C4D Instance Core running in Mumbai", M),
    ("c4d_ram_gb_h", "CA2D-2A52-B49E", "C4D Instance Ram running in Mumbai", M),
    ("c3_core_h", "1B93-073E-DFE5", "C3 Instance Core running in Mumbai", M),
    ("c3_ram_gib_h", "61A0-38AE-BC2D", "C3 Instance Ram running in Mumbai", M),
    ("n2_core_h", "3A1A-7CFC-5747", "N2 Instance Core running in Mumbai", M),
    ("n2_ram_gib_h", "23FA-BF9A-7C7A", "N2 Instance Ram running in Mumbai", M),
    ("e2_core_h", "DFC1-04D4-B4A1", "E2 Instance Core running in Mumbai", M),
    ("e2_ram_gib_h", "90AB-A7A8-F873", "E2 Instance Ram running in Mumbai", M),
    # 3-yr CUD reference (NOT the pricing basis; used only to reproduce the doc)
    ("cud3_g2_core_h", "C13C-86D2-4291", "G2 Cpu in Mumbai for 3 Years", M),
    ("cud3_g2_ram_gib_h", "E2CF-A78E-CCC3", "G2 Ram in Mumbai for 3 Years", M),
    ("cud3_l4_gpu_h", "3D44-B7C2-56AD", "Nvidia L4 GPU running in Mumbai for 3 Years", M),
    ("cud1_g2_core_h", "C0F8-853A-346E", "G2 Cpu in Mumbai for 1 Year", M),
    ("cud1_g2_ram_gib_h", "E47D-A49A-40F9", "G2 Ram in Mumbai for 1 Year", M),
    ("cud1_l4_gpu_h", "2F5D-68A6-1945", "Nvidia L4 GPU running in Mumbai for 1 Year", M),
    ("cud3_c4_core_h", "2699-895D-A705", "C4 Cpu in Mumbai for 3 Years", M),
    ("cud3_c4_ram_gib_h", "DCA5-2AF1-D3CB", "C4 Ram in Mumbai for 3 Years", M),
    # disks
    ("pd_balanced_gib_mo", "C24C-88D9-75ED", "Balanced PD Capacity in Mumbai", M),
    ("pd_ssd_gib_mo", "FF66-41CB-DE90", "SSD backed PD Capacity in Mumbai", M),
    ("pd_standard_gib_mo", "320C-7688-1A62", "Storage PD Capacity in Mumbai", M),
    ("hdb_capacity_gib_mo", "9953-32B6-978B", "Hyperdisk Balanced Capacity in Mumbai", M),
    ("hdb_iops_mo", "519B-E9DD-3AF5", "Hyperdisk Balanced IOPS in Mumbai", M),
    ("hdb_throughput_mibps_mo", "947E-BF81-9AE3", "Hyperdisk Balanced Throughput in Mumbai", M),
    ("snapshot_regional_gib_mo", "A0EA-3B8B-0794", "Storage PD Snapshot in Mumbai", M),
    ("snapshot_multiregional_asia_gib_mo", "6E69-DD68-8DCA", "Storage PD Snapshot in Asia", "asia"),
    ("pd_ssd_gib_mo_us_central1_reference", "B188-61DD-52E4", "SSD backed PD Capacity", "us-central1"),
    # network (compute service)
    ("egress_standard_tier_mumbai", "4C3B-5563-1BF6", "Network Standard Data Transfer Out to Internet from Mumbai", M),
    ("egress_premium_to_india", "31FB-BB20-320E", "from Mumbai to India", M),
    ("egress_premium_to_americas", "1507-FFFF-0D8E", "from Mumbai to Americas", M),
    ("egress_premium_to_emea", "09EF-5244-AB40", "from Mumbai to EMEA", M),
    ("egress_premium_to_western_europe", "863B-7F19-62E9", "from Mumbai to Western Europe", M),
    ("egress_premium_to_eastern_europe", "1D96-2EA8-C01C", "from Mumbai to Eastern Europe", M),
    ("egress_premium_to_central_america", "EC9D-4D53-6C97", "from Mumbai to Central America", M),
    ("egress_premium_to_apac_ex_kr_id", "95E2-380C-BFFC", "from Mumbai to Apac(Excluding Korea and Indonesia)", M),
    ("egress_premium_to_middle_east", "CC55-F2D0-1A51", "from Mumbai to Middle East", M),
    ("egress_premium_to_africa", "2F21-9AA2-DFA4", "from Mumbai to Africa", M),
    ("egress_premium_to_australia", "5708-C46F-8E73", "from Mumbai to Australia", M),
    ("egress_premium_to_indonesia", "92C0-71AC-1535", "from Mumbai to Indonesia", M),
    ("egress_premium_to_south_korea", "C46F-9E5F-4D55", "from Mumbai to South Korea", M),
    ("egress_premium_to_south_america", "A3F9-E63A-36FA", "from Mumbai to South America", M),
    ("egress_premium_to_china", "96B7-A85B-0114", "from Mumbai to China", M),
    ("egress_intrazone", "14F9-7705-2FD4", "Network Intra Zone Data Transfer Out", "global"),
    ("egress_interzone", "DE9E-AFBC-A15A", "Network Inter Zone Data Transfer Out", None),
    ("ip_external_standard_vm_h", "C054-7F72-A02E", "External IP Charge on a Standard VM", "global"),
    ("ip_external_spot_vm_h", "4AF8-7C1F-39C4", "External IP Charge on a Spot Preemptible VM", "global"),
    ("ip_static_unassigned_mumbai_h", "1268-F71A-6285", "Static Ip Charge in Mumbai", M),
    # networking service
    ("lb_regional_ext_alb_fr_min_h", "22FA-9BFA-EC10", "Regional External Application Load Balancer Forwarding Rule Minimum for Mumbai", M),
    ("lb_regional_ext_alb_fr_additional_h", "1A70-4FE8-9FBA", "Regional External Application Load Balancer Forwarding Rule Additional for Mumbai", M),
    ("lb_regional_ext_alb_inbound_gib", "7087-03B4-CF6F", "Regional External Application Load Balancer Inbound Data Processing for Mumbai", M),
    ("lb_regional_ext_alb_outbound_gib", "8625-A380-1224", "Regional External Application Load Balancer Outbound Data Processing for Mumbai", M),
    ("lb_global_fr_min_h", "DEE3-C42E-3E4D", "Cloud Load Balancer Forwarding Rule Minimum Global", "global"),
    ("lb_global_fr_additional_h", "1211-928B-AF92", "Cloud Load Balancer Forwarding Rule Additional Global", "global"),
    ("lb_global_ext_alb_inbound_gib_mumbai", "91F1-423B-CF42", "Global External Application Load Balancer Inbound Data Processing for Mumbai", M),
    ("lb_global_ext_alb_outbound_gib_mumbai", "91CC-DEC4-4AC7", "Global External Application Load Balancer Outbound Data Processing for Mumbai", M),
    ("nat_gateway_uptime_per_vm_h", "32E2-4EFC-EF9F", "Networking Cloud Nat Gateway Uptime", "global"),
    ("nat_data_processing_gib", "015F-5732-FFF0", "Networking Cloud Nat Data Processing", "global"),
    ("nat_ip_usage_h", "8515-9425-D2CE", "Networking Cloud NAT IP Usage", "global"),
    ("armor_std_policy_mo", "4B13-E64F-4A2B", "Networking Cloud Armor Policy", "global"),
    ("armor_std_rule_mo", "A321-89BD-F5BC", "Networking Cloud Armor Rule", "global"),
    ("armor_std_request", "1A87-DEB9-C4BE", "Networking Cloud Armor Requests", "global"),
    ("armor_regional_request", "928E-CF60-E186", "Networking Cloud Armor Requests Regional", "global"),
    # memorystore
    ("valkey_shared_core_nano_node_h", "BBE1-7BC7-645D", "Shared Core Nano Node Mumbai", M),
    ("valkey_standard_small_node_h", "44CF-B864-F1F7", "Standard Small Node Mumbai", M),
    ("valkey_highmem_medium_node_h", "DEEA-ABC3-D53D", "Highmem Medium Node Mumbai", M),
    ("valkey_highcpu_medium_node_h", "2335-465B-42DF", "Highcpu Medium Node Mumbai", M),
    ("redis_basic_m1_gib_h", "1A79-AA76-9403", "Redis Capacity Basic M1 Mumbai", M),
    ("redis_basic_m2_gib_h", "C0DE-CBF3-DEE8", "Redis Capacity Basic M2 Mumbai", M),
    ("redis_basic_m3_gib_h", "A8E3-58B2-16A7", "Redis Capacity Basic M3 Mumbai", M),
    ("redis_basic_m4_gib_h", "6213-D978-3159", "Redis Capacity Basic M4 Mumbai", M),
    ("redis_basic_m5_gib_h", "0B85-062F-82FF", "Redis Capacity Basic M5 Mumbai", M),
    ("redis_standard_m1_gib_h", "E546-A818-9A77", "Redis Capacity Standard M1 Mumbai", M),
    ("redis_standard_m2_gib_h", "F042-8822-7B79", "Redis Capacity Standard M2 Mumbai", M),
    ("redis_standard_m3_gib_h", "7C7C-D23A-8C50", "Redis Capacity Standard M3 Mumbai", M),
    ("redis_standard_m4_gib_h", "651E-DF06-719C", "Redis Capacity Standard M4 Mumbai", M),
    ("redis_standard_m5_gib_h", "88FA-7986-6593", "Redis Capacity Standard M5 Mumbai", M),
    ("redis_cluster_node_shared_core_nano_h", "B72B-FDED-C923", "Redis Cluster Node Shared Core Nano Mumbai", M),
    ("redis_cluster_node_default_h", "A531-0942-CD60", "Redis Cluster Node Default Mumbai", M),
    # cloud sql postgres
    ("sql_pg_regional_vcpu_h", "65ED-2048-3203", "Cloud SQL for PostgreSQL: Regional - vCPU in Mumbai", M),
    ("sql_pg_regional_ram_gib_h", "34E0-9B1D-C80D", "Cloud SQL for PostgreSQL: Regional - RAM in Mumbai", M),
    ("sql_pg_regional_ssd_gib_mo", "E77A-52C7-BB42", "Cloud SQL for PostgreSQL: Regional - Standard storage in Mumbai", M),
    ("sql_pg_zonal_vcpu_h", "DF80-E0D1-CCE3", "Cloud SQL for PostgreSQL: Zonal - vCPU in Mumbai", M),
    ("sql_pg_zonal_ram_gib_h", "72C1-62A1-2C8B", "Cloud SQL for PostgreSQL: Zonal - RAM in Mumbai", M),
    ("sql_pg_zonal_ssd_gib_mo", "42A1-880A-9B0F", "Cloud SQL for PostgreSQL: Zonal - Standard storage in Mumbai", M),
    ("sql_backups_gib_mo", "D737-A9D2-80AB", "Cloud SQL: Backups in Mumbai", M),
    # ops
    ("logging_storage_gib", "143F-A1B0-E0BE", "Log Storage cost", M),
    ("logging_retention_gib_mo", "F4AE-5A52-ACE3", "Log Retention cost", "global"),
    ("monitoring_metric_volume_mib", "A924-09D0-8854", "Metric Volume", "global"),
    ("monitoring_prometheus_sample", "A4E4-DF03-CDB6", "Prometheus Samples Ingested", "global"),
    ("gke_regional_cluster_h", "B561-BFBD-1264", "Regional Kubernetes Clusters", "global"),
    ("gke_zonal_cluster_h", "6B92-A835-08AB", "Zonal Kubernetes Clusters", "global"),
    ("artifact_registry_storage_gib_mo", "8502-299A-ABAF", "Artifact Registry Storage", "global"),
    ("gcs_standard_mumbai_gib_mo", "2717-BEFE-3773", "Standard Storage Mumbai", M),
    ("gcs_nearline_mumbai_gib_mo", "FD55-A8ED-B1A6", "Nearline Storage Mumbai", M),
    ("gcs_coldline_mumbai_gib_mo", "81E6-58F6-12C7", "Coldline Storage Mumbai", M),
    ("gcs_archive_mumbai_gib_mo", "891B-EBF4-A8DC", "Archive Storage Mumbai", M),
]
P = {}
SKU_TABLE = []
for key, sid, desc, region in REG:
    sku(sid, must_contain=desc, region=region)
    P[key] = price(sid)
    SKU_TABLE.append(sku_record(sid, key))
TIERS = {key: skulib.tiers(ALL[sid]) for key, sid, _, _ in REG}

# ---------------------------------------------------------------- machine specs
mt = json.load(open(os.path.join(RAW, "machine_types_asia-south1.json")))
SPEC = {}
for m in mt:
    z = m["zone"].rsplit("/", 1)[-1]
    acc = m.get("accelerators") or []
    rec = SPEC.setdefault(m["name"], {"vcpu": m["guestCpus"], "memory_mib": m["memoryMb"],
                                      "gpus": sum(a["guestAcceleratorCount"] for a in acc),
                                      "gpu_type": acc[0]["guestAcceleratorType"] if acc else None, "zones": []})
    assert (rec["vcpu"], rec["memory_mib"]) == (m["guestCpus"], m["memoryMb"])
    rec["zones"].append(z)

FAM = {  # family -> (core key, ram key, ram unit is decimal GB?)
    "g2": ("g2_core_h", "g2_ram_gib_h", False), "c4": ("c4_core_h", "c4_ram_gib_h", False),
    "c4d": ("c4d_core_h", "c4d_ram_gb_h", True), "c3": ("c3_core_h", "c3_ram_gib_h", False),
    "n2": ("n2_core_h", "n2_ram_gib_h", False), "e2": ("e2_core_h", "e2_ram_gib_h", False),
}
CUD3 = {"g2": ("cud3_g2_core_h", "cud3_g2_ram_gib_h"), "c4": ("cud3_c4_core_h", "cud3_c4_ram_gib_h")}


def shape_price(name):
    fam = name.split("-")[0]
    core_k, ram_k, decimal_gb = FAM[fam]
    sp = SPEC[name]
    vcpu = D(sp["vcpu"]); mem_bytes = D(sp["memory_mib"]) * D(2) ** 20
    # usage metered in By.s; SKU baseUnitConversionFactor converts to the priced unit
    conv = D(str(ALL[dict((k, s) for k, s, _, _ in REG)[ram_k]]["pricingInfo"][0]["pricingExpression"]["baseUnitConversionFactor"]))
    ram_units_per_h = mem_bytes * D(3600) / conv
    cpu = vcpu * P[core_k]; ram = ram_units_per_h * P[ram_k]
    gpu = D(sp["gpus"]) * P["l4_gpu_h"] if sp["gpus"] else D(0)
    tot = cpu + ram + gpu
    out = {
        "family": fam.upper(), "vcpu": sp["vcpu"], "memory_mib": sp["memory_mib"],
        "memory_gib": float(D(sp["memory_mib"]) / 1024), "gpus": sp["gpus"], "gpu_type": sp["gpu_type"],
        "zones_asia_south1": sorted(sp["zones"]),
        "ram_billed_units_per_hour": f"{ram_units_per_h.normalize()} {'GBy.h (decimal GB, per SKU baseUnitConversionFactor 3.6e12)' if decimal_gb else 'GiBy.h'}",
        "usd_per_hour": {"cpu": str(cpu), "ram": str(ram), "gpu": str(gpu), "total": str(tot)},
        "usd_per_hour_total": float(tot), "usd_per_month_730h": c2(tot * H),
        "sku_ids": [dict((k, s) for k, s, _, _ in REG)[k] for k in (core_k, ram_k)] + (["AE59-12DA-20E3"] if sp["gpus"] else []),
        "sustained_use_discount_eligible": fam == "n2",
    }
    if decimal_gb:
        alt = D(sp["memory_mib"]) / 1024 * P[ram_k]
        out["note_c4d_ram"] = ("C4D RAM SKU is priced per decimal GB (GBy.h, baseUnitConversionFactor=3.6e12 By.s). "
                               f"If Google instead billed the nominal {sp['memory_mib']/1024:g} 'GB', RAM would be "
                               f"${c2(alt * H)}/mo instead of ${c2(ram * H)}/mo (total ${c2((cpu + alt + gpu) * H)}/mo).")
    if fam in CUD3:
        ck, rk = CUD3[fam]
        cud = vcpu * P[ck] + D(sp["memory_mib"]) / 1024 * P[rk] + (D(sp["gpus"]) * P["cud3_l4_gpu_h"] if sp["gpus"] else 0)
        out["reference_only_3yr_cud_usd_per_month_730h"] = c2(cud * H)
    return out


SHAPES = ["g2-standard-4", "g2-standard-8", "g2-standard-12", "g2-standard-16", "g2-standard-24", "g2-standard-32",
          "g2-standard-48", "c4-standard-4", "c4-standard-8", "c4-standard-16", "c4-standard-32", "c4-highcpu-8",
          "c4-highcpu-16", "c4-highcpu-32", "c4d-highcpu-8", "c4d-highcpu-16", "c4d-highcpu-32", "n2-standard-8",
          "n2-standard-16",
          # extra (control-plane candidates)
          "n2-standard-4", "c3-standard-8", "c3-highcpu-8", "e2-standard-2", "e2-standard-4", "e2-standard-8"]
SHP = {n: shape_price(n) for n in SHAPES}

# ---------------------------------------------------------------- disks / per-node extras
def hdb_month(size_gib, iops=3000, mibps=140):
    return (D(size_gib) * P["hdb_capacity_gib_mo"] + max(D(iops) - 3000, D(0)) * P["hdb_iops_mo"]
            + max(D(mibps) - 140, D(0)) * P["hdb_throughput_mibps_mo"])


DISK = {
    "g2_boot_pd_balanced_200gib": c2(200 * P["pd_balanced_gib_mo"]),   # what the G2 lanes create (GCP.md / disks list)
    "g2_boot_pd_balanced_100gib": c2(100 * P["pd_balanced_gib_mo"]),
    "g2_boot_pd_ssd_100gib": c2(100 * P["pd_ssd_gib_mo"]),
    "c4_boot_hdb_60gib_default_provisioning_3360iops_230mibps": c2(hdb_month(60, 3360, 230)),  # rv-guard-c4-1 as created
    "c4_boot_hdb_50gib_baseline_3000iops_140mibps": c2(hdb_month(50)),
    "controller_boot_hdb_512gib_100000iops_2400mibps": c2(hdb_month(512, 100000, 2400)),       # ai-mesh-firewall VM as created
}
IP_VM_MONTH = P["ip_external_standard_vm_h"] * H   # ignores the 720 free IP-hours/account/month (shared allowance)

# ---------------------------------------------------------------- fixed items
def valkey(key, nodes):
    return P[key] * nodes * H


def sql_pg(ha, vcpu, ram_gib, ssd_gib):
    pre = "sql_pg_regional" if ha else "sql_pg_zonal"
    return (D(vcpu) * P[pre + "_vcpu_h"] + D(ram_gib) * P[pre + "_ram_gib_h"]) * H + D(ssd_gib) * P[pre + "_ssd_gib_mo"]


FIXED = [
    ("lb_regional_external_alb_up_to_5_rules", P["lb_regional_ext_alb_fr_min_h"] * H, "22FA-9BFA-EC10", "+$0.01/GiB inbound AND +$0.01/GiB outbound processed (7087-03B4-CF6F, 8625-A380-1224)"),
    ("valkey_1x_shared_core_nano", valkey("valkey_shared_core_nano_node_h", 1), "BBE1-7BC7-645D", "node capacity per Google docs, not API"),
    ("valkey_2x_shared_core_nano", valkey("valkey_shared_core_nano_node_h", 2), "BBE1-7BC7-645D", ""),
    ("valkey_2x_standard_small", valkey("valkey_standard_small_node_h", 2), "44CF-B864-F1F7", ""),
    ("valkey_2x_highmem_medium (doc line 9 parity)", valkey("valkey_highmem_medium_node_h", 2), "DEEA-ABC3-D53D", ""),
    ("redis_basic_1gib (M1)", 1 * P["redis_basic_m1_gib_h"] * H, "1A79-AA76-9403", "tier sizes M1..M5 per Google docs, not API"),
    ("redis_basic_5gib (M2)", 5 * P["redis_basic_m2_gib_h"] * H, "C0DE-CBF3-DEE8", ""),
    ("redis_standard_ha_1gib (M1)", 1 * P["redis_standard_m1_gib_h"] * H, "E546-A818-9A77", ""),
    ("redis_standard_ha_5gib (M2)", 5 * P["redis_standard_m2_gib_h"] * H, "F042-8822-7B79", ""),
    ("redis_standard_ha_13gib (M3)", 13 * P["redis_standard_m3_gib_h"] * H, "7C7C-D23A-8C50", ""),
    ("cloudsql_pg_ha_4vcpu_16gib_100gib (doc line 7 parity)", sql_pg(True, 4, 16, 100), "65ED-2048-3203,34E0-9B1D-C80D,E77A-52C7-BB42", ""),
    ("cloudsql_pg_ha_2vcpu_8gib_20gib", sql_pg(True, 2, 8, 20), "65ED-2048-3203,34E0-9B1D-C80D,E77A-52C7-BB42", ""),
    ("cloudsql_pg_zonal_2vcpu_8gib_20gib", sql_pg(False, 2, 8, 20), "DF80-E0D1-CCE3,72C1-62A1-2C8B,42A1-880A-9B0F", ""),
    ("control_plane_vm_e2-standard-4", D(str(SHP["e2-standard-4"]["usd_per_month_730h"])), "DFC1-04D4-B4A1,90AB-A7A8-F873", "E2_CPUS quota in ai-mesh-firewall/asia-south1 reads limit=0"),
    ("control_plane_vm_n2-standard-4 (list; SUD-eligible)", D(str(SHP["n2-standard-4"]["usd_per_month_730h"])), "3A1A-7CFC-5747,23FA-BF9A-7C7A", ""),
    ("control_plane_vm_c4-standard-4", D(str(SHP["c4-standard-4"]["usd_per_month_730h"])), "70F1-D1A1-608C,BC60-A7D5-6A2C", ""),
    ("control_plane_boot_pd_balanced_100gib", 100 * P["pd_balanced_gib_mo"], "C24C-88D9-75ED", ""),
    ("mongo_chroma_data_250gib_pd_ssd (doc line 3 data part)", 250 * P["pd_ssd_gib_mo"], "FF66-41CB-DE90", ""),
    ("mongo_chroma_data_250gib_pd_balanced", 250 * P["pd_balanced_gib_mo"], "C24C-88D9-75ED", ""),
    ("gke_regional_cluster_fee", P["gke_regional_cluster_h"] * H, "B561-BFBD-1264", "$0 if plain VMs/MIGs"),
    ("artifact_registry_20gib", (20 - D("0.5")) * P["artifact_registry_storage_gib_mo"], "8502-299A-ABAF", "first 0.5 GiB free"),
    ("cloud_monitoring_doc_parity", D(15), "A924-09D0-8854", "unit: first 150 MiB free then $0.258/MiB; $15 = 208.1 MiB/mo (doc's quantity)"),
    ("cloudsql_backups_doc_parity", D(15), "D737-A9D2-80AB", "unit $0.096/GiB-mo; $15 = 156.25 GiB (doc's quantity)"),
    ("snapshots_doc_parity", D(18), "A0EA-3B8B-0794", "unit $0.052/GiB-mo regional, $0.083 multi-regional asia; $18 = 346 GiB regional (doc's quantity)"),
    ("cloud_nat_per_vm_uptime (if NAT instead of VM IPs)", P["nat_gateway_uptime_per_vm_h"] * H, "32E2-4EFC-EF9F", "per VM; +$0.045/GiB processed both directions (015F-5732-FFF0) + $3.65/mo per NAT IP (8515-9425-D2CE)"),
]
FIXED_J = [{"item": n, "usd_per_month": c2(v), "sku_ids": s, "note": note} for n, v, s, note in FIXED]
FX = {n: D(str(c2(v))) for n, v, _, _ in FIXED}

BUNDLES = {
    "F-doc-parity (doc lines 4,7,8,9,14,15,17,18 + 250 GiB pd-ssd data disks, on-demand Mumbai)": [
        "lb_regional_external_alb_up_to_5_rules", "cloudsql_pg_ha_4vcpu_16gib_100gib (doc line 7 parity)",
        "cloudsql_backups_doc_parity", "valkey_2x_highmem_medium (doc line 9 parity)", "gke_regional_cluster_fee",
        "artifact_registry_20gib", "cloud_monitoring_doc_parity", "snapshots_doc_parity",
        "mongo_chroma_data_250gib_pd_ssd (doc line 3 data part)"],
    "F-lead-list (LB + Redis Standard HA 1 GiB + control-plane n2-standard-4 w/ 100 GiB pd-balanced + 250 GiB pd-balanced data + monitoring $15)": [
        "lb_regional_external_alb_up_to_5_rules", "redis_standard_ha_1gib (M1)", "control_plane_vm_n2-standard-4 (list; SUD-eligible)",
        "control_plane_boot_pd_balanced_100gib", "mongo_chroma_data_250gib_pd_balanced", "cloud_monitoring_doc_parity"],
}
BUNDLE_J = {k: {"items": v, "usd_per_month": c2(sum(FX[i] for i in v))} for k, v in BUNDLES.items()}

# ---------------------------------------------------------------- egress helpers
EGRESS = {
    "note": "Tiers are GiB/month, aggregated per billing ACCOUNT per SKU (aggregationInfo). Multiply MEASURED bytes/request "
            "x requests/month / 2^30 to get GiB. 1 RPS sustained = 2,628,000 req/month at 730 h (the doc used 30 d = 2,592,000).",
    "standard_tier_from_mumbai": [{"from_gib": str(a), "usd_per_gib": str(p)} for a, p in TIERS["egress_standard_tier_mumbai"]],
    "premium_tier_from_mumbai_by_destination": {k.replace("egress_premium_to_", ""): [{"from_gib": str(a), "usd_per_gib": str(p)} for a, p in TIERS[k]]
                                                for k in TIERS if k.startswith("egress_premium_to_")},
    "regional_external_alb_processing_usd_per_gib": {"inbound": str(P["lb_regional_ext_alb_inbound_gib"]), "outbound": str(P["lb_regional_ext_alb_outbound_gib"])},
    "cloud_nat_processing_usd_per_gib_each_direction": str(P["nat_data_processing_gib"]),
    "inter_zone_vm_to_vm_usd_per_gib": str(P["egress_interzone"]),
    "inter_zone_caveat": "SKU DE9E-AFBC-A15A serviceRegions lists us-central1/us-east1/us-west1/asia-east1/europe-west1 only; the catalog has no asia-south1-specific VM inter-zone SKU.",
    "intra_zone_usd_per_gib": str(P["egress_intrazone"]),
    "ingress_usd_per_gib": "0 (all PremiumInternetIngress / StandardInternetIngress SKUs for Mumbai are $0)",
    "usd_per_million_requests_per_KiB_per_request_at_rate": "0.9536743 x rate  (1e6 x 1024 / 2^30)",
    "reference_points_usd": {},
}
for g in (1024, 10240, 16037, 51200, 102400):
    EGRESS["reference_points_usd"][f"{g}_GiB"] = {
        "standard_tier": c2(banded(g, TIERS["egress_standard_tier_mumbai"])),
        "premium_to_india": c2(banded(g, TIERS["egress_premium_to_india"])),
        "premium_to_americas": c2(banded(g, TIERS["egress_premium_to_americas"])),
    }

# ---------------------------------------------------------------- doc recompute (2026-09-02 cost matrix §2)
REQ_MONTH_DOC = D(1064) * 86400 * 30
assert REQ_MONTH_DOC == 2757888000
DOC_BANDS = [(D(0), D(0)), (D(200), D("0.085")), (D(1024), D("0.065")), (D(10240), D("0.045"))]
EG_GIB_DOC = D(6244) * REQ_MONTH_DOC / GIB
RESP_GIB = D(2950) * REQ_MONTH_DOC / GIB
PROMPT_GIB = D(3294) * REQ_MONTH_DOC / GIB
AUDIT_GIB = D(1024) * REQ_MONTH_DOC / GIB

g2_24 = SHP["g2-standard-24"]
cud_g2_4 = 4 * (24 * P["cud3_g2_core_h"] + 96 * P["cud3_g2_ram_gib_h"] + 2 * P["cud3_l4_gpu_h"]) * H
cud1_g2_4 = 4 * (24 * P["cud1_g2_core_h"] + 96 * P["cud1_g2_ram_gib_h"] + 2 * P["cud1_l4_gpu_h"]) * H
od_g2_4 = D(4) * D(g2_24["usd_per_hour"]["total"]) * H
sql_od = sql_pg(True, 4, 16, 100)
sql_compute_od = (4 * P["sql_pg_regional_vcpu_h"] + 16 * P["sql_pg_regional_ram_gib_h"]) * H
valkey_od = valkey("valkey_highmem_medium_node_h", 2)
std_egress_doc_vol = banded(EG_GIB_DOC, TIERS["egress_standard_tier_mumbai"])
doc_band_egress = banded(EG_GIB_DOC, DOC_BANDS)
lb_in_out = (RESP_GIB * P["lb_regional_ext_alb_outbound_gib"]) + (PROMPT_GIB * P["lb_regional_ext_alb_inbound_gib"])
premium_mix = banded(RESP_GIB, TIERS["egress_standard_tier_mumbai"]) + banded(PROMPT_GIB, TIERS["egress_premium_to_americas"])
armor_std = REQ_MONTH_DOC * P["armor_std_request"] + P["armor_std_policy_mo"]
armor_reg = REQ_MONTH_DOC * P["armor_regional_request"] + P["armor_std_policy_mo"]

LINES = [
    # n, service, doc $, on-demand $, assumes 3yr CUD?, verdict, derivation
    (1, "GCE G2 x4 (96 vCPU + 384 GiB + 8 L4)", D("2736.76"), od_g2_4, "YES (exact)",
     f"Doc value = 3-yr CUD SKUs C13C-86D2-4291/E2CF-A78E-CCC3/3D44-B7C2-56AD: 4x(24x{P['cud3_g2_core_h']}+96x{P['cud3_g2_ram_gib_h']}+2x{P['cud3_l4_gpu_h']})x730 = ${c2(cud_g2_4)} (matches to the cent). "
     f"On-demand: 4x(24x{P['g2_core_h']}+96x{P['g2_ram_gib_h']}+2x{P['l4_gpu_h']})x730 = ${c2(od_g2_4)}. (1-yr CUD would be ${c2(cud1_g2_4)}.)"),
    (2, "NVIDIA L4 x8 (bundled)", D(0), D(0), "n/a", "Correct: L4 is priced inside line 1 (SKU AE59-12DA-20E3 on-demand $0.582975726/GPU-h)."),
    (3, "Persistent disks 4x100 GiB pd-ssd + 250 GiB data", D("68.00"), 650 * P["pd_ssd_gib_mo"], "no",
     f"ERROR x2: $68.00 = 400 GiB x $0.17 = us-central1 pd-ssd (B188-61DD-52E4), not Mumbai ($0.204, FF66-41CB-DE90); and the 250 GiB data disk is not priced. "
     f"Mumbai: 650 x 0.204 = ${c2(650 * P['pd_ssd_gib_mo'])} (all pd-ssd) or ${c2(400 * P['pd_ssd_gib_mo'] + 250 * P['pd_balanced_gib_mo'])} with the 250 GiB on pd-balanced."),
    (4, "Regional external ALB (Standard tier)", D("18.25"), P["lb_regional_ext_alb_fr_min_h"] * H, "no",
     "ERROR: $18.25 = 730 x $0.025 (global / us rate DEE3-C42E-3E4D). Mumbai regional ext ALB first-5-rules = $0.03/h (22FA-9BFA-EC10) = $21.90."),
    (5, "Egress 16,037 GiB banded + LB bytes", D("1090.32"), std_egress_doc_vol + D("0.01") * EG_GIB_DOC, "no",
     f"Doc arithmetic is internally consistent (824x0.085+9216x0.065+5797x0.045 = ${c2(doc_band_egress)}; +16,037x0.01 = ${c2(D('0.01') * EG_GIB_DOC)}), "
     f"BUT the bands are not Mumbai's: SKU 4C3B-5563-1BF6 is 0-200 GiB free, 200-10,240 @ $0.11, 10,240-153,600 @ $0.075, >153,600 @ $0.07 (no 1,024 GiB break). "
     f"Same {c2(EG_GIB_DOC)} GiB at Mumbai Standard tier = ${c2(std_egress_doc_vol)}; LB processing is billed on BOTH inbound (client request) and outbound (response) at $0.01/GiB each, "
     f"and the prompt leg to the provider does not traverse the LB: resp {c2(RESP_GIB)} GiB out + client-request {c2(PROMPT_GIB)} GiB in (if request ~= prompt leg) = ${c2(lb_in_out)} (numerically = doc's $160.37). "
     f"If the prompt leg leaves via Premium-tier VM IPs to US providers: ${c2(premium_mix)} egress instead of ${c2(std_egress_doc_vol)}."),
    (6, "External IPs (LB + node addresses)", D("15.00"), 4 * IP_VM_MONTH, "no",
     "OK: 4 VM IPs x $0.005/h (C054-7F72-A02E) x 730 = $14.60; IPs on forwarding rules are not charged (Google network-pricing page). First 720 IP-h/account/month free would lower it to $11.00."),
    (7, "Cloud SQL Postgres HA 4 vCPU/16 GiB/100 GiB", D("273.91"), sql_od, "YES (inferred)",
     f"On-demand Mumbai regional(HA): (4x{P['sql_pg_regional_vcpu_h']} + 16x{P['sql_pg_regional_ram_gib_h']})x730 + 100x{P['sql_pg_regional_ssd_gib_mo']} = ${c2(sql_od)}. "
     f"Doc's $273.91 reproduces to $0.02 as 0.48 x compute (${c2(sql_compute_od)}) + storage $40.80 = ${c2(D('0.48') * sql_compute_od + D('40.8'))} => a 52% compute discount (3-yr CUD rate); the catalog exposes no Cloud SQL commit SKUs to prove it."),
    (8, "SQL backups (PITR)", D("15.00"), D("15.00"), "no", "Unit verified $0.096/GiB-mo (D737-A9D2-80AB) => $15 = 156.25 GiB; quantity is the doc's assumption (kept)."),
    (9, "Memorystore Valkey HA 2 x highmem-medium", D("175.20"), valkey_od, "YES (inferred)",
     f"Mumbai on-demand $0.20/node-h (DEEA-ABC3-D53D) x2 x730 = ${c2(valkey_od)}. Doc = $0.12/node-h = 0.60 x Mumbai; no region lists $0.12 on-demand (min $0.1923 Iowa) => 40% discount (3-yr CUD rate); no Memorystore commit SKUs in catalog."),
    (10, "Control + Celery + MCP + PgBouncer (co-located)", D(0), D(0), "n/a", "$0 by co-location assumption (not a price claim)."),
    (11, "MongoDB (co-located)", D(0), D(0), "n/a", "Disk is supposed to be in line 3 but line 3 never priced it (see line 3)."),
    (12, "ChromaDB (co-located)", D(0), D(0), "n/a", "Same as 11."),
    (13, "SPA + nginx (co-located)", D(0), D(0), "n/a", "$0 by co-location assumption."),
    (14, "GKE regional cluster fee", D("73.00"), P["gke_regional_cluster_h"] * H, "no", "OK: $0.10/h (B561-BFBD-1264) x 730."),
    (15, "Artifact Registry ~20 GB", D("10.00"), (20 - D("0.5")) * P["artifact_registry_storage_gib_mo"], "no",
     "OVERSTATED: 8502-299A-ABAF = first 0.5 GiB free then $0.10/GiB-mo => 20 GiB = $1.95."),
    (16, "Audit -> GCS", D("53.00"), AUDIT_GIB * P["gcs_standard_mumbai_gib_mo"], "no",
     f"Doc uses $0.02/GiB; Mumbai Standard is $0.023/GiB-MONTH (2717-BEFE-3773): {c2(AUDIT_GIB)} GiB (1 KiB x 2.758 B req) = ${c2(AUDIT_GIB * P['gcs_standard_mumbai_gib_mo'])} for ONE month of data held one month. "
     f"It is storage, so it accumulates: R months of retention => R x that per month (Nearline $0.016, Coldline $0.006, Archive $0.0025 with lifecycle rules)."),
    (17, "Cloud Monitoring", D("15.00"), D("15.00"), "no", "Unit verified: first 150 MiB free then $0.258/MiB (A924-09D0-8854) => $15 = 208.1 MiB/mo; quantity is the doc's assumption (kept)."),
    (18, "Disk snapshots weekly", D("18.00"), D("18.00"), "no", "Unit verified: $0.052/GiB-mo regional (A0EA-3B8B-0794) or $0.083 multi-regional asia (6E69-DD68-8DCA); $18 = 346 / 217 GiB; quantity unstated (kept)."),
    (19, "Cloud NAT (not bought)", D(0), D(0), "n/a", "Claim '$0.045/GiB both ways' CONFIRMED: 015F-5732-FFF0 $0.045/GiB; Google doc: 'Price per GiB processed, inbound and outbound data transfer'."),
    (20, "Cloud Armor (not bought)", D(0), D(0), "n/a",
     f"Claim '$15,180/mo' NOT REPRODUCIBLE: at the doc's 2,757,888,000 req/mo, Standard Cloud Armor = $0.75/M req (1A87-DEB9-C4BE) + $5/policy = ${c2(armor_std)} (+$1/rule); regional $0.60/M (928E-CF60-E186) = ${c2(armor_reg)}."),
    (21, "Kafka/ClickHouse/BigQuery/Vertex (not bought)", D(0), D(0), "n/a", "$0."),
]
doc_total = sum(l[2] for l in LINES)
od_total = sum(D(str(c2(l[3]))) for l in LINES)
assert c2(doc_total) == 4561.44, doc_total

# §1 headline claims ------------------------------------------------------------
resp16_doc_bands = banded(RESP_GIB, DOC_BANDS) + RESP_GIB * D("0.01")
SECTION1 = [
    ("Headroom $438.56", c2(D(5000) - doc_total), "arithmetic OK"),
    ("Unit $1.65 / million requests", c2(doc_total / (REQ_MONTH_DOC / D(10**6))), "arithmetic OK"),
    ("G2 on-demand (no 3-yr CUD) $7,906", c2(doc_total - D("2736.76") + od_g2_4),
     "arithmetic OK but swaps ONLY line 1; lines 7 and 9 keep implied 3-yr CUD, lines 3/4/5/16 keep non-Mumbai rates"),
    ("Cloud Logging @1 KiB/req instead of GCS $5,798.44", c2(doc_total - 53 + (AUDIT_GIB - 50) * P["logging_storage_gib"]),
     "arithmetic OK; unit $0.50/GiB after 50 GiB/project free (143F-A1B0-E0BE)"),
    ("Optional control plane on own nodes +$270", None,
     f"unit not stated; n2-standard-4 = ${SHP['n2-standard-4']['usd_per_month_730h']}, e2-standard-4 = ${SHP['e2-standard-4']['usd_per_month_730h']} (+disk)"),
    ("Upside tenant provider in-region ~$3,970", c2(doc_total - D("1090.32") + resp16_doc_bands),
     "NOT reproducible exactly: removing the prompt leg under the doc's own bands gives this value"),
    ("Quota to 14 L4 (A', 1,862 RPS) $7,228.70", None, "see section8_model_check: doc's own formula gives a different value"),
    ("Traffic month vs compute month", None, "doc prices compute at 730 h but traffic at 30 d = 720 h (2,592,000 s); traffic is understated 1.39% vs a 730 h month"),
]

# §8 cost model check (doc's formula) --------------------------------------------
def doc_model(rps, node=D("701.19"), fixed=D("613.36"), bands=DOC_BANDS, lb=D("0.010"), audit_rate=D("0.02"), per_node_rps=266):
    nodes = math.ceil(rps / per_node_rps)
    gib = D(rps) * D(15.07)
    audit = D(rps) * 86400 * 30 * 1024 / GIB * audit_rate
    return nodes, fixed + nodes * node + banded(gib, bands) + gib * lb + audit

S8 = []
for rps, doc_nodes, doc_usd in [(1064, 4, 4561), (1330, 5, 5401), (2000, 8, 8188), (5320, 20, 19519), (10000, 38, 36251), (1862, 7, 7228.70)]:
    n, v = doc_model(rps)
    node_od = D(str(g2_24["usd_per_month_730h"])) + 100 * P["pd_ssd_gib_mo"] + IP_VM_MONTH
    fixed_od = D(str(BUNDLE_J[list(BUNDLES)[0]]["usd_per_month"]))
    gib = D(rps) * D(15.07)
    od = fixed_od + n * node_od + banded(gib, TIERS["egress_standard_tier_mumbai"]) + gib * D("0.01") + D(rps) * 86400 * 30 * 1024 / GIB * P["gcs_standard_mumbai_gib_mo"]
    S8.append({"rps": rps, "doc_nodes": doc_nodes, "formula_nodes": n, "doc_usd": doc_usd, "doc_formula_recomputed_usd": c2(v),
               "on_demand_mumbai_usd": c2(od)})

# ---------------------------------------------------------------- fleet table (compute + per-node disk + per-node IP; NO egress)
def node_month(shape, disk_key):
    return D(str(SHP[shape]["usd_per_month_730h"])) + D(str(DISK[disk_key])) + IP_VM_MONTH

G2_DISK = "g2_boot_pd_balanced_200gib"
C4_DISK = "c4_boot_hdb_60gib_default_provisioning_3360iops_230mibps"
FLEETS = []
for k in range(1, 7):
    FLEETS.append(("g2-standard-24", k, None, 0))
for k in range(1, 17):
    FLEETS.append(("g2-standard-8", k, None, 0))
for k in range(1, 17):
    FLEETS.append(("g2-standard-4", k, None, 0))
GW_SHAPES = ["c4-highcpu-8", "c4-highcpu-16", "c4-highcpu-32", "c4d-highcpu-8", "c4d-highcpu-16", "c4d-highcpu-32"]
for gw, gwn in itertools.product(GW_SHAPES, [1, 2, 3, 4]):
    for gd, gdn in itertools.product(["g2-standard-4", "g2-standard-8"], range(1, 17)):
        FLEETS.append((gd, gdn, gw, gwn))
FLEET_J = []
for gd, gdn, gw, gwn in FLEETS:
    guard = gdn * node_month(gd, G2_DISK)
    gate = gwn * node_month(gw, C4_DISK) if gw else D(0)
    tot = guard + gate
    l4 = gdn * SPEC[gd]["gpus"]
    vcpu = gdn * SPEC[gd]["vcpu"] + (gwn * SPEC[gw]["vcpu"] if gw else 0)
    row = {"guard_shape": gd, "guard_nodes": gdn, "gateway_shape": gw, "gateway_nodes": gwn, "l4_gpus": l4, "vcpus": vcpu,
           "compute_disk_ip_usd_per_month": c2(tot)}
    for bname, b in BUNDLE_J.items():
        rem = D(5000) - tot - D(str(b["usd_per_month"]))
        tag = "doc_parity" if bname.startswith("F-doc") else "lead_list"
        row[f"remaining_for_egress_usd_{tag}"] = c2(rem)
        row[f"standard_tier_egress_GiB_that_remaining_buys_{tag}"] = (c2(banded_inverse(rem, TIERS["egress_standard_tier_mumbai"])) if rem > 0 else 0)
    FLEET_J.append(row)

# max guard nodes that fit under $5,000 BEFORE egress, per gateway config and bundle
SUMMARY = []
for gd in ("g2-standard-4", "g2-standard-8"):
    for gw, gwn in [(None, 0)] + list(itertools.product(GW_SHAPES, [1, 2, 3, 4])):
        rows = [r for r in FLEET_J if r["guard_shape"] == gd and r["gateway_shape"] == gw and r["gateway_nodes"] == gwn]
        out = {"guard_shape": gd, "gateway_shape": gw, "gateway_nodes": gwn}
        for tag in ("doc_parity", "lead_list"):
            ok = [r for r in rows if r[f"remaining_for_egress_usd_{tag}"] >= 0]
            best = max(ok, key=lambda r: r["guard_nodes"]) if ok else None
            out[f"max_guard_nodes_{tag}"] = best["guard_nodes"] if best else 0
            out[f"fleet_usd_{tag}"] = best["compute_disk_ip_usd_per_month"] if best else None
            out[f"remaining_for_egress_usd_{tag}"] = best[f"remaining_for_egress_usd_{tag}"] if best else None
        SUMMARY.append(out)
PER_UNIT = {}
for n in SHAPES:
    s = SHP[n]; disk = DISK[G2_DISK] if n.startswith("g2") else DISK[C4_DISK]
    node = D(str(s["usd_per_month_730h"])) + D(str(disk)) + IP_VM_MONTH
    PER_UNIT[n] = {"node_usd_per_month_incl_disk_ip": c2(node),
                   "usd_per_L4_month": c2(node / s["gpus"]) if s["gpus"] else None,
                   "usd_per_vcpu_month_vm_only": c2(D(str(s["usd_per_month_730h"])) / s["vcpu"])}

# ---------------------------------------------------------------- write outputs
fetched = open(os.path.join(RAW, "FETCHED_AT_UTC.txt")).read().strip()
prices = {
    "meta": {
        "source": "Cloud Billing Catalog API v1 (cloudbilling.googleapis.com/v1/services/*/skus?currencyCode=USD), raw pages in raw/",
        "fetched_at_utc": fetched, "pricing_effectiveTime_all_used_skus": sorted({r["effectiveTime"] for r in SKU_TABLE}),
        "region": "asia-south1 (Mumbai)", "basis": "ON-DEMAND list price; no CUD, no SUD, no Spot, no free-tier credits",
        "hours_per_month": 730, "currency": "USD",
        "services": {"Compute Engine": "6F81-5844-456A", "Networking": "E505-1604-58F8", "Cloud Memorystore for Redis": "5AF5-2C11-D467",
                     "Cloud Memorystore (Valkey)": "A2B5-E0F1-B0F3", "Cloud SQL": "9662-B51E-5089", "Cloud Logging": "5490-F7B7-8DF6",
                     "Cloud Monitoring": "58CD-E7C3-72CA", "Kubernetes Engine": "CCD8-9BF1-090E", "Artifact Registry": "149C-F9EC-3994",
                     "Cloud Storage": "95FF-2EF5-5EA1"},
        "machine_specs_source": "gcloud compute machine-types list --zones asia-south1-a,b,c (raw/machine_types_asia-south1.json); "
                                "cross-checked g2-standard-24 and c4d-highcpu-16 with gcloud compute machine-types describe (raw/describe_*.json)",
        "doc_sourced_rules (not in the API)": [
            "Hyperdisk Balanced bills provisioned IOPS/throughput only in excess of 3,000 IOPS and 140 MBps (docs/compute_disks-image-pricing.html)",
            "Static external IPs assigned to forwarding rules are not charged (docs/vpc_network-pricing.html)",
            "Cloud NAT data processing is charged on inbound and outbound bytes (docs/vpc_network-pricing.html)",
            "SUD applies to N1/N2/N2D/C2/M1/M2 vCPU+RAM only; G2, C4, C4D, C3, E2 get none (docs/compute_docs_sustained-use-discounts.html)",
        ],
    },
    "unit_prices_usd": {k: str(v) for k, v in P.items()},
    "skus": SKU_TABLE,
    "shapes": SHP,
    "per_node_extras_usd_per_month": {**DISK, "external_ip_standard_vm": c2(IP_VM_MONTH)},
    "fixed_items": FIXED_J,
    "fixed_bundles": BUNDLE_J,
    "egress": EGRESS,
}
json.dump(prices, open(os.path.join(OUT, "prices.json"), "w"), indent=1)
json.dump({"lines": [{"line": n, "service": s, "doc_usd": float(d), "on_demand_mumbai_usd": c2(o), "assumes_3yr_cud": cud, "verdict_and_derivation": why}
                     for n, s, d, o, cud, why in LINES],
           "doc_total_usd": c2(doc_total), "on_demand_total_usd": c2(od_total),
           "doc_claim_g2_on_demand_usd": 7906, "doc_claim_g2_on_demand_recomputed": c2(doc_total - D("2736.76") + od_g2_4),
           "section8_model_check": S8,
           "section1_claims": [{"claim": a, "recomputed": b, "note": c} for a, b, c in SECTION1],
           "volumes_used (doc's own)": {"requests_per_month": int(REQ_MONTH_DOC), "egress_gib_6244B": c2(EG_GIB_DOC), "response_gib_2950B": c2(RESP_GIB),
                                        "prompt_gib_3294B": c2(PROMPT_GIB), "audit_gib_1KiB": c2(AUDIT_GIB)}},
          open(os.path.join(OUT, "cost_matrix_recompute.json"), "w"), indent=1)
json.dump({"assumptions": {"guard_boot_disk": G2_DISK, "gateway_boot_disk": C4_DISK, "external_ip_per_node_usd": c2(IP_VM_MONTH),
                           "egress": "NOT included; remaining_* columns = 5000 - fleet - bundle; *_GiB_that_remaining_buys = Standard-tier Mumbai GiB (LB processing excluded)"},
           "bundles": BUNDLE_J, "per_unit": PER_UNIT, "max_guard_nodes_under_5000_before_egress": SUMMARY, "fleets": FLEET_J},
          open(os.path.join(OUT, "fleets.json"), "w"), indent=1)

# ---------------------------------------------------------------- print log
print("fetched_at", fetched)
print("\n== SHAPES (on-demand, asia-south1)")
for n, s in SHP.items():
    print(f"{n:<16} vcpu={s['vcpu']:>2} mem={s['memory_gib']:>6.1f}GiB gpu={s['gpus']} $/h={s['usd_per_hour_total']:.6f} $/mo={s['usd_per_month_730h']:>9.2f}"
          f"  cpu/h={D(s['usd_per_hour']['cpu']):.6f} ram/h={D(s['usd_per_hour']['ram']):.6f} gpu/h={D(s['usd_per_hour']['gpu']):.6f}"
          f"  3yrCUDref={s.get('reference_only_3yr_cud_usd_per_month_730h', '-')}  zones={','.join(z[-1] for z in s['zones_asia_south1'])}")
print("\n== PER-NODE EXTRAS", json.dumps(prices["per_node_extras_usd_per_month"]))
print("\n== FIXED ITEMS")
for f in FIXED_J:
    print(f"  {f['usd_per_month']:>9.2f}  {f['item']}  [{f['sku_ids']}] {f['note']}")
print("\n== BUNDLES")
for k, v in BUNDLE_J.items():
    print(f"  {v['usd_per_month']:>9.2f}  {k}")
print("\n== EGRESS reference points", json.dumps(EGRESS["reference_points_usd"]))
print("\n== DOC COST MATRIX RECOMPUTE")
for n, s, d, o, cud, why in LINES:
    print(f"{n:>2} {s:<52} doc={float(d):>9.2f} od={c2(o):>9.2f} CUD={cud:<15} | {why}")
print(f"TOTAL doc={c2(doc_total)} on-demand={c2(od_total)} ; doc 'G2 on-demand' claim 7906 recomputed={c2(doc_total - D('2736.76') + od_g2_4)}")
print("\n== §1 claims"); [print("  ", r) for r in SECTION1]
print("\n== §8 model check"); [print("  ", r) for r in S8]
print("\n== PER UNIT"); [print(f"  {k:<16} {v}") for k, v in PER_UNIT.items()]
print("\n== FLEETS single-shape series (compute+boot disk+IP, no egress)")
for r in FLEET_J:
    if r["gateway_shape"] is None:
        print(f"  {r['guard_nodes']:>2} x {r['guard_shape']:<15} L4={r['l4_gpus']:>2} vCPU={r['vcpus']:>3} fleet=${r['compute_disk_ip_usd_per_month']:>9.2f}"
              f"  rem(doc-parity)=${r['remaining_for_egress_usd_doc_parity']:>9.2f} ({r['standard_tier_egress_GiB_that_remaining_buys_doc_parity']} GiB std)"
              f"  rem(lead-list)=${r['remaining_for_egress_usd_lead_list']:>9.2f} ({r['standard_tier_egress_GiB_that_remaining_buys_lead_list']} GiB std)")
print("\n== MAX GUARD NODES under $5,000 before egress")
for r in SUMMARY:
    print(f"  guard={r['guard_shape']:<14} gw={str(r['gateway_nodes'])+'x'+str(r['gateway_shape']):<18} "
          f"doc-parity: n={r['max_guard_nodes_doc_parity']:>2} fleet=${r['fleet_usd_doc_parity']} rem=${r['remaining_for_egress_usd_doc_parity']} | "
          f"lead-list: n={r['max_guard_nodes_lead_list']:>2} fleet=${r['fleet_usd_lead_list']} rem=${r['remaining_for_egress_usd_lead_list']}")
