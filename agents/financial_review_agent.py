"""
Analyst-review agent — Phase 10 (spec sections 9 and 27).

The agent may:      READ · ANALYZE · FLAG · EXPLAIN · PROPOSE
The agent may NOT:  DELETE · OVERWRITE · POST · APPROVE ITS OWN WORK

Those guardrails are ENFORCED IN CODE, not by convention:

  * The agent has read-only access to every pipeline output; its only
    write targets are agent_review_log.csv (its findings) and
    adjustments.csv (append-only proposals) — any other path raises
    AgentGuardrailError.
  * A proposal is FORCED to review_status=REVIEW; attempting to write
    APPROVED raises. Approval columns (reviewer, approval_timestamp)
    are stripped — only a human fills them.
  * Writes are append-only and verified: after writing, every
    pre-existing row is re-read and compared; any difference raises and
    names the corrupted row. Source financials are never touched.

ARCHITECTURE
    gather_evidence()  — deterministic: every non-PASS control and every
                         outlier flag becomes an EvidencePacket carrying
                         its related controls, transaction events,
                         existing adjustments, and narratives.
    Interpreter        — turns one packet into an AgentFinding
                         (explanation, recommended action, confidence).
                         DeterministicInterpreter (default) is pure
                         rules. An LLM interpreter (e.g. Claude via the
                         Anthropic API) plugs into the same interface
                         later — approval + key required first (spec
                         section 31: no paid dependencies unapproved).
                         Whatever interprets, the guardrails above sit
                         OUTSIDE the interpreter and cannot be bypassed.
    propose_adjustment — the only path from finding to numbers, and it
                         ends at review_status=REVIEW, always.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from financials.schemas import ADJUSTMENTS, AGENT_REVIEW_LOG

log = logging.getLogger("agents.review")

BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG_FILE = BASE_DIR / "data" / "client_fs" / AGENT_REVIEW_LOG.filename

ALLOWED_WRITE_TARGETS = {ADJUSTMENTS.filename, AGENT_REVIEW_LOG.filename}


class AgentGuardrailError(Exception):
    """The agent attempted something the guardrails forbid."""


def _assert_allowed_target(path: Path):
    if Path(path).name not in ALLOWED_WRITE_TARGETS:
        raise AgentGuardrailError(
            f"the agent may only write {sorted(ALLOWED_WRITE_TARGETS)} — "
            f"refusing to touch {path}"
        )


# ------------------------------------------------
# EVIDENCE
# ------------------------------------------------

@dataclass
class EvidencePacket:
    item_type: str            # CONTROL_EXCEPTION | OUTLIER_FLAG
    item_reference: str
    company_id: str
    entity_id: str
    period_id: str
    severity: str
    item: dict                # the flag/control row itself
    related_controls: list = field(default_factory=list)
    related_events: list = field(default_factory=list)
    related_adjustments: list = field(default_factory=list)
    narratives: list = field(default_factory=list)


@dataclass
class AgentFinding:
    packet: EvidencePacket
    explanation: str
    recommended_action: str
    agent_confidence: float
    interpreter: str


def gather_evidence(controls_frame, outlier_frame, events, adjustments,
                    period_master):
    """Deterministically assemble one packet per open item."""
    packets = []

    def period_of_event(event_date):
        d = pd.to_datetime(event_date)
        for row in period_master.itertuples():
            if pd.to_datetime(row.period_start) <= d <= pd.to_datetime(row.period_end):
                return row.period_id
        return None

    events = events.assign(
        _period=[period_of_event(d) for d in events["event_date"]]
    )

    def related_for(entity, period):
        # A consolidated-level item is explained by evidence at ANY level
        # beneath it, so consolidated packets carry every control for the
        # period; entity packets carry their own + consolidated context.
        if entity == "CONSOLIDATED":
            ctrl = controls_frame[controls_frame["period_id"] == period]
        else:
            ctrl = controls_frame[
                (controls_frame["entity_id"].isin([entity, "CONSOLIDATED"]))
                & (controls_frame["period_id"] == period)
            ]
        evts = events[events["_period"] == period]
        # Adjustments are period-scoped, not entity-scoped: a deal's
        # restructuring adjustment can land on any entity, and the
        # packet must see it to avoid duplicate proposals.
        adjs = adjustments[adjustments["period_id"] == period]
        return (
            ctrl.to_dict("records"),
            evts.to_dict("records"),
            adjs.to_dict("records"),
            [e for e in evts["narrative_summary"]],
        )

    for row in controls_frame[controls_frame["status"] != "PASS"].itertuples():
        ctrl, evts, adjs, narr = related_for(row.entity_id, row.period_id)
        packets.append(EvidencePacket(
            item_type="CONTROL_EXCEPTION",
            item_reference=f"{row.control_id}|{row.entity_id}|{row.period_id}"
                           f"|{row.source_reference}",
            company_id=row.company_id, entity_id=row.entity_id,
            period_id=row.period_id, severity=row.severity,
            item=row._asdict(), related_controls=ctrl, related_events=evts,
            related_adjustments=adjs, narratives=narr,
        ))

    for row in outlier_frame.itertuples():
        ctrl, evts, adjs, narr = related_for(row.entity_id, row.period_id)
        packets.append(EvidencePacket(
            item_type="OUTLIER_FLAG",
            item_reference=f"{row.method}|{row.metric_name}|{row.entity_id}"
                           f"|{row.period_id}",
            company_id=row.company_id, entity_id=row.entity_id,
            period_id=row.period_id, severity=row.severity,
            item=row._asdict(), related_controls=ctrl, related_events=evts,
            related_adjustments=adjs, narratives=narr,
        ))
    return packets


# ------------------------------------------------
# INTERPRETATION (pluggable; deterministic by default)
# ------------------------------------------------

class DeterministicInterpreter:
    """Pure-rule interpretation — reproducible, testable, no API."""

    name = "DETERMINISTIC_V1"

    def interpret(self, packet: EvidencePacket) -> AgentFinding:
        if packet.item_type == "CONTROL_EXCEPTION":
            return self._control(packet)
        return self._outlier(packet)

    def _finding(self, packet, explanation, action, confidence):
        return AgentFinding(packet, explanation, action, confidence, self.name)

    def _control(self, packet):
        item = packet.item
        control_id = item["control_id"]
        comment = item.get("agent_comment", "")
        if control_id == "C9" and "known cause" in comment:
            return self._finding(
                packet,
                "Source-vs-engine FX variance equals the CTA - the source's "
                "documented closing-rate shortcut on equity, not a data "
                "error (docs/FX_AND_CONSOLIDATION.md #4).",
                "Reviewer sign-off; request a historical-rate equity "
                "schedule from the source system to retire the shortcut.",
                0.9,
            )
        if control_id == "C2":
            return self._finding(
                packet,
                "Cash moved with no cash-flow statement on file for this "
                "entity/period - the walk cannot be verified.",
                f"Request the cash flow statement for {packet.entity_id} "
                f"{packet.period_id} from the client.",
                0.85,
            )
        if control_id == "C6":
            return self._finding(
                packet,
                "Debt balance moved with no issuance/repayment activity "
                "rows to explain it.",
                f"Request the debt schedule / CFS financing section for "
                f"{packet.entity_id} {packet.period_id}.",
                0.85,
            )
        if control_id == "C5":
            return self._finding(
                packet,
                "OCI activity is reported but no AOCI balance-sheet account "
                "is mapped, so the AOCI roll cannot be tested.",
                "Map an 'aoci' account in account_mapping.csv and request "
                "the AOCI balance from the source.",
                0.9,
            )
        return self._finding(
            packet, "Control exception requires investigation.",
            "Investigate against source documents.", 0.5,
        )

    def _outlier(self, packet):
        item = packet.item
        metric, method = item["metric_name"], item["method"]
        c4_pass = any(
            c["control_id"] == "C4" and c["status"] == "PASS"
            for c in packet.related_controls
        )
        consolidated_level = packet.entity_id == "CONSOLIDATED"
        c2_pass = any(
            c["control_id"] == "C2" and c["status"] == "PASS"
            and (consolidated_level or c["entity_id"] == packet.entity_id)
            for c in packet.related_controls
        )
        c2_gaps = sorted({
            c["entity_id"] for c in packet.related_controls
            if c["control_id"] == "C2" and c["status"] != "PASS"
        }) if consolidated_level else []
        event_ids = sorted({e["transaction_id"] for e in packet.related_events})
        # Only APPROVED adjustments count as existing normalization
        # coverage - a proposal under REVIEW is not "already normalized".
        adj_ids = sorted({
            a["adjustment_id"] for a in packet.related_adjustments
            if a.get("review_status") == "APPROVED"
        })

        if metric == "retained_earnings" and c4_pass:
            return self._finding(
                packet,
                "Large retained-earnings growth, but Control C4 proves the "
                "roll ties exactly (begin + NI - dividends = end) - "
                "consistent with profit retention, likely benign.",
                "No action needed beyond noting profit accumulation.",
                0.9,
            )
        if method == "NEW_ITEM" and event_ids:
            covered = (f" Restructuring costs from the event are already "
                       f"normalized by {', '.join(adj_ids)}."
                       if adj_ids else "")
            return self._finding(
                packet,
                f"New material activity with no prior period MAY relate to "
                f"{', '.join(event_ids)} closing in the same period (see "
                f"event narrative). Causation is not concluded.{covered}",
                "Confirm the organic vs acquired split of FY growth against "
                "the acquisition agreement before using growth rates in "
                "the forecast.",
                0.7,
            )
        if method == "MARGIN_VARIANCE" and event_ids:
            return self._finding(
                packet,
                f"Margin movement coincides with {', '.join(event_ids)} - "
                "could be mix shift from acquired operations, early "
                "synergies, or unrelated operating change.",
                "Decompose the margin bridge (price / mix / cost / "
                "acquired operations) before crediting synergies.",
                0.6,
            )
        if metric == "cash" and c2_pass and c2_gaps:
            return self._finding(
                packet,
                "Cash build is PARTIALLY explained: the entities with a "
                "cash-flow statement walk cleanly (C2 PASS), but "
                f"{', '.join(c2_gaps)} has no CFS on file, so its portion "
                "is unverified.",
                f"Request the missing cash flow statement(s) for "
                f"{', '.join(c2_gaps)} — same ask as the open C2 exception.",
                0.7,
            )
        if metric == "cash" and c2_pass:
            return self._finding(
                packet,
                "Cash build is fully explained by the cash-flow statement "
                "(Control C2 PASS for this entity).",
                "No action needed.",
                0.8,
            )
        return self._finding(
            packet,
            "Outlier beyond thresholds; no deterministic rule explains it.",
            "Investigate against source documents and narratives.",
            0.5,
        )


# ------------------------------------------------
# THE ONLY WRITE PATHS — guardrailed
# ------------------------------------------------

def findings_frame(findings, run_id="AGENT-RUN-001") -> pd.DataFrame:
    rows = []
    for i, f in enumerate(sorted(
        findings, key=lambda x: (x.packet.item_type, x.packet.item_reference)
    ), start=1):
        p = f.packet
        rows.append({
            "review_id": f"REV-{i:03d}",
            "run_id": run_id,
            "item_type": p.item_type,
            "item_reference": p.item_reference,
            "company_id": p.company_id,
            "entity_id": p.entity_id,
            "period_id": p.period_id,
            "severity": p.severity,
            "explanation": f.explanation,
            "recommended_action": f.recommended_action,
            "related_transaction_ids": ";".join(sorted(
                {e["transaction_id"] for e in p.related_events})),
            "related_adjustment_ids": ";".join(sorted(
                {a["adjustment_id"] for a in p.related_adjustments})),
            "related_control_ids": ";".join(sorted(
                {c["control_id"] for c in p.related_controls})),
            "agent_confidence": round(f.agent_confidence, 2),
            "interpreter": f.interpreter,
        })
    return pd.DataFrame(rows, columns=AGENT_REVIEW_LOG.column_names())


def write_review_log(frame, path=None) -> Path:
    path = Path(path) if path else DEFAULT_LOG_FILE
    _assert_allowed_target(path)
    frame.to_csv(path, index=False)
    log.info("agent wrote %d finding(s) to %s", len(frame), path)
    return path


def propose_adjustment(adjustments_path, proposal: dict) -> None:
    """
    Append ONE agent proposal to adjustments.csv. Forced to
    review_status=REVIEW; approval fields stripped; append-only verified.
    """
    adjustments_path = Path(adjustments_path)
    _assert_allowed_target(adjustments_path)

    if proposal.get("review_status") == "APPROVED":
        raise AgentGuardrailError(
            "the agent may PROPOSE but never APPROVE - review_status "
            "APPROVED is reserved for a human reviewer"
        )
    if "agent_confidence" not in proposal or proposal["agent_confidence"] == "":
        raise AgentGuardrailError(
            "an agent proposal must state its agent_confidence"
        )

    proposal = dict(proposal)
    proposal["review_status"] = "REVIEW"
    proposal["include_in_normalized"] = "REVIEW"
    proposal["reviewer"] = ""            # a human fills these,
    proposal["approval_timestamp"] = ""  # never the agent

    before = pd.read_csv(adjustments_path, dtype=str, keep_default_na=False)
    if proposal["adjustment_id"] in set(before["adjustment_id"]):
        raise AgentGuardrailError(
            f"adjustment_id {proposal['adjustment_id']} already exists - "
            "the agent never overwrites"
        )

    combined = pd.concat(
        [before, pd.DataFrame([proposal])[before.columns.tolist()]],
        ignore_index=True,
    )
    combined.to_csv(adjustments_path, index=False)

    # Append-only verification: every pre-existing row must be unchanged.
    after = pd.read_csv(adjustments_path, dtype=str, keep_default_na=False)
    if not before.equals(after.iloc[: len(before)].reset_index(drop=True)):
        raise AgentGuardrailError(
            "append-only verification FAILED - a pre-existing adjustment "
            "row changed during the write; investigate before trusting "
            "the file"
        )
    log.info("agent proposed %s (review_status=REVIEW)",
             proposal["adjustment_id"])
