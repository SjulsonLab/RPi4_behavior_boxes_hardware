"""Bounded repair of delivery order; original edge payloads are never changed."""

from .decoder import EdgeEvent

REORDER_WAIT_NS = 1_000_000
MAX_PENDING_EVENTS = 256
SEQUENCE_MODULUS = 2**32


class EventOrderBuffer:
    """Hold future global sequences briefly; leave validation to the decoder.

    One instance belongs to one acquisition/synchronization loop. A successful
    reorder is one buffering episode that drains without an unresolved gap.
    After a limit failure, buffered events are exposed in serial order and
    subsequent input passes through for strict failed-run diagnostic decoding.
    """

    def __init__(
        self,
        initial_sequence: int = 0,
        *,
        max_wait_ns: int = REORDER_WAIT_NS,
        max_pending: int = MAX_PENDING_EVENTS,
    ) -> None:
        """Accept uint32 baseline, positive ns grace and event-count capacity.

        Returns None. No clocks, GPIO, IPC, sleeps or physical units are used.
        """
        if max_wait_ns <= 0 or max_pending <= 0:
            raise ValueError("Ordering grace and capacity must be positive")
        self.last_sequence = initial_sequence
        self.max_wait_ns = max_wait_ns
        self.max_pending = max_pending
        self.pending_count = 0
        self.deadline_ns: int | None = None
        self.delivery_reorder_count = 0
        self.failed = False
        self._pending: dict[int, list[EdgeEvent]] = {}

    # User-facing operations: callers supply monotonic time and service deadlines.
    def push(self, events: list[EdgeEvent], now_ns: int) -> list[EdgeEvent]:
        """Return records ready for decoding from a raw batch and monotonic ns.

        Empty batches service expiry. Contiguous input is returned immediately;
        duplicates/older/ambiguous serial values remain visible to the decoder.
        Retained records include duplicates, so capacity bounds all event copies.
        """
        ready = []
        if self.deadline_ns is not None and now_ns >= self.deadline_ns:
            ready.extend(self.flush())
        for event in events:
            if self.failed:
                ready.append(event)
                continue
            distance = (
                event.global_sequence_number - self.last_sequence
            ) % SEQUENCE_MODULUS
            if distance == 1:
                ready.append(event)
                self.last_sequence = event.global_sequence_number
                ready.extend(self._release_contiguous())
            elif 1 < distance < SEQUENCE_MODULUS // 2:
                if self.pending_count == self.max_pending:
                    ready.extend(self.flush())
                    ready.append(event)
                    continue
                self._pending.setdefault(event.global_sequence_number, []).append(event)
                self.pending_count += 1
                if self.deadline_ns is None:
                    self.deadline_ns = now_ns + self.max_wait_ns
            else:
                # Never hide a replay/duplicate or move the serial baseline back.
                ready.append(event)
        return ready

    def flush(self) -> list[EdgeEvent]:
        """Expose all retained records once; an unresolved tail latches failed.

        Returns original EdgeEvents sorted by forward uint32 serial distance,
        preserving same-sequence groups. The first missing predecessor is left
        absent, so the decoder's existing gap rules invalidate the trajectory.
        """
        if not self._pending:
            return []
        self.failed = True
        sequences = sorted(self._pending, key=self._serial_distance)
        result = [event for sequence in sequences for event in self._pending[sequence]]
        self._pending.clear()
        self.pending_count = 0
        self.deadline_ns = None
        return result

    # Helpers: serial distance and release of already received successors.
    def _serial_distance(self, sequence: int) -> int:
        """Return unsigned distance from the last released uint32 sequence."""
        return (sequence - self.last_sequence) % SEQUENCE_MODULUS

    def _release_contiguous(self) -> list[EdgeEvent]:
        """Return queued successors; increment the episode count only on full drain."""
        result = []
        while (
            sequence := (self.last_sequence + 1) % SEQUENCE_MODULUS
        ) in self._pending:
            group = self._pending.pop(sequence)
            result.extend(group)
            self.pending_count -= len(group)
            self.last_sequence = sequence
        if self.deadline_ns is not None and not self._pending:
            self.deadline_ns = None
            self.delivery_reorder_count += 1
        return result
