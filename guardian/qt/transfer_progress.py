"""A segmented meter for the negotiated payload currently on the air.

VARA reports two numbers Guardian already carries in its snapshot: how many
bytes were handed to the modem for this transfer, and how many are still
sitting in its RF queue. The difference is what has actually been transmitted,
which is the only honest progress an HF/VHF link can offer -- there is no
per-byte acknowledgement to count.

OFDM reports acknowledged bytes and its live ARQ/profile measurements through
the same panel.  The meter is deliberately segmented rather than a smooth bar.
On a 566 bps
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
    fec: str = ""
    burst_bytes: int = 0
    arq_block_bytes: int = 0
    retries: int = 0
    retransmitted_bytes: int = 0
    goodput_bps: float | None = None
    channel_goodput_bps: float | None = None
    phy_payload_bps: float | None = None
    keyed_duty_cycle: float | None = None
    ptt_cycles: int = 0
    last_burst_blocks: int = 0
    last_first_pass_ok: int = 0

    @property
    def fraction(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return max(0.0, min(1.0, self.sent_bytes / self.total_bytes))


def transfer_state(snapshot, payload_active: bool, ofdm_status=None) -> TransferState:
    """Read a transfer state out of an application snapshot.

    ``data_bytes_written`` is reset by prepare_data_transfer(), so it is the
    size of *this* envelope rather than a session total. ``tx_buffer_bytes``
    stays None until VARA sends its first BUFFER notice; that is genuinely
    "queued, nothing confirmed on the air yet", so it reports zero sent rather
    than guessing.

    On receive, VARA publishes bytes read against the wire size learned from
    the envelope header. OFDM publishes acknowledged bytes for either
    direction. The active modem's reported or measured bit rate is carried in
    the same state for the shared speed line.
    """
    if payload_active and ofdm_status is not None:
        total = int(getattr(ofdm_status, "total_bytes", 0) or 0)
        tx_bytes = int(getattr(ofdm_status, "tx_bytes", 0) or 0)
        rx_bytes = int(getattr(ofdm_status, "rx_bytes", 0) or 0)
        moved = max(tx_bytes, rx_bytes)
        direction = str(getattr(ofdm_status, "direction", "") or "")
        if direction not in {"send", "receive"}:
            direction = "receive" if rx_bytes > tx_bytes else "send"
        return TransferState(
            active=True,
            sent_bytes=max(0, min(total, moved)),
            total_bytes=total,
            transport="ofdm",
            direction=direction,
            fec=str(getattr(ofdm_status, "fec", "")),
            burst_bytes=int(getattr(ofdm_status, "burst_bytes", 0) or 0),
            arq_block_bytes=int(getattr(ofdm_status, "arq_block_bytes", 0) or 0),
            retries=int(getattr(ofdm_status, "retries", 0) or 0),
            retransmitted_bytes=int(
                getattr(ofdm_status, "retransmitted_bytes", 0) or 0
            ),
            # Prefer the true elapsed transfer rate.  Keep the modeled,
            # channel-occupancy rate as a fallback for older status producers.
            goodput_bps=(getattr(ofdm_status, "goodput_bps", None)
                         if getattr(ofdm_status, "goodput_bps", None) is not None
                         else getattr(ofdm_status, "est_bitrate_bps", None)),
            channel_goodput_bps=getattr(ofdm_status, "est_bitrate_bps", None),
            phy_payload_bps=(
                moved * 8.0 / float(getattr(ofdm_status, "data_airtime_seconds", 0.0))
                if moved and float(getattr(ofdm_status, "data_airtime_seconds", 0.0)) > 0
                else None
            ),
            keyed_duty_cycle=getattr(ofdm_status, "keyed_duty_cycle", None),
            ptt_cycles=int(getattr(ofdm_status, "ptt_cycles", 0) or 0),
            last_burst_blocks=int(
                getattr(ofdm_status, "last_burst_blocks", 0) or 0
            ),
            last_first_pass_ok=int(
                getattr(ofdm_status, "last_first_pass_ok", 0) or 0
            ),
        )
    vara = snapshot.vara
    if not payload_active:
        return TransferState()
    direction = str(getattr(vara, "transfer_direction", "") or "")
    if direction == "receive":
        total = int(getattr(vara, "rx_transfer_total", 0) or 0)
        received = int(getattr(vara, "rx_transfer_bytes", 0) or 0)
        return TransferState(
            active=True,
            sent_bytes=max(0, min(total, received)),
            total_bytes=total,
            direction="receive",
            goodput_bps=getattr(vara, "tx_bitrate_bps", None),
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
        direction="send",
        goodput_bps=getattr(vara, "tx_bitrate_bps", None),
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
    """The segmented bar plus byte counts from the active payload modem."""

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

    def apply(self, state: TransferState) -> None:
        """Show the transfer, or take the whole panel out of the header."""
        self.setVisible(state.active)
        if not state.active:
            self.bar.set_fraction(0.0)
            self.detail.clear()
            return
        transport = "OFDM VHF" if state.transport == "ofdm" else "VARA"
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
            base = tr("transfer.detail_receive_waiting")
        else:
            base = tr(
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
        lines = [base, tr("transfer.speed", speed=speed)]
        if state.transport == "ofdm":
            live = dual(
                f"FEC {state.fec} · burst {state.burst_bytes} B · "
                f"ARQ {state.arq_block_bytes} B · first pass "
                f"{state.last_first_pass_ok}/{state.last_burst_blocks} · "
                f"{state.retries} retries / {state.retransmitted_bytes} B",
                f"FEC {state.fec} · dávka {state.burst_bytes} B · "
                f"ARQ {state.arq_block_bytes} B · napoprvé "
                f"{state.last_first_pass_ok}/{state.last_burst_blocks} · "
                f"{state.retries} opakování / {state.retransmitted_bytes} B",
            )
            lines.append(live)
            rates = []
            if state.phy_payload_bps is not None:
                rates.append(dual(
                    f"PHY payload {state.phy_payload_bps:.0f} bit/s",
                    f"PHY payload {state.phy_payload_bps:.0f} bit/s",
                ))
            if state.channel_goodput_bps is not None:
                rates.append(dual(
                    f"channel {state.channel_goodput_bps:.0f} bit/s",
                    f"kanál {state.channel_goodput_bps:.0f} bit/s",
                ))
            if state.keyed_duty_cycle is not None:
                rates.append(dual(
                    f"TX duty {state.keyed_duty_cycle * 100:.0f}%",
                    f"TX duty {state.keyed_duty_cycle * 100:.0f} %",
                ))
            if state.ptt_cycles:
                rates.append(f"PTT {state.ptt_cycles}")
            if rates:
                lines.append(" · ".join(rates))
        self.detail.setText("\n".join(lines))
