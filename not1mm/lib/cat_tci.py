"""
K6GTE, CAT interface abstraction
Email: michael.bridak@gmail.com
GPL V3

TCI backend, for SDRs such as AetherSDR and ExpertSDR.

Unlike flrig and rigctld, nothing here queries the radio. TCI pushes state
changes and TCIClient caches them, so the getters are cache reads: instant,
non-blocking, and fresher than a poll would be.
"""

import logging

from not1mm.lib.cat_interface import CAT
from not1mm.lib.tci_client import TCIClient
from not1mm.lib.tci_protocol import build_command, not1mm_mode_to_tci

if __name__ == "__main__":
    print("I'm not the program you are looking for.")

logger = logging.getLogger("cat_tci")

# Long enough for a local SDR's handshake, short enough not to stall startup.
READY_TIMEOUT_MS = 1500


class TciCAT(CAT):
    """CAT control via the TCI protocol"""

    def __init__(self, host: str, port: int, trx: int = 0) -> None:
        """
        Computer Aided Transceiver abstraction class.
        Offers a normalized interface; this is the TCI class.

        Takes 2 inputs to setup the class.

        A string defining the host, example: 'localhost' or '127.0.0.1'

        An integer defining the network port used. Commonly 50001 for TCI.

        An optional integer selecting the TCI transceiver to control, 0 or 1.
        AetherSDR exposes its first two slices as trx 0 and trx 1.

        A variable 'online' is set to True once the TCI server completes its
        handshake, otherwise False.
        """
        super().__init__(host, port)
        self.interface = "tci"
        self.trx = int(trx)
        self.client = TCIClient(host, port, trx=self.trx)
        # Radio.__init__ reads get_mode_list() immediately, so give the
        # handshake a moment to land before returning.
        self.client.wait_for_ready(READY_TIMEOUT_MS)

    def set_trx(self, trx: int) -> None:
        """Switch which TCI transceiver this backend reads and commands.

        No reconnect: the client caches every trx, so the getters report the
        new trx's state immediately.
        """
        self.trx = int(trx)
        self.client.set_trx(self.trx)

    @property
    def online(self) -> bool:
        """True once the TCI server has completed its handshake.

        Reads straight through to the transport rather than caching, so a
        server that dies is reflected immediately instead of only after the
        next getter call happens to refresh a stale flag.
        """
        return self.client.online

    @online.setter
    def online(self, _value: bool) -> None:
        """No-op: CAT.__init__ does `self.online = False`, which would raise
        against a read-only property. Swallow that one assignment; `online`
        itself always reads through to the client."""

    # ---- getters ----
    # Offline returns "" rather than cached values: Radio only overwrites on a
    # truthy result, so "" holds the last known reading and reports offline.
    # Returning stale cache would paint a dead radio as live.

    def get_vfo(self) -> str:
        """Poll the radio for current vfo using the interface"""
        if not self.online:
            return ""
        return self.client.get("vfo", "")

    def get_mode(self) -> str:
        """Returns the current mode of the radio"""
        if not self.online:
            return ""
        return self.client.get("mode", "")

    def get_bw(self) -> str:
        """Get current vfo bandwidth"""
        if not self.online:
            return ""
        return self.client.get("bw", "")

    def get_ptt(self) -> str:
        """Get PTT state"""
        if not self.online:
            return "0"
        return self.client.get("ptt", "0")

    def set_rx_audio(self, trx: int, pan: int, mute: bool) -> bool:
        """Pan (0 = hard left, 50 = centre, 100 = hard right) and mute one
        trx's receive audio. Unlike cw_macros, AetherSDR applies rx_balance
        and rx_mute to the slice the trx index names."""
        if not self.online:
            return False
        balance = max(0, min(100, int(pan))) - 50  # TCI balance: 0 = centre
        self.client.send(build_command("rx_balance", int(trx), balance))
        self.client.send(build_command("rx_mute", int(trx), "true" if mute else "false"))
        return True

    def is_transmitting(self) -> bool:
        """True while the radio's one transmitter is keyed, on any slice."""
        if not self.online:
            return False
        return bool(self.client.get("tx_on", False))

    def reset_tx_state(self) -> None:
        """Forget the cached TX state; the next edge from the radio restores it."""
        self.client.reset_tx_state()

    def get_mode_list(self) -> list:
        """Get a list of modes supported by the radio.

        Served even while offline: it is a device capability captured at
        handshake, and Radio caches it once at construction.
        """
        return self.client.get("modes", [])

    # ---- setters ----

    def set_vfo(self, freq: str) -> bool:
        """Sets the radios VFO. Defaults to VFOA."""
        if not self.online:
            return False
        self.client.send(build_command("vfo", self.trx, 0, str(freq)))
        # Update the cache now rather than on the server's echo. A poll that
        # read the old value in between would look like a QSY back and, with
        # "S&P QSY clears inputs", wipe the call a bandmap click just entered.
        if str(freq).isnumeric():
            self.client.set_for_trx(str(self.trx), "vfo", str(freq))
        return True

    def set_mode(self, mode: str) -> bool:
        """Sets the radios mode"""
        if not self.online:
            return False
        self.client.send(build_command("modulation", self.trx, not1mm_mode_to_tci(mode)))
        return True

    def swap_trx_settings(self, a: int = 0, b: int = 1) -> bool:
        """Trade frequency, mode and filter between two trx (slices).

        Only what AetherSDR reports live is swapped: RIT/XIT, AGC and noise
        settings arrive in the connect handshake only, so not1mm's copies go
        stale and swapping them would do harm. Antennas stay with the slice.
        Returns False, sending nothing, unless both trx are fully known.
        """
        if not self.online:
            return False
        # Snapshot everything first: the second half of the swap must not
        # read values the first half has already overwritten.
        keys = ("vfo", "modulation", "filter", "mode", "bw")
        snap = {
            trx: {key: self.client.get_trx(key, trx) for key in keys} for trx in (a, b)
        }
        for trx, values in snap.items():
            if not values["vfo"].isnumeric() or not values["modulation"]:
                logger.warning("swap_trx_settings: trx %d state unknown: %s", trx, values)
                return False
        for trx, other in ((a, b), (b, a)):
            new = snap[other]
            # Mode before filter: changing mode resets the filter.
            self.client.send(build_command("vfo", trx, 0, new["vfo"]))
            self.client.send(build_command("modulation", trx, new["modulation"]))
            if new["filter"]:
                low, high = new["filter"].split(",")
                self.client.send(build_command("rx_filter_band", trx, low, high))
            # Update the cache now, as set_vfo does, so displays and polls
            # show the swapped state before the radio echoes it.
            for key in keys:
                if new[key]:
                    self.client.set_for_trx(str(trx), key, new[key])
        return True

    def ptt_on(self) -> bool:
        """turn ptt on"""
        if not self.online:
            return False
        self.client.send(build_command("trx", self.trx, "true"))
        return True

    def ptt_off(self) -> bool:
        """turn ptt off"""
        if not self.online:
            return False
        self.client.send(build_command("trx", self.trx, "false"))
        return True

    # ---- CW, reached via the existing "CW via CAT" option (cwtype == 3) ----

    def sendcw(self, texttosend, trx: int | None = None) -> None:
        """Send CW text through the radio's keyer. AetherSDR keys whichever
        slice holds TX; trx names the radio the text is meant for."""
        if not self.online:
            return
        target = self.trx if trx is None else int(trx)
        self.client.send(build_command("cw_macros", target, texttosend))

    def stopcw(self) -> None:
        """Abort CW transmission"""
        if not self.online:
            return
        self.client.send(build_command("cw_terminate"))
        self.client.send(build_command("cw_macros_stop"))

    def set_cw_speed(self, speed: int) -> None:
        """Set the CW speed in wpm"""
        if not self.online:
            return
        self.client.send(build_command("cw_macros_speed", int(speed)))

    def close(self) -> None:
        """Shut down the transport thread. Radio.run() calls this on exit."""
        self.client.close()
