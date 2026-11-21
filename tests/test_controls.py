import asyncio

from intg_monoprice_htp1.config import HTP1Config
from intg_monoprice_htp1.device import HTP1Device


def make_device():
    return HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))


def test_power_mute_and_volume_commands_send_expected_patches():
    async def exercise():
        device = make_device()
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        assert await device.turn_on()
        assert sent[-1] == [{"op": "replace", "path": "/powerIsOn", "value": True}]
        assert await device.turn_off()
        assert sent[-1] == [{"op": "replace", "path": "/powerIsOn", "value": False}]

        device.power = False
        assert await device.toggle_power()
        assert sent[-1][0]["value"] is True
        device.power = True
        assert await device.toggle_power()
        assert sent[-1][0]["value"] is False

        assert await device.mute_toggle(True)
        assert sent[-1] == [{"op": "replace", "path": "/muted", "value": True}]
        assert await device.mute_toggle(False)
        assert sent[-1][0]["value"] is False

    asyncio.run(exercise())


def test_volume_up_down_respect_receiver_limits():
    async def exercise():
        device = make_device()
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device._state = {"volume": 11}
        device.vph = 12
        assert await device.volume_up()
        assert sent[-1][0]["value"] == 12
        device._state["volume"] = 12
        before = len(sent)
        assert await device.volume_up()
        assert len(sent) == before

        device._state = {"volume": -79}
        device.vpl = -80
        device.zp = 0
        assert await device.volume_down()
        assert sent[-1][0]["value"] == -80
        device._state["volume"] = -80
        before = len(sent)
        assert await device.volume_down()
        assert len(sent) == before

    asyncio.run(exercise())


def test_volume_slider_clamps_range_and_limits_large_jumps():
    async def exercise():
        device = make_device()
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device._state = {"volume": -30, "cal": {}}
        device.vpl = -80
        device.vph = 12

        assert await device.set_volume_level(0.5)
        assert sent[-1][0]["value"] == -34  # midpoint target is within the five dB safety window
        assert await device.set_volume_level(1.0)
        assert sent[-1][0]["value"] == -25
        assert await device.set_volume_level(0.0)
        assert sent[-1][0]["value"] == -35

        device.vph = -90
        before = len(sent)
        assert not await device.set_volume_level(0.5)
        assert len(sent) == before

        device._state = None
        assert not await device.set_volume_level(0.5)

    asyncio.run(exercise())


def test_input_mode_and_dirac_selection_send_expected_native_values():
    async def exercise():
        device = make_device()
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device._state = {
            "inputs": {
                "hdmi1": {"label": "Apple TV"},
                "hdmi2": {"label": "Game"},
            },
            "shaker": {},
        }
        assert await device.select_source("Game")
        assert sent[-1] == [{"op": "replace", "path": "/input", "value": "hdmi2"}]
        before = len(sent)
        assert not await device.select_source("Unknown")
        assert len(sent) == before

        assert await device.select_sound_mode("DOLBY SURROUND")
        assert sent[-1] == [{"op": "replace", "path": "/upmix/select", "value": "dolby"}]
        before = len(sent)
        assert not await device.select_sound_mode("Unknown")
        assert len(sent) == before

        device._slot_index = {"Movie": 2}
        assert await device.select_calibration("Movie")
        assert sent[-1] == [{"op": "replace", "path": "/cal/currentdiracslot", "value": 2}]
        before = len(sent)
        assert not await device.select_calibration("Missing")
        assert len(sent) == before

    asyncio.run(exercise())


def test_seat_shaker_controls_validate_and_send_native_values():
    async def exercise():
        device = make_device()
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device._state = {"shaker": {}}
        assert await device.ss_mute_toggle(True)
        assert sent[-1][0]["value"] == "on"
        assert await device.ss_mute_toggle(False)
        assert sent[-1][0]["value"] == "off"
        assert await device.set_ss_trim(5)
        assert sent[-1] == [{"op": "replace", "path": "/shaker/trim", "value": 5}]

        assert await device.select_ss_preset(0)
        assert sent[-1][0]["value"] == 0
        assert await device.select_ss_preset(5)
        assert sent[-1][0]["value"] == 5
        before = len(sent)
        assert not await device.select_ss_preset(-1)
        assert not await device.select_ss_preset(6)
        assert len(sent) == before

    asyncio.run(exercise())

def test_device_command_boundaries_reject_invalid_arguments():
    async def exercise():
        device = make_device()
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device.vpl = -80
        device.vph = 12
        for value in (True, 12.5, float("nan"), float("inf"), -81, 13):
            assert not await device.set_volume(value)
        for value in (True, float("nan"), float("inf"), "0"):
            assert not await device.set_volume_level(value)
        for value in (0, 1, "yes", None):
            assert not await device.mute_toggle(value)
            assert not await device.ss_mute_toggle(value)
        for value in (True, 1.5, "5", None):
            assert not await device.set_ss_trim(value)
        for value in (True, 1.5, "1"):
            assert not await device.select_ss_preset(value)
        assert sent == []

    asyncio.run(exercise())
