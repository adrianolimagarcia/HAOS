"""B2 — ANP identity did:wba (stdlib, sem runtime externo).

Modelo mínimo real do ANP-03: parse/validação de DIDs ``did:wba:domain:path``
com perfil de chave embutido no caminho (default ``e1_``; compat ``k1_``),
DID document com verificationMethod + service, e o fingerprint determinístico
da chave pública (relação: mesma chave => mesmo fingerprint; chaves distintas
=> fingerprints distintos). Assinatura/verificação ficam em SEAMS injetáveis
(o processo nunca toca crypto de terceiros; stdlib não tem Ed25519 em 3.14? —
deixamos o algoritmo fora do processo, como os demais peers).
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DID_WBA_PREFIX = "did:wba:"
_E1 = "e1_"
_K1 = "k1_"
KEY_PROFILES = (_E1, _K1)


class ANPIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class DID:
    """did:wba:<domain>:<path-segments>[?pk_<profile>_<fingerprint>]."""

    domain: str
    path: List[str]            # segmentos após o domínio (agente + opcionais)
    key_profile: Optional[str] = None      # e1_ | k1_
    key_fingerprint: Optional[str] = None  # fingerprint da chave pública

    @property
    def agent_id(self) -> str:
        """Identidade de negócio do agente (caminho sem fingerprint)."""
        return ":".join(p for p in self.path if not p.startswith("pk_"))

    @property
    def did(self) -> str:
        base = DID_WBA_PREFIX + self.domain + ":" + ":".join(self.path)
        if self.key_fingerprint:
            base += f":pk_{self.key_profile}{self.key_fingerprint}"
        return base

    def __str__(self) -> str:
        return self.did

    @staticmethod
    def parse(value: str) -> "DID":
        if not isinstance(value, str) or not value.startswith(DID_WBA_PREFIX):
            raise ANPIdentityError(
                f"DID must start with '{DID_WBA_PREFIX}': {value!r}")
        rest = value[len(DID_WBA_PREFIX):]
        segments = rest.split(":")
        if len(segments) < 2:
            raise ANPIdentityError(
                f"did:wba requires at least domain:agent: {value!r}")
        domain = segments[0]
        if not domain or not re.match(r"^[a-z0-9.-]+$", domain):
            raise ANPIdentityError(f"invalid did:wba domain: {domain!r}")
        path = segments[1:]
        key_profile = key_fingerprint = None
        tail = path[-1] if path else ""
        if tail.startswith("pk_"):
            profile = tail[3:6]
            fingerprint = tail[6:]
            if profile not in KEY_PROFILES or not fingerprint:
                raise ANPIdentityError(
                    f"did:wba key segment invalid: {tail!r}")
            key_profile, key_fingerprint = profile, fingerprint
            path = path[:-1]
        if not path or not all(p for p in path):
            raise ANPIdentityError(
                f"did:wba requires a non-empty agent path: {value!r}")
        return DID(domain=domain, path=list(path),
                   key_profile=key_profile, key_fingerprint=key_fingerprint)


def public_key_fingerprint(profile: str, public_key_hex: str) -> str:
    """Fingerprint determinístico ``<profile><sha256[:40]>`` (o ``pk_`` do DID
    é acrescentado pela serialização, per ANP-03).

    Contrato: mesmo (profile, chave) => mesmo fingerprint; chaves diferentes =>
    fingerprints diferentes. (Formato normativo multibase é camada de outro
    perfil; aqui a relação chave->fingerprint é o que importa.)"""
    if profile not in KEY_PROFILES:
        raise ANPIdentityError(f"unknown key profile {profile!r}")
    digest = hashlib.sha256(public_key_hex.encode("ascii")).hexdigest()[:40]
    return f"{profile}{digest}"


def did_with_key(domain: str, agent_path: str, profile: str,
                 public_key_hex: str) -> DID:
    fp = public_key_fingerprint(profile, public_key_hex)
    return DID(domain=domain, path=[agent_path],
               key_profile=profile, key_fingerprint=fp)


@dataclass
class VerificationMethod:
    id: str
    controller: str
    key_type: str  # "Ed25519VerificationKey2020" | "EcdsaSecp256k1VerificationKey2019"
    public_key: str

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.key_type,
                "controller": self.controller,
                "publicKeyMultibase": self.public_key}


@dataclass
class DIDDocument:
    id: str
    verification_method: List[VerificationMethod] = field(default_factory=list)
    services: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def build(did: DID, *, public_key: Optional[str] = None,
              messaging_endpoint: str = "") -> "DIDDocument":
        methods = []
        if did.key_fingerprint and public_key:
            key_type = ("Ed25519VerificationKey2020" if did.key_profile == _E1
                        else "EcdsaSecp256k1VerificationKey2019")
            methods.append(VerificationMethod(
                id=f"{did.did}#key",
                controller=did.did,
                key_type=key_type,
                public_key=public_key,
            ))
        services = ([{"id": f"{did.did}#messaging", "type": "ANPMessageService",
                      "serviceEndpoint": messaging_endpoint}]
                    if messaging_endpoint else [])
        return DIDDocument(id=did.did, verification_method=methods,
                           services=services)

    @staticmethod
    def from_dict(raw: Dict[str, Any]) -> "DIDDocument":
        doc_id = raw.get("id")
        if not isinstance(doc_id, str) or not doc_id.startswith(DID_WBA_PREFIX):
            raise ANPIdentityError("DID document without valid 'id'")
        methods = []
        for vm in raw.get("verificationMethod") or []:
            if not isinstance(vm, dict):
                raise ANPIdentityError("verificationMethod entries must be objects")
            methods.append(VerificationMethod(
                id=vm.get("id", ""), controller=vm.get("controller", ""),
                key_type=vm.get("type", ""), public_key=vm.get("publicKeyMultibase", ""),
            ))
        return DIDDocument(id=doc_id, verification_method=methods,
                           services=list(raw.get("service") or []))


def did_wba_document_url(did: DID) -> str:
    """Resolução did:wba (estilo did:web): https://<domain>/.well-known/
    did-wba/<path...> (caminho vazio -> did-wba)."""
    base = f"https://{did.domain}/.well-known/did-wba"
    if did.path:
        base += "/" + "/".join(did.path)
    return base
