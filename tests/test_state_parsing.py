import asyncio
import json

import pytest

from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device


@pytest.fixture
def device():
    loop = asyncio.new_event_loop()
    instance = HTP1Device(
        HTP1Config("test", "Test HTP-1", "127.0.0.1"),
        loop=loop,
    )
    yield instance
    loop.close()


def real_receiver_state():
    """Relevant state copied from the user's real HTP-1 mso response."""
    return {
        "powerIsOn": False,
        "muted": False,
        "volume": -27,
        "input": "h1",
        "inputs": {
            "h1": {"label": "Movies", "visible": True},
            "h2": {"label": "Streaming", "visible": True},
            "h3": {"label": "Disc", "visible": True},
            "h4": {"label": "HTPC", "visible": True},
            "h5": {"label": "HDMI 5", "visible": False},
            "h6": {"label": "HDMI 6", "visible": False},
            "h7": {"label": "HDMI 7", "visible": False},
            "h8": {"label": "HDMI 8", "visible": False},
        },
        "upmix": {"select": "native"},
        "status": {
            "DECSourceProgram": "PCM",
            "DECProgramFormat": "2.0.0",
            "SurroundMode": "Direct",
            "ENCListeningFormat": "2.3.0",
        },
        "videostat": {
            "VideoResolution": "-----",
            "HDRstatus": "--",
            "VideoColorSpace": "---",
            "VideoMode": "--",
            "VideoBitDepth": "--",
        },
        "cal": {
            "zeroPoint": -7,
            "vpl": -80,
            "vph": -14,
            "diracactive": "on",
            "currentdiracslot": 4,
            "slots": [
                {"valid": True, "name": "6 Point Tweaked"},
                {"valid": True, "name": "10 Point Tweaked"},
                {"valid": True, "name": "6 Point Default Support"},
                {"valid": True, "name": "10 Point Default Support"},
                {"valid": True, "name": "6 Point Directional"},
                {"valid": True, "name": "10 Point Defaults"},
            ],
        },
        "peq": {"peqsw": True, "location": "pre"},
        "loudness": "off",
        "night": "off",
        "shaker": {
            "mute": "off",
            "activePreset": 0,
            "presets": {"0": {"trim": -3}},
        },
    }


def test_parses_power_volume_mute_real_inputs_modes_and_formats(device):
    device._state = real_receiver_state()

    device._parse_state()

    assert device.power is False
    assert device.muted is False
    # Display volume is raw receiver volume minus the configured zero point.
    assert device.volume_db == -20
    assert device.zp == -7
    assert device.vpl == -80
    assert device.vph == -14

    assert device.current_source == "Movies"
    assert device.source_list == ["Movies", "Streaming", "Disc", "HTPC"]
    assert device.surround_mode == "native"
    assert device.sound_mode_display == "NATIVE"
    assert device.audio_format == "2.0.0 PCM"
    assert device.output_audio_format == "2.3.0 Direct"
    assert device.video_mode == "----- -- --- -- --"

    assert device.get_sensor_value("input") == "Movies"
    assert device.get_sensor_value("volume") == "-20"
    assert device.get_sensor_value("mute") == "Off"
    assert device.get_sensor_value("peq") == "On"
    assert device.get_sensor_value("beq_active") == "None"
    assert device.get_sensor_value("loudness") == "Off"
    assert device.get_sensor_value("night_mode") == "Off"


def test_dirac_slot_parsing_uses_real_slots_and_handles_modes(device):
    state = real_receiver_state()
    device._state = state

    device._parse_state()
    assert device.slot_names == [
        "6 Point Tweaked",
        "10 Point Tweaked",
        "6 Point Default Support",
        "10 Point Default Support",
        "6 Point Directional",
        "10 Point Defaults",
    ]
    assert device._slot_index == {
        "6 Point Tweaked": 0,
        "10 Point Tweaked": 1,
        "6 Point Default Support": 2,
        "10 Point Default Support": 3,
        "6 Point Directional": 4,
        "10 Point Defaults": 5,
    }
    assert device.dirac_slot_name == "6 Point Directional"

    state["cal"]["diracactive"] = "bypass"
    device._parse_state()
    assert device.dirac_slot_name == "Dirac Bypass"

    state["cal"]["diracactive"] = "off"
    device._parse_state()
    assert device.dirac_slot_name == "Dirac Off"

    state["cal"]["diracactive"] = "on"
    state["cal"]["currentdiracslot"] = 99
    device._parse_state()
    assert device.dirac_slot_name == "Dirac Off"


def test_seat_shaker_preset_trim_and_mute_use_real_values(device):
    device._state = real_receiver_state()
    device._parse_state()

    assert device.ss_mute == "off"
    assert device.ss_preset == 1
    assert device.ss_trim == -3
    assert device.get_sensor_value("ss_mute") == "Off"
    assert device.get_sensor_value("ss_preset") == 1
    assert device.get_sensor_value("ss_trim") == "-3"


def test_mso_and_incremental_patch_update_real_receiver_state(device):
    async def exercise():
        device.push_update = lambda: None
        await device.handle_message("mso " + json.dumps(real_receiver_state()))
        assert device.power is False
        assert device.volume_db == -20
        assert device.current_source == "Movies"

        await device.handle_message(
            'msoupdate [{"op":"replace","path":"/powerIsOn","value":true},'
            '{"op":"replace","path":"/muted","value":true},'
            '{"op":"replace","path":"/volume","value":-30},'
            '{"op":"replace","path":"/input","value":"h2"}]'
        )
        assert device.power is True
        assert device.muted is True
        assert device.volume_db == -23
        assert device.current_source == "Streaming"

    device._loop.run_until_complete(exercise())


def test_malformed_state_sections_reset_to_safe_defaults(device):
    device._state = real_receiver_state()
    device._parse_state()

    device._state = {
        "powerIsOn": "yes",
        "muted": 1,
        "volume": float("inf"),
        "cal": [],
        "inputs": [],
        "upmix": [],
        "status": [],
        "videostat": [],
        "peq": [],
        "shaker": [],
    }
    device._parse_state()

    assert device.power is False
    assert device.muted is False
    assert device.volume_db == 0
    assert device.current_source == ""
    assert device.source_list == []
    assert device.slot_names == []
    assert device._slot_index == {}
    assert device.dirac_slot_name == "Dirac Off"
    assert device.sound_mode_display == ""
    assert device.audio_format == "none"
    assert device.output_audio_format == ""
    assert device.video_mode == "-----"

def test_msoupdate_is_atomic_and_requests_full_state_on_invalid_patch(device):
    async def exercise():
        device.push_update = lambda: None
        original = real_receiver_state()
        device._state = json.loads(json.dumps(original))
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        device.send_message = capture
        await device.handle_message(
            'msoupdate [{"op":"replace","path":"/powerIsOn","value":true},'
            '{"op":"replace","path":"/missing/key","value":1}]'
        )
        assert device._state == original
        assert device.power is False
        assert sent == ["getmso"]

    device._loop.run_until_complete(exercise())


def test_msoupdate_rejects_non_object_entries_atomically(device):
    async def exercise():
        device.push_update = lambda: None
        original = real_receiver_state()
        device._state = json.loads(json.dumps(original))
        sent = []

        async def capture(message):
            sent.append(message)
            return True

        device.send_message = capture
        await device.handle_message(
            'msoupdate [{"op":"replace","path":"/powerIsOn","value":true},null]'
        )
        assert device._state == original
        assert sent == ["getmso"]

    device._loop.run_until_complete(exercise())
