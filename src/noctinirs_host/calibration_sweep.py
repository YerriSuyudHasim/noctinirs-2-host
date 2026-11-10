"""RAW sweep accounting and canonical, versioned calibration artifacts."""

import hashlib
import json
import random
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from statistics import fmean, stdev

from .calibration import Assumptions, LEGACY_DEFAULT, Level, empirical_choice, level_exclusions
from .control.profile import IREF_SLOTS
from .preview.slots import ADC_MAX, ADC_MIN, SlotAssembler


@dataclass(frozen=True)
class SweepPlan:
    # Integer sampling grid, configurable. These are probe points, not defaults or
    # measurements of LED turn-on. Short slots get a denser grid at the low end.
    long_codes: tuple[int, ...] = (1, 8, 32, 67, 133, 177, 222)
    short_codes: tuple[int, ...] = (1, 4, 8, 16, 32, 67, 132)
    reference_index: int = 3

    def __post_init__(self):
        if not (len(self.long_codes) == len(self.short_codes) >= 3
                and 0 <= self.reference_index < len(self.long_codes)
                and all(type(code) is int and 1 <= code < LEGACY_DEFAULT for code in self.short_codes)
                and all(all(type(code) is int and 1 <= code <= 222 for code in codes)
                        and tuple(sorted(set(codes))) == codes
                        for codes in (self.long_codes, self.short_codes))):
            raise ValueError("Invalid sweep grid.")

    def visits(self, short_slots, seed, short_interim_code=Assumptions.short_interim_code):
        vectors = [tuple(short if slot in short_slots else long for slot in range(IREF_SLOTS))
                   for long, short in zip(self.long_codes, self.short_codes)]
        reference = vectors[self.reference_index]
        interim = []
        if short_slots and short_interim_code not in self.short_codes:
            interim.append(tuple(short_interim_code if slot in short_slots else code
                                 for slot, code in enumerate(reference)))
        high = [vector for vector in vectors if max(vector) > 133]
        rng = random.Random(seed)
        order = [*vectors, *interim, reference, *reversed(vectors), *interim, reference]
        for _ in range(3):
            rng.shuffle(high)
            order.extend(high)
            if high:
                order.append(reference)
        return reference, order


def load_configuration(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or set(config) - {"parameters", "sweep_grid"}:
        raise ValueError("Expected a calibration JSON object with parameters and/or sweep_grid.")
    grid = dict(config.get("sweep_grid", {}))
    for key in ("long_codes", "short_codes"):
        if key in grid:
            grid[key] = tuple(grid[key])
    return Assumptions(**config.get("parameters", {})), SweepPlan(**grid)


@dataclass(frozen=True)
class CalibrationProfile:
    profile_id: int
    profile_hash: str
    codes: tuple[int, ...]
    document: dict

    @classmethod
    def create(cls, codes, metadata, *, used_ids=None):
        if used_ids is None:
            used_ids = set()
        ident = secrets.randbelow(0xffffffff) + 1
        while ident in used_ids:
            ident = secrets.randbelow(0xffffffff) + 1
        used_ids.add(ident)
        document = dict(metadata, schema="noctinirs.iref-profile/1", profile_id=ident, codes=list(codes))
        return cls(ident, hashlib.sha256(canonical(document)).hexdigest(), tuple(codes), document)

    def matches(self, report, *, applied=False, stopped=False):
        if not report or not report.supported or report.warnings or report.armed is not True:
            return False
        if (report.profile_id, report.profile_hash, report.profile_code) != (
                self.profile_id, self.profile_hash, self.codes):
            return False
        if stopped:
            return (report.state == "CONSUMED" and report.start_gate in ("PROFILE_CONSUMED", "START_EXHAUSTED")
                    if report.start_gate is not None else report.state == "ARMED")
        return (report.state == "ARMED" if not applied else
                report.state == "APPLIED" and report.start_gate in (None, "PROFILE_CONSUMED", "START_EXHAUSTED")
                and report.source == "PROFILE" and report.start_result == "OK"
                and report.applied_valid is True and report.applied_read is True
                and (report.applied_profile_id, report.applied_profile_hash, report.requested_code,
                     report.applied_code) == (self.profile_id, self.profile_hash, self.codes, self.codes))


    @staticmethod
    def table_matches(report, defaults):
        return (defaults.get("contract") is None or
                (report.session_table_id, report.session_short_status) == (defaults["id"], defaults["short_status"]))


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


class Visit:
    def __init__(self, profile, count, map_id):
        self.profile = profile
        self.count = count
        self.map_id = map_id
        self.rows = [[] for _ in profile.codes]
        self.maximum = [[ADC_MIN] * 4 for _ in profile.codes]
        self.minimum = [[ADC_MAX] * 4 for _ in profile.codes]
        self.assembler = SlotAssembler()
        self.hash = hashlib.sha256()
        self.epoch = None
        self.next_sample = None
        self.flags = set()
        self.running = None
        self.stopped = None

    def feed(self, packet, wire, host_us):
        self.hash.update(wire)
        if packet.gap_before or (self.next_sample is not None and packet.first_sample_sequence != self.next_sample):
            self.flags.add("INCOMPLETE_DATA")
        self.next_sample = (packet.first_sample_sequence + packet.sample_count) & 0xffffffff
        if self.epoch is None:
            self.epoch = packet.slot_plan_epoch
        if packet.geometry_map_id != self.map_id or packet.slot_plan_epoch != self.epoch:
            self.flags.add("SWEEP_CONFIG_MISMATCH")
            return
        for sample in packet.samples:
            if sample.slot_index >= IREF_SLOTS:
                continue
            for i, value in enumerate(sample.ain):
                hi, lo = self.maximum[sample.slot_index], self.minimum[sample.slot_index]
                hi[i], lo[i] = max(hi[i], value), min(lo[i], value)
        for row in self.assembler.feed(packet, host_us):
            rows = self.rows[row.slot]
            if row.gap:
                self.flags.add("INCOMPLETE_DATA")
            if not row.partial and row.on_n and row.off_n and len(rows) < self.count:
                rows.append(row)

    @property
    def done(self):
        return all(len(rows) >= self.count for rows in self.rows)

    def summary(self):
        return dict(profile_id=self.profile.profile_id, profile_hash=self.profile.profile_hash,
                    codes=self.profile.codes, raw_sha256=self.hash.hexdigest(), epoch=self.epoch,
                    counts=[len(rows) for rows in self.rows], flags=sorted(self.flags),
                    running=self.running, stopped=self.stopped)


def levels_for_slot(visits, slot, reference, targets, params):
    """Pool independent visits to the same code. Exclude a drift-affected block."""
    groups = {}
    reference_step = None
    previous_reference = 0
    for index, visit in enumerate(visits):
        rows = visit.rows[slot]
        if visit.profile.codes != reference or ({"INCOMPLETE_DATA", "SWEEP_CONFIG_MISMATCH"} & visit.flags) or len(rows) < params.min_occurrences:
            continue
        step = [fmean(row.on_mean(i) - row.off_mean(i) for row in rows) for i in targets]
        if reference_step is None:
            reference_step = step
        if any(abs(now - baseline) > abs(baseline) * params.prediction_error
               for now, baseline in zip(step, reference_step)):
            for affected in visits[previous_reference:index + 1]:
                affected.flags.add(f"REFERENCE_DRIFT_SLOT_{slot}")
        previous_reference = index + 1
    for visit in visits:
        code = visit.profile.codes[slot]
        groups.setdefault(code, []).append(visit)
    levels = []
    for code, group in sorted(groups.items()):
        valid = [visit for visit in group if not ({"INCOMPLETE_DATA", "SWEEP_CONFIG_MISMATCH",
                                                   f"REFERENCE_DRIFT_SLOT_{slot}"} & visit.flags)
                 and len(visit.rows[slot]) >= params.min_occurrences
                 and 1 - len(visit.rows[slot]) / visit.count <= params.loss_fraction]
        # A clipped visit is evidence against the code even if it also lost data.
        rows = [row for visit in valid for row in visit.rows[slot]]
        deltas = [[row.on_mean(i) - row.off_mean(i) for row in rows] for i in range(4)]
        levels.append(Level(
            code, tuple(fmean(x) if x else 0 for x in deltas),
            tuple(stdev(x) if len(x) > 1 else 0 for x in deltas),
            tuple(max(visit.maximum[slot][i] for visit in group) for i in range(4)),
            tuple(min(visit.minimum[slot][i] for visit in group) for i in range(4)),
            len(valid), len(rows), envelope=tuple(max((fmean(r.on_mean(i) - r.off_mean(i)
                for r in visit.rows[slot]) for visit in valid), default=0) for i in range(4)),
        ))
    return levels


def evidence(report, reply):
    return dict(boot_id=reply.boot_id, session_id=reply.session_id, state=reply.state_text,
                generation=None, table_id=report.session_table_id, short_status=report.session_short_status,
                report=report.text)


def config_metadata(plan, params):
    return dict(parameters=asdict(params), sweep_grid=asdict(plan),
                flags=["ASSUMED_REV22_PARAMETERS", "ENBW_PLACEHOLDER", "D1_ANCHOR_UNAVAILABLE",
                       "WHITENESS_UNVERIFIED", "GAIN_READBACK_UNAVAILABLE", "SPECTRAL_PROFILE_UNAVAILABLE"],
                estimator="RAW scheduled ON_STABLE minus OFF_STABLE means",
                spectral_profile=None)

class Sweep:
    """One cancellable sweep, advanced by the session worker's normal tick."""

    def __init__(self, run, config, defaults):
        self.run = run
        self.ctl = run.controller
        self.config = config
        self.boot = self.ctl.boot_id
        self.device = run.context.get("observed_info")
        self.defaults = defaults
        self.fs = self.ctl.settings.fs_hz
        self.seed = secrets.randbits(32)
        self.params = run.calibration_params
        self.plan = run.sweep_plan
        self.targets = [tuple(i for i in range(4) if run.geometry.channel_for(slot, i))
                        for slot in range(IREF_SLOTS)]
        self.short_slots = {slot for slot, targets in enumerate(self.targets)
                 if any(run.geometry.channel_for(slot, i).kind == "short" for i in targets)}
        self.reference, self.order = self.plan.visits(self.short_slots, self.seed, self.params.short_interim_code)
        self.visits = []
        self.phase = "next"
        self.deadline = 0
        self.session = 0

    @property
    def progress(self):
        return f"Calibration {min(len(self.visits) + 1, len(self.order))}/{len(self.order)} · {self.phase}"

    def feed(self, packet, wire, host_us):
        if self.phase == "collect":
            self.visits[-1].feed(packet, wire, host_us)

    def tick(self):
        ctl, run = self.ctl, self.run
        now = ctl._clock()
        if not ctl.connected or ctl.boot_id != self.boot:
            raise ValueError("Connection changed during calibration. Reconnect and run a new sweep.")
        if self.phase == "next":
            if len(self.visits) == len(self.order):
                return self.finish()
            context = run._observe(verify_config=True, profile_phase=None)
            if not run.stopped or context["config"] != self.config:
                raise ValueError("Device settings changed during calibration.")
            codes = self.order[len(self.visits)]
            profile = run._new_calibration_profile(codes, dict(purpose="temporary-sweep",
                                                              visit=len(self.visits), seed=self.seed,
                                                              fs_hz=self.fs, boot_id=self.boot))
            report, _ = run._calibration_readback()
            run._require_start_capacity(report)
            if run._read_defaults(report) != self.defaults:
                raise ValueError("Default table changed during calibration.")
            run._arm_calibration_profile(profile)
            self.visits.append(Visit(profile, self.params.min_occurrences + 1, run.geometry.geometry_map_id))
            run._result(ctl.start())
            self.session = ctl.session_id
            self.phase, self.deadline = "verify application", now + 5
            return
        visit = self.visits[-1]
        if self.phase == "verify application":
            if not self.session or ctl.session_id != self.session:
                raise ValueError("Sweep START ended before profile application was verified.")
            if ctl.state_text == "STREAMING":
                report, reply = run._calibration_readback(allow_busy=True)
                if report is not None:
                    if (not visit.profile.matches(report, applied=True) or reply.session_id != self.session
                            or reply.state_text != "STREAMING" or not visit.profile.table_matches(report, self.defaults)):
                        raise ValueError("Sweep profile readback mismatch. Check the device and retry calibration.")
                    visit.running = evidence(report, reply)
                    self.phase = "collect"
                    self.deadline = now + visit.count / self.fs + 5
                    return
        elif self.phase == "collect":
            if "SWEEP_CONFIG_MISMATCH" in visit.flags:
                raise ValueError("RAW geometry or slot plan changed during a calibration level.")
            if ctl.session_id != self.session:
                raise ValueError("Sweep stream ended before collection finished.")
            if visit.done or now >= self.deadline:
                self.phase, self.deadline = "verify stop", now + 5
                run._result(ctl.stop())
                return
        elif self.phase == "verify stop" and run.stopped:
            report, reply = run._calibration_readback()
            if (not visit.profile.matches(report, stopped=True) or report.applied_at_stop != visit.profile.codes
                    or report.applied_valid is not False or report.stop_integrity_effective != "OK"
                    or reply.session_id or reply.state_text != "STOPPED"
                    or (report.applied_profile_id, report.applied_profile_hash) != (
                        visit.profile.profile_id, visit.profile.profile_hash)
                    or not visit.profile.table_matches(report, self.defaults)):
                raise ValueError("Sweep STOP readback failed. Recover the device before calibrating again.")
            visit.stopped = evidence(report, reply)
            self.phase = "next"
            return
        if now >= self.deadline:
            raise ValueError(f"Timed out during calibration ({self.phase}).")

    def finish(self):
        run = self.run
        choices, summary = [], []
        for slot, targets in enumerate(self.targets):
            if not targets:
                raise ValueError(f"Slot {slot} has no target detectors in the geometry map.")
            levels = levels_for_slot(self.visits, slot, self.reference, targets, self.params)
            choice = empirical_choice(levels, targets, self.defaults["codes"][slot], self.fs, self.params,
                                      short=slot in self.short_slots)
            if choice.code is None:
                raise ValueError(f"Slot {slot} has no usable headroom evidence. Check the probe and run a new sweep.")
            choices.append(choice)
            summary.append(dict(slot=slot, targets=targets, selection=asdict(choice),
                                levels=[dict(asdict(level), exclusions=level_exclusions(level, self.params))
                                        for level in levels]))
        metadata = config_metadata(self.plan, self.params)
        metadata.update(purpose="sweep-result", fs_hz=self.fs, boot_id=self.boot, configuration=self.config,
                        geometry_sha256=run.geometry.sha256, simulated=self.ctl.simulated,
                        firmware_info=self.device, host_sha=run.host_sha,
                        completed_utc=datetime.now(timezone.utc).isoformat(),
                        default_table=self.defaults, seed=self.seed, slots=summary,
                        short_interim=dict(code=self.params.short_interim_code, slots=sorted(self.short_slots),
                                           scope="host tier 3", status="assumed_pending_caliper",
                                           firmware_table_written=False),
                        visits=[visit.summary() for visit in self.visits])
        profile = run._new_calibration_profile([choice.code for choice in choices], metadata)
        armed = run._arm_calibration_profile(profile)
        if run._read_defaults(run.controller.profile) != self.defaults:
            raise ValueError("The reported default table changed during calibration.")
        return profile, armed
