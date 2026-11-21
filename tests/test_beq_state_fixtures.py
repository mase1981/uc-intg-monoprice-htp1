import asyncio
import json
from pathlib import Path

from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device


FIXTURE = Path(__file__).parent / "fixtures" / "mso_beq_pre_active.json"
BEQ_TITLE = (
    "13 Hours- The Secret Soldiers of Benghazi (2016) "
    "(-0.5 gain) BEQ Atmos"
)


def test_realistic_mso_beq_pre_snapshot_parses_and_preserves_slot_validity():
    device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
    state = json.loads(FIXTURE.read_text(encoding="utf-8"))

    device._state = state
    device._parse_state()

    assert device.beq_active == BEQ_TITLE
    assert device.get_sensor_value("beq_active") == BEQ_TITLE
    assert state["peq"]["location"] == "pre"
    assert state["peq"]["peqsw"] is True

    # The firmware reports valid:false for these slots even though the BEQ
    # coefficients and per-channel BEQ tags are populated. Do not conflate them.
    assert all(slot["valid"] is False for slot in state["peq"]["slots"])
    populated = [
        (index, slot["channels"]["sub1"])
        for index, slot in enumerate(state["peq"]["slots"])
        if slot["channels"]["sub1"].get("beq") is True
    ]
    assert [index for index, _ in populated] == [0, 2, 3]
    assert [(item["Fc"], item["gaindB"], item["Q"], item["FilterType"])
            for _, item in populated] == [
        (23, 3.2, 1, 1),
        (34, -3, 5, 0),
        (37, 0.8, 0.8, 0),
    ]

    assert device._get_sub_channels() == ["sub1"]
    assert all(
        slot["channels"][channel].get("beq") is not True
        for slot in state["peq"]["slots"]
        for channel in ("sub2", "sub3")
    )


def test_mso_and_msoupdate_messages_keep_beq_state_in_sync():
    device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
    device.push_update = lambda: None
    state = json.loads(FIXTURE.read_text(encoding="utf-8"))

    import asyncio

    async def exercise():
        await device.handle_message("mso " + json.dumps(state))
        assert device.beq_active == BEQ_TITLE
        assert device._get_sub_channels() == ["sub1"]

        await device.handle_message(
            'msoupdate [{"op":"replace",'
            '"path":"/peq/slots/0/channels/sub1/gaindB","value":3.5}]'
        )
        assert device._state["peq"]["slots"][0]["channels"]["sub1"]["gaindB"] == 3.5
        assert device.beq_active == BEQ_TITLE

    asyncio.run(exercise())


def test_clear_beq_fixture_clears_only_beq_tagged_channels():
    loop = asyncio.new_event_loop()
    try:
        device = HTP1Device(
            HTP1Config("test", "Test HTP-1", "127.0.0.1"),
            loop=loop,
        )
        device._state = json.loads(FIXTURE.read_text(encoding="utf-8"))

        operations = device._build_clear_beq_ops()
        assert operations is not None

        removed_tags = {
            operation["path"]
            for operation in operations
            if operation["op"] == "remove" and operation["path"].endswith("/beq")
        }
        assert removed_tags == {
            "/peq/slots/0/channels/sub1/beq",
            "/peq/slots/2/channels/sub1/beq",
            "/peq/slots/3/channels/sub1/beq",
        }
        assert not any("/channels/sub2/" in path or "/channels/sub3/" in path
                       for path in removed_tags)
        assert operations[-1] == {"op": "remove", "path": "/peq/beqActive"}
    finally:
        loop.close()

def test_malformed_tagged_beq_coefficients_abort_cleanup_preflight():
    loop = asyncio.new_event_loop()
    try:
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"), loop=loop)
        state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        state["peq"]["slots"][0]["channels"]["sub1"]["Fc"] = float("nan")
        device._state = state
        assert device._build_clear_beq_ops() is None
    finally:
        loop.close()


def test_empty_peq_slot_requires_complete_finite_coefficients():
    loop = asyncio.new_event_loop()
    try:
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"), loop=loop)
        state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        device._state = state

        # A malformed slot must be skipped; the next valid empty slot is usable.
        state["peq"]["slots"][1]["channels"]["sub1"]["Q"] = float("inf")
        assert device._find_empty_peq_slot(1, "sub1") == 2
    finally:
        loop.close()
