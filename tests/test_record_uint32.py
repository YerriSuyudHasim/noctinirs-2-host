"""IR B1: validate complete CSV groups before the replay encoder can mask IDs."""

import csv
import json

import pytest

from helpers import build_envelope, build_opt2
from test_record import GEO
from noctinirs_host.record.codec import COLUMNS, ROWS_PER_FRAME
from noctinirs_host.record.reader import read_recording
from noctinirs_host.record.writer import Recorder


@pytest.mark.parametrize("field", ["session_id", "msg_sequence"])
@pytest.mark.parametrize("value", [-1, 2**32, 0, 0xFFFFFFFF])
@pytest.mark.parametrize("sidecar", ["missing", "incomplete"])
def test_full_frame_uint32_ids_reject_invalid_and_recover_following_frame(
        tmp_path, field, value, sidecar):
    rec = Recorder(tmp_path, GEO, name="uint32")
    originals = [build_envelope(build_opt2(frame_sequence=i), msg_type=2,
                                session_id=17, msg_sequence=23) for i in range(2)]
    rec.start()
    for packet in originals:
        rec.feed(packet)
    assert rec.stop()

    with rec.csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    column = COLUMNS.index(field)
    first_frame = rows[1:1 + ROWS_PER_FRAME]
    assert len(first_frame) == ROWS_PER_FRAME
    for row in first_frame:
        row[column] = str(value)  # all 80 agree: consistency checks alone cannot reject this
    with rec.csv_path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)

    if sidecar == "missing":
        rec.json_path.unlink()
    else:
        meta = json.loads(rec.json_path.read_text(encoding="utf-8"))
        meta.update(complete=False, csv_bytes=None, csv_sha256=None)
        rec.json_path.write_text(json.dumps(meta), encoding="utf-8")

    loaded = read_recording(rec.csv_path)
    if not 0 <= value <= 0xFFFFFFFF:
        assert [f.datagram() for f in loaded.frames] == originals[1:]
        assert loaded.bad_rows == ROWS_PER_FRAME
    else:
        envelope_ids = {"session_id": 17, "msg_sequence": 23, field: value}
        expected = build_envelope(build_opt2(frame_sequence=0), msg_type=2, **envelope_ids)
        assert [f.datagram() for f in loaded.frames] == [expected, originals[1]]
        assert getattr(loaded.frames[0], field) == value
        assert loaded.bad_rows == 0
    assert loaded.partial_frames_dropped == 0
    if sidecar == "missing":
        assert loaded.meta is None and "missing" in loaded.meta_problem
    else:
        assert loaded.meta["complete"] is False and loaded.meta_problem is None
