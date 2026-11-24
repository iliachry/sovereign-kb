from __future__ import annotations

import dataclasses
import hashlib
import json
from typing import Any

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
