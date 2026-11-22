from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading
from typing import Any

from .territory_tokens import (
    PISTIS_PROTOCOL_VERSION,
    NegotiationSession,
    PlatformLicenseToken,
    TermBundle,
    _require_exact_int,
    encode_territories,
    is_territory_permitted,
    territories_overlap,
)


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
            self._did_keys[did] = hashlib.sha256(f"pistis-did-key:{did}".encode()).digest()
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
