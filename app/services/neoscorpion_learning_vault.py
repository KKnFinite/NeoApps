"""Provider-neutral SPEAR Vault boundary with a private Cloudflare R2 backend."""

from dataclasses import dataclass
from datetime import datetime
import gzip
import hashlib
import json
import os
import re


MAX_REVIEW_BYTES = 1_000_000
CALIBRATION_PREFIX = "calibration-review/"


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
    try:
        client = _r2_client(config)
        client.put_object(Bucket=config["bucket"], Key=key, Body=b'{"ok":true}', ContentType="application/json")
        client.head_object(Bucket=config["bucket"], Key=key)
    except Exception as exc:
        if client is not None:
            _best_effort_delete(client, config["bucket"], key)
        raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    _best_effort_delete(client, config["bucket"], key)
    return True


def archive_calibration_review(review, *, gateway, operation, user, learning_capture_enabled, _config=None):
    """Archive one human-selected review; no Neon payload mirror is retained."""
    config = _require_r2_config(_config)
    canonical_review = _canonical_json(review)
    checksum = hashlib.sha256(canonical_review).hexdigest()
    key = _calibration_key(operation, gateway, checksum)
    client = _r2_client(config)
    try:
        client.head_object(Bucket=config["bucket"], Key=key)
        return {"key": key, "checksum": checksum, "already_saved": True}
    except Exception:
        # Deterministic keys make retry/double-click writes idempotent.
        pass
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
    compressed = gzip.compress(_canonical_json(payload))
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
        body = _r2_client(config).get_object(Bucket=config["bucket"], Key=key)["Body"].read(MAX_REVIEW_BYTES + 1)
        if len(body) > MAX_REVIEW_BYTES:
            raise ValueError("Stored SPEAR Vault review is too large.")
        payload = json.loads(gzip.decompress(body).decode("utf-8"))
    except ValueError:
        raise
    except Exception as exc:
        raise LearningVaultUnavailable(_safe_provider_error(exc)) from None
    if not isinstance(payload, dict) or payload.get("capture_mode") != "manual_calibration_review" or payload.get("training_eligible") is not False:
        raise ValueError("Stored SPEAR Vault review is malformed.")
    return payload


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
    return boto3.client("s3", endpoint_url=config["endpoint"], region_name=config["region"], aws_access_key_id=config["access_key"], aws_secret_access_key=config["secret_key"], config=Config(signature_version="s3v4"))


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
