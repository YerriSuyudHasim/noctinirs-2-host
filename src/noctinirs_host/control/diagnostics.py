"""Optional R3 diagnostics; absent R2 counters are unknown, never zero."""
R3_PIN = "b11ada2238e5cde7a25df712b098b7033e0a0e09"
RAW_COUNTERS = ("raw_packets_sent", "raw_dropped_samples", "raw_buffer_highwater",
                "raw_tx_failed_attempts", "raw_tx_retried_ok", "raw_tx_abandoned_packets",
                "raw_tx_abandoned_samples", "frame_send_enomem")


def counters(fields):
    return {key: int(v) if isinstance(v := fields.get(key), str) and v.isdecimal()
            and len(v) <= 10 and int(v) <= 0xffffffff else None for key in RAW_COUNTERS}


def loss_summary(before, after, sequence):
    before, after = before or {}, after or {}
    info = (after.get("observed_info") or {}).get("fields", {})
    diag = after.get("firmware_raw_diagnostics") or {}
    net = after.get("netdiag") or {}
    values = {key: diag.get(key) if diag.get(key) is not None else
              (net.get("counters") or {}).get(key) for key in RAW_COUNTERS}
    same_boot = before.get("boot_id") is not None and before.get("boot_id") == after.get("boot_id")
    r3 = same_boot and info.get("raw_tx_policy") == "hold_retry"
    return dict(firmware_r3=r3, device_abandoned_packets=values["raw_tx_abandoned_packets"] if r3 else None,
                device_abandoned_samples=values["raw_tx_abandoned_samples"] if r3 else None,
                air_or_host_message_gaps=sequence.get("missing") if r3 else None,
                message_sequence=sequence, counters=values,
                limitation="Message gaps include lost replies; not a RAW packet count. "
                           "Abandoned counters reset at START; ENOMEM is boot cumulative.")


class MessageCoverage:
    """All received envelopes, including replies. Bounded reorder correction."""
    def __init__(self):
        self.reset()

    def reset(self):
        self.tail = None
        self.holes = set()
        self.missing = 0
        self.recovered = 0

    def observe(self, sequence):
        if sequence in self.holes:
            self.holes.remove(sequence)
            self.missing -= 1
            self.recovered += 1
            return
        if self.tail is None:
            self.tail = sequence
            return
        delta = (sequence - self.tail) & 0xffffffff
        if 0 < delta < 0x80000000:
            self.missing += delta - 1
            self.holes.update((self.tail + i) & 0xffffffff for i in range(max(1, delta-4096), delta))
            self.tail = sequence
            self.holes = {v for v in self.holes if (sequence-v) & 0xffffffff <= 4096}

    def snapshot(self):
        return dict(missing=self.missing, reordered_recovered=self.recovered,
                    reorder_horizon_messages=4096, includes_control_replies=True)
