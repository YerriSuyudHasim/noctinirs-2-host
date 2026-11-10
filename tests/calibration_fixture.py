"""Test-only post-sweep fixture for recording, recovery, and UI regressions."""

from noctinirs_host.control import protocol as p
from noctinirs_host.control.profile import encode_profile_text
from noctinirs_host.engineering import EngineeringRun
from test_udp_session import Peer


def seed_calibration(run, codes=(133,) * 20):
    ctl = run.controller
    context = ctl.recording_snapshot(info=ctl.refresh_info())
    report, _ = run._calibration_readback()
    profile = run._new_calibration_profile(codes, dict(boot_id=ctl.boot_id,
        configuration=context["config"], default_table=run._read_defaults(report), slots=[],
        fixture="synthetic post-sweep state"))
    run.calibration_armed = run._arm_calibration_profile(profile)
    run.calibration = profile
    return profile


class CalibrationReadyRun(EngineeringRun):
    def configure(self, fs):
        super().configure(fs)
        self.seed_calibration()

    def seed_calibration(self):
        seed_calibration(self)


class CalibrationPeer(Peer):
    profile = None

    def execute(self, req, source):
        if req.opcode == p.OP_SET_PROFILE:
            self.profile = (int.from_bytes(req.args[:4], "little"), req.args[4:36], tuple(req.args[36:]))
            return self.reply(req)
        if req.opcode == p.OP_GET_PROFILE:
            if self.profile is None:
                return self.reply(req, extension=encode_profile_text())
            ident, digest, codes = self.profile
            live = self.state == p.ST_STREAMING
            return self.reply(req, extension=encode_profile_text(
                state="APPLIED" if live else "ARMED", armed=True, profile_id=ident,
                profile_hash=digest, profile_code=codes, source="PROFILE" if live else "NONE",
                start_result="OK" if live else "NONE", applied_profile_id=ident if live else 0,
                applied_profile_hash=digest if live else None, requested_code=codes if live else None,
                applied_code=codes if live else None, applied_read=live, applied_valid=live))
        return super().execute(req, source)
