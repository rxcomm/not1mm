"""
Transmit arbiter for SO2V over TCI with one transmitter.
GPL V3

Two receivers, one transmitter. The run radio (trx 0) keeps calling CQ while
the operator works stations on the S&P radio (trx 1); every CW transmission is
routed to the slice it belongs to by moving TX between transmissions.

Rules:
  * TX is only ever moved between messages, never inside one. With semi
    break-in or QSK the radio unkeys in every gap of a message, so a single
    key-up does not mean the message is over: a message is over only once the
    radio has stayed unkeyed for longer than any gap inside it (quiet_s).
  * A send to the radio that already holds TX goes straight out (CWX appends
    to its own buffer), exactly as before SO2V.
  * A manual send to the other radio interrupts an auto-CQ in progress: the
    CQ is aborted, the send goes out, and on_cq_interrupted fires so the CQ
    can be resumed.
  * Any other send to the other radio waits for the current transmission to
    end, then TX moves and it goes out.
  * When idle, TX is handed back to the focused radio, so voice keying and
    anything else that does not pass through here keys the radio in focus.

Pure Python, no Qt: MainWindow calls tick() from a timer and supplies the
radio operations as callables, so the logic is testable with a fake clock.
"""

import logging
import time
from collections import deque
from dataclasses import dataclass, field

if __name__ == "__main__":
    print("I'm not the program you are looking for.")

logger = logging.getLogger("so2v_tx")

# A send that never keys the radio (empty text, radio refused it) must not
# wedge the arbiter.
START_TIMEOUT_S = 2.0
# How long to wait for an aborted CQ to unkey before giving up waiting.
STOP_TIMEOUT_S = 2.0
# Quiet time that ends an aborted message: the buffer was cleared, so no
# further word is coming once the radio unkeys.
ABORT_QUIET_S = 0.2


def quiet_seconds(wpm: int) -> float:
    """Unkeyed time that means a CW message has ended: one and a half word
    spaces (7 dits) at this speed, and never less than 0.35 s."""
    dit = 1.2 / max(int(wpm or 20), 5)
    return max(0.35, 1.5 * 7 * dit)


@dataclass
class Transmission:
    trx: int
    kind: str  # "cq" (auto-CQ) or "manual"
    sent_at: float
    started: bool = False


@dataclass
class So2vTxArbiter:
    is_transmitting: object  # () -> bool
    select_tx: object  # (trx) -> bool, moves the TX slice
    sendcw: object  # (trx, text) -> None, sends through the current TX slice
    stopcw: object  # () -> None, aborts the CW in progress
    clock: object = time.monotonic
    settle_s: float = 0.15  # time for the radio to apply a TX-slice move
    on_cq_interrupted: object = None  # () -> None
    quiet_s: float = 0.63  # unkeyed time that ends a message; see quiet_seconds

    tx_slice: int | None = None
    focus: int = 0
    queue: deque = field(default_factory=deque)
    current: Transmission | None = None
    settle_until: float = 0.0
    abort_until: float = 0.0
    last_keyed: float = float("-inf")  # last moment the radio was seen keyed

    # ---- state ----

    def init_tx(self, trx: int) -> None:
        """Put TX on a known slice at startup."""
        self.focus = int(trx)
        self.select_tx(self.focus)
        self.tx_slice = self.focus
        self.settle_until = self.clock() + self.settle_s

    def set_focus(self, trx: int) -> None:
        """The operator moved to the other radio. TX follows on the next idle
        tick, never mid-transmission."""
        self.focus = int(trx)

    def observe(self) -> bool:
        """Sample the radio. Returns True while keyed."""
        transmitting = bool(self.is_transmitting())
        if transmitting:
            self.last_keyed = self.clock()
        return transmitting

    def quiet(self, quiet_s: float | None = None) -> bool:
        """Unkeyed, and unkeyed for long enough that no message is mid-flight."""
        if self.observe():
            return False
        hold = self.quiet_s if quiet_s is None else quiet_s
        return self.clock() - self.last_keyed >= hold

    def in_progress(self) -> bool:
        """A message is on the air, starting, being aborted, or between words."""
        return self.current is not None or self.abort_until > 0 or not self.quiet()

    def busy(self) -> bool:
        """True while anything is transmitting, aborting or waiting to go."""
        return bool(self.queue) or self.in_progress()

    def needs_tick(self) -> bool:
        return self.busy() or (self.tx_slice is not None and self.tx_slice != self.focus)

    def clear(self) -> None:
        """Operator hit stop: drop everything pending."""
        self.queue.clear()
        self.current = None
        self.abort_until = 0.0
        # Whatever was keyed is being stopped now: still let it fall quiet
        # before TX may move.
        self.last_keyed = self.clock()

    # ---- sending ----

    def send(self, trx: int, text: str, kind: str = "manual") -> None:
        trx = int(trx)
        now = self.clock()
        current = self.current
        if (
            not self.queue
            and self.abort_until == 0
            and self.tx_slice == trx
            and now >= self.settle_until
            and (current is None or current.trx == trx)
        ):
            # Same radio as TX: straight out, CWX buffers it behind anything
            # already sending.
            self.sendcw(trx, text)
            if current is None:
                self.current = Transmission(trx, kind, now)
            elif kind == "manual":
                current.kind = "manual"
            return

        if kind == "manual" and current is not None and current.kind == "cq" and current.trx != trx:
            logger.debug("SO2V: interrupting CQ on trx %d for trx %d", current.trx, trx)
            self.stopcw()
            self.current = None
            self.abort_until = now + STOP_TIMEOUT_S
            # Anything else queued for the CQ radio goes with the CQ.
            self.queue = deque(item for item in self.queue if item[2] != "cq")
            if self.on_cq_interrupted:
                self.on_cq_interrupted()
        self.queue.append((trx, text, kind))

    # ---- driven by a timer ----

    def tick(self) -> None:
        now = self.clock()
        transmitting = self.observe()

        if self.current is not None:
            if not self.current.started:
                if transmitting:
                    self.current.started = True
                elif now - self.current.sent_at > START_TIMEOUT_S:
                    logger.debug("SO2V: send on trx %d never keyed", self.current.trx)
                    self.current = None
            elif self.quiet():
                # Unkeyed past any word gap: the message really is over.
                self.current = None
        if self.current is not None:
            return

        if self.abort_until:
            if not self.quiet(ABORT_QUIET_S) and now < self.abort_until:
                return
            # The aborted message's buffer was cleared: no further word is
            # coming, so the short abort quiet time is enough.
            self.abort_until = 0.0
            self.last_keyed = float("-inf")
        if not self.quiet():
            return  # keyed, or between words of something we did not send

        if self.queue:
            trx = self.queue[0][0]
            if self.tx_slice != trx:
                self.move_tx(trx, now)
                return
            if now < self.settle_until:
                return
            _trx, text, kind = self.queue.popleft()
            self.sendcw(trx, text)
            self.current = Transmission(trx, kind, now)
            while self.queue and self.queue[0][0] == trx:
                _trx, more, more_kind = self.queue.popleft()
                self.sendcw(trx, more)
                if more_kind == "manual":
                    self.current.kind = "manual"
            return

        if self.tx_slice != self.focus and now >= self.settle_until:
            self.move_tx(self.focus, now)

    def move_tx(self, trx: int, now: float) -> None:
        logger.debug("SO2V: TX slice -> trx %d", trx)
        self.select_tx(trx)
        self.tx_slice = trx
        self.settle_until = now + self.settle_s
