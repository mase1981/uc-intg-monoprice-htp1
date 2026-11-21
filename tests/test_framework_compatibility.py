def test_pinned_ucapi_and_framework_imports():
    # Smoke-test the actual runtime dependencies and the integration's
    # framework-backed classes, not just the standalone catalogue builder.
    from ucapi import StatusCodes
    from ucapi_framework import BaseIntegrationDriver, WebSocketDevice
    from intg_monoprice_htp1.device import HTP1Device
    from intg_monoprice_htp1.driver import HTP1Driver
    from intg_monoprice_htp1.media_player import HTP1MediaPlayer

    assert StatusCodes.OK is not None
    assert issubclass(HTP1Device, WebSocketDevice)
    assert issubclass(HTP1Driver, BaseIntegrationDriver)
    assert HTP1MediaPlayer is not None


def test_driver_waits_for_receiver_connection_before_registry():
    import asyncio

    async def check():
        from intg_monoprice_htp1.driver import HTP1Driver

        driver = HTP1Driver()
        # ucapi-framework has no public accessor for this constructor option.
        assert driver._require_connection_before_registry is True

    asyncio.run(check())


def test_connection_loss_clears_stale_state_and_notifies_entities():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._is_connected = True
        device._state = {"powerIsOn": True}
        device._sensor_data = {"volume": "-20", "input": "HDMI 1"}
        device._state_ready.set()
        updates = []
        device.push_update = lambda: updates.append(True)

        device._on_connection_lost()

        assert device.is_connected is False
        assert device._state is None
        assert device._sensor_data == {}
        assert not device._state_ready.is_set()
        assert updates == [True]

    asyncio.run(exercise())


def test_invalid_full_state_payload_is_ignored():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        await device.handle_message("mso []")
        assert device._state is None
        assert not device._state_ready.is_set()

    asyncio.run(exercise())


def test_malformed_patch_entries_reject_entire_batch_and_request_full_state():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = {"powerIsOn": False}
        updates = []
        sent = []
        device.push_update = lambda: updates.append(True)

        async def capture(message):
            sent.append(message)
            return True

        device.send_message = capture
        await device.handle_message(
            'msoupdate [42, {"op": "replace", "path": null, "value": true}, '
            '{"op": "replace", "path": "/powerIsOn", "value": true}]'
        )

        # Invalid patches are atomic: don't apply the valid trailing operation
        # to a potentially incomplete state; request a complete receiver snapshot.
        assert device._state == {"powerIsOn": False}
        assert updates == []
        assert sent == ["getmso"]

    asyncio.run(exercise())


def test_remote_repeat_and_delay_validation():
    from intg_monoprice_htp1.remote import HTP1Remote

    assert HTP1Remote._repeat_delay(None) == (1, 0.0)
    assert HTP1Remote._repeat_delay({"repeat": 3, "delay": 250}) == (3, 0.25)
    assert HTP1Remote._repeat_delay({"repeat": 0}) is None
    assert HTP1Remote._repeat_delay({"repeat": 21}) is None
    assert HTP1Remote._repeat_delay({"repeat": True}) is None
    assert HTP1Remote._repeat_delay({"delay": -1}) is None
    assert HTP1Remote._repeat_delay({"delay": 5001}) is None
    assert HTP1Remote._repeat_delay({"delay": "100"}) is None


def test_device_command_validation_and_requested_mute_state():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture

        assert await device.mute_toggle(True)
        assert sent[-1][0]["value"] is True

        assert await device.ss_mute_toggle(False)
        assert sent[-1][0]["value"] == "off"
        assert await device.ss_mute_toggle(True)
        assert sent[-1][0]["value"] == "on"

        assert await device.select_sound_mode("DIRECT")
        assert sent[-1][0]["value"] == "off"
        before = len(sent)
        assert not await device.select_sound_mode("not-a-mode")
        assert len(sent) == before

        device._state = {"shaker": {}}
        assert await device.select_ss_preset(0)
        assert await device.select_ss_preset(5)
        before = len(sent)
        assert not await device.select_ss_preset(6)
        assert not await device.select_ss_preset(-1)
        assert len(sent) == before

        assert not await device.send_http_command("not-hex")
        assert not await device.send_http_command("123")
        assert not await device.send_http_command("12345")

    asyncio.run(exercise())


def test_remote_rejects_unadvertised_commands_as_bad_request():
    from intg_monoprice_htp1.remote import HTP1Remote
    from ucapi import StatusCodes

    remote = HTP1Remote.__new__(HTP1Remote)
    remote.id = "remote.test"

    async def exercise():
        assert await remote._send_single("not-a-command") == StatusCodes.BAD_REQUEST

    import asyncio
    asyncio.run(exercise())



def test_select_commands_validate_advertised_options_and_connection():
    import asyncio
    from intg_monoprice_htp1.selector import HTP1Select
    from ucapi import StatusCodes
    from ucapi.select import Commands

    class Device:
        log_id = "test"
        is_connected = True

    device = Device()
    calls = []

    async def command(option):
        calls.append(option)
        return True

    # Avoid framework registration; exercise the command boundary directly.
    select = HTP1Select.__new__(HTP1Select)
    select._device = device
    select._get_options = lambda: ["Option A", "Option B"]
    select._command_fn = command
    select.name = "Test Select"

    async def exercise():
        assert await select._handle_command(
            select, Commands.SELECT_OPTION, {"option": "Option B"}
        ) == StatusCodes.OK
        assert calls == ["Option B"]
        assert await select._handle_command(
            select, Commands.SELECT_OPTION, {"option": "Not Advertised"}
        ) == StatusCodes.BAD_REQUEST
        assert await select._handle_command(
            select, Commands.SELECT_OPTION, {"option": 1}
        ) == StatusCodes.BAD_REQUEST
        assert calls == ["Option B"]

        device.is_connected = False
        assert await select._handle_command(
            select, Commands.SELECT_OPTION, {"option": "Option A"}
        ) == StatusCodes.SERVER_ERROR
        assert calls == ["Option B"]

    asyncio.run(exercise())



def test_remote_rejects_malformed_and_excessive_command_sequences():
    import asyncio
    from intg_monoprice_htp1.remote import HTP1Remote
    from ucapi import StatusCodes
    from ucapi.remote import Commands

    remote = HTP1Remote.__new__(HTP1Remote)
    remote.id = "remote.test"
    remote._device = object()
    dispatched = []

    async def capture(commands, repeat, delay):
        dispatched.append((commands, repeat, delay))
        return StatusCodes.OK

    remote._send_commands = capture

    async def exercise():
        assert await remote._handle_command(
            remote, Commands.SEND_CMD, ["not", "a", "mapping"]
        ) == StatusCodes.BAD_REQUEST
        assert await remote._handle_command(
            remote, Commands.SEND_CMD_SEQUENCE, ["not", "a", "mapping"]
        ) == StatusCodes.BAD_REQUEST
        assert await remote._handle_command(
            remote,
            Commands.SEND_CMD_SEQUENCE,
            {"sequence": ["POWER"] * 6, "repeat": 20},
        ) == StatusCodes.BAD_REQUEST
        assert dispatched == []

        assert await remote._handle_command(
            remote,
            Commands.SEND_CMD_SEQUENCE,
            {"sequence": ["POWER"] * 5, "repeat": 20},
        ) == StatusCodes.OK
        assert dispatched == [(["POWER"] * 5, 20, 0.0)]

    asyncio.run(exercise())



def test_media_player_rejects_malformed_command_parameters():
    import asyncio
    from intg_monoprice_htp1.media_player import HTP1MediaPlayer
    from ucapi import StatusCodes
    from ucapi.media_player import Commands

    player = HTP1MediaPlayer.__new__(HTP1MediaPlayer)
    player.id = "media_player.test"
    player._device = object()

    async def exercise():
        assert await player._handle_command(
            player, Commands.VOLUME, ["not", "a", "mapping"]
        ) == StatusCodes.BAD_REQUEST
        assert await player._handle_command(
            player, Commands.SELECT_SOURCE, ["not", "a", "mapping"]
        ) == StatusCodes.BAD_REQUEST
        assert await player._handle_command(
            player, Commands.SELECT_SOUND_MODE, ["not", "a", "mapping"]
        ) == StatusCodes.BAD_REQUEST
        assert await player._handle_command(
            player, Commands.PLAY_MEDIA, ["not", "a", "mapping"]
        ) == StatusCodes.BAD_REQUEST
        assert await player._handle_play_media({"media_id": 123}) == StatusCodes.BAD_REQUEST
        assert await player._handle_play_media({"media_id": "   "}) == StatusCodes.BAD_REQUEST

    asyncio.run(exercise())



def test_remote_dispatches_direct_simple_commands_from_ui_and_button_mappings():
    import asyncio
    from intg_monoprice_htp1.remote import HTP1Remote, HTTP_COMMANDS, SIMPLE_COMMANDS
    from ucapi import StatusCodes

    class Device:
        power = False
        muted = False
        ss_mute = "off"
        ss_trim = 0

        def __init__(self):
            self.http_commands = []
            self.menu_commands = []

        async def send_http_command(self, command):
            self.http_commands.append(command)
            return True

        async def send_command(self, command):
            self.menu_commands.append(command)
            return True

    assert "Mute Toggle" in SIMPLE_COMMANDS
    device = Device()
    remote = HTP1Remote.__new__(HTP1Remote)
    remote.id = "remote.test"
    remote._device = device

    async def exercise():
        for command in ("Mute Toggle", "Dialog Up", "User Input 1"):
            assert await remote._handle_command(remote, command, None) == StatusCodes.OK
        assert device.http_commands == [
            HTTP_COMMANDS["Mute Toggle"],
            HTTP_COMMANDS["Dialog Up"],
            HTTP_COMMANDS["User Input 1"],
        ]

        assert await remote._handle_command(
            remote, "send_avcui: hpe", None
        ) == StatusCodes.OK
        assert device.menu_commands == ["send_avcui: hpe"]

    asyncio.run(exercise())



def test_malformed_nested_state_sections_do_not_crash_state_parsing():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = {
            "powerIsOn": "yes",
            "muted": [],
            "volume": "very loud",
            "cal": [],
            "shaker": [],
            "inputs": [],
            "status": [],
            "upmix": [],
            "videostat": [],
            "peq": [],
            "speakers": [],
        }

        device._parse_state()

        assert device.power is False
        assert device.muted is False
        assert device.source_list == []
        assert device.current_source == ""
        assert device.slot_names == []
        assert device._slot_index == {}
        assert device.video_mode == "-----"
        assert device.audio_format == "none"
        assert device.output_audio_format == ""
        assert device.sound_mode_display == ""
        assert device.dirac_slot_name == "Dirac Off"
        assert device._sensor_data["peq"] == "Off"

    asyncio.run(exercise())


def test_beq_operations_reject_malformed_nested_state_and_filter_payloads():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device._state = {"peq": {"location": "pre", "slots": [None, {"channels": []}]}, "speakers": {"groups": []}}
        assert device._get_sub_channels() == ["sub1"]
        assert device._find_empty_peq_slot(0, "sub1") is None
        assert await device.clear_beq()
        assert sent == []

        device._state = {
            "peq": {"location": "post", "slots": [{}], "beqActive": "Active"},
            "speakers": {"groups": []},
        }
        assert await device.clear_beq()
        assert sent == [[{"op": "remove", "path": "/peq/beqActive"}]]
        sent.clear()

        device._state = {"shaker": {}}
        assert not await device.load_beq("Invalid", [{"freq": "bad", "gain": 1, "q": 1}])
        assert not await device.load_beq("Invalid", [{"freq": 100, "gain": float("nan"), "q": 1}])
        assert not await device.load_beq("Invalid", [{"type": "Unknown", "freq": 100, "gain": 1, "q": 1}])
        assert sent == []

    asyncio.run(exercise())



def test_json_patch_validates_operations_paths_and_array_bounds():
    import pytest
    from intg_monoprice_htp1.device import apply_json_patch

    state = {"a/b": {"items": [1, 2]}, "tilde~key": True}
    apply_json_patch(state, "replace", "/a~1b/items/1", 3)
    apply_json_patch(state, "add", "/a~1b/items/-", 4)
    apply_json_patch(state, "remove", "/tilde~0key")

    assert state == {"a/b": {"items": [1, 3, 4]}}

    for operation, path in (
        ("move", "/a~1b/items/0"),
        ("replace", "/a~1b/items/8"),
        ("add", "/a~1b/items/8"),
        ("replace", "/a~1b/items/-"),
        ("replace", "/a~2b"),
    ):
        with pytest.raises((ValueError, IndexError)):
            apply_json_patch(state, operation, path, 99)

    with pytest.raises(KeyError):
        apply_json_patch(state, "replace", "/missing", True)



def test_source_selection_handles_malformed_input_state():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        device._state = {"inputs": []}
        assert not await device.select_source("HDMI 1")
        device._state = {"inputs": {"input1": {"label": "HDMI 1"}}}
        assert await device.select_source("HDMI 1")
        assert sent[-1][0]["value"] == "input1"
        assert not await device.select_source(123)

    asyncio.run(exercise())



def _make_peq_test_state(
    lfe_free=(), lfe_beq=(), sub1_free=(), sub1_beq=(), sub2_free=(), sub2_beq=(),
    lfe_key="LFE", location="pre", sub1_present=True, sub2_present=True,
):
    slots = []
    for index in range(16):
        channels = {}
        for channel, free, beq in (
            (lfe_key, lfe_free, lfe_beq),
            ("sub1", sub1_free, sub1_beq),
            ("sub2", sub2_free, sub2_beq),
        ):
            channels[channel] = {
                "Fc": 100,
                "gaindB": 0 if index in free else -5,
                "Q": 1,
                "FilterType": 0,
            }
            if index in beq:
                channels[channel]["beq"] = True
        slots.append({"channels": channels})
    return {
        "peq": {"location": location, "peqsw": False, "slots": slots},
        "speakers": {"groups": {
            "sub1": {"present": sub1_present},
            "sub2": {"present": sub2_present},
        }},
    }


def test_beq_pre_targets_sub1_only_and_cleans_legacy_tags_on_all_channels():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = _make_peq_test_state(
            sub1_free=(0, 1, 2), lfe_beq=(3,), sub1_beq=(4,), sub2_beq=(6,),
        )
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        assert await device.load_beq("Test BEQ", [
            {"type": "PeakingEQ", "freq": 30, "gain": 4, "q": 1},
            {"type": "LowShelf", "freq": 40, "gain": 3, "q": 0.7},
            {"type": "HighShelf", "freq": 60, "gain": -2, "q": 1.2},
        ])

        assert len(sent) == 1
        operations = sent[0]
        for channel, slot in (("LFE", 3), ("sub1", 4), ("sub2", 6)):
            assert {"op": "remove", "path": f"/peq/slots/{slot}/channels/{channel}/beq"} in operations

        for slot, freq in zip((0, 1, 2), (30, 40, 60)):
            assert {
                "op": "replace",
                "path": f"/peq/slots/{slot}/channels/sub1/Fc",
                "value": freq,
            } in operations

        # Pre placement writes new filters to Sub1 only; stale tags are still
        # removed from every channel so switching PEQ placement cannot strand them.
        assert not any(
            op["op"] == "add" and op["path"].startswith("/peq/slots/")
            and "/channels/sub2/" in op["path"] and op["path"].endswith("/beq")
            for op in operations
        )
        for attribute, value in (("Fc", 100), ("gaindB", 0), ("Q", 1), ("FilterType", 0)):
            assert {
                "op": "replace",
                "path": f"/peq/slots/6/channels/sub2/{attribute}",
                "value": value,
            } in operations
        assert operations[-2] == {"op": "add", "path": "/peq/beqActive", "value": "Test BEQ"}
        assert operations[-1] == {"op": "replace", "path": "/peq/peqsw", "value": True}

    asyncio.run(exercise())


def test_beq_pre_uses_sub1_even_when_peq_channels_have_no_lfe_key():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = {
            "peq": {
                "location": "pre",
                "peqsw": False,
                "slots": [{"channels": {"sub1": {"Fc": 100, "gaindB": 0, "Q": 1, "FilterType": 0}}}],
            },
            "speakers": {"groups": {"sub1": {"present": True}}},
        }
        assert device._get_sub_channels() == ["sub1"]

    asyncio.run(exercise())


def test_beq_non_pre_targets_all_present_subwoofers():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = _make_peq_test_state(
            sub1_free=(0,), sub2_free=(0,), location="post",
        )
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        assert await device.load_beq("Post PEQ", [
            {"type": "PeakingEQ", "freq": 30, "gain": 4, "q": 1},
        ])
        operations = sent[0]
        for channel in ("sub1", "sub2"):
            assert {
                "op": "replace",
                "path": f"/peq/slots/0/channels/{channel}/Fc",
                "value": 30,
            } in operations
            assert {
                "op": "add",
                "path": f"/peq/slots/0/channels/{channel}/beq",
                "value": True,
            } in operations
        assert not any("/channels/LFE/" in op["path"] for op in operations)

    asyncio.run(exercise())


def test_beq_capacity_failure_preserves_current_filter_without_sending():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = _make_peq_test_state(sub1_beq=(0,), sub2_beq=(0,))
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        assert not await device.load_beq("Too Many", [
            {"freq": 30, "gain": 4, "q": 1},
            {"freq": 40, "gain": 3, "q": 1},
        ])
        assert sent == []

    asyncio.run(exercise())


def test_beq_clamps_imported_values_and_rejects_malformed_values_and_more_than_16_bands():
    import asyncio
    from intg_monoprice_htp1.config import HTP1Config
    from intg_monoprice_htp1.device import HTP1Device

    async def exercise():
        device = HTP1Device(HTP1Config("test", "Test HTP-1", "127.0.0.1"))
        device._state = _make_peq_test_state(sub1_free=range(16))
        sent = []

        async def capture(operations):
            sent.append(operations)
            return True

        device._send_transaction = capture
        malformed_filters = [
            {"type": [], "freq": 100, "gain": 0, "q": 1},
            {"freq": True, "gain": 0, "q": 1},
            {"freq": 10**10000, "gain": 0, "q": 1},
        ]
        for filt in malformed_filters:
            assert not await device.load_beq("Malformed", [filt])
        assert sent == []

        assert await device.load_beq("Normalized", [
            {"type": "PeakingEQ", "freq": 9, "gain": 25.7, "q": 11},
        ])
        operations = sent.pop()
        for attribute, value in (("Fc", 10), ("gaindB", 20), ("Q", 10)):
            assert {
                "op": "replace",
                "path": f"/peq/slots/0/channels/sub1/{attribute}",
                "value": value,
            } in operations
        assert not any("/channels/sub2/" in op["path"] for op in operations if op["op"] in ("add", "replace"))

        assert not await device.load_beq("Too many", [{"freq": 100, "gain": 1, "q": 1}] * 17)
        assert sent == []

    asyncio.run(exercise())



def test_remote_dispatch_routes_simple_commands_to_expected_device_methods():
    import asyncio
    from intg_monoprice_htp1.remote import HTP1Remote, HTTP_COMMANDS
    from ucapi import StatusCodes

    class Device:
        power = False
        muted = False
        ss_mute = "off"
        ss_trim = 4

        def __init__(self):
            self.calls = []

        async def send_http_command(self, value):
            self.calls.append(("http", value))
            return True

        async def send_command(self, value):
            self.calls.append(("command", value))
            return True

        async def ss_mute_toggle(self, value):
            self.calls.append(("ss_mute", value))
            return True

        async def set_ss_trim(self, value):
            self.calls.append(("trim", value))
            return True

        async def select_ss_preset(self, value):
            self.calls.append(("preset", value))
            return True

    device = Device()
    remote = HTP1Remote.__new__(HTP1Remote)
    remote._device = device
    remote.id = "remote.test"

    async def exercise():
        assert await remote._handle_command(None, "Mode Native", None) == StatusCodes.OK
        assert await remote._handle_command(None, "Info", None) == StatusCodes.OK
        assert await remote._handle_command(None, "Seat Shaker Mute Toggle", None) == StatusCodes.OK
        assert await remote._handle_command(None, "Seat Shaker Trim +1", None) == StatusCodes.OK
        assert await remote._handle_command(None, "Seat Shaker Preset 6", None) == StatusCodes.OK
        assert device.calls == [
            ("http", HTTP_COMMANDS["Mode Native"]),
            ("http", HTTP_COMMANDS["Info"]),
            ("ss_mute", True),
            ("trim", 5),
            ("preset", 5),
        ]

    asyncio.run(exercise())


def test_remote_sequence_stops_after_first_failed_command():
    import asyncio
    from intg_monoprice_htp1.remote import HTP1Remote
    from ucapi import StatusCodes
    from ucapi.remote import Commands

    class Device:
        def __init__(self):
            self.calls = []

        async def send_http_command(self, value):
            self.calls.append(value)
            return value != "0bf4"

    from intg_monoprice_htp1.remote import HTTP_COMMANDS
    device = Device()
    remote = HTP1Remote.__new__(HTP1Remote)
    remote._device = device
    remote.id = "remote.test"

    async def exercise():
        status = await remote._handle_command(
            None, Commands.SEND_CMD_SEQUENCE,
            {"sequence": ["VOLUME_DOWN", "VOLUME_UP", "Info"]},
        )
        assert status == StatusCodes.SERVER_ERROR
        assert device.calls == [
            HTTP_COMMANDS["VOLUME_DOWN"],
            HTTP_COMMANDS["VOLUME_UP"],
        ]

    asyncio.run(exercise())


def test_remote_command_handlers_reject_malformed_and_unsupported_requests():
    import asyncio
    from intg_monoprice_htp1.remote import HTP1Remote
    from ucapi import StatusCodes
    from ucapi.remote import Commands

    remote = HTP1Remote.__new__(HTP1Remote)
    remote.id = "remote.test"

    async def exercise():
        assert await remote._handle_command(None, Commands.SEND_CMD, None) == StatusCodes.BAD_REQUEST
        assert await remote._handle_command(None, Commands.SEND_CMD, {"command": 7}) == StatusCodes.BAD_REQUEST
        assert await remote._handle_command(None, Commands.SEND_CMD_SEQUENCE, {"sequence": []}) == StatusCodes.BAD_REQUEST
        assert await remote._handle_command(None, Commands.SEND_CMD_SEQUENCE, {"sequence": ["Info"] * 51}) == StatusCodes.BAD_REQUEST
        assert await remote._handle_command(None, "not-supported", None) == StatusCodes.NOT_IMPLEMENTED

    asyncio.run(exercise())
