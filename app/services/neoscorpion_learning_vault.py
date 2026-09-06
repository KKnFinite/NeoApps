"""Provider-neutral SPEAR Vault boundary with a private Cloudflare R2 backend."""

from dataclasses import dataclass
from datetime import datetime
import gzip
import hashlib
import hmac
import json
import os
import re
import zlib


MAX_REVIEW_BYTES = 1_000_000
# v1 contains aggregate rows, not observations or mission records. A synthetic
# 4,096-scope review is below 1 MB (see boundary tests). 4 MB allows substantial
# headroom without allowing compressed R2 objects to inflate without a limit.
# There is no finite row-count maximum in the canonical current-sort contract.
MAX_DECOMPRESSED_REVIEW_BYTES = 4_000_000
VAULT_CONNECT_TIMEOUT_SECONDS = 5
VAULT_READ_TIMEOUT_SECONDS = 10
CALIBRATION_PREFIX = "calibration-review/"
ARCHIVE_FIELDS = frozenset({"learning_capture_enabled", "saved_at", "saved_by_user_id", "gateway", "sort", "checksum"})
REVIEW_FIELDS = frozenset({"schema_version", "spear_algorithm_version", "capture_mode", "operation_id", "training_eligible", "calibrations"})


class LearningVaultNotConfigured(ValueError):
    """Raised before a capture could be written without durable storage."""


class LearningVaultUnavailable(ValueError):
    """Safe, credential-free provider failure for an operator-facing action."""


@dataclass(frozen=True)
class LearningVaultStatus:
    configured: bool
    label: str
    detail: str
    bucket: str | None = None


def learning_vault_status(_config=None):
    config = _vault_config(_config)
    if not _is_valid_r2_config(config):
        return LearningVaultStatus(False, "SPEAR VAULT · NOT CONFIGURED", "Configure a durable SPEAR Vault before enabling capture.")
    return LearningVaultStatus(True, "SPEAR VAULT · CONNECTED", "Private R2 SPEAR Vault configured.", config["bucket"])


def require_learning_vault(_config=None):
    status = learning_vault_status(_config)
    if not status.configured:
        raise LearningVaultNotConfigured("SPEAR Learning Capture requires a configured durable Learning Vault.")
    return status


def export_learning_record(record, _config=None):
    """Automatic learning capture remains intentionally unimplemented."""
    require_learning_vault(_config)
    raise NotImplementedError("Automatic SPEAR Learning Capture is not enabled.")


def test_learning_vault_connection(_config=None):
    config = _require_r2_config(_config)
    key = f"_system/healthchecks/neoapps-{datetime.utcnow():%Y%m%dT%H%M%S%fZ}.json"
    client = None
    deleting = False
    try:
        client = _r2_client(config)
        client.put_object(Bucket=config["bucket"], Key=key, Body=b'{"ok":true}', ContentType="application/json")
        client.head_object(Bucket=config["bucket"], Key=key)
        deleting = True
        client.delete_object(Bucket=config["bucket"], Key=key)
    except Exception as exc:
        if client is not None and not deleting:
            _best_effort_delete(client, config["bucket"], key)
        raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    return True


def archive_calibration_review(review, *, gateway, operation, user, learning_capture_enabled, _config=None):
    """Archive one human-selected review; no Neon payload mirror is retained."""
    config = _require_r2_config(_config)
    canonical_review = _canonical_json(review)
    checksum = hashlib.sha256(canonical_review).hexdigest()
    key = _calibration_key(operation, gateway, checksum)
    try:
        client = _r2_client(config)
        client.head_object(Bucket=config["bucket"], Key=key)
        return {"key": key, "checksum": checksum, "already_saved": True}
    except Exception as exc:
        # Only a confirmed missing object permits a write. A failed HEAD is not
        # evidence of absence, and must not turn into an unsafe retry/overwrite.
        response = getattr(exc, "response", {})
        metadata = response.get("ResponseMetadata") if isinstance(response, dict) else None
        if not isinstance(metadata, dict) or metadata.get("HTTPStatusCode") != 404:
            raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    payload = dict(review)
    payload.update({
        "capture_mode": "manual_calibration_review",
        "learning_capture_enabled": bool(learning_capture_enabled),
        "training_eligible": False,
        "saved_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "saved_by_user_id": getattr(user, "id", None),
        "gateway": {"id": getattr(gateway, "id", None), "code": _safe_component(getattr(gateway, "code", "gateway"))},
        "sort": {"operation_id": getattr(operation, "id", None), "sort_date": str(getattr(operation, "sort_date", ""))},
        "checksum": checksum,
    })
    serialized = _canonical_json(payload)
    if len(serialized) > MAX_DECOMPRESSED_REVIEW_BYTES:
        raise LearningVaultUnavailable("SPEAR Vault archive is too large.")
    _validate_review(payload, key)
    compressed = gzip.compress(serialized)
    if len(compressed) > MAX_REVIEW_BYTES:
        raise LearningVaultUnavailable("SPEAR Vault archive is too large.")
    try:
        client.put_object(Bucket=config["bucket"], Key=key, Body=compressed, ContentType="application/json", ContentEncoding="gzip", Metadata={"schema-version": str(payload.get("schema_version", "v1")), "checksum": checksum, "training-eligible": "false"})
    except Exception as exc:
        raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    return {"key": key, "checksum": checksum, "already_saved": False}


def list_calibration_reviews(*, limit=50, _config=None):
    config = _require_r2_config(_config)
    try:
        response = _r2_client(config).list_objects_v2(Bucket=config["bucket"], Prefix=CALIBRATION_PREFIX, MaxKeys=max(1, min(int(limit), 50)))
    except Exception as exc:
        raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    records = []
    for item in response.get("Contents", ()):
        key = item.get("Key", "")
        if _valid_calibration_key(key):
            records.append({"key": key, "saved_at": item.get("LastModified"), "size": item.get("Size", 0), "checksum": key.rsplit("/", 1)[-1].split("-", 1)[0][:12]})
    return tuple(sorted(records, key=lambda item: str(item["saved_at"]), reverse=True)[:50])


def read_calibration_review(key, _config=None):
    if not _valid_calibration_key(key):
        raise ValueError("Invalid SPEAR Vault review.")
    config = _require_r2_config(_config)
    try:
        response = _r2_client(config).get_object(Bucket=config["bucket"], Key=key)
        stream = response["Body"]
        try:
            body = stream.read(MAX_REVIEW_BYTES + 1)
        finally:
            stream.close()
    except Exception as exc:
        raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    if len(body) > MAX_REVIEW_BYTES:
        raise ValueError("Stored SPEAR Vault review is too large.")
    try:
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        raw = inflater.decompress(body, MAX_DECOMPRESSED_REVIEW_BYTES + 1)
        if len(raw) > MAX_DECOMPRESSED_REVIEW_BYTES:
            raise ValueError("Stored SPEAR Vault review is too large.")
        # eof includes gzip CRC/length verification. Never flush unboundedly;
        # reject truncation, additional members and any trailing bytes.
        if not inflater.eof or inflater.unused_data or inflater.unconsumed_tail:
            raise ValueError("Stored SPEAR Vault review is malformed.")
    except zlib.error:
        raise ValueError("Stored SPEAR Vault review is malformed.") from None
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                             parse_constant=_reject_json_constant)
        _validate_review(payload, key, response.get("Metadata", {}))
    except (ValueError, TypeError, KeyError, RecursionError, OverflowError):
        raise ValueError("Stored SPEAR Vault review is malformed.") from None
    return payload


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise ValueError("Non-finite JSON number")


def _validate_review(payload, key, metadata=None):
    from app.services.neoscorpion_spear_calibration import METRICS

    def require(condition):
        if not condition:
            raise ValueError("Stored SPEAR Vault review is malformed.")

    def identity(value):
        return type(value) is int and value > 0

    require(isinstance(payload, dict))
    require(set(payload) <= REVIEW_FIELDS | ARCHIVE_FIELDS)
    require(payload.get("schema_version") == "v1")
    require(payload.get("capture_mode") == "manual_calibration_review")
    require(payload.get("training_eligible") is False)
    require(type(payload.get("learning_capture_enabled")) is bool)
    require(payload.get("saved_by_user_id") is None or identity(payload["saved_by_user_id"]))
    require(isinstance(payload.get("saved_at"), str))
    require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", payload["saved_at"])))
    datetime.fromisoformat(payload["saved_at"].replace("Z", "+00:00"))
    gateway, sort = payload.get("gateway"), payload.get("sort")
    require(isinstance(gateway, dict) and set(gateway) == {"id", "code"})
    require(identity(gateway["id"]) and isinstance(gateway["code"], str))
    require(bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", gateway["code"])))
    require(isinstance(sort, dict) and set(sort) == {"operation_id", "sort_date"})
    require(identity(sort["operation_id"]) and isinstance(sort["sort_date"], str))
    date = datetime.strptime(sort["sort_date"], "%Y-%m-%d")
    prefix = f"{CALIBRATION_PREFIX}{date:%Y/%m/%d}/gateway-{gateway['id']}/sort-{sort['operation_id']}/"
    require(key.startswith(prefix))
    # Historical minimal v1 reviews omit BOTH algorithm and operation ID. Do
    # not synthesize fields before hashing: their absence was part of that hash.
    if "operation_id" in payload or "spear_algorithm_version" in payload:
        require(identity(payload.get("operation_id")) and payload["operation_id"] == sort["operation_id"])
        require(payload.get("spear_algorithm_version") == "spear-v1")
    require(isinstance(payload.get("calibrations"), list))
    for item in payload["calibrations"]:
        require(isinstance(item, dict) and set(item) == {"metric", "scope", "configured", "observed", "effective", "samples", "excluded_samples", "confidence"})
        require(item["metric"] in METRICS)
        require(isinstance(item["scope"], str) and 0 < len(item["scope"]) <= 256)
        for field in ("configured", "observed", "effective"):
            value = item[field]
            require(value is None or (isinstance(value, str) and bool(re.fullmatch(r"-?\d{1,16}\.\d", value))))
        for field in ("samples", "excluded_samples"):
            require(type(item[field]) is int and item[field] >= 0)
        require(item["confidence"] in ("COLLECTING", "LOW", "MEDIUM", "HIGH"))
    original = {k: v for k, v in payload.items() if k not in ARCHIVE_FIELDS}
    original["capture_mode"] = "live_calibration_review"
    checksum = hashlib.sha256(_canonical_json(original)).hexdigest()
    require(isinstance(payload.get("checksum"), str))
    require(hmac.compare_digest(checksum, payload["checksum"]))
    require(key == prefix + checksum + "-calibration-review-v1.json.gz")
    require(metadata is None or isinstance(metadata, dict))
    if metadata:
        require(metadata.get("checksum", checksum) == checksum)
        require(metadata.get("schema-version", "v1") == "v1")
        require(metadata.get("training-eligible", "false") == "false")


def _vault_config(source=None):
    source = os.environ if source is None else source
    return {"provider": str(source.get("SPEAR_VAULT_PROVIDER", "")).strip().lower(), "bucket": str(source.get("SPEAR_VAULT_BUCKET", "")).strip(), "region": str(source.get("SPEAR_VAULT_REGION", "")).strip(), "endpoint": str(source.get("SPEAR_VAULT_ENDPOINT", "")).strip(), "access_key": str(source.get("SPEAR_VAULT_ACCESS_KEY_ID", "")).strip(), "secret_key": str(source.get("SPEAR_VAULT_SECRET_ACCESS_KEY", "")).strip()}


def _is_valid_r2_config(config):
    return config["provider"] == "r2" and all(config[key] for key in ("bucket", "region", "endpoint", "access_key", "secret_key"))


def _require_r2_config(source=None):
    config = _vault_config(source)
    if not _is_valid_r2_config(config):
        raise LearningVaultNotConfigured("SPEAR Vault is not configured.")
    return config


def _r2_client(config):
    import boto3
    from botocore.config import Config
    return boto3.client("s3", endpoint_url=config["endpoint"], region_name=config["region"], aws_access_key_id=config["access_key"], aws_secret_access_key=config["secret_key"], config=Config(
        signature_version="s3v4",
        connect_timeout=VAULT_CONNECT_TIMEOUT_SECONDS,
        read_timeout=VAULT_READ_TIMEOUT_SECONDS,
        retries={"mode": "standard", "total_max_attempts": 1},
    ))


def _calibration_key(operation, gateway, checksum):
    sort_date = getattr(operation, "sort_date", None)
    stamp = sort_date.strftime("%Y/%m/%d") if hasattr(sort_date, "strftime") else "unknown/unknown/unknown"
    return f"{CALIBRATION_PREFIX}{stamp}/gateway-{_safe_component(getattr(gateway, 'id', 'unknown'))}/sort-{_safe_component(getattr(operation, 'id', 'unknown'))}/{checksum}-calibration-review-v1.json.gz"


def _valid_calibration_key(key):
    return bool(re.fullmatch(r"calibration-review/\d{4}/\d{2}/\d{2}/gateway-[A-Za-z0-9_-]+/sort-[A-Za-z0-9_-]+/[0-9a-f]{64}-calibration-review-v1\.json\.gz", str(key or "")))


def _safe_component(value):
    return re.sub(r"[^A-Za-z0-9_-]", "-", str(value or "unknown"))[:64] or "unknown"


def _canonical_json(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _best_effort_delete(client, bucket, key):
    try:
        client.delete_object(Bucket=bucket, Key=key)
    except Exception:
        pass


def _safe_provider_error(_error):
    return "SPEAR Vault connection failed. Check the R2 bucket, credentials, and Render configuration."
