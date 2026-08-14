"""Per-user SSH keypair generation (docs/PLATFORM_SPEC.md §7.4).

Keypairs are generated here, stored in Vault keyed by user ID, and mounted
into sandboxes only for a single run.
"""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519


def generate_ssh_keypair() -> tuple[str, str]:
    """Return ``(private_pem, public_pem)``."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")
    public_openssh = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("utf-8")
    return private_pem, public_openssh
