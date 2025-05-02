import json
import os
import re
import time
import logging
import zlib
import requests

# ------------------------------------------------------------------------------------------------
# Configuration via ENV VARS
# - *_METRICS - Controls what keys are picked up as distinct metrics
# - EXCLUDE_ATTRIBUTES - Controls what keys are excluded to be added as attributes to any metric
# ------------------------------------------------------------------------------------------------
def parse_env_list(name):
    raw = os.getenv(name, "")
    return {p.strip() for p in re.split(r"[,;]\s*", raw) if p.strip()}

GAUGE_METRICS = parse_env_list("GAUGE_METRICS")
COUNT_METRICS = parse_env_list("COUNT_METRICS")
SUMMARY_METRICS = parse_env_list("SUMMARY_METRICS")
EXCLUDE_ATTRIBUTES = parse_env_list("EXCLUDE_ATTRIBUTES")

# New Relic API settings
NR_METRIC_ENDPOINT = os.getenv("NEW_RELIC_METRIC_ENDPOINT", "https://metric-api.newrelic.com/metric/v1")
NR_INGEST_KEY = os.getenv("NEW_RELIC_INGEST_KEY")
if not NR_INGEST_KEY:
    raise RuntimeError("Missing New Relic API key (set NEW_RELIC_INGEST_KEY)")

# Logging Config
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Plural→singular map for specific nested arrays (for metric/attr names)
SINGULAR = {
    "components": "component",
    "children":   "child"
}

# Helper to compress metric payload
@staticmethod
def _compress_payload(payload):
    level = zlib.Z_DEFAULT_COMPRESSION
    compressor = zlib.compressobj(level, zlib.DEFLATED, 31)
    payload = compressor.compress(payload)
    payload += compressor.flush()
    return payload


def post_to_nr(payload):
    headers = {"Content-Type": "application/json", "Content-Encoding": "gzip", "Api-Key": NR_INGEST_KEY}
    json_payload = json.dumps(payload, separators=(",",":"))
    if not isinstance(json_payload, bytes):
        json_payload = json_payload.encode("utf-8")
    compressed = _compress_payload(json_payload)

    resp = requests.post(NR_METRIC_ENDPOINT, headers=headers, data=compressed, timeout=30)
    resp.raise_for_status()

    count = len(payload[0].get("metrics", [])) if isinstance(payload, list) and payload else 0
    logger.debug("Posted %d metrics → status %d", count, resp.status_code)

    return resp.status_code, resp.text


def lambda_handler(event, context):
    body = event.get("body", event)
    payload = json.loads(body) if isinstance(body, str) else body

    # Timestamp in ms
    timestamp = int(time.time() * 1000)

    # Build Prefix
    access = payload.get("access_type", "").lower()
    prefix = f"probe.session.{access}".replace("-", "_")

    # Common attributes
    common_attrs = {}
    vendor = "TAGVS"
    probetype = "VIDEOPROBE"
    device = payload.get("device")
    label = payload.get("label")
    receivers = payload.get("receivers", [])
    prog_nums = [r.get("program_number") for r in receivers if r.get("program_number") is not None]
    first_prog = prog_nums[0] if prog_nums else None

    if vendor:
        common_attrs["probe.tag.vendor"] = vendor
    if device and label and first_prog is not None:
        tagname = f"{device}-{label}-{first_prog}"
        common_attrs["probe.session.tag.name"] = tagname
        common_attrs["probe.session.entity.name"] = f"{vendor}-{tagname}"
    if payload.get("access_type"):
        common_attrs["probe.session.tag.type"] = payload["access_type"]
    if probetype:
        common_attrs["probe.tag.type"] = probetype

    # Root-level dynamic attributes flattening
    root_dynamic = {}
    def flatten_root(obj, path):
        if isinstance(obj, (str, int, float, bool)):
            key = prefix + "." + ".".join(path)
            root_dynamic[key] = obj
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k == 'components' or k in EXCLUDE_ATTRIBUTES or k in GAUGE_METRICS or k in COUNT_METRICS or k in SUMMARY_METRICS:
                    continue
                flatten_root(v, path + [k.replace("-", "_")])
        elif isinstance(obj, list) and path:
            base = path[:-1]
            for idx, item in enumerate(obj):
                flatten_root(item, base + [f"{path[-1]}_{idx}"])
    flatten_root(payload, [])

    # Helper for item-level dynamic flattening
    def flatten_item(obj, path, dest):
        if isinstance(obj, (str, int, float, bool)):
            key = prefix + "." + ".".join(path)
            dest[key] = obj
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k in EXCLUDE_ATTRIBUTES or k in GAUGE_METRICS or k in COUNT_METRICS or k in SUMMARY_METRICS or k in ('components', 'children'):
                    continue
                flatten_item(v, path + [k.replace("-", "_")], dest)
        elif isinstance(obj, list) and path:
            base = path[:-1]
            for idx, item in enumerate(obj):
                flatten_item(item, base + [f"{path[-1]}_{idx}"], dest)

    metrics = [] # init metric array

    # Recursively parse through objects to build metrics
    # TODO: Not sure what `interval.ms` on count and summary metrics should be set to. For now, setting to 60 seconds.
    def parse_dict(obj, path, dynamic_attrs):
        attrs = dict(dynamic_attrs)
        for k, v in obj.items():
            seg_path = path + [k]
            segs = [SINGULAR.get(p, p).replace("-", "_") for p in seg_path]
            metric_name = prefix + "." + ".".join(segs)

            # Summary metrics
            if k in SUMMARY_METRICS and isinstance(v, dict) and all(x in v for x in ('min','avg','max')):
                metrics.append({
                    'name': metric_name.replace("-", "_"), 'type': 'summary',
                    'interval.ms': 60000,
                    'value': {
                        'count': 1, 'sum': v['avg'], 'min': v['min'], 'max': v['max']
                    },
                    'timestamp': timestamp, 'attributes': attrs
                })
                continue

            # pcr_drift handling (nested metrics in a string)
            if k in GAUGE_METRICS and k == 'pcr_drift' and isinstance(v, str):
                for label_match, value_match in re.findall(r'([A-Za-z]+)\s*([\d.]+)ms', v):
                    suffix = label_match.strip().lower()
                    try:
                        seg_val = float(value_match)
                    except:
                        continue
                    metrics.append({
                        'name': f"{metric_name}.{suffix}".replace("-", "_"),
                        'type': 'gauge',
                        'value': seg_val,
                        'timestamp': timestamp,
                        'attributes': attrs
                    })
                continue

            # Gauge metrics
            if k in GAUGE_METRICS and not isinstance(v, (dict, list)):
                try:
                    val = float(v)
                except:
                    continue
                metrics.append({
                    'name': metric_name.replace("-", "_"), 'type': 'gauge', 'value': val,
                    'timestamp': timestamp, 'attributes': attrs
                })
                continue

            # Count metrics
            if k in COUNT_METRICS and not isinstance(v, (dict, list)):
                try:
                    val = float(v)
                except:
                    continue
                metrics.append({
                    'name': metric_name.replace("-","_"), 'type': 'count', 'value': val,
                    'interval.ms': 60000, 'timestamp': timestamp,
                    'attributes': attrs
                })
                continue

        # recurse
        for k, v in obj.items():
            if isinstance(v, dict):
                parse_dict(v, path + [k], dynamic_attrs)
            elif isinstance(v, list):
                if k in SINGULAR:
                    seg = SINGULAR[k]
                    for item in v:
                        if not isinstance(item, dict):
                            continue
                        new_path = path + [seg]
                        item_dynamic = {}
                        flatten_item(item, new_path, item_dynamic)
                        parse_dict(item, new_path, item_dynamic)
                else:
                    for idx, item in enumerate(v):
                        if isinstance(item, dict):
                            parse_dict(item, path + [f"{k}_{idx}"], dynamic_attrs)

    # start parsing
    parse_dict(payload, [], root_dynamic)

    # Final metric payload
    final_output = [{"common": {"interval.ms": 60000, "attributes": common_attrs}, "metrics": metrics}]
    # print(json.dumps(final_output))

    # Ship metrics to New Relic
    status, resp_text = post_to_nr(final_output)
    return {
        "statusCode": status,
        "body": json.dumps({"metrics_sent": len(metrics), "response": resp_text})
    }