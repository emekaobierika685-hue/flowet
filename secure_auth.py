# secure_auth.py
import base64
import json
import os
import time

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes, serialization

from headers import headers2
import utils

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

def _generate_keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    # JWK export for clientPublicKey
    pub = private.public_key().public_numbers()
    n = pub.n.to_bytes((pub.n.bit_length() + 7) // 8, "big")
    e = pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big")
    jwk = {"e": _b64url(e), "kty": "RSA", "n": _b64url(n)}
    return private, jwk

def get_sa(session):
    """
    Returns (client_public_key, client_epoch_timestamp, server_nonce, sai_signature)
    The four fields for signup_payload["secureAuthenticationIntent"].
    """
    try:
        # Step 1: get server nonce
        r = session.post(
            "https://auth.roblox.com/v1/authentication-nonce",
            headers=headers2(""),   # no csrf needed
            timeout=15,
        )
        if r.status_code != 200:
            utils.fprint(f"authentication-nonce failed: {r.status_code}")
            return None, None, None, None
        nonce_json = r.json()
        server_nonce = nonce_json.get("nonce")
        if not server_nonce:
            utils.fprint("authentication-nonce: missing 'nonce'")
            return None, None, None, None

        # Step 2: build a keypair
        private_key, jwk = _generate_keypair()
        client_public_key = json.dumps(jwk, separators=(",", ":"))
        client_epoch_timestamp = int(time.time() * 1000)

        # Step 3: sign the nonce
        # Roblox expects signature over:  server_nonce
        # (some clients also include clientEpochTimestamp; adjust if rejected)
        to_sign = server_nonce.encode("utf-8")
        signature = private_key.sign(
            to_sign,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        sai_signature = base64.b64encode(signature).decode()

        return client_public_key, client_epoch_timestamp, server_nonce, sai_signature

    except Exception as e:
        utils.fprint(f"get_sa error: {e}")
        return None, None, None, None
