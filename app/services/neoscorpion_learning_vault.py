"""Provider-neutral boundary for future durable SPEAR learning storage.

No provider is configured in V1.  Keeping this boundary explicit prevents
operational recommendations from quietly becoming Render- or Neon-backed
training telemetry before the data source is complete and a durable vault is
chosen.
"""

from dataclasses import dataclass


class LearningVaultNotConfigured(ValueError):
    """Raised before any learning capture could be written unsafely."""


@dataclass(frozen=True)
class LearningVaultStatus:
    configured: bool
    label: str
    detail: str


def learning_vault_status(_config=None):
    """Return the only V1 vault state: no durable backend is configured."""
    return LearningVaultStatus(
        configured=False,
        label="LEARNING VAULT NOT CONFIGURED",
        detail="Configure a durable external Learning Vault before enabling capture.",
    )


def require_learning_vault(_config=None):
    status = learning_vault_status(_config)
    if not status.configured:
        raise LearningVaultNotConfigured(
            "SPEAR Learning Capture requires a configured durable Learning Vault."
        )
    return status


def export_learning_record(record, _config=None):
    """Future provider entry point; V1 refuses instead of falling back to storage."""
    require_learning_vault(_config)
    raise NotImplementedError("No SPEAR Learning Vault provider is configured.")
