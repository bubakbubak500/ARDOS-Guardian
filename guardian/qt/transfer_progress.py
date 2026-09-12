"""A segmented meter for the VARA payload currently on the air.

VARA reports two numbers Guardian already carries in its snapshot: how many
bytes were handed to the modem for this transfer, and how many are still
sitting in its RF queue. The difference is what has actually been transmitted,
which is the only honest progress an HF/VHF link can offer -- there is no
per-byte acknowledgement to count.

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
    direction: str = "send"
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


def transfer_state(snapshot, payload_active: bool) -> TransferState:
    """Read a transfer state out of an application snapshot.

    ``data_bytes_written`` is reset by prepare_data_transfer(), so it is the
    size of *this* envelope rather than a session total. ``tx_buffer_bytes``
    stays None until VARA sends its first BUFFER notice; that is genuinely
    "queued, nothing confirmed on the air yet", so it reports zero sent rather
    than guessing.
    """
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
        self.title.setText(
            tr(
                "transfer.title_receive"
                if state.direction == "receive"
                else "transfer.title_send",
                transport="VARA",
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
        self.detail.setText("\n".join(lines))
