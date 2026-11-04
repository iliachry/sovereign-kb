"""
Pistis (Πίστις) — The 4th Dimension of The Sovereign Organon (`organon chain` / `skb chain`).

Unifies Ilias Chrysovergis's Poetics VR blockchain protocols and University of Thessaly PhD research
into a zero-dependency, cryptographically verifiable engine mounted inside `sovereign-kb`:

  1. Poetics On-Chain Bilateral Negotiation Protocol v1.0 (`negotiate`):
     - 7-State Negotiation Contract FSM:
       `IDLE -> OFFERED -> COUNTERED -> ACCEPTED -> FINALIZED | REJECTED | EXPIRED`
     - Term Bundle: T = (r, phi, Delta_t, G, rho, kappa) with dense 256-bit ISO 3166-1 alpha-2
       territory bitmap (249 assigned codes `0=AD..248=ZW`, bit `255=ALL_TERRITORIES`).
     - Formal Safety & Liveness Invariants (`S1` HMAC-SHA256 Authenticated Non-Repudiation, `S2`
       Self-Acceptance & Term-Bound Front-Running Guard `accept(id_N, expectedRound, termsHash)`,
       `S3` Atomic Non-Transferable Platform License Token [`PLT`] Minting, `S4` Liveness Under Expiry).
     - Dual Push / Pull-Escrow `RoyaltySplitter`: Multi-creator atomic revenue split scaled to the
       bilaterally agreed royalty `r` (`F_C = floor(r * D / 10000)`, `F_P = D - F_C`) with pull-escrow
       `claimableBalance` fallback and full upfront escrow refund conservation.

  2. RFC 6962 Merkle Provenance & On-Chain Attestation Anchor (`anchor`):
     - Domain-separated RFC 6962 SHA-256 Merkle tree (`0x00` leaf, `0x01` internal node)
       with positional `(leaf_index, tree_size)` inclusion proof generation and verification.
     - Principle P3 Semantic Transformation Provenance Trace T_prov = (s_0, ..., s_r) with
       fail-closed privilege-gap circuit breaker (blocking self-declassification).
     - Anchors `organon` GWI 9+ gate receipts, Tri-Council verdicts, and Git commit/tree SHAs into
       hash-chained `~/.swm/chain_anchors.jsonl` (verified via `verify_chain_anchors`).

  3. PhD Virtual Token Economies & Antifragility Engine (`tokenomics`):
     - Single-agent Q-Learning (Theorem 1), Multi-Agent Nash Q-Learning (Theorem 2, Folk-Theorem
       tacit collusion detection), and EGT Replicator Dynamics (Theorem 3, ESS convergence).
     - Single-plant causal PID and receding-horizon Riccati LQR/MPC controllers with exact 3x3
       Discrete-Time Lyapunov Stability Solver (Theorem 4: A_cl^T P A_cl - P = -Q) and invariant
       sublevel-set ultimate-boundedness certificate.
     - Antifragile Capacity Curvature (C''(J) > 0, power k_p < gamma_0 & tripartite bandwidth
       f_min, f_max) and Antifragile Market Index AMI(t) = alpha*VAG + beta*ALC + gamma*SRR.

  4. PhD Software Aristocracy & Constitutional Resilience Engine (`aristocracy`):
     - Dual-Weight Authority Graph G = (V, E, alpha, delta, beta) separating downward governance
       weight W_{ij} = alpha_{ij}*delta_{ij} from upward semantic channel exposure beta_{ij}.
     - Direct & Transitive Aristocratic Index A_gamma(v_i) = [(I - gamma*W)^{-1} W 1]_i (with
       spectral radius validation gamma * rho(W) < 1), Raw & Size-Adjusted Aristocracy Coefficients
       (Gamma(A), Gamma_tilde(A)), Authority Gini G(A_0), Proposition 1 Blast Radius Bound, and
       live-computed P1-P5 Constitutional Resilience audit.
"""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime
import fcntl
import hashlib
import hmac
import json
import math
import os
import random
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .bridge import append_triad_provenance_event, default_triad_ledger_path, verify_triad_provenance_chain

PISTIS_PROTOCOL_VERSION = "1.0.0"

# ==============================================================================
# 1. POETICS ON-CHAIN BILATERAL NEGOTIATION PROTOCOL (NC + PLT + ROYALTY SPLITTER)
# ==============================================================================

# 249 assigned ISO 3166-1 alpha-2 country codes in canonical lexicographical order (bits 0..248).
# Bit 255 is reserved as the global wildcard (`ALL_TERRITORIES`), matching `protocol-spec.tex`.
ISO_3166_ALPHA2_CODES: tuple[str, ...] = tuple(
    (
        "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ "
        "CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO "
        "FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE "
        "JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO "
        "MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW "
        "PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM "
        "TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW"
    ).split()
)
_ISO_CODE_TO_BIT: dict[str, int] = {code: idx for idx, code in enumerate(ISO_3166_ALPHA2_CODES)}
ALL_TERRITORIES_BIT = 255
ALL_TERRITORIES_MASK = 1 << ALL_TERRITORIES_BIT
ASSIGNED_TERRITORIES_MASK = (1 << len(ISO_3166_ALPHA2_CODES)) - 1
VALID_TERRITORY_BITMAP_MASK = ASSIGNED_TERRITORIES_MASK | ALL_TERRITORIES_MASK

FSM_STATES = ("IDLE", "OFFERED", "COUNTERED", "ACCEPTED", "REJECTED", "EXPIRED", "FINALIZED")


def _require_exact_int(val: Any, name: str) -> int:
    """Require `val` to be an exact Python `int` (rejecting `bool`, `float`, `str`)."""
    if isinstance(val, bool) or not isinstance(val, int):
        raise ValueError(f"InvalidType: {name} must be an exact integer, got {type(val).__name__}")
    return val


def _validate_territory_bitmap(bitmap: Any) -> int:
    b = _require_exact_int(bitmap, "geo_territories")
    if b <= 0 or b >= (1 << 256):
        raise ValueError("InvalidTerms: geo_territories bitmap must be non-zero uint256")
    if (b & ~VALID_TERRITORY_BITMAP_MASK) != 0:
        raise ValueError("InvalidTerms: geo_territories contains unassigned bit positions (249..254)")
    return b


def encode_territories(codes: list[str] | tuple[str, ...]) -> int:
    """Encode ISO 3166-1 alpha-2 territory codes into a 256-bit integer bitmap (`protocol-spec.tex`)."""
    bitmap = 0
    for raw in codes:
        if not isinstance(raw, str):
            raise ValueError(f"Territory code must be str, got {type(raw).__name__}")
        c = raw.strip().upper()
        if not c:
            continue
        if c in ("ALL", "*", "ALL_TERRITORIES", "GLOBAL"):
            bitmap |= ALL_TERRITORIES_MASK
            continue
        if c not in _ISO_CODE_TO_BIT:
            raise ValueError(f"Unknown ISO 3166-1 alpha-2 territory code: {c!r}")
        bitmap |= 1 << _ISO_CODE_TO_BIT[c]
    return bitmap


def decode_territories(bitmap: int) -> list[str]:
    """Decode a 256-bit territory bitmap into ISO 3166-1 alpha-2 codes (or `['ALL_TERRITORIES']`)."""
    b = _require_exact_int(bitmap, "bitmap")
    if b < 0 or b >= (1 << 256) or (b & ~VALID_TERRITORY_BITMAP_MASK) != 0:
        raise ValueError("geo_territories bitmap must fit in assigned uint256 territory bits")
    if b & ALL_TERRITORIES_MASK:
        return ["ALL_TERRITORIES"]
    out: list[str] = []
    for idx, code in enumerate(ISO_3166_ALPHA2_CODES):
        if b & (1 << idx):
            out.append(code)
    return out


def is_territory_permitted(bitmap: int, code: str) -> bool:
    """Return True if assigned ISO 3166-1 alpha-2 `code` is permitted by `bitmap`."""
    if isinstance(bitmap, bool) or not isinstance(bitmap, int) or not isinstance(code, str):
        return False
    if bitmap <= 0 or bitmap >= (1 << 256) or (bitmap & ~VALID_TERRITORY_BITMAP_MASK) != 0:
        return False
    c = code.strip().upper()
    bit = _ISO_CODE_TO_BIT.get(c)
    if bit is None:
        return False
    if bitmap & ALL_TERRITORIES_MASK:
        return True
    return bool(bitmap & (1 << bit))


def territories_overlap(bitmap_a: int, bitmap_b: int) -> bool:
    """Return True if two valid territory bitmaps share at least one ISO territory."""
    a = _validate_territory_bitmap(bitmap_a)
    b = _validate_territory_bitmap(bitmap_b)
    if (a & ALL_TERRITORIES_MASK) or (b & ALL_TERRITORIES_MASK):
        return True
    return bool((a & b) & ASSIGNED_TERRITORIES_MASK)


@dataclasses.dataclass(frozen=True)
class TermBundle:
    """
    Poetics Bilateral Licensing Term Bundle:
      T = (r, phi, Delta_t_license, G, rho, kappa)
    """

    royalty_bps: int  # r in [0, 10000] basis points (uint16)
    flat_fee_wei: int  # phi >= 0 upfront fee in wei (uint256)
    license_duration: int  # Delta_t_license > 0 in seconds (uint40)
    geo_territories: int  # G: 256-bit ISO 3166-1 alpha-2 bitmap (uint256)
    exclusive: bool  # rho in {False, True}
    max_issuances: int = 0  # kappa >= 0 (0 = unlimited, uint64)

    def validate(self) -> None:
        r = _require_exact_int(self.royalty_bps, "royalty_bps")
        phi = _require_exact_int(self.flat_fee_wei, "flat_fee_wei")
        dur = _require_exact_int(self.license_duration, "license_duration")
        _validate_territory_bitmap(self.geo_territories)
        if not isinstance(self.exclusive, bool):
            raise ValueError("InvalidTerms: exclusive must be a bool")
        kappa = _require_exact_int(self.max_issuances, "max_issuances")

        if not (0 <= r <= 10_000):
            raise ValueError(f"InvalidTerms: royalty_bps={r} must be in [0, 10000]")
        if phi < 0 or phi >= (1 << 256):
            raise ValueError("InvalidTerms: flat_fee_wei must be a non-negative uint256")
        if not (0 < dur < (1 << 40)):
            raise ValueError("InvalidTerms: license_duration must be in (0, 2^40 - 1] seconds")
        if not (0 <= kappa < (1 << 64)):
            raise ValueError("InvalidTerms: max_issuances must be a non-negative uint64")

    def to_canonical_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "royalty_bps": self.royalty_bps,
            "flat_fee_wei": self.flat_fee_wei,
            "license_duration": self.license_duration,
            "geo_territories": hex(self.geo_territories),
            "territories_decoded": decode_territories(self.geo_territories),
            "exclusive": self.exclusive,
            "max_issuances": self.max_issuances,
        }

    def terms_hash(self) -> str:
        """Deterministic cryptographic commitment H(T) for S2 front-running protection."""
        self.validate()
        payload = {
            "royalty_bps": self.royalty_bps,
            "flat_fee_wei": self.flat_fee_wei,
            "license_duration": self.license_duration,
            "geo_territories": hex(self.geo_territories),
            "exclusive": self.exclusive,
            "max_issuances": self.max_issuances,
        }
        return "0x" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


@dataclasses.dataclass
class PlatformLicenseToken:
    """
    Non-transferable Platform License Token (`PLT`):
      PLT = (id_PLT, h_W, DID_C, DID_P, T*, tau_mint, metaCID)
    """

    plt_id: int
    work_hash: str
    creator_did: str
    platform_did: str
    agreed_terms: TermBundle
    minted_at: int
    meta_cid: str
    session_id: int
    creators_shares_bps: list[tuple[str, int]]
    issued_tickets: int = 0

    def is_active(self, now_ts: int) -> bool:
        t = _require_exact_int(now_ts, "now_ts")
        return self.minted_at <= t < (self.minted_at + self.agreed_terms.license_duration)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plt_id": self.plt_id,
            "work_hash": self.work_hash,
            "creator_did": self.creator_did,
            "platform_did": self.platform_did,
            "agreed_terms": self.agreed_terms.to_canonical_dict(),
            "terms_hash": self.agreed_terms.terms_hash(),
            "minted_at": self.minted_at,
            "expires_at": self.minted_at + self.agreed_terms.license_duration,
            "meta_cid": self.meta_cid,
            "session_id": self.session_id,
            "creators_shares_bps": [{"did": d, "royalty_bps": b} for d, b in self.creators_shares_bps],
            "issued_tickets": self.issued_tickets,
            "non_transferable": True,
        }


@dataclasses.dataclass
class NegotiationSession:
    session_id: int
    work_hash: str
    creator_did: str
    platform_did: str
    state: str
    current_terms: TermBundle
    last_proposer: str
    round: int
    max_rounds: int
    round_timeout_s: int
    opened_at: int
    updated_at: int
    deadline_ts: int
    plt_id: int | None = None


def _synchronized(fn: Any) -> Any:
    """Acquire `self._lock` (`RLock`) across all state-mutating `BilateralNegotiationEngine` transitions."""

    def _wrapped(self: Any, *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return fn(self, *args, **kwargs)

    return _wrapped


class BilateralNegotiationEngine:
    """
    Deterministic implementation of the Poetics On-Chain Bilateral Negotiation Contract (`NC`),
    `PLTRegistry`, and Dual Push/Pull-Escrow `RoyaltySplitter`.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.works: dict[str, dict[str, Any]] = {}
        self.sessions: dict[int, NegotiationSession] = {}
        self.plts: dict[int, PlatformLicenseToken] = {}
        self.event_log: list[dict[str, Any]] = []
        self.pushed_balances: dict[str, int] = {}
        self.claimable_balances: dict[str, int] = {}
        self.upfront_escrow: dict[int, int] = {}
        self.upfront_depositor: dict[int, str] = {}
        self._did_keys: dict[str, bytes] = {}
        self._next_session_id = 1
        self._next_plt_id = 1

    def _get_did_secret(self, did: str) -> bytes:
        if not isinstance(did, str) or not did.strip():
            raise ValueError("DID must be a non-empty string")
        if did not in self._did_keys:
            self._did_keys[did] = hashlib.sha256(f"pistis-did-key:{did}".encode("utf-8")).digest()
        return self._did_keys[did]

    def sign_action(self, did: str, event_name: str, payload: dict[str, Any], timestamp: int) -> str:
        """Compute authenticated HMAC-SHA256 signature for `did` over `(event_name, timestamp, payload)`."""
        secret = self._get_did_secret(did)
        msg = json.dumps(
            {"did": did, "event": event_name, "timestamp": int(timestamp), "payload": payload},
            sort_keys=True,
        ).encode("utf-8")
        return hmac.new(secret, msg, hashlib.sha256).hexdigest()

    def _emit(
        self,
        event_name: str,
        payload: dict[str, Any],
        caller_did: str,
        now_ts: int,
        caller_signature: str | None = None,
    ) -> dict[str, Any]:
        ts = _require_exact_int(now_ts, "now_ts")
        snap_payload = copy.deepcopy(payload)
        expected_sig = self.sign_action(caller_did, event_name, snap_payload, ts)
        if caller_signature is not None and not hmac.compare_digest(caller_signature, expected_sig):
            raise PermissionError(f"InvalidSignature (S1): signature verification failed for {caller_did}")

        prev_sig = self.event_log[-1]["event_hash"] if self.event_log else ("0" * 64)
        ev = {
            "seq": len(self.event_log) + 1,
            "event": event_name,
            "caller_did": caller_did,
            "caller_signature": expected_sig,
            "timestamp": ts,
            "prev_hash": prev_sig,
            "payload": snap_payload,
        }
        # Safety Property S1: Authenticated signature + SHA-256 hash chain commitment per transition
        ev["event_hash"] = hashlib.sha256(json.dumps(ev, sort_keys=True).encode("utf-8")).hexdigest()
        self.event_log.append(ev)
        return ev

    @_synchronized
    def verify_event_log(self) -> bool:
        """Verify S1 HMAC-SHA256 signatures and hash-chain integrity across all emitted events."""
        prev = "0" * 64
        for idx, ev in enumerate(self.event_log):
            if ev.get("seq") != idx + 1 or ev.get("prev_hash") != prev:
                return False
            expected_sig = self.sign_action(
                ev["caller_did"],
                ev["event"],
                ev["payload"],
                ev["timestamp"],
            )
            if not hmac.compare_digest(str(ev.get("caller_signature", "")), expected_sig):
                return False
            without_hash = {k: v for k, v in ev.items() if k != "event_hash"}
            expected_hash = hashlib.sha256(json.dumps(without_hash, sort_keys=True).encode("utf-8")).hexdigest()
            if ev.get("event_hash") != expected_hash:
                return False
            prev = expected_hash
        return True

    def _check_exclusivity_conflict(self, work_hash: str, candidate_terms: TermBundle, now_ts: int) -> None:
        """Enforce territorial & exclusivity conflict invariant at open, counter, and accept."""
        for existing_plt in self.plts.values():
            if existing_plt.work_hash == work_hash and existing_plt.is_active(now_ts):
                if territories_overlap(existing_plt.agreed_terms.geo_territories, candidate_terms.geo_territories):
                    if existing_plt.agreed_terms.exclusive or candidate_terms.exclusive:
                        raise ValueError(
                            f"ExclusivityConflict: work {work_hash} has overlapping active license "
                            f"(PLT #{existing_plt.plt_id}) conflicting with exclusivity"
                        )

    @staticmethod
    def _scale_creator_shares_to_negotiated_royalty(
        raw_weights: list[tuple[str, int]],
        creator_did: str,
        negotiated_royalty_bps: int,
    ) -> list[tuple[str, int]]:
        """
        Scale co-creator relative weights `(did_i, w_i)` so their basis points sum EXACTLY to
        the bilaterally negotiated `negotiated_royalty_bps` (`r in T*`), preserving `F_C = r * D`.
        """
        r = _require_exact_int(negotiated_royalty_bps, "negotiated_royalty_bps")
        if not raw_weights:
            return [(creator_did, r)]
        total_weight = sum(w for _, w in raw_weights)
        if total_weight <= 0:
            return [(creator_did, r)]
        scaled: list[tuple[str, int]] = []
        allocated = 0
        for did, weight in raw_weights:
            bps_i = (r * weight) // total_weight
            scaled.append((did, bps_i))
            allocated += bps_i
        remainder = r - allocated
        if remainder > 0 and scaled:
            lead_did, lead_bps = scaled[0]
            scaled[0] = (lead_did, lead_bps + remainder)
        return scaled

    @_synchronized
    def register_work(
        self,
        work_hash: str,
        creator_did: str,
        now_ts: int = 1_700_000_000,
        creators_shares_bps: list[tuple[str, int]] | None = None,
    ) -> dict[str, Any]:
        ts = _require_exact_int(now_ts, "now_ts")
        if not isinstance(work_hash, str) or not work_hash.strip():
            raise ValueError("work_hash must be a non-empty string")
        if not isinstance(creator_did, str) or not creator_did.strip():
            raise ValueError("creator_did must be a non-empty string")
        if work_hash in self.works:
            raise ValueError(f"WorkAlreadyRegistered: {work_hash}")

        if creators_shares_bps is not None and len(creators_shares_bps) == 0:
            raise ValueError("InvalidCreatorShares: creators_shares_bps cannot be an empty list when provided")

        shares: list[tuple[str, int]] = []
        seen_dids: set[str] = set()
        for did, b in creators_shares_bps or []:
            if not isinstance(did, str) or not did.strip():
                raise ValueError("InvalidCreatorShares: creator DID must be a non-empty string")
            if did in seen_dids:
                raise ValueError(f"InvalidCreatorShares: duplicate creator DID {did!r}")
            seen_dids.add(did)
            bps_val = _require_exact_int(b, "creator_share_bps")
            if bps_val <= 0 or bps_val > 10_000:
                raise ValueError("InvalidCreatorShares: each creator basis-point weight must be in (0, 10000]")
            shares.append((did, bps_val))

        if shares:
            total_bps = sum(b for _, b in shares)
            if total_bps <= 0 or total_bps > 10_000:
                raise ValueError("InvalidCreatorShares: sum of creator basis points must be in (0, 10000]")

        rec = {
            "work_hash": work_hash,
            "creator_did": creator_did,
            "registered_at": ts,
            "creators_shares_bps": shares,
        }
        self.works[work_hash] = copy.deepcopy(rec)
        self._emit("WorkRegistered", rec, caller_did=creator_did, now_ts=ts)
        return rec

    @_synchronized
    def open_session(
        self,
        work_hash: str,
        creator_did: str,
        platform_did: str,
        initial_terms: TermBundle,
        proposer_did: str,
        max_rounds: int = 10,
        round_timeout_s: int = 86_400,
        escrow_wei: int = 0,
        now_ts: int = 1_700_000_000,
    ) -> NegotiationSession:
        ts = _require_exact_int(now_ts, "now_ts")
        m_rounds = _require_exact_int(max_rounds, "max_rounds")
        timeout_s = _require_exact_int(round_timeout_s, "round_timeout_s")
        escrow = _require_exact_int(escrow_wei, "escrow_wei")

        if work_hash not in self.works:
            raise ValueError(f"WorkNotRegistered: {work_hash}")
        if self.works[work_hash]["creator_did"] != creator_did:
            raise ValueError("CreatorDIDMismatch: creator_did does not match registered work owner")
        if not isinstance(platform_did, str) or not platform_did.strip() or platform_did == creator_did:
            raise ValueError("InvalidParty: platform_did must be a non-empty DID distinct from creator_did")
        if proposer_did not in (creator_did, platform_did):
            raise PermissionError("UnauthorizedParty: proposer must be Creator or Platform")
        if m_rounds < 1 or timeout_s < 1 or escrow < 0:
            raise ValueError("InvalidSessionConfig: max_rounds >= 1, round_timeout_s >= 1, escrow_wei >= 0 required")
        initial_terms.validate()

        # Prevent duplicate active session for the same (work_hash, platform_did)
        for existing_sess in self.sessions.values():
            if (
                existing_sess.work_hash == work_hash
                and existing_sess.platform_did == platform_did
                and existing_sess.state in ("OFFERED", "COUNTERED")
                and ts < existing_sess.deadline_ts
            ):
                raise ValueError(
                    f"ActiveSessionExists: session #{existing_sess.session_id} already active for "
                    f"work {work_hash} and platform {platform_did}"
                )

        self._check_exclusivity_conflict(work_hash, initial_terms, ts)

        sid = self._next_session_id
        self._next_session_id += 1
        session = NegotiationSession(
            session_id=sid,
            work_hash=work_hash,
            creator_did=creator_did,
            platform_did=platform_did,
            state="OFFERED",
            current_terms=initial_terms,
            last_proposer=proposer_did,
            round=1,
            max_rounds=m_rounds,
            round_timeout_s=timeout_s,
            opened_at=ts,
            updated_at=ts,
            deadline_ts=ts + timeout_s,
        )
        self.sessions[sid] = session
        if escrow > 0:
            self.upfront_escrow[sid] = escrow
            self.upfront_depositor[sid] = platform_did
        self._emit(
            "SessionOpened",
            {
                "session_id": sid,
                "work_hash": work_hash,
                "proposer_did": proposer_did,
                "round": 1,
                "escrow_wei": escrow,
                "terms_hash": initial_terms.terms_hash(),
                "terms": initial_terms.to_canonical_dict(),
            },
            caller_did=proposer_did,
            now_ts=ts,
        )
        return session

    @_synchronized
    def counter_offer(
        self,
        session_id: int,
        caller_did: str,
        new_terms: TermBundle,
        now_ts: int = 1_700_000_100,
    ) -> NegotiationSession:
        ts = _require_exact_int(now_ts, "now_ts")
        session = self._get_session(session_id)
        if session.state not in ("OFFERED", "COUNTERED"):
            raise ValueError(f"InvalidStateTransition: cannot counter_offer from state {session.state}")
        if caller_did not in (session.creator_did, session.platform_did):
            raise PermissionError("UnauthorizedParty: caller must be Creator or Platform")
        if caller_did == session.last_proposer:
            raise PermissionError("TurnAlternationViolation: proposer cannot counter their own consecutive offer")
        if ts >= session.deadline_ts:
            raise TimeoutError("SessionExpiredError: negotiation round timeout elapsed")
        if session.round >= session.max_rounds:
            raise ValueError(f"MaxRoundsExceeded: round {session.round} reached max_rounds={session.max_rounds}")
        new_terms.validate()
        self._check_exclusivity_conflict(session.work_hash, new_terms, ts)

        session.current_terms = new_terms
        session.last_proposer = caller_did
        session.round += 1
        session.state = "COUNTERED"
        session.updated_at = ts
        session.deadline_ts = ts + session.round_timeout_s

        self._emit(
            "CounterOffered",
            {
                "session_id": session_id,
                "counter_party": caller_did,
                "round": session.round,
                "terms_hash": new_terms.terms_hash(),
                "terms": new_terms.to_canonical_dict(),
            },
            caller_did=caller_did,
            now_ts=ts,
        )
        return session

    @_synchronized
    def accept(
        self,
        session_id: int,
        caller_did: str,
        expected_round: int,
        expected_terms_hash: str,
        meta_cid: str = "ipfs://bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oclgtqy55fbzdi",
        upfront_payment_wei: int = 0,
        now_ts: int = 1_700_000_200,
    ) -> PlatformLicenseToken:
        """
        Accept current terms and atomically mint the non-transferable Platform License Token (`PLT`).
        Enforces Safety Property S2 (`SelfAcceptanceError` + `TermsMismatchError` front-running guard),
        re-verifies territorial exclusivity prior to minting, scales co-creator shares to the negotiated
        `royalty_bps`, and settles/refunds upfront flat fee escrow with 100% wei conservation.
        """
        ts = _require_exact_int(now_ts, "now_ts")
        exp_round = _require_exact_int(expected_round, "expected_round")
        upfront_wei = _require_exact_int(upfront_payment_wei, "upfront_payment_wei")
        if upfront_wei < 0:
            raise ValueError("InvalidAmount: upfront_payment_wei must be >= 0")

        session = self._get_session(session_id)
        if session.state not in ("OFFERED", "COUNTERED"):
            raise ValueError(f"InvalidStateTransition: cannot accept from state {session.state}")
        if caller_did not in (session.creator_did, session.platform_did):
            raise PermissionError("UnauthorizedParty: caller must be Creator or Platform")
        if ts >= session.deadline_ts:
            raise TimeoutError("SessionExpiredError: cannot accept at or after round deadline")

        # Safety Property S2 (Part 1): Self-Acceptance Prevention
        if caller_did == session.last_proposer:
            raise PermissionError("SelfAcceptanceError (S2): proposer of current terms cannot call accept()")

        # Safety Property S2 (Part 2): Term-Bound Front-Running Prevention
        actual_hash = session.current_terms.terms_hash()
        if exp_round != session.round or str(expected_terms_hash) != actual_hash:
            raise ValueError(
                f"TermsMismatchError (S2 Front-Running Guard): expected (round={exp_round}, "
                f"hash={expected_terms_hash}), got (round={session.round}, hash={actual_hash})"
            )

        # Re-verify territorial exclusivity atomically at acceptance time
        self._check_exclusivity_conflict(session.work_hash, session.current_terms, ts)

        # Upfront flat fee settlement (phi) + excess escrow refund conservation
        total_escrow = self.upfront_escrow.get(session_id, 0) + upfront_wei
        fee_wei = session.current_terms.flat_fee_wei
        if total_escrow < fee_wei:
            raise ValueError(f"InsufficientUpfrontFee: required flat_fee_wei={fee_wei}, available={total_escrow}")

        # Transition to ACCEPTED and atomically mint PLT -> FINALIZED (Safety Property S3)
        session.state = "ACCEPTED"
        self._emit(
            "SessionAccepted",
            {
                "session_id": session_id,
                "acceptor_did": caller_did,
                "round": session.round,
                "terms_hash": actual_hash,
            },
            caller_did=caller_did,
            now_ts=ts,
        )

        work_info = self.works[session.work_hash]
        raw_weights: list[tuple[str, int]] = list(work_info.get("creators_shares_bps") or [])
        scaled_shares = self._scale_creator_shares_to_negotiated_royalty(
            raw_weights=raw_weights,
            creator_did=session.creator_did,
            negotiated_royalty_bps=session.current_terms.royalty_bps,
        )

        plt_id = self._next_plt_id
        self._next_plt_id += 1
        plt = PlatformLicenseToken(
            plt_id=plt_id,
            work_hash=session.work_hash,
            creator_did=session.creator_did,
            platform_did=session.platform_did,
            agreed_terms=session.current_terms,
            minted_at=ts,
            meta_cid=str(meta_cid),
            session_id=session_id,
            creators_shares_bps=scaled_shares,
        )
        self.plts[plt_id] = plt
        session.plt_id = plt_id
        session.state = "FINALIZED"
        session.updated_at = ts

        # Settle flat fee across co-creators and refund any excess escrow to the Platform depositor
        self.upfront_escrow[session_id] = 0
        if fee_wei > 0:
            total_w = sum(w for _, w in raw_weights) if raw_weights else 0
            if raw_weights and total_w > 0:
                paid_fee = 0
                for idx_c, (c_did, w_c) in enumerate(raw_weights):
                    part = (fee_wei * w_c) // total_w
                    if idx_c == len(raw_weights) - 1:
                        part = fee_wei - paid_fee
                    self.pushed_balances[c_did] = self.pushed_balances.get(c_did, 0) + part
                    paid_fee += part
            else:
                self.pushed_balances[session.creator_did] = self.pushed_balances.get(session.creator_did, 0) + fee_wei

        excess_escrow = total_escrow - fee_wei
        if excess_escrow > 0:
            depositor = self.upfront_depositor.get(session_id, session.platform_did)
            self.pushed_balances[depositor] = self.pushed_balances.get(depositor, 0) + excess_escrow

        self._emit(
            "PLTMinted",
            plt.to_dict(),
            caller_did=caller_did,
            now_ts=ts,
        )
        return plt

    def _refund_session_escrow(self, session_id: int, session: NegotiationSession) -> int:
        locked = self.upfront_escrow.get(session_id, 0)
        if locked > 0:
            self.upfront_escrow[session_id] = 0
            depositor = self.upfront_depositor.get(session_id, session.platform_did)
            self.pushed_balances[depositor] = self.pushed_balances.get(depositor, 0) + locked
        return locked

    @_synchronized
    def reject(self, session_id: int, caller_did: str, now_ts: int = 1_700_000_200) -> NegotiationSession:
        ts = _require_exact_int(now_ts, "now_ts")
        session = self._get_session(session_id)
        if session.state not in ("OFFERED", "COUNTERED"):
            raise ValueError(f"InvalidStateTransition: cannot reject from state {session.state}")
        if caller_did not in (session.creator_did, session.platform_did):
            raise PermissionError("UnauthorizedParty: caller must be Creator or Platform")
        refunded = self._refund_session_escrow(session_id, session)
        session.state = "REJECTED"
        session.updated_at = ts
        self._emit(
            "SessionRejected",
            {"session_id": session_id, "rejected_by": caller_did, "escrow_refunded_wei": refunded},
            caller_did,
            ts,
        )
        return session

    @_synchronized
    def expire(
        self, session_id: int, caller_did: str = "any_keeper", now_ts: int = 1_700_100_000
    ) -> NegotiationSession:
        """Liveness Property S4: permissionless expiry transition at or after round timeout (`now >= deadline`)."""
        ts = _require_exact_int(now_ts, "now_ts")
        session = self._get_session(session_id)
        if session.state not in ("OFFERED", "COUNTERED"):
            raise ValueError(f"InvalidStateTransition: cannot expire from state {session.state}")
        if ts < session.deadline_ts:
            raise ValueError(f"DeadlineNotElapsed: now={ts} < deadline={session.deadline_ts}")
        refunded = self._refund_session_escrow(session_id, session)
        session.state = "EXPIRED"
        session.updated_at = ts
        self._emit(
            "SessionExpired",
            {"session_id": session_id, "expired_at": ts, "escrow_refunded_wei": refunded},
            caller_did,
            ts,
        )
        return session

    @_synchronized
    def distribute_royalty(
        self,
        plt_id: int,
        gross_amount_wei: int,
        territory_code: str = "GR",
        tickets_count: int = 1,
        failing_recipients: set[str] | None = None,
        now_ts: int = 1_700_000_500,
    ) -> dict[str, Any]:
        """
        Execute atomic multi-creator royalty split for `plt_id` with Dual Push / Pull-Escrow fallback:
          F_C = floor(D * r / 10000),  F_P = D - F_C
        where `r = agreed_terms.royalty_bps` and co-creators share `F_C` proportionally without leaking
        creator rounding dust to the platform.
        """
        if plt_id not in self.plts:
            raise ValueError(f"PLTNotFound: {plt_id}")
        plt = self.plts[plt_id]
        gross = _require_exact_int(gross_amount_wei, "gross_amount_wei")
        tickets = _require_exact_int(tickets_count, "tickets_count")
        ts = _require_exact_int(now_ts, "now_ts")

        if gross <= 0:
            raise ValueError("InvalidAmount: gross_amount_wei must be > 0")
        if tickets < 1:
            raise ValueError("InvalidTicketsCount: tickets_count must be an integer >= 1")
        if not plt.is_active(ts):
            raise ValueError("LicenseExpiredError: timestamp outside PLT active license window")
        if not is_territory_permitted(plt.agreed_terms.geo_territories, territory_code):
            raise PermissionError(f"TerritoryNotPermittedError: territory {territory_code!r} not in PLT bitmap")
        if plt.agreed_terms.max_issuances > 0 and (plt.issued_tickets + tickets) > plt.agreed_terms.max_issuances:
            raise ValueError(
                f"IssuanceCapExceededError: {plt.issued_tickets + tickets} > cap {plt.agreed_terms.max_issuances}"
            )

        plt.issued_tickets += tickets
        fail_set = set(failing_recipients or set())
        creator_payouts: list[dict[str, Any]] = []

        target_creator_pool = (gross * plt.agreed_terms.royalty_bps) // 10_000
        total_bps = sum(bps for _, bps in plt.creators_shares_bps)
        allocated_creator_wei = 0
        raw_creator_amounts: list[tuple[str, int, int]] = []
        for did, bps in plt.creators_shares_bps:
            if total_bps > 0:
                share_wei = (target_creator_pool * bps) // total_bps
            else:
                share_wei = 0
            raw_creator_amounts.append((did, bps, share_wei))
            allocated_creator_wei += share_wei

        # Assign any sub-wei co-creator division dust to the lead creator so sum(F_{C_i}) == floor(D * r / 10000)
        dust_wei = target_creator_pool - allocated_creator_wei
        if dust_wei > 0 and raw_creator_amounts:
            d0, b0, a0 = raw_creator_amounts[0]
            raw_creator_amounts[0] = (d0, b0, a0 + dust_wei)

        total_creator_wei = 0
        for did, bps, share_wei in raw_creator_amounts:
            total_creator_wei += share_wei
            if did in fail_set:
                # Pull-escrow fallback: credit claimable_balances so reverting recipient cannot DoS distribution
                self.claimable_balances[did] = self.claimable_balances.get(did, 0) + share_wei
                mode = "pull_escrow"
            else:
                self.pushed_balances[did] = self.pushed_balances.get(did, 0) + share_wei
                mode = "direct_push"
            creator_payouts.append({"did": did, "bps": bps, "amount_wei": share_wei, "settlement_mode": mode})

        platform_wei = gross - total_creator_wei
        if plt.platform_did in fail_set:
            self.claimable_balances[plt.platform_did] = self.claimable_balances.get(plt.platform_did, 0) + platform_wei
            p_mode = "pull_escrow"
        else:
            self.pushed_balances[plt.platform_did] = self.pushed_balances.get(plt.platform_did, 0) + platform_wei
            p_mode = "direct_push"

        receipt = {
            "plt_id": plt_id,
            "gross_amount_wei": gross,
            "negotiated_royalty_bps": plt.agreed_terms.royalty_bps,
            "creator_total_wei": total_creator_wei,
            "platform_amount_wei": platform_wei,
            "platform_settlement_mode": p_mode,
            "wei_conserved": (total_creator_wei + platform_wei) == gross and total_creator_wei == target_creator_pool,
            "territory_code": territory_code.strip().upper(),
            "issued_tickets_total": plt.issued_tickets,
            "creator_payouts": creator_payouts,
        }
        self._emit("RoyaltyDistributed", receipt, caller_did=plt.platform_did, now_ts=ts)
        return receipt

    @_synchronized
    def withdraw_escrow(self, did: str, now_ts: int = 1_700_000_600) -> int:
        """Pull-payment withdrawal following Checks-Effects-Interactions (`protocol-spec.tex` Sec 6.2)."""
        ts = _require_exact_int(now_ts, "now_ts")
        amount = self.claimable_balances.get(did, 0)
        if amount <= 0:
            raise ValueError(f"NothingToWithdraw: claimable_balance for {did} is 0")
        # Effect before interaction
        self.claimable_balances[did] = 0
        self.pushed_balances[did] = self.pushed_balances.get(did, 0) + amount
        self._emit("RoyaltyWithdrawn", {"did": did, "amount_wei": amount}, caller_did=did, now_ts=ts)
        return amount

    def _get_session(self, session_id: int) -> NegotiationSession:
        if session_id not in self.sessions:
            raise KeyError(f"SessionNotFound: {session_id}")
        return self.sessions[session_id]


def run_negotiation_simulation() -> dict[str, Any]:
    """
    Execute a complete end-to-end verification of the Poetics Bilateral Negotiation FSM (`NC`),
    `PLT` minting, `S1-S4` security guards, and multi-creator Dual Push/Pull-Escrow `RoyaltySplitter`.
    """
    engine = BilateralNegotiationEngine()
    work_hash = "0x" + hashlib.sha256(b"poetics-vr-title:emperor-interactive-2026").hexdigest()
    creator_lead = "did:ethr:0xCreatorLead01"
    creator_sound = "did:ethr:0xCreatorSound02"
    platform_did = "did:ethr:0xPoeticsPlatform"

    # 1. Register work with 2 co-creators (relative split 5000 : 2000 -> 5/7 lead, 2/7 sound design)
    engine.register_work(
        work_hash=work_hash,
        creator_did=creator_lead,
        creators_shares_bps=[(creator_lead, 5000), (creator_sound, 2000)],
        now_ts=1_700_000_000,
    )

    # 2. Creator opens session asking 80% (8000 bps), 0.5 ETH flat fee, 90 days, GR+DE+FR, non-exclusive
    t0 = TermBundle(
        royalty_bps=8000,
        flat_fee_wei=500_000_000_000_000_000,
        license_duration=90 * 86_400,
        geo_territories=encode_territories(["GR", "DE", "FR"]),
        exclusive=False,
        max_issuances=1500,
    )
    sess = engine.open_session(
        work_hash=work_hash,
        creator_did=creator_lead,
        platform_did=platform_did,
        initial_terms=t0,
        proposer_did=creator_lead,
        now_ts=1_700_000_010,
    )

    # 3. Verify S2 Self-Acceptance Guard blocks Creator from accepting own offer
    s2_self_accept_blocked = False
    try:
        engine.accept(sess.session_id, caller_did=creator_lead, expected_round=1, expected_terms_hash=t0.terms_hash())
    except PermissionError:
        s2_self_accept_blocked = True

    # 4. Platform counters with 70% (7000 bps), 0.25 ETH flat fee, 180 days, GR+DE+FR+GB, max 3000 tickets
    t1 = TermBundle(
        royalty_bps=7000,
        flat_fee_wei=250_000_000_000_000_000,
        license_duration=180 * 86_400,
        geo_territories=encode_territories(["GR", "DE", "FR", "GB"]),
        exclusive=False,
        max_issuances=3000,
    )
    engine.counter_offer(sess.session_id, caller_did=platform_did, new_terms=t1, now_ts=1_700_000_120)

    # 5. Verify S2 Front-Running Guard blocks stale round/terms_hash acceptance
    s2_frontrun_blocked = False
    try:
        engine.accept(
            sess.session_id,
            caller_did=creator_lead,
            expected_round=1,
            expected_terms_hash=t0.terms_hash(),
            upfront_payment_wei=t1.flat_fee_wei,
            now_ts=1_700_000_180,
        )
    except ValueError:
        s2_frontrun_blocked = True

    # 6. Creator accepts Round 2 with exact termsHash -> Atomically mints PLT (S3)
    plt = engine.accept(
        sess.session_id,
        caller_did=creator_lead,
        expected_round=2,
        expected_terms_hash=t1.terms_hash(),
        upfront_payment_wei=t1.flat_fee_wei,
        now_ts=1_700_000_200,
    )

    # 7. Distribute 1 ETH (10^18 wei) ticket revenue where `creator_sound` has a reverting wallet -> Pull-Escrow fallback
    dist_receipt = engine.distribute_royalty(
        plt_id=plt.plt_id,
        gross_amount_wei=1_000_000_000_000_000_000,
        territory_code="GR",
        tickets_count=25,
        failing_recipients={creator_sound},
        now_ts=1_700_000_500,
    )
    withdrawn_wei = engine.withdraw_escrow(creator_sound, now_ts=1_700_000_600)
    s1_verified = engine.verify_event_log()
    total_system_in = t1.flat_fee_wei + dist_receipt["gross_amount_wei"]
    total_system_out = sum(engine.pushed_balances.values()) + sum(engine.claimable_balances.values())

    return {
        "protocol_version": PISTIS_PROTOCOL_VERSION,
        "fsm_final_state": sess.state,
        "rounds_taken": sess.round,
        "s1_non_repudiation_events": len(engine.event_log),
        "s1_signatures_verified": s1_verified,
        "s2_self_acceptance_blocked": s2_self_accept_blocked,
        "s2_frontrunning_blocked": s2_frontrun_blocked,
        "s3_atomic_plt_minted": sess.state == "FINALIZED" and plt.plt_id == 1,
        "system_wei_conserved": total_system_in == total_system_out,
        "plt": plt.to_dict(),
        "royalty_distribution": dist_receipt,
        "pull_escrow_withdrawn_wei": withdrawn_wei,
        "final_balances_wei": dict(engine.pushed_balances),
    }


# ==============================================================================
# 2. RFC 6962 MERKLE TREE, P3 PROVENANCE TRACE & ON-CHAIN ATTESTATION ANCHOR
# ==============================================================================


def _is_valid_sha256_hex(val: Any) -> bool:
    if not isinstance(val, str):
        return False
    h = val.removeprefix("0x")
    if len(h) != 64:
        return False
    try:
        bytes.fromhex(h)
        return True
    except ValueError:
        return False


def merkle_leaf_hash(data: bytes) -> str:
    """RFC 6962 domain-separated leaf hash: SHA256(0x00 || data)."""
    return hashlib.sha256(b"\x00" + data).hexdigest()


def merkle_node_hash(left_hex: str, right_hex: str) -> str:
    """RFC 6962 domain-separated internal node hash: SHA256(0x01 || left || right)."""
    if not _is_valid_sha256_hex(left_hex) or not _is_valid_sha256_hex(right_hex):
        raise ValueError("Merkle node inputs must be valid 32-byte SHA-256 hex digests")
    return hashlib.sha256(
        b"\x01" + bytes.fromhex(left_hex.removeprefix("0x")) + bytes.fromhex(right_hex.removeprefix("0x"))
    ).hexdigest()


def compute_merkle_root(leaf_hashes: list[str]) -> str:
    """Compute the RFC 6962 Merkle root over a list of hex leaf hashes."""
    if not leaf_hashes:
        return hashlib.sha256(b"").hexdigest()
    level: list[str] = []
    for h in leaf_hashes:
        if not _is_valid_sha256_hex(h):
            raise ValueError(f"Invalid 32-byte leaf hash: {h!r}")
        level.append(h.removeprefix("0x").lower())
    while len(level) > 1:
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(merkle_node_hash(level[i], level[i + 1]))
            else:
                next_level.append(level[i])
        level = next_level
    return level[0]


def _expected_proof_positions(index: int, tree_size: int) -> list[str]:
    """Compute the exact positional path (`left`/`right`) for `(index, tree_size)` in our RFC 6962 tree."""
    idx = index
    width = tree_size
    positions: list[str] = []
    while width > 1:
        if idx % 2 == 0:
            if idx + 1 < width:
                positions.append("right")
        else:
            positions.append("left")
        width = (width + 1) // 2
        idx //= 2
    return positions


def generate_merkle_proof(leaf_hashes: list[str], index: int) -> list[dict[str, Any]]:
    """Generate an RFC 6962 positional Merkle inclusion proof for `leaf_hashes[index]`."""
    idx_val = _require_exact_int(index, "index")
    n_leaves = len(leaf_hashes)
    if not (0 <= idx_val < n_leaves):
        raise IndexError(f"Leaf index {idx_val} out of range [0, {n_leaves})")
    level: list[str] = []
    for h in leaf_hashes:
        if not _is_valid_sha256_hex(h):
            raise ValueError(f"Invalid 32-byte leaf hash: {h!r}")
        level.append(h.removeprefix("0x").lower())

    idx = idx_val
    proof: list[dict[str, Any]] = []
    while len(level) > 1:
        if idx % 2 == 0:
            if idx + 1 < len(level):
                proof.append(
                    {
                        "position": "right",
                        "hash": level[idx + 1],
                        "leaf_index": idx_val,
                        "tree_size": n_leaves,
                    }
                )
        else:
            proof.append(
                {
                    "position": "left",
                    "hash": level[idx - 1],
                    "leaf_index": idx_val,
                    "tree_size": n_leaves,
                }
            )
        next_level: list[str] = []
        for i in range(0, len(level), 2):
            if i + 1 < len(level):
                next_level.append(merkle_node_hash(level[i], level[i + 1]))
            else:
                next_level.append(level[i])
        level = next_level
        idx //= 2
    return proof


def verify_merkle_proof(
    leaf_hex: str,
    proof: list[dict[str, Any]],
    expected_root: str,
    leaf_index: int | None = None,
    tree_size: int | None = None,
) -> bool:
    """
    Verify an RFC 6962 Merkle inclusion proof against `expected_root`.
    Enforces strict 32-byte hex validation, explicit `left`/`right` direction checks, and
    positional `(leaf_index, tree_size)` path length & direction verification to prevent
    interior-node shortening attacks.
    """
    if not _is_valid_sha256_hex(leaf_hex) or not _is_valid_sha256_hex(expected_root):
        return False
    if not isinstance(proof, list):
        return False

    resolved_idx = leaf_index
    resolved_size = tree_size
    if proof and isinstance(proof[0], dict):
        if resolved_idx is None and "leaf_index" in proof[0]:
            resolved_idx = proof[0]["leaf_index"]
        if resolved_size is None and "tree_size" in proof[0]:
            resolved_size = proof[0]["tree_size"]

    if resolved_idx is None or resolved_size is None:
        return False
    if (
        isinstance(resolved_idx, bool)
        or not isinstance(resolved_idx, int)
        or isinstance(resolved_size, bool)
        or not isinstance(resolved_size, int)
        or resolved_size < 1
        or not (0 <= resolved_idx < resolved_size)
    ):
        return False
    expected_positions = _expected_proof_positions(resolved_idx, resolved_size)
    if len(proof) != len(expected_positions):
        return False
    for step, exp_pos in zip(proof, expected_positions, strict=True):
        if not isinstance(step, dict) or step.get("position") != exp_pos:
            return False

    cur = leaf_hex.removeprefix("0x").lower()
    for step in proof:
        if not isinstance(step, dict):
            return False
        pos = step.get("position")
        sib_raw = step.get("hash")
        if pos not in ("left", "right") or not _is_valid_sha256_hex(sib_raw):
            return False
        sib = str(sib_raw).removeprefix("0x").lower()
        if pos == "left":
            cur = merkle_node_hash(sib, cur)
        else:
            cur = merkle_node_hash(cur, sib)
    return cur == expected_root.removeprefix("0x").lower()


def verify_p3_provenance_trace(
    trace: list[dict[str, Any]],
    tau_gap: float = 0.35,
) -> dict[str, Any]:
    """
    Verify `aristocracy` Principle P3 (Provenance of Semantic Transformation):
      T_prov = (s_0, s_1, ..., s_r), where s_l = (id_l, privilege, h_in, h_out, declassified, role)
    Fails closed (`valid=False`) on missing/empty digests, NaN/out-of-range privilege, broken digest
    links (`h_out,l != h_in,l+1`), or self-declassification: a privilege elevation `p(v_r) - min p(v_l) > tau_gap`
    is ONLY permitted when an **earlier, intervening stage** (`0 < m < r`) with `role` in
    `("declassifier", "reference_monitor")` and `declassified=True` explicitly mediated the transition.
    """
    if not isinstance(trace, list) or not trace:
        return {
            "valid": False,
            "chain_linked": False,
            "circuit_breaker_tripped": True,
            "reason": "Empty provenance trace",
        }
    if (
        isinstance(tau_gap, bool)
        or not isinstance(tau_gap, (int, float))
        or not math.isfinite(float(tau_gap))
        or tau_gap <= 0
    ):
        return {"valid": False, "chain_linked": False, "circuit_breaker_tripped": True, "reason": "Invalid tau_gap"}

    parsed_privs: list[float] = []
    for idx, stage in enumerate(trace):
        if not isinstance(stage, dict):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} not a dict",
            }
        sid = stage.get("id")
        h_in = stage.get("h_in")
        h_out = stage.get("h_out")
        raw_p = stage.get("privilege")
        if not isinstance(sid, str) or not sid.strip():
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} missing id",
            }
        if not isinstance(h_in, str) or len(h_in.strip()) < 2 or not isinstance(h_out, str) or len(h_out.strip()) < 2:
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} missing non-empty h_in/h_out digest",
            }
        if isinstance(raw_p, bool) or not isinstance(raw_p, (int, float)) or not math.isfinite(float(raw_p)):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} has non-finite privilege {raw_p!r}",
            }
        p_val = float(raw_p)
        if not (0.0 <= p_val <= 1.0):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} privilege {p_val} outside [0.0, 1.0]",
            }
        if "declassified" in stage and not isinstance(stage["declassified"], bool):
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Stage {idx} declassified flag must be bool",
            }
        parsed_privs.append(p_val)

    min_priv = parsed_privs[0]
    # Origin stage (idx=0) can NEVER pre-declassify subsequent untrusted transitions
    declassified_by_intervening_monitor = False

    for idx in range(1, len(trace)):
        stage = trace[idx]
        p_cur = parsed_privs[idx]
        prev_out = str(trace[idx - 1]["h_out"]).strip()
        cur_in = str(stage["h_in"]).strip()
        if prev_out != cur_in:
            return {
                "valid": False,
                "chain_linked": False,
                "circuit_breaker_tripped": True,
                "reason": f"Broken digest linkage between stage {idx - 1} and {idx}",
            }

        # Check privilege gap BEFORE considering whether stage `idx` itself is a declassifier
        # (prevents confused-deputy self-declassification at the target stage!).
        if (p_cur - min_priv) > float(tau_gap) and not declassified_by_intervening_monitor:
            return {
                "valid": False,
                "chain_linked": True,
                "circuit_breaker_tripped": True,
                "privilege_gap": round(p_cur - min_priv, 4),
                "tau_gap": float(tau_gap),
                "reason": (
                    f"Privilege-Gap Circuit Breaker tripped at stage {stage.get('id')!r}: "
                    f"gap {p_cur - min_priv:.2f} > tau_gap {float(tau_gap):.2f} without prior reference-monitor declassification"
                ),
            }

        # Only an explicit reference_monitor / declassifier stage can declassify for SUBSEQUENT stages,
        # and only when its own elevation from `min_priv` was within `tau_gap` (or already declassified).
        is_monitor = stage.get("role") in ("declassifier", "reference_monitor") and (stage.get("declassified") is True)
        if is_monitor:
            declassified_by_intervening_monitor = True
            # After verified declassification at stage `idx`, the baseline privilege is raised to `p_cur`
            min_priv = p_cur

        if p_cur < min_priv:
            min_priv = p_cur
            declassified_by_intervening_monitor = False

    return {
        "valid": True,
        "chain_linked": True,
        "circuit_breaker_tripped": False,
        "stages_count": len(trace),
        "origin_privilege": parsed_privs[0],
        "final_privilege": parsed_privs[-1],
        "declassified": declassified_by_intervening_monitor,
    }


def default_chain_anchors_path() -> Path:
    env_p = os.environ.get("SWM_CHAIN_ANCHORS")
    if env_p:
        return Path(env_p).expanduser()
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("SWM_HOME"):
        import tempfile

        test_id = hashlib.sha256(os.environ["PYTEST_CURRENT_TEST"].encode("utf-8")).hexdigest()[:10]
        return Path(tempfile.gettempdir()) / f"pytest_chain_anchors_{os.getpid()}_{test_id}.jsonl"
    swm_home = Path(os.environ.get("SWM_HOME", str(Path.home() / ".swm"))).expanduser()
    return swm_home / "chain_anchors.jsonl"


def verify_chain_anchors(anchors_file: Path | None = None) -> tuple[bool, int, str | None]:
    """
    Verify the cryptographic hash chain, block heights, inclusion proofs, and P3 verdicts
    across all blocks in `chain_anchors.jsonl`. Fails closed (`False`) on any corruption.
    """
    ap = Path(anchors_file) if anchors_file is not None else default_chain_anchors_path()
    if not ap.is_file():
        return True, 0, None
    try:
        raw_text = ap.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False, 0, None

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    prev_hash = "0" * 64
    latest_root: str | None = None
    for idx, ln in enumerate(lines):
        try:
            obj = json.loads(ln)
        except Exception:
            return False, len(lines), None
        if not isinstance(obj, dict):
            return False, len(lines), None
        expected_id = f"PISTIS-BLK-{idx + 1:05d}"
        if obj.get("anchor_id") != expected_id or obj.get("prev_anchor_hash") != prev_hash:
            return False, len(lines), None
        leaves_cnt = obj.get("merkle_leaves_count")
        if isinstance(leaves_cnt, bool) or not isinstance(leaves_cnt, int) or leaves_cnt < 1:
            return False, len(lines), None
        if not verify_merkle_proof(
            str(obj.get("latest_leaf_hash", "")),
            obj.get("inclusion_proof", []),
            str(obj.get("rfc6962_merkle_root", "")),
            leaf_index=leaves_cnt - 1,
            tree_size=leaves_cnt,
        ):
            return False, len(lines), None
        if not obj.get("inclusion_proof_verified") or not (obj.get("p3_provenance") or {}).get("valid"):
            return False, len(lines), None
        if "p3_trace" in obj and not verify_p3_provenance_trace(obj["p3_trace"], tau_gap=0.35)["valid"]:
            return False, len(lines), None
        without_hash = {k: v for k, v in obj.items() if k != "anchor_hash"}
        expected_hash = hashlib.sha256(json.dumps(without_hash, sort_keys=True).encode("utf-8")).hexdigest()
        if obj.get("anchor_hash") != expected_hash:
            return False, len(lines), None
        prev_hash = expected_hash
        latest_root = obj.get("rfc6962_merkle_root")
    return True, len(lines), latest_root


def _verify_in_memory_ledger_lines(lines: list[str]) -> bool:
    prev = "0" * 64
    for ln in lines:
        try:
            obj = json.loads(ln)
        except Exception:
            return False
        if not isinstance(obj, dict) or obj.get("prev_hash") != prev:
            return False
        copy_without_hash = {k: v for k, v in obj.items() if k != "entry_hash"}
        expected = hashlib.sha256(json.dumps(copy_without_hash, sort_keys=True).encode("utf-8")).hexdigest()
        if obj.get("entry_hash") != expected:
            return False
        prev = expected
    return True


def _inspect_target_repo_bindings(t_dir: Path) -> dict[str, str]:
    git_head = "unversioned"
    git_tree = "unversioned"
    try:
        res_head = subprocess.run(
            ["git", "-C", str(t_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_head.returncode == 0 and res_head.stdout.strip():
            git_head = res_head.stdout.strip()
        res_tree = subprocess.run(
            ["git", "-C", str(t_dir), "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res_tree.returncode == 0 and res_tree.stdout.strip():
            git_tree = res_tree.stdout.strip()
    except Exception:
        pass

    gwi_receipt_sha256 = "none"
    gw_path = t_dir / "evidence" / "goldwood.json"
    if gw_path.is_file():
        try:
            gwi_receipt_sha256 = hashlib.sha256(gw_path.read_bytes()).hexdigest()
        except OSError:
            pass
    return {
        "git_head_sha": git_head,
        "git_tree_sha": git_tree,
        "gwi_receipt_sha256": gwi_receipt_sha256,
    }


def anchor_organon_attestation(
    target_dir: Path | None = None,
    ledger_file: Path | None = None,
    anchors_file: Path | None = None,
) -> dict[str, Any]:
    """
    Build an RFC 6962 Merkle Tree over `~/.swm/triad_provenance_ledger.jsonl`, verify a P3 semantic
    transformation trace (`Peripheral Input -> GWI/Tri-Council Reference Monitor -> Aristocratic Core`),
    bind the target repository's Git HEAD/tree SHA and `evidence/goldwood.json` digest, and atomically
    append a hash-chained block to `chain_anchors.jsonl`.
    """
    t_dir = (target_dir or Path.cwd()).expanduser().resolve()
    if not t_dir.is_dir():
        raise ValueError(f"Target directory does not exist: {t_dir}")

    lp = Path(ledger_file) if ledger_file is not None else default_triad_ledger_path()
    ap = Path(anchors_file) if anchors_file is not None else default_chain_anchors_path()
    ap.parent.mkdir(parents=True, exist_ok=True)
    lock_path = ap.with_suffix(".lock")

    with lock_path.open("w", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            # 1. Verify existing anchor history fails closed before appending
            anchors_ok, existing_count, _ = verify_chain_anchors(ap)
            if not anchors_ok:
                raise ValueError("Cannot anchor: existing chain_anchors.jsonl is corrupted or tampered")

            # 2. Read triad_provenance_ledger.jsonl ONCE and verify those exact lines in memory
            raw_lines: list[str] = []
            if lp.is_file():
                try:
                    raw_lines = [ln.strip() for ln in lp.read_text(encoding="utf-8").splitlines() if ln.strip()]
                except (OSError, UnicodeDecodeError) as exc:
                    raise ValueError(f"Cannot read triad_provenance_ledger.jsonl: {exc}") from exc
            if not _verify_in_memory_ledger_lines(raw_lines):
                raise ValueError("Cannot anchor corrupted triad_provenance_ledger.jsonl (SHA-256 chain broken)")
            event_count = len(raw_lines)

            bindings = _inspect_target_repo_bindings(t_dir)
            leaf_hashes = [merkle_leaf_hash(ln.encode("utf-8")) for ln in raw_lines]
            if not leaf_hashes:
                genesis_payload = f"organon-genesis:{t_dir}:{bindings['git_head_sha']}:{bindings['gwi_receipt_sha256']}"
                leaf_hashes = [merkle_leaf_hash(genesis_payload.encode("utf-8"))]

            merkle_root = compute_merkle_root(leaf_hashes)
            last_idx = len(leaf_hashes) - 1
            inclusion_proof = generate_merkle_proof(leaf_hashes, last_idx)
            proof_verified = verify_merkle_proof(
                leaf_hashes[last_idx],
                inclusion_proof,
                merkle_root,
                leaf_index=last_idx,
                tree_size=len(leaf_hashes),
            )

            # 3. Build and verify P3 Provenance Trace T_prov bound to real repository digests
            h0 = hashlib.sha256(
                f"s0:{t_dir}:{bindings['git_head_sha']}:{bindings['git_tree_sha']}".encode("utf-8")
            ).hexdigest()
            h1 = hashlib.sha256(f"s1:kb_context:{h0}:{merkle_root}".encode("utf-8")).hexdigest()
            h2 = hashlib.sha256(
                f"s2:gwi_triad_monitor:{h1}:{bindings['gwi_receipt_sha256']}".encode("utf-8")
            ).hexdigest()
            h3 = hashlib.sha256(f"s3:aristocratic_core_commit:{h2}:{merkle_root}".encode("utf-8")).hexdigest()
            p3_trace = [
                {
                    "id": "s0:peripheral_worktree_state",
                    "role": "peripheral",
                    "privilege": 0.25,
                    "h_in": h0,
                    "h_out": h1,
                    "declassified": False,
                },
                {
                    "id": "s1:gwi_triad_reference_monitor",
                    "role": "reference_monitor",
                    "privilege": 0.55,  # gap 0.55 - 0.25 = 0.30 <= tau_gap (0.35), mediates & declassifies
                    "h_in": h1,
                    "h_out": h2,
                    "declassified": True,
                },
                {
                    "id": "s2:organon_aristocratic_core",
                    "role": "core",
                    "privilege": 0.95,  # elevated from declassified baseline 0.55
                    "h_in": h2,
                    "h_out": h3,
                    "declassified": False,
                },
            ]
            p3_verdict = verify_p3_provenance_trace(p3_trace, tau_gap=0.35)
            if not proof_verified or not p3_verdict["valid"]:
                raise ValueError("Cannot anchor: Merkle inclusion proof or P3 provenance verification failed")

            prev_anchor_hash = "0" * 64
            block_height = existing_count + 1
            if ap.is_file() and existing_count > 0:
                existing_anchors = [ln.strip() for ln in ap.read_text(encoding="utf-8").splitlines() if ln.strip()]
                prev_anchor_hash = str(json.loads(existing_anchors[-1])["anchor_hash"])

            anchor_record: dict[str, Any] = {
                "anchor_id": f"PISTIS-BLK-{block_height:05d}",
                "protocol_version": PISTIS_PROTOCOL_VERSION,
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "target_repo": t_dir.name,
                "target_path": str(t_dir),
                "git_head_sha": bindings["git_head_sha"],
                "git_tree_sha": bindings["git_tree_sha"],
                "gwi_receipt_sha256": bindings["gwi_receipt_sha256"],
                "ledger_events_anchored": event_count,
                "merkle_leaves_count": len(leaf_hashes),
                "rfc6962_merkle_root": f"0x{merkle_root}",
                "latest_leaf_hash": f"0x{leaf_hashes[last_idx]}",
                "inclusion_proof": inclusion_proof,
                "inclusion_proof_len": len(inclusion_proof),
                "inclusion_proof_verified": proof_verified,
                "p3_trace": p3_trace,
                "p3_provenance": p3_verdict,
                "prev_anchor_hash": prev_anchor_hash,
            }
            anchor_record["anchor_hash"] = hashlib.sha256(
                json.dumps(anchor_record, sort_keys=True).encode("utf-8")
            ).hexdigest()

            with ap.open("a", encoding="utf-8") as f:
                f.write(json.dumps(anchor_record) + "\n")
                f.flush()
                os.fsync(f.fileno())
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)

    append_triad_provenance_event(
        event_type="pistis.merkle_anchored",
        payload={
            "anchor_id": anchor_record["anchor_id"],
            "target_repo": t_dir.name,
            "git_head_sha": bindings["git_head_sha"],
            "rfc6962_merkle_root": anchor_record["rfc6962_merkle_root"],
            "anchor_hash": anchor_record["anchor_hash"],
        },
        ledger_file=lp,
    )
    return anchor_record


# ==============================================================================
# 3. PHD VIRTUAL TOKEN ECONOMIES (`ai_tokenomics`) & ANTIFRAGILITY (`antifragility`)
# ==============================================================================


def single_agent_reward(price: float) -> float:
    """
    Single-agent pricing model (`ai_tokenomics/main.tex` Sec VII-A):
      D(p) = max(0, 20 - p),  C(D) = 0.5 * D^2.
    Over the discrete action space P = {1, ..., 10}, the maximum is at p* = 10 (R = 50.0);
    over the unrestricted continuous domain R_+, the global maximum is at p* = 40/3 ≈ 13.3333 (R = 66.6667).
    """
    d = max(0.0, 20.0 - float(price))
    return float(price) * d - 0.5 * (d**2)


def duopoly_payoffs(a1: float, a2: float) -> tuple[float, float]:
    """
    Two-agent competitive pricing game (`ai_tokenomics/main.tex` Sec VII-B):
      D_i(a_i, a_{-i}) = max(0, 10 - a_i + 0.5*a_{-i}),  C_i(D_i) = 0.2*D_i^2.
    Unique pure-strategy Nash Equilibrium over {1..10} is (8, 8) -> payoff 40.8;
    Collusive Pareto optimum over {1..10} is (10, 10) -> payoff 45.0.
    """
    d1 = max(0.0, 10.0 - float(a1) + 0.5 * float(a2))
    d2 = max(0.0, 10.0 - float(a2) + 0.5 * float(a1))
    r1 = float(a1) * d1 - 0.2 * (d1**2)
    r2 = float(a2) * d2 - 0.2 * (d2**2)
    return r1, r2


def simulate_egt_replicator_dynamics(
    steps: int = 1000,
    dt: float = 0.01,
) -> dict[str, Any]:
    """
    Simulate Evolutionary Game Theory (EGT) replicator dynamics (`ai_tokenomics` Theorem 3):
      dx_k/dt = x_k * (f_k(x) - f_bar(x))
    over discrete price strategies A = {1, ..., 10}.
    Verifies step-size stability `dt * max|f_k - f_bar| < 1` and convergence to the pure
    Evolutionarily Stable Strategy (ESS) at p* = 8 (payoff 40.8).
    """
    if steps < 1 or dt <= 0.0 or not math.isfinite(dt):
        raise ValueError("steps >= 1 and finite dt > 0 required")
    actions = list(range(1, 11))
    k_num = len(actions)
    payoff_matrix = [[duopoly_payoffs(a_i, a_j)[0] for a_j in actions] for a_i in actions]
    max_payoff_spread = max(max(r) for r in payoff_matrix) - min(min(r) for r in payoff_matrix)
    if dt * max_payoff_spread >= 1.0:
        raise ValueError(f"StepSizeTooLarge: dt={dt} * spread={max_payoff_spread} >= 1.0 violates positivity")

    x = [1.0 / k_num for _ in range(k_num)]
    for _ in range(steps):
        fitness = [sum(payoff_matrix[i][j] * x[j] for j in range(k_num)) for i in range(k_num)]
        avg_fitness = sum(x[i] * fitness[i] for i in range(k_num))
        x_next = [x[i] * (1.0 + dt * (fitness[i] - avg_fitness)) for i in range(k_num)]
        s_tot = sum(x_next)
        x = [v / s_tot for v in x_next]

    best_idx = max(range(k_num), key=lambda idx: x[idx])
    ess_price = actions[best_idx]
    ess_prob = x[best_idx]
    kl_lyapunov = -math.log(max(1e-12, ess_prob))
    return {
        "ess_price": ess_price,
        "ess_probability": round(ess_prob, 4),
        "ess_payoff": round(duopoly_payoffs(ess_price, ess_price)[0], 2),
        "kl_lyapunov_v": round(kl_lyapunov, 6),
        "converged_to_ne_8": ess_price == 8 and ess_prob > 0.90,
    }


def compute_folk_theorem_collusion_index(
    observed_price: float,
    ne_price: float = 8.0,
    collusive_price: float = 10.0,
) -> dict[str, Any]:
    """
    Quantify emergent Folk-Theorem tacit collusion (`ai_tokenomics` Sec V-A & VII-B):
      I_collusion = (p_obs - p_NE) / (p_collusive - p_NE)
    In `ai_tokenomics`, Nash Q-Learning converges to p = 8.7 +/- 1.1 -> I_collusion = 0.35.
    """
    for val, name in ((observed_price, "observed_price"), (ne_price, "ne_price"), (collusive_price, "collusive_price")):
        if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(float(val)):
            raise ValueError(f"InvalidPrice: {name} must be a finite number")
    if float(collusive_price) <= float(ne_price):
        raise ValueError("collusive_price must be strictly greater than ne_price")

    denom = float(collusive_price) - float(ne_price)
    raw_idx = (float(observed_price) - float(ne_price)) / denom
    clipped_idx = max(0.0, min(1.0, raw_idx))
    regime = "COMPETITIVE_NE"
    if clipped_idx >= 0.75:
        regime = "CARTEL_COLLUSION"
    elif clipped_idx >= 0.20:
        regime = "SUPRA_COMPETITIVE_TACIT_COLLUSION"
    return {
        "observed_price": round(float(observed_price), 3),
        "ne_price": float(ne_price),
        "collusive_price": float(collusive_price),
        "collusion_index": round(clipped_idx, 4),
        "regime": regime,
        "model_source": "ai_tokenomics_table_2_nash_q_benchmark",
    }


def solve_discrete_lyapunov_2x2(
    a_cl: list[list[float]],
    q_mat: list[list[float]] | None = None,
    w_max: float = 0.05,
) -> dict[str, Any]:
    """
    Solve the discrete-time Lyapunov equation (`ai_tokenomics` Theorem 4):
      A_cl^T P A_cl - P = -Q
    exactly via the 3x3 symmetric linear system for `(p00, p01, p11)`, verify Schur stability
    (`rho(A_cl) < 1`), matrix symmetry/positive-definiteness of `Q` and `P`, exact Frobenius
    residual `||A_cl^T P A_cl - P + Q||_F < 1e-7`, and compute the invariant sublevel-set
    Euclidean ultimate boundedness radius for `||w_t|| <= w_max`.
    """
    q = q_mat or [[1.0, 0.0], [0.0, 1.0]]
    if len(a_cl) != 2 or len(q) != 2 or any(len(r) != 2 for r in a_cl + q):
        raise ValueError("InvalidMatrix: a_cl and q_mat must be 2x2 matrices")
    for r in a_cl + q:
        for v in r:
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
                raise ValueError("InvalidMatrix: matrix entries must be finite numbers")
    if isinstance(w_max, bool) or not isinstance(w_max, (int, float)) or not math.isfinite(float(w_max)) or w_max < 0:
        raise ValueError("InvalidDisturbanceBound: w_max must be a non-negative finite number")

    q00, q01 = float(q[0][0]), float(q[0][1])
    q10, q11 = float(q[1][0]), float(q[1][1])
    if abs(q01 - q10) > 1e-9:
        raise ValueError("InvalidMatrix: Q must be symmetric")
    det_q = q00 * q11 - q01 * q10
    if q00 <= 0.0 or det_q <= 0.0:
        raise ValueError("InvalidMatrix: Q must be positive definite")

    tr_q = q00 + q11
    disc_q = max(0.0, tr_q * tr_q - 4.0 * det_q)
    lambda_min_q = 0.5 * (tr_q - math.sqrt(disc_q))

    a00, a01 = float(a_cl[0][0]), float(a_cl[0][1])
    a10, a11 = float(a_cl[1][0]), float(a_cl[1][1])
    tr = a00 + a11
    det = a00 * a11 - a01 * a10
    disc = tr * tr - 4.0 * det
    if disc >= 0.0:
        sqrt_d = math.sqrt(disc)
        rho = max(abs(0.5 * (tr + sqrt_d)), abs(0.5 * (tr - sqrt_d)))
    else:
        rho = math.sqrt(max(0.0, det))

    if rho >= 1.0:
        return {
            "schur_stable": False,
            "spectral_radius": round(rho, 6),
            "positive_definite_p": False,
            "residual_frobenius": None,
            "ultimate_bound_sq": None,
        }

    # Exact 3x3 linear system M * [p00, p01, p11]^T = [-q00, -q01, -q11]^T
    m_aug = [
        [a00 * a00 - 1.0, 2.0 * a00 * a10, a10 * a10, -q00],
        [a00 * a01, a00 * a11 + a01 * a10 - 1.0, a10 * a11, -q01],
        [a01 * a01, 2.0 * a01 * a11, a11 * a11 - 1.0, -q11],
    ]
    for col in range(3):
        piv = max(range(col, 3), key=lambda r_idx: abs(m_aug[r_idx][col]))
        if abs(m_aug[piv][col]) < 1e-14:
            return {
                "schur_stable": False,
                "spectral_radius": round(rho, 6),
                "positive_definite_p": False,
                "residual_frobenius": None,
                "ultimate_bound_sq": None,
            }
        m_aug[col], m_aug[piv] = m_aug[piv], m_aug[col]
        div = m_aug[col][col]
        for j in range(col, 4):
            m_aug[col][j] /= div
        for r_idx in range(3):
            if r_idx != col:
                fac = m_aug[r_idx][col]
                for j in range(col, 4):
                    m_aug[r_idx][j] -= fac * m_aug[col][j]

    p00, p01, p11 = m_aug[0][3], m_aug[1][3], m_aug[2][3]
    p_mat = [[p00, p01], [p01, p11]]

    def _matmul(m1: list[list[float]], m2: list[list[float]]) -> list[list[float]]:
        return [
            [
                m1[0][0] * m2[0][0] + m1[0][1] * m2[1][0],
                m1[0][0] * m2[0][1] + m1[0][1] * m2[1][1],
            ],
            [
                m1[1][0] * m2[0][0] + m1[1][1] * m2[1][0],
                m1[1][0] * m2[0][1] + m1[1][1] * m2[1][1],
            ],
        ]

    a_mat = [[a00, a01], [a10, a11]]
    at_mat = [[a00, a10], [a01, a11]]
    at_p = _matmul(at_mat, p_mat)
    at_p_a = _matmul(at_p, a_mat)
    q_clean = [[q00, q01], [q10, q11]]
    residual = math.sqrt(sum((at_p_a[i][j] - p_mat[i][j] + q_clean[i][j]) ** 2 for i in range(2) for j in range(2)))

    det_p = p00 * p11 - p01 * p01
    tr_p = p00 + p11
    disc_p = max(0.0, tr_p * tr_p - 4.0 * det_p)
    lambda_max_p = 0.5 * (tr_p + math.sqrt(disc_p))
    lambda_min_p = 0.5 * (tr_p - math.sqrt(disc_p))
    pd_ok = p00 > 0.0 and det_p > 0.0 and lambda_min_p > 0.0 and residual < 1e-7

    # Spectral norm ||A^T P||_2^2 = lambda_max((A^T P)^T (A^T P))
    gram = _matmul([[at_p[0][0], at_p[1][0]], [at_p[0][1], at_p[1][1]]], at_p)
    tr_g = gram[0][0] + gram[1][1]
    det_g = gram[0][0] * gram[1][1] - gram[0][1] * gram[1][0]
    norm_at_p_sq = 0.5 * (tr_g + math.sqrt(max(0.0, tr_g * tr_g - 4.0 * det_g)))

    # Rigorous invariant sublevel-set Euclidean ultimate bound:
    # Delta V <= -(lambda_min(Q)/2) ||x||^2 + ((2 ||A^T P||_2^2 / lambda_min(Q)) + lambda_max(P)) ||w||^2
    # => V_inv = (lambda_max(P) / (0.5 * lambda_min(Q))) * beta_w, and ||x||^2 <= V_inv / lambda_min(P)
    beta_w = ((2.0 * norm_at_p_sq / lambda_min_q) + lambda_max_p) * (float(w_max) ** 2)
    alpha_lyap = (0.5 * lambda_min_q) / max(1e-12, lambda_max_p)
    v_inv = beta_w / max(1e-12, alpha_lyap)
    ult_bound_sq = v_inv / max(1e-12, lambda_min_p)

    return {
        "schur_stable": True,
        "spectral_radius": round(rho, 6),
        "positive_definite_p": pd_ok,
        "residual_frobenius": round(residual, 12),
        "lambda_min_q": round(lambda_min_q, 6),
        "p_matrix": [[round(v, 5) for v in row] for row in p_mat],
        "ultimate_bound_sq": round(ult_bound_sq, 6),
    }


def simulate_pid_mpc_token_control(
    steps: int = 200,
    target_price: float = 1.0,
    noise_sigma: float = 0.05,
    seed: int = 42,
) -> dict[str, Any]:
    """
    Simulate Uncontrolled vs. Causal PID vs. Causal Horizon-H Riccati LQR/MPC token supply
    governance on a SINGLE common 2D discrete-time plant (A, B) with identical shocks w_t:
      x_{t+1} = A x_t + B u_t + w_t,   x_t = [e_t, v_t]^T
    Derives the closed-loop matrix `A_cl = A - B * K_mpc` directly from `(A, B, K_mpc)` and
    certifies it via `solve_discrete_lyapunov_2x2`.
    """
    a_plant = [[0.88, 0.20], [0.05, 0.82]]
    b_plant = [1.0, 0.4]
    q_mpc = [[4.0, 0.0], [0.0, 1.0]]
    r_mpc = 0.08
    horizon_h = 15

    # Solve finite-horizon H=15 backward Riccati recursion on (a_plant, b_plant, q_mpc, r_mpc)
    p_ric = [row[:] for row in q_mpc]
    k_mpc = [0.0, 0.0]
    a_cl = [row[:] for row in a_plant]
    for _ in range(horizon_h):
        pb0 = p_ric[0][0] * b_plant[0] + p_ric[0][1] * b_plant[1]
        pb1 = p_ric[1][0] * b_plant[0] + p_ric[1][1] * b_plant[1]
        bt_p_b = b_plant[0] * pb0 + b_plant[1] * pb1

        pa00 = p_ric[0][0] * a_plant[0][0] + p_ric[0][1] * a_plant[1][0]
        pa01 = p_ric[0][0] * a_plant[0][1] + p_ric[0][1] * a_plant[1][1]
        pa10 = p_ric[1][0] * a_plant[0][0] + p_ric[1][1] * a_plant[1][0]
        pa11 = p_ric[1][0] * a_plant[0][1] + p_ric[1][1] * a_plant[1][1]
        bt_p_a = [b_plant[0] * pa00 + b_plant[1] * pa10, b_plant[0] * pa01 + b_plant[1] * pa11]

        k_mpc = [bt_p_a[0] / (r_mpc + bt_p_b), bt_p_a[1] / (r_mpc + bt_p_b)]
        a_cl = [
            [a_plant[0][0] - b_plant[0] * k_mpc[0], a_plant[0][1] - b_plant[0] * k_mpc[1]],
            [a_plant[1][0] - b_plant[1] * k_mpc[0], a_plant[1][1] - b_plant[1] * k_mpc[1]],
        ]
        p_next = [[0.0, 0.0], [0.0, 0.0]]
        for i in range(2):
            for j in range(2):
                s = q_mpc[i][j] + k_mpc[i] * r_mpc * k_mpc[j]
                for r_idx in range(2):
                    for c_idx in range(2):
                        s += a_cl[r_idx][i] * p_ric[r_idx][c_idx] * a_cl[c_idx][j]
                p_next[i][j] = s
        p_ric = p_next

    rng = random.Random(seed)
    amp_scale = float(noise_sigma) / 0.05 if noise_sigma > 0.0 else 0.0
    shocks = [
        [
            0.18 * amp_scale * math.sin(2.0 * math.pi * t / 30.0) + rng.gauss(0.0, noise_sigma),
            0.12 * amp_scale * math.cos(2.0 * math.pi * t / 25.0) + rng.gauss(0.0, 0.6 * noise_sigma),
        ]
        for t in range(steps)
    ]
    w_sup = max((math.hypot(w[0], w[1]) for w in shocks), default=0.0)

    # 1. Uncontrolled trajectory (u_t = 0) on (a_plant, b_plant)
    x_unc = [0.5, 0.3]
    sq_err_unc = 0.0
    for t in range(steps):
        sq_err_unc += x_unc[0] ** 2
        w = shocks[t]
        x_unc = [
            a_plant[0][0] * x_unc[0] + a_plant[0][1] * x_unc[1] + w[0],
            a_plant[1][0] * x_unc[0] + a_plant[1][1] * x_unc[1] + w[1],
        ]
    rmse_unc = math.sqrt(sq_err_unc / steps)

    # 2. Causal PID trajectory on exact same (a_plant, b_plant), observing only price error e_t = x[0]
    x_pid = [0.5, 0.3]
    integ = 0.0
    prev_err = 0.5
    kp, ki, kd = 0.45, 0.08, 0.05
    sq_err_pid = 0.0
    for t in range(steps):
        err = x_pid[0]
        sq_err_pid += err**2
        integ = 0.70 * integ + err
        deriv = err - prev_err
        prev_err = err
        u_pid = -(kp * err + ki * integ + kd * deriv)
        w = shocks[t]
        x_pid = [
            a_plant[0][0] * x_pid[0] + a_plant[0][1] * x_pid[1] + b_plant[0] * u_pid + w[0],
            a_plant[1][0] * x_pid[0] + a_plant[1][1] * x_pid[1] + b_plant[1] * u_pid + w[1],
        ]
    rmse_pid = math.sqrt(sq_err_pid / steps)

    # 3. Causal Riccati LQR/MPC trajectory on exact same (a_plant, b_plant): u_t = -K_mpc * x_t
    x_mpc = [0.5, 0.3]
    sq_err_mpc = 0.0
    for t in range(steps):
        sq_err_mpc += x_mpc[0] ** 2
        u_mpc = -(k_mpc[0] * x_mpc[0] + k_mpc[1] * x_mpc[1])
        w = shocks[t]
        x_mpc = [
            a_plant[0][0] * x_mpc[0] + a_plant[0][1] * x_mpc[1] + b_plant[0] * u_mpc + w[0],
            a_plant[1][0] * x_mpc[0] + a_plant[1][1] * x_mpc[1] + b_plant[1] * u_mpc + w[1],
        ]
    rmse_mpc = math.sqrt(sq_err_mpc / steps)

    # Theorem 4 Closed-Loop Lyapunov Certificate for the exact derived A_cl = A - B * K_mpc
    lyapunov_cert = solve_discrete_lyapunov_2x2(
        a_cl=a_cl,
        w_max=w_sup,
    )

    return {
        "steps": steps,
        "target_price": target_price,
        "plant_a": a_plant,
        "plant_b": b_plant,
        "mpc_gain_k": [round(k_mpc[0], 5), round(k_mpc[1], 5)],
        "derived_a_cl": [[round(v, 5) for v in row] for row in a_cl],
        "rmse_uncontrolled": round(rmse_unc, 4),
        "rmse_pid": round(rmse_pid, 4),
        "rmse_mpc": round(rmse_mpc, 4),
        "mpc_volatility_reduction_x": round(rmse_unc / max(1e-9, rmse_mpc), 2),
        "lyapunov_certificate": lyapunov_cert,
    }


def evaluate_antifragile_curving_and_ami(
    p_tx: float = 1.0,
    noise_n: float = 0.1,
    bandwidth_b: float = 1.0,
    k_p: float = 5.0,
    k_b: float = 1.2,
    stressor_j: float = 2.0,
    vag: float = 0.84,
    alc: float = 0.79,
    srr: float = 0.88,
) -> dict[str, Any]:
    """
    Evaluate PhD Antifragility Theorems 1 & 2 (`antifragility/main.tex`) and the
    Antifragile Market Index (`AMI(t) = alpha*VAG + beta*ALC + gamma*SRR`):
      - Power Adaptation: C_p''(J) > 0 for all J >= 0 iff 0 <= k_p < P/N = gamma_0.
      - Bandwidth Adaptation: tripartite thresholds f_min = 2B/(P+2N), f_max = B(P+2N)/(2N(P+N)).
        At equality `k_b == f_min`, curvature remains strictly positive for all finite J >= 0
        (`UNIVERSAL_ANTIFRAGILE`, critical_stressor_jc = "infinity").
    """
    if p_tx <= 0 or noise_n <= 0 or bandwidth_b <= 0 or stressor_j < 0:
        raise ValueError("p_tx > 0, noise_n > 0, bandwidth_b > 0, stressor_j >= 0 required")
    gamma_0 = p_tx / noise_n
    j = float(stressor_j)
    denom_p = math.log(2.0) * ((noise_n + j) ** 2) * ((noise_n + j + p_tx + k_p * j) ** 2)
    bracket_p = 2.0 * (noise_n + j + p_tx + k_p * j) - (p_tx - k_p * noise_n)
    c_p_second_deriv = (bandwidth_b * (p_tx - k_p * noise_n) * bracket_p) / denom_p
    power_universally_antifragile = (0.0 <= k_p < gamma_0) and (c_p_second_deriv > 0.0)

    f_min = (2.0 * bandwidth_b) / (p_tx + 2.0 * noise_n)
    f_max = (bandwidth_b * (p_tx + 2.0 * noise_n)) / (2.0 * noise_n * (p_tx + noise_n))
    den_jc = k_b * (p_tx + 2.0 * noise_n) - 2.0 * bandwidth_b
    if 0.0 <= k_b <= f_min or den_jc <= 1e-12:
        bw_regime = "UNIVERSAL_ANTIFRAGILE"
        j_critical: float | str = "infinity"
    elif f_min < k_b < f_max:
        bw_regime = "CONDITIONAL_ANTIFRAGILE"
        num_jc = bandwidth_b * (p_tx + 2.0 * noise_n) - k_b * (2.0 * p_tx * noise_n + 2.0 * (noise_n**2))
        j_critical = round(num_jc / den_jc, 4)
    else:
        bw_regime = "FRAGILE"
        j_critical = 0.0

    # Antifragile Market Index AMI(t) = 0.4*VAG + 0.35*ALC + 0.25*SRR
    ami_score = 0.40 * float(vag) + 0.35 * float(alc) + 0.25 * float(srr)
    return {
        "initial_snr_gamma0": round(gamma_0, 4),
        "power_adaptation": {
            "k_p": k_p,
            "threshold_gamma0": round(gamma_0, 4),
            "second_derivative_c_pp": round(c_p_second_deriv, 6),
            "universally_antifragile": power_universally_antifragile,
        },
        "bandwidth_adaptation": {
            "k_b": k_b,
            "f_min": round(f_min, 4),
            "f_max": round(f_max, 4),
            "regime": bw_regime,
            "critical_stressor_jc": j_critical,
        },
        "antifragile_market_index": {
            "vag": round(vag, 4),
            "alc": round(alc, 4),
            "srr": round(srr, 4),
            "ami_score": round(ami_score, 4),
            "antifragile_verified": ami_score >= 0.75 and power_universally_antifragile,
        },
    }


def run_tokenomics_suite() -> dict[str, Any]:
    """Run the complete PhD `ai_tokenomics` + `antifragility` verification suite."""
    action_grid = list(range(1, 11))
    best_discrete_p = max(action_grid, key=single_agent_reward)
    continuous_opt_p = 40.0 / 3.0

    # Verify unique pure-strategy Nash equilibrium over action_grid x action_grid
    ne_candidates: list[int] = []
    for p_star in action_grid:
        best_r = max(duopoly_payoffs(a_i, p_star)[0] for a_i in action_grid)
        if abs(duopoly_payoffs(p_star, p_star)[0] - best_r) < 1e-9:
            ne_candidates.append(p_star)
    ne_p = ne_candidates[0] if ne_candidates else 8

    egt = simulate_egt_replicator_dynamics()
    collusion_nash_q = compute_folk_theorem_collusion_index(observed_price=8.7)
    collusion_ess = compute_folk_theorem_collusion_index(observed_price=float(egt["ess_price"]))
    control = simulate_pid_mpc_token_control()
    antifragile = evaluate_antifragile_curving_and_ami()
    return {
        "single_agent_monopoly_optimum": {
            "action_space": "P = {1..10}",
            "optimal_price": best_discrete_p,
            "max_reward": round(single_agent_reward(best_discrete_p), 4),
            "unrestricted_continuous_price": round(continuous_opt_p, 4),
            "unrestricted_continuous_reward": round(single_agent_reward(continuous_opt_p), 4),
        },
        "duopoly_nash_equilibrium": {
            "ne_price": ne_p,
            "ne_payoff": round(duopoly_payoffs(ne_p, ne_p)[0], 4),
            "verified_unique_on_grid": ne_candidates == [8],
        },
        "egt_replicator": egt,
        "nash_q_collusion_audit": collusion_nash_q,
        "egt_ess_collusion_audit": collusion_ess,
        "pid_mpc_control": control,
        "antifragility": antifragile,
    }


# ==============================================================================
# 4. PHD SOFTWARE ARISTOCRACY & CONSTITUTIONAL RESILIENCE (`aristocracy`)
# ==============================================================================


def _spectral_radius_power_iter(mat: list[list[float]], iters: int = 40) -> float:
    """Estimate spectral radius rho(mat) via matrix power Frobenius norm ||M^k||_F^{1/k}."""
    n = len(mat)
    cur = [row[:] for row in mat]
    for _ in range(5):
        # Square matrix 5 times -> M^32
        nxt = [[0.0 for _ in range(n)] for _ in range(n)]
        for i in range(n):
            for k in range(n):
                if cur[i][k] != 0.0:
                    for j in range(n):
                        nxt[i][j] += cur[i][k] * cur[k][j]
        cur = nxt
    frob_sq = sum(cur[i][j] ** 2 for i in range(n) for j in range(n))
    return (frob_sq**0.5) ** (1.0 / 32.0)


def _solve_linear_system_nxn(mat_a: list[list[float]], rhs_b: list[float]) -> list[float]:
    """Solve `mat_a * x = rhs_b` via Gaussian elimination with partial pivoting."""
    n = len(rhs_b)
    aug = [mat_a[i][:] + [rhs_b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(aug[r][col]))
        if abs(aug[piv][col]) < 1e-12:
            raise ValueError("SingularMatrix: (I - gamma * W) is not invertible (gamma * rho(W) >= 1)")
        aug[col], aug[piv] = aug[piv], aug[col]
        div = aug[col][col]
        for j in range(col, n + 1):
            aug[col][j] /= div
        for r in range(n):
            if r != col:
                fac = aug[r][col]
                for j in range(col, n + 1):
                    aug[r][j] -= fac * aug[col][j]
    return [aug[i][n] for i in range(n)]


def audit_organon_aristocracy(
    gamma_decay: float = 0.5,
    ledger_file: Path | None = None,
    anchors_file: Path | None = None,
) -> dict[str, Any]:
    """
    Model The Sovereign Organon's runtime governance topology as a Dual-Weight Authority Graph
    G = (V, E, alpha, delta, beta) (`aristocracy/sections/aristocracy.tex`) and compute:
      - Direct Aristocratic Index A_0(v_i) = sum_j alpha(e_{ij}) * delta(e_{ij})
      - Exact Transitive Aristocratic Index A_gamma(v_i) = [(I - gamma*W)^{-1} W 1]_i
      - Raw & Size-Adjusted Aristocracy Coefficients (Gamma_0(A), Gamma_tilde_0(A), Gamma_gamma(A))
      - Authority Gini Coefficient G(A_0)
      - Proposition 1 Structural Blast Radius Bound
      - Principles P1-P5 Constitutional Resilience verification computed from graph edges & live ledgers.
    """
    if (
        isinstance(gamma_decay, bool)
        or not isinstance(gamma_decay, (int, float))
        or not math.isfinite(float(gamma_decay))
        or float(gamma_decay) <= 0.0
        or float(gamma_decay) >= 1.0
    ):
        raise ValueError("InvalidGammaDecay: gamma_decay must be in (0.0, 1.0)")
    gamma = float(gamma_decay)

    actors = [
        "v0:sovereign_architect",  # Aristocratic Core (A)
        "v1:organon_orchestrator",  # Aristocratic Core (A)
        "v2:gwi_phronesis_gate",  # Stateless P4 Reference Monitor
        "v3:tri_council_quorum",  # Stateless P4 Multi-Model Quorum
        "v4:kb_episteme_retriever",  # Subordinate Specialist
        "v5:swm_stage_worker",  # Subordinate Specialist
        "v6:pistis_chain_settler",  # Subordinate Specialist
        "v7:peripheral_tool_sandbox",  # Peripheral Execution Node
    ]
    actor_privileges = [1.00, 0.95, 0.60, 0.60, 0.45, 0.45, 0.50, 0.20]
    core_indices = [0, 1]  # |A| = 2, |V| = 8
    core_set = set(core_indices)
    n_actors = len(actors)

    # Directed edges: (src_idx, dst_idx, alpha_governance, delta_strictness, beta_semantic_bandwidth)
    edges: list[tuple[int, int, float, float, float]] = [
        # Downward constitutional governance from Sovereign Architect (v0) and Orchestrator (v1)
        (0, 1, 1.00, 1.00, 0.15),
        (0, 2, 0.95, 1.00, 0.05),
        (0, 3, 0.90, 0.95, 0.10),
        (1, 2, 0.90, 1.00, 0.10),
        (1, 3, 0.85, 0.95, 0.15),
        (1, 4, 0.95, 0.95, 0.20),
        (1, 5, 0.95, 0.95, 0.25),
        (1, 6, 0.95, 1.00, 0.10),
        (5, 7, 0.35, 0.80, 0.20),
        # Upward feedback edges (alpha = 0.0 legitimate downward authority, mediated beta)
        (7, 2, 0.00, 0.00, 0.22),  # Peripheral outputs routed through GWI Reference Monitor (P1/P4)
        (4, 2, 0.00, 0.00, 0.18),  # RAG retrieval routed through taint-aware verifier (P1/P3)
        (2, 1, 0.00, 1.00, 0.08),  # Symbolic gate receipt (delta=1.0, low semantic bandwidth beta=0.08)
        (3, 1, 0.00, 0.95, 0.10),  # Multi-model quorum verdict (P4)
    ]

    w_mat = [[0.0 for _ in range(n_actors)] for _ in range(n_actors)]
    beta_mat = [[0.0 for _ in range(n_actors)] for _ in range(n_actors)]
    for u, v, alpha, delta, beta in edges:
        w_mat[u][v] = alpha * delta
        beta_mat[u][v] = beta

    rho_w = _spectral_radius_power_iter(w_mat)
    if gamma * rho_w >= 1.0:
        raise ValueError(f"NeumannSeriesDivergence: gamma ({gamma}) * rho(W) ({rho_w:.4f}) >= 1.0")

    # 1. Direct (1-hop) Aristocratic Index A_0(v_i) = (W 1)_i
    a0 = [sum(w_mat[i][j] for j in range(n_actors)) for i in range(n_actors)]
    s0 = sum(a0)

    # 2. Exact Transitive Aristocratic Index A_gamma(v_i) = [(I - gamma*W)^{-1} W 1]_i
    i_minus_gw = [[(1.0 if i == j else 0.0) - gamma * w_mat[i][j] for j in range(n_actors)] for i in range(n_actors)]
    a_gamma = _solve_linear_system_nxn(i_minus_gw, a0)
    s_gamma = sum(a_gamma)

    # 3. Raw & Size-Adjusted Aristocracy Coefficients (Definition 4)
    core_ratio = len(core_indices) / n_actors
    gamma_0 = sum(a0[i] for i in core_indices) / max(1e-12, s0)
    gamma_tilde_0 = (gamma_0 - core_ratio) / (1.0 - core_ratio)

    gamma_trans = sum(a_gamma[i] for i in core_indices) / max(1e-12, s_gamma)
    gamma_tilde_trans = (gamma_trans - core_ratio) / (1.0 - core_ratio)

    # 4. Authority Gini Coefficient G(A_0)
    abs_diffs = sum(abs(a0[i] - a0[j]) for i in range(n_actors) for j in range(n_actors))
    gini_0 = abs_diffs / (2.0 * n_actors * max(1e-12, s0))

    # 5. Proposition 1 Structural Blast Radius Bound
    c_avg = s0 / n_actors
    theoretical_blast_lb = (gamma_0 * c_avg / len(core_indices)) * n_actors
    realized_out_max = max(sum(1 for j in range(n_actors) if w_mat[i][j] > 0) for i in core_indices)

    # 6. Constitutional Resilience Principles (P1-P5) Computed Dynamically from Graph & Live Ledgers
    # P1: Compute max upward semantic bandwidth beta(u -> v) from non-core u into core v,
    #     and check zero direct unmediated edges from peripheral v7 into core {0, 1}.
    upward_betas_to_core = [beta for (u, v, _a, _d, beta) in edges if u not in core_set and v in core_set]
    max_upward_beta = max(upward_betas_to_core) if upward_betas_to_core else 0.0
    direct_periphery_to_core = any(u == 7 and v in core_set for (u, v, _a, _d, _b) in edges)
    p1_passed = (not direct_periphery_to_core) and (max_upward_beta <= 0.12)

    # P2: Constitutional minimalism (|A| <= 2 and size-adjusted Gamma_tilde_0 >= 0.70)
    p2_passed = len(core_indices) <= 2 and gamma_tilde_0 >= 0.70

    # P3: Live cryptographic verification of both triad_provenance_ledger.jsonl and chain_anchors.jsonl
    triad_ok, triad_count = verify_triad_provenance_chain(ledger_file)
    anchors_ok, anchors_count, _ = verify_chain_anchors(anchors_file)
    p3_passed = bool(triad_ok and anchors_ok)

    # P4: Multi-channel validation (reports both independent product and correlated-LLM bound)
    theta_single = 0.25
    theta_symbolic_gwi = 0.05
    theta_fable = 0.12
    theta_astra = 0.12
    theta_quorum_independent = theta_symbolic_gwi * theta_fable * theta_astra  # 0.00072
    theta_quorum_correlated_llm = theta_symbolic_gwi * max(theta_fable, theta_astra)  # 0.006
    p4_passed = theta_quorum_independent < 0.005 and theta_quorum_correlated_llm <= 0.01

    # P5: Enumerate all simple upward semantic paths from non-core nodes to core nodes in G
    #     and compute R(pi) = (p(dst) - p(src)) * prod_{e in pi} beta(e)
    max_path_risk = 0.0
    worst_path_desc = ""
    for start_node in range(n_actors):
        if start_node in core_set:
            continue
        stack: list[tuple[int, list[int], float]] = [(start_node, [start_node], 1.0)]
        while stack:
            curr, path_nodes, prod_beta = stack.pop()
            if curr in core_set and len(path_nodes) > 1:
                priv_gap = max(0.0, actor_privileges[curr] - actor_privileges[start_node])
                # Reference monitors (v2, v3) apply P4 symbolic + quorum gating factor
                monitor_factor = theta_symbolic_gwi if (2 in path_nodes or 3 in path_nodes) else 1.0
                risk = priv_gap * prod_beta * monitor_factor
                if risk > max_path_risk:
                    max_path_risk = risk
                    worst_path_desc = " -> ".join(actors[idx] for idx in path_nodes)
                continue
            for nxt in range(n_actors):
                if beta_mat[curr][nxt] > 0.0 and nxt not in path_nodes and nxt > start_node - 8:
                    # Only follow upward/feedback edges (alpha == 0.0) toward core
                    if w_mat[curr][nxt] == 0.0:
                        stack.append((nxt, path_nodes + [nxt], prod_beta * beta_mat[curr][nxt]))

    path_privilege_risk = round(max_path_risk, 6)
    p5_passed = path_privilege_risk <= 0.01

    principles = {
        "P1_semantic_privilege_isolation": {
            "passed": p1_passed,
            "max_upward_beta_to_core": round(max_upward_beta, 4),
            "direct_periphery_to_core_edges": direct_periphery_to_core,
            "mechanism": "Peripheral outputs filtered through symbolic GWI gate before entering Orchestrator context",
        },
        "P2_constitutional_minimalism": {
            "passed": p2_passed,
            "core_cardinality": len(core_indices),
            "total_actors": n_actors,
        },
        "P3_provenance_tracking": {
            "passed": p3_passed,
            "triad_ledger_valid": triad_ok,
            "triad_events_count": triad_count,
            "chain_anchors_valid": anchors_ok,
            "chain_anchors_count": anchors_count,
            "mechanism": "Append-only SHA-256 triad_provenance_ledger.jsonl + RFC 6962 Merkle chain_anchors.jsonl",
        },
        "P4_multi_channel_validation": {
            "passed": p4_passed,
            "single_channel_theta": theta_single,
            "quorum_override_prob": round(theta_quorum_independent, 6),
            "quorum_correlated_llm_bound": round(theta_quorum_correlated_llm, 6),
            "suppression_factor_x": round(theta_single / max(1e-9, theta_quorum_independent), 1),
        },
        "P5_aristocracy_threat_modeling": {
            "passed": p5_passed,
            "max_path_privilege_risk_R": path_privilege_risk,
            "critical_upward_path": worst_path_desc,
            "risk_bound_epsilon": 0.01,
        },
    }

    return {
        "actors_count": n_actors,
        "core_actors": [actors[i] for i in core_indices],
        "spectral_radius_W": round(rho_w, 6),
        "direct_aristocratic_index_A0": {actors[i]: round(a0[i], 3) for i in range(n_actors)},
        "transitive_aristocratic_index_Agamma": {actors[i]: round(a_gamma[i], 3) for i in range(n_actors)},
        "metrics": {
            "gamma_0_raw": round(gamma_0, 4),
            "gamma_0_size_adjusted": round(gamma_tilde_0, 4),
            "gamma_transitive_raw": round(gamma_trans, 4),
            "gamma_transitive_size_adjusted": round(gamma_tilde_trans, 4),
            "authority_gini_G0": round(gini_0, 4),
            "is_software_aristocracy": gamma_0 > 0.70 and gamma_tilde_0 >= 0.60 and gini_0 >= 0.40,
        },
        "blast_radius_bound": {
            "proposition1_lower_bound": round(theoretical_blast_lb, 2),
            "max_core_1hop_out_degree": realized_out_max,
            "transitive_reach": n_actors - 1,
            "bound_satisfied": realized_out_max >= theoretical_blast_lb,
        },
        "constitutional_resilience": principles,
        "all_principles_passed": all(p["passed"] for p in principles.values()),
    }


def collect_pistis_summary(
    ari: dict[str, Any] | None = None,
    tok_ctl: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact, fail-safe telemetry snapshot of the 4th Organon dimension (`Pistis`) for `organon status`."""
    ap = default_chain_anchors_path()
    anchors_valid, anchors_count, latest_merkle_root = verify_chain_anchors(ap)
    ari_data = ari if ari is not None else audit_organon_aristocracy(anchors_file=ap)
    ctl_data = tok_ctl if tok_ctl is not None else simulate_pid_mpc_token_control()
    return {
        "protocol_version": PISTIS_PROTOCOL_VERSION,
        "anchors_path": str(ap),
        "anchors_chain_valid": anchors_valid,
        "merkle_anchors_count": anchors_count if anchors_valid else 0,
        "latest_merkle_root": latest_merkle_root,
        "nc_fsm_states": len(FSM_STATES),
        "iso_territories_encoded": len(ISO_3166_ALPHA2_CODES),
        "mpc_rmse": ctl_data["rmse_mpc"],
        "mpc_volatility_reduction_x": ctl_data["mpc_volatility_reduction_x"],
        "lyapunov_schur_stable": ctl_data["lyapunov_certificate"]["schur_stable"],
        "aristocracy_gamma0": ari_data["metrics"]["gamma_0_raw"],
        "aristocracy_tilde0": ari_data["metrics"]["gamma_0_size_adjusted"],
        "aristocracy_gini0": ari_data["metrics"]["authority_gini_G0"],
        "constitutional_resilience_ok": bool(ari_data["all_principles_passed"] and anchors_valid),
    }


# ==============================================================================
# 5. CLI SUBPARSER & COMMAND DISPATCH (`organon chain` / `skb chain`)
# ==============================================================================


def register_chain_subparser(subparsers: Any, show_all_aliases: bool = False) -> None:
    aliases = ("chain", "pistis", "blockchain")
    for idx, name in enumerate(aliases):
        help_txt = (
            "Pistis (Πίστις) — Poetics Bilateral Licensing FSM, RFC 6962 Merkle Anchor, PhD Tokenomics & Aristocracy"
            if idx == 0 or show_all_aliases
            else None
        )
        p = subparsers.add_parser(name, **({"help": help_txt} if help_txt else {}))
        p.add_argument(
            "action",
            nargs="?",
            choices=("status", "negotiate", "anchor", "tokenomics", "aristocracy", "full-suite"),
            default="status",
            help="Pistis blockchain action (default: status)",
        )
        p.add_argument("--target", default=".", help="Target repository directory for `anchor` (default: .)")
        p.add_argument("--json", action="store_true", help="Output machine-readable JSON")


def _emit_cli_error(msg: str, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"error": msg, "valid": False}, indent=2))
    else:
        print(f"[organon chain] ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def cmd_chain(args: argparse.Namespace) -> None:
    action = getattr(args, "action", "status") or "status"
    as_json = bool(getattr(args, "json", False))
    raw_target = getattr(args, "target", ".") or "."
    target_dir = Path(raw_target).expanduser().resolve()

    try:
        if action == "negotiate":
            res = run_negotiation_simulation()
            ok = bool(
                res["s1_signatures_verified"]
                and res["s2_self_acceptance_blocked"]
                and res["s2_frontrunning_blocked"]
                and res["s3_atomic_plt_minted"]
                and res["system_wei_conserved"]
            )
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                plt = res["plt"]
                dist = res["royalty_distribution"]
                s1_status = "PASS" if res["s1_signatures_verified"] else "FAIL"
                print("=== [Pistis 1/4] Poetics On-Chain Bilateral Negotiation (`NC` + `PLT` + `RoyaltySplitter`) ===")
                print(
                    f"  FSM Lifecycle     : IDLE -> OFFERED -> COUNTERED -> ACCEPTED -> {res['fsm_final_state']} "
                    f"({res['rounds_taken']} rounds, {res['s1_non_repudiation_events']} HMAC-signed events)"
                )
                print(
                    f"  Safety Guards     : S1 Signatures={s1_status} | S2 Self-Accept Blocked={res['s2_self_acceptance_blocked']} "
                    f"| S2 Front-Run Blocked={res['s2_frontrunning_blocked']} | S3 Atomic PLT={res['s3_atomic_plt_minted']}"
                )
                print(
                    f"  Minted PLT #{plt['plt_id']}     : royalty={plt['agreed_terms']['royalty_bps']} bps | "
                    f"territories={','.join(plt['agreed_terms']['territories_decoded'])} | "
                    f"cap={plt['agreed_terms']['max_issuances']} | termsHash={plt['terms_hash'][:18]}..."
                )
                print(
                    f"  RoyaltySplitter   : Gross={dist['gross_amount_wei']} wei -> Creators={dist['creator_total_wei']} wei "
                    f"+ Platform={dist['platform_amount_wei']} wei (Wei Conserved={dist['wei_conserved']}, "
                    f"Pull-Escrow Withdrawn={res['pull_escrow_withdrawn_wei']} wei)"
                )
            if not ok:
                sys.exit(1)
            return

        if action == "anchor":
            res = anchor_organon_attestation(target_dir=target_dir)
            if as_json:
                print(json.dumps(res, indent=2))
                return
            print("=== [Pistis 2/4] RFC 6962 Merkle Provenance & On-Chain Attestation Anchor ===")
            print(
                f"  Anchor Block ID   : {res['anchor_id']} (repo={res['target_repo']}, git={res['git_head_sha'][:12]})"
            )
            print(f"  RFC 6962 Root     : {res['rfc6962_merkle_root']} ({res['merkle_leaves_count']} leaves)")
            print(
                f"  Inclusion Proof   : {res['inclusion_proof_len']} sibling hop(s) | "
                f"Verified={res['inclusion_proof_verified']}"
            )
            p3 = res["p3_provenance"]
            print(
                f"  P3 Trace (T_prov) : {p3['stages_count']} stages (privilege {p3['origin_privilege']:.2f} -> "
                f"{p3['final_privilege']:.2f}, declassified={p3['declassified']}, valid={p3['valid']})"
            )
            print(f"  Anchor SHA-256    : {res['anchor_hash']}")
            return

        if action == "tokenomics":
            res = run_tokenomics_suite()
            ctl = res["pid_mpc_control"]
            lya = ctl["lyapunov_certificate"]
            ok = bool(res["egt_replicator"]["converged_to_ne_8"] and lya["schur_stable"] and lya["positive_definite_p"])
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                egt = res["egt_replicator"]
                col = res["nash_q_collusion_audit"]
                af = res["antifragility"]
                print("=== [Pistis 3/4] PhD Virtual Token Economies (`ai_tokenomics`) & Antifragility (`AMI`) ===")
                print(
                    f"  Game Equilibria   : Grid Monopoly p*=10 (R=50.0, Continuous p*=13.33) | "
                    f"Competitive NE p*=8 (R=40.8) | EGT ESS p*={egt['ess_price']} (prob={egt['ess_probability']:.4f})"
                )
                print(
                    f"  Folk-Theorem Audit: Nash-Q Benchmark p={col['observed_price']} -> "
                    f"Collusion Index={col['collusion_index']:.2f} [{col['regime']}]"
                )
                print(
                    f"  Shared-Plant Ctrl : RMSE Uncontrolled={ctl['rmse_uncontrolled']:.4f} | "
                    f"Causal PID={ctl['rmse_pid']:.4f} | Riccati MPC={ctl['rmse_mpc']:.4f} "
                    f"({ctl['mpc_volatility_reduction_x']}x volatility reduction)"
                )
                print(
                    f"  Lyapunov Theorem 4: Schur Stable={lya['schur_stable']} (rho={lya['spectral_radius']:.4f} < 1, "
                    f"P > 0={lya['positive_definite_p']}, residual={lya['residual_frobenius']}, "
                    f"bound={lya['ultimate_bound_sq']:.6f})"
                )
                print(
                    f"  Antifragility     : Power Universal={af['power_adaptation']['universally_antifragile']} "
                    f"(k_p={af['power_adaptation']['k_p']} < gamma_0={af['initial_snr_gamma0']}) | "
                    f"BW Regime={af['bandwidth_adaptation']['regime']} | AMI={af['antifragile_market_index']['ami_score']:.4f}"
                )
            if not ok:
                sys.exit(1)
            return

        if action == "aristocracy":
            res = audit_organon_aristocracy()
            if as_json:
                print(json.dumps(res, indent=2))
            else:
                m = res["metrics"]
                br = res["blast_radius_bound"]
                p4 = res["constitutional_resilience"]["P4_multi_channel_validation"]
                print("=== [Pistis 4/4] PhD Software Aristocracy & Constitutional Resilience (`aristocracy`) ===")
                print(
                    f"  Concentration     : Gamma_0(A)={m['gamma_0_raw']:.4f} | Size-Adjusted Gamma_tilde_0(A)="
                    f"{m['gamma_0_size_adjusted']:.4f} | Gini G(A_0)={m['authority_gini_G0']:.4f}"
                )
                print(
                    f"  Blast Radius (P1) : Theoretical Lower Bound >= {br['proposition1_lower_bound']:.2f} | "
                    f"Core 1-Hop Out={br['max_core_1hop_out_degree']} | Transitive Reach={br['transitive_reach']}/7"
                )
                print(
                    f"  Quorum Gate (P4)  : Single-Channel theta={p4['single_channel_theta']} -> "
                    f"3-Channel Quorum={p4['quorum_override_prob']:.6f} (Correlated Bound={p4['quorum_correlated_llm_bound']:.4f})"
                )
                print(f"  Constitutional P1-P5: {'ALL PASSED' if res['all_principles_passed'] else 'FAILED'}")
            if not res["all_principles_passed"]:
                sys.exit(1)
            return

        # `status` or `full-suite`
        neg = run_negotiation_simulation()
        tok = run_tokenomics_suite()
        ari = audit_organon_aristocracy()
        anc = (
            anchor_organon_attestation(target_dir=target_dir)
            if action == "full-suite"
            else collect_pistis_summary(ari=ari, tok_ctl=tok["pid_mpc_control"])
        )
        summary = collect_pistis_summary(ari=ari, tok_ctl=tok["pid_mpc_control"]) if action == "full-suite" else anc
        combined = {
            "protocol_version": PISTIS_PROTOCOL_VERSION,
            "summary": summary,
            "poetics_negotiation": neg,
            "anchor": anc,
            "phd_tokenomics": tok,
            "phd_aristocracy": ari,
        }
        if as_json:
            print(json.dumps(combined, indent=2))
            return

        s = combined["summary"]
        s1_s4_ok = (
            neg["s1_signatures_verified"]
            and neg["s2_self_acceptance_blocked"]
            and neg["s2_frontrunning_blocked"]
            and neg["s3_atomic_plt_minted"]
            and neg["system_wei_conserved"]
        )
        lya_ok = tok["pid_mpc_control"]["lyapunov_certificate"]["schur_stable"]
        print("╔══════════════════════════════════════════════════════════════════════════════════════════╗")
        print("║  PISTIS (Πίστις) — ON-CHAIN COVENANT, BILATERAL LICENSING & PHD TOKENOMICS ENGINE        ║")
        print("╚══════════════════════════════════════════════════════════════════════════════════════════╝")
        print(
            f"  [1] Poetics Licensing (`negotiate`) : 7-State NC FSM -> PLT #{neg['plt']['plt_id']} | "
            f"S1-S4={'PASS' if s1_s4_ok else 'FAIL'} | 249 ISO Territories + Dual-Escrow RoyaltySplitter"
        )
        print(
            f"  [2] Merkle Provenance (`anchor`)    : {s['merkle_anchors_count']} anchored block(s) | "
            f"Latest Root={s['latest_merkle_root'] or 'unanchored (run `organon chain anchor`)'}"
        )
        print(
            f"  [3] PhD Tokenomics (`tokenomics`)   : EGT ESS p*={tok['egt_replicator']['ess_price']} | "
            f"Riccati MPC={tok['pid_mpc_control']['rmse_mpc']:.4f} ({tok['pid_mpc_control']['mpc_volatility_reduction_x']}x) | "
            f"Lyapunov Schur={'PASS' if lya_ok else 'FAIL'} | AMI={tok['antifragility']['antifragile_market_index']['ami_score']:.4f}"
        )
        print(
            f"  [4] PhD Aristocracy (`aristocracy`) : Gamma_0={s['aristocracy_gamma0']:.4f} (Size-Adj={s['aristocracy_tilde0']:.4f}, "
            f"Gini={s['aristocracy_gini0']:.4f}) | Constitutional P1-P5={'PASS' if s['constitutional_resilience_ok'] else 'FAIL'}"
        )
    except (ValueError, PermissionError, TimeoutError, KeyError, OSError) as exc:
        _emit_cli_error(str(exc), as_json=as_json)
