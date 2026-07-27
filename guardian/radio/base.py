"""RadioDriver interface and a no-op implementation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RadioState:
    """Snapshot of what the radio is doing right now."""

    connected: bool = False
    frequency_hz: int | None = None
    mode: str | None = None
    ptt: bool = False
    signal: int | None = None      # raw S-meter value if available
    error: str | None = None

    def freq_mhz(self) -> str:
        if self.frequency_hz is None:
            return "--"
        return f"{self.frequency_hz / 1_000_000:.4f} MHz"


class RadioDriver:
    """Uniform radio-control interface.

    Methods raise on hard failure; callers should catch and surface the error
    in RadioState.error rather than crashing the UI.
    """

    name = "radio"

    def open(self) -> None:
        """Establish the connection (open port / connect to rigctld)."""
        raise NotImplementedError

    def close(self) -> None:
        """Release the connection."""

    @property
    def is_open(self) -> bool:
        return False

    def set_ptt(self, on: bool) -> None:
        """Key (True) or unkey (False) the transmitter."""
        raise NotImplementedError

    def get_state(self) -> RadioState:
        """Poll the radio for a fresh state snapshot."""
        return RadioState(connected=self.is_open)

    # Optional capabilities — default to "not supported".
    def set_frequency(self, hz: int) -> None:
        raise NotImplementedError(f"{self.name} cannot set frequency")

    def set_mode(self, mode: str) -> None:
        raise NotImplementedError(f"{self.name} cannot set mode")


class NullRadio(RadioDriver):
    """Used when no radio backend is configured. Everything is a no-op."""

    name = "none"

    def open(self) -> None:
        pass

    @property
    def is_open(self) -> bool:
        return False

    def set_ptt(self, on: bool) -> None:
        pass

    def get_state(self) -> RadioState:
        return RadioState(connected=False, error="No radio backend configured")
