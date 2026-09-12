"""A segmented meter for the negotiated payload currently on the air.

VARA reports two numbers Guardian already carries in its snapshot: how many
bytes were handed to the modem for this transfer, and how many are still
sitting in its RF queue. SC-FTN reports acknowledged bytes and live ARQ
measurements through the same small state object. Route identity remains common
to both transports.

The meter is deliberately segmented rather than a smooth bar. On a 566 bps
unregistered link a 256-byte envelope takes the better part of a minute, and a
sliver of a continuous bar creeping forward reads as "stuck"; a block that
lights up every few seconds reads as "working".
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..i18n import dual, tr
from .theme import DARK_TOKENS, ThemeTokens

SEGMENTS = 20
_SEGMENT_GAP = 3
_SEGMENT_RADIUS = 2
_BAR_HEIGHT = 18


@dataclass(frozen=True, slots=True)
class TransferState:
    """What the header should say about the payload in flight."""

    active: bool = False
    sent_bytes: int = 0
    total_bytes: int = 0
    transport: str = "vara"
    direction: str = "send"
    profile: str = ""
    mcs: int | None = None
    fec: str = ""
    burst_bytes: int = 0
    arq_block_bytes: int = 0
    retries: int = 0
    retransmitted_bytes: int = 0
    snr_db: float | None = None
    evm_rms: float | None = None
    channel_goodput_bps: float | None = None
    phy_payload_bps: float | None = None
    keyed_duty_cycle: float | None = None
    ptt_cycles: int = 0
    last_burst_blocks: int = 0
    last_first_pass_ok: int = 0
    goodput_bps: float | None = None
    # `source` is the original station when known.  For an inbound relayed
    # payload it stays empty until the bundle manifest has been read; `via`
    # remains the immediate VARA peer so the two roles cannot be confused.
    source: str = ""
    destination: str = ""
    via: str = ""

    @property
    def fraction(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, self.sent_bytes / self.total_bytes))


def transfer_state(snapshot, payload_active: bool, sc_status=None) -> TransferState:
    """Read a transfer state out of an application snapshot.

    ``data_bytes_written`` is reset by prepare_data_transfer(), so it is the
    size of *this* envelope rather than a session total. ``tx_buffer_bytes``
    stays None until VARA sends its first BUFFER notice; that is genuinely
    "queued, nothing confirmed on the air yet", so it reports zero sent rather
    than guessing.
    """
    if not payload_active:
        return TransferState()
    vara = snapshot.vara
    # Operations exposes the active SC-FTN status through the negotiated
    # payload contract. The caller passes ``None`` for VARA, so an idle SC
    # backend can never hide a live VARA transfer.
    if sc_status is not None:
        state = str(getattr(sc_status, "state", "idle") or "idle").lower()
        if state != "idle":
            total = int(getattr(sc_status, "total_bytes", 0) or 0)
            tx_bytes = int(getattr(sc_status, "tx_bytes", 0) or 0)
            rx_bytes = int(getattr(sc_status, "rx_bytes", 0) or 0)
            moved = max(tx_bytes, rx_bytes)
            direction = str(getattr(sc_status, "direction", "") or "")
            if direction not in {"send", "receive"}:
                direction = "receive" if rx_bytes > tx_bytes else "send"
            airtime = float(
                getattr(sc_status, "data_airtime_seconds", 0.0) or 0.0
            )
            return TransferState(
                active=True,
                sent_bytes=max(0, min(total, moved)),
                total_bytes=total,
                transport="sc_ftn",
                direction=direction,
                profile=str(getattr(sc_status, "profile", "") or ""),
                mcs=(
                    int(getattr(sc_status, "mcs"))
                    if getattr(sc_status, "mcs", None) is not None
                    else None
                ),
                fec=str(getattr(sc_status, "fec", "") or ""),
                burst_bytes=int(getattr(sc_status, "burst_bytes", 0) or 0),
                arq_block_bytes=int(
                    getattr(sc_status, "arq_block_bytes", 0) or 0
                ),
                retries=int(getattr(sc_status, "retries", 0) or 0),
                retransmitted_bytes=int(
                    getattr(sc_status, "retransmitted_bytes", 0) or 0
                ),
                snr_db=getattr(sc_status, "snr_db", None),
                evm_rms=getattr(sc_status, "evm_rms", None),
                goodput_bps=(
                    getattr(sc_status, "goodput_bps", None)
                    if getattr(sc_status, "goodput_bps", None) is not None
                    else getattr(sc_status, "est_bitrate_bps", None)
                ),
                channel_goodput_bps=getattr(
                    sc_status, "est_bitrate_bps", None
                ),
                phy_payload_bps=(
                    moved * 8.0 / airtime if moved and airtime > 0.0 else None
                ),
                keyed_duty_cycle=getattr(
                    sc_status, "keyed_duty_cycle", None
                ),
                ptt_cycles=int(getattr(sc_status, "ptt_cycles", 0) or 0),
                last_burst_blocks=int(
                    getattr(sc_status, "last_burst_blocks", 0) or 0
                ),
                last_first_pass_ok=int(
                    getattr(sc_status, "last_first_pass_ok", 0) or 0
                ),
                source=str(
                    getattr(sc_status, "transfer_source", "") or ""
                ).strip(),
                destination=str(
                    getattr(sc_status, "transfer_destination", "") or ""
                ).strip(),
                via=str(
                    getattr(sc_status, "transfer_via", "") or ""
                ).strip(),
            )
    direction = str(getattr(vara, "transfer_direction", "") or "")
    if direction == "receive":
        total = int(getattr(vara, "rx_transfer_total", 0) or 0)
        received = int(getattr(vara, "rx_transfer_bytes", 0) or 0)
        return TransferState(
            active=True,
            sent_bytes=max(0, min(total, received)),
            total_bytes=total,
            transport="vara",
            direction="receive",
            goodput_bps=getattr(vara, "tx_bitrate_bps", None),
            source=str(getattr(vara, "transfer_source", "") or "").strip(),
            destination=str(
                getattr(vara, "transfer_destination", "") or ""
            ).strip(),
            via=str(getattr(vara, "transfer_via", "") or "").strip(),
        )
    total = int(getattr(vara, "data_bytes_written", 0) or 0)
    if total <= 0:
        return TransferState()
    queued = getattr(vara, "tx_buffer_bytes", None)
    sent = 0 if queued is None else max(0, min(total, total - int(queued)))
    return TransferState(
        active=True,
        sent_bytes=sent,
        total_bytes=total,
        transport="vara",
        direction="send",
        goodput_bps=getattr(vara, "tx_bitrate_bps", None),
        source=str(getattr(vara, "transfer_source", "") or "").strip(),
        destination=str(
            getattr(vara, "transfer_destination", "") or ""
        ).strip(),
        via=str(getattr(vara, "transfer_via", "") or "").strip(),
    )


class SegmentedBar(QWidget):
    """A row of blocks that fill as the payload leaves the modem."""

    def __init__(self, tokens: ThemeTokens = DARK_TOKENS, parent=None) -> None:
        super().__init__(parent)
        self._tokens = tokens
        self._fraction = 0.0
        self.setFixedHeight(_BAR_HEIGHT)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self._tokens = tokens
        self.update()

    def set_fraction(self, fraction: float) -> None:
        value = max(0.0, min(1.0, float(fraction)))
        if abs(value - self._fraction) < 1e-4:
            return
        self._fraction = value
        self.update()

    @property
    def fraction(self) -> float:
        return self._fraction

    def lit_segments(self) -> int:
        """Blocks to light. A started transfer always shows at least one."""
        if self._fraction <= 0.0:
            return 0
        return max(1, min(SEGMENTS, round(self._fraction * SEGMENTS)))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        width = self.width()
        height = self.height()
        span = (width - _SEGMENT_GAP * (SEGMENTS - 1)) / SEGMENTS
        if span <= 0:
            return
        lit = self.lit_segments()
        on = QColor(self._tokens.warning)
        off = QColor(self._tokens.surface_3)
        edge = QColor(self._tokens.panel_border)
        for index in range(SEGMENTS):
            left = index * (span + _SEGMENT_GAP)
            block = QRectF(left, 0.0, span, float(height))
            path = QPainterPath()
            path.addRoundedRect(block, _SEGMENT_RADIUS, _SEGMENT_RADIUS)
            painter.fillPath(path, on if index < lit else off)
            if index >= lit:
                painter.strokePath(path, edge)
        painter.end()


class TransferPanel(QWidget):
    """The segmented bar plus the byte counts VARA is working through."""

    def __init__(self, tokens: ThemeTokens = DARK_TOKENS, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.title = QLabel(tr("transfer.title"))
        self.title.setObjectName("SectionLabel")
        self.bar = SegmentedBar(tokens)
        self.detail = QLabel()
        self.detail.setObjectName("Metadata")
        self.detail.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.bar)
        layout.addWidget(self.detail)
        layout.addStretch()
        self.apply(TransferState())

    def set_tokens(self, tokens: ThemeTokens) -> None:
        self.bar.set_tokens(tokens)

    @staticmethod
    def _route_detail(state: TransferState) -> str:
        """Format payload identity without inventing an unknown origin."""
        if not (state.source or state.destination or state.via):
            return ""
        source = state.source or dual("unknown", "neznámý")
        destination = state.destination or dual("unknown", "neurčený")
        route = dual(
            f"From {source} → To {destination}",
            f"Od {source} → Komu {destination}",
        )
        # `via` is the immediate VARA peer.  It is useful for a relayed leg,
        # while repeating the source on a direct link only adds noise.  If the
        # source is unknown, retain the peer marker so the operator can still
        # see who is on the other end of this leg.
        source_key = state.source.strip().upper()
        destination_key = state.destination.strip().upper()
        via_key = state.via.strip().upper()
        if state.via and (
            not state.source or via_key not in {source_key, destination_key}
        ):
            route += f"  @ {state.via}"
        return route

    def apply(self, state: TransferState) -> None:
        """Show the transfer, or take the whole panel out of the header."""
        self.setVisible(state.active)
        if not state.active:
            self.bar.set_fraction(0.0)
            self.detail.clear()
            return
        transport = "SC-FTN" if state.transport == "sc_ftn" else "VARA"
        self.title.setText(
            tr(
                "transfer.title_receive"
                if state.direction == "receive"
                else "transfer.title_send",
                transport=transport,
            )
        )
        self.bar.set_fraction(state.fraction)
        if state.direction == "receive" and state.total_bytes <= 0:
            detail = tr("transfer.detail_receive_waiting")
        else:
            detail = tr(
                "transfer.detail_receive"
                if state.direction == "receive"
                else "transfer.detail_send",
                sent=state.sent_bytes,
                total=state.total_bytes,
                percent=round(state.fraction * 100),
            )
        speed = (
            dual("measuring", "měří se")
            if state.goodput_bps is None
            else f"{state.goodput_bps:.0f} bit/s"
        )
        lines = [
            line
            for line in (
                self._route_detail(state),
                detail,
                tr("transfer.speed", speed=speed),
            )
            if line
        ]
        if state.transport == "sc_ftn":
            details: list[str] = []
            if state.profile:
                details.append(state.profile)
            if state.mcs is not None:
                details.append(f"MCS{state.mcs}")
            if state.fec:
                details.append(f"FEC {state.fec}")
            if state.burst_bytes:
                details.append(f"burst {state.burst_bytes} B")
            if state.arq_block_bytes:
                details.append(f"ARQ {state.arq_block_bytes} B")
            if state.last_burst_blocks:
                details.append(
                    f"first pass {state.last_first_pass_ok}/{state.last_burst_blocks}"
                )
            details.append(
                f"{state.retries} retries / {state.retransmitted_bytes} B"
            )
            if details:
                lines.append(" · ".join(details))
            quality: list[str] = []
            if state.snr_db is not None:
                quality.append(f"SNR {float(state.snr_db):.1f} dB")
            if state.evm_rms is not None:
                quality.append(f"EVM {float(state.evm_rms) * 100:.1f}%")
            if state.channel_goodput_bps is not None:
                quality.append(
                    f"channel {state.channel_goodput_bps:.0f} bit/s"
                )
            if state.phy_payload_bps is not None:
                quality.append(f"PHY {state.phy_payload_bps:.0f} bit/s")
            if state.keyed_duty_cycle is not None:
                quality.append(
                    f"TX duty {state.keyed_duty_cycle * 100:.0f}%"
                )
            if state.ptt_cycles:
                quality.append(f"PTT {state.ptt_cycles}")
            if quality:
                lines.append(" · ".join(quality))
        self.detail.setText("\n".join(lines))
