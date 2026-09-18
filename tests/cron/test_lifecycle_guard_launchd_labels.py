"""The order-independent launchctl pass must recognize the fork's gateway label.

`cron/lifecycle_guard.py` has two launchctl passes. The first needs the label in the SAME
`[^\\n]*` span as the verb; the second (`_contains_launchctl_gateway_lifecycle`) is
order-independent — "verb anywhere AND label anywhere" — and exists for the shape where the
label is built in an EARLIER `;`-segment:

    label=ai.haos.gateway; launchctl bootout gui/501/$label

That second pass anchored on a literal `hermes`, so on the fork (which ships
`haos-gateway`, and whose LaunchAgent is `ai.haos.gateway`) it failed OPEN: the command
stopped the running gateway without registering as a gateway lifecycle command.

`gateway/service_names.py` exists precisely so a unit name is never a literal at a call
site, and its docstring names this guard as one that must recognize both spellings —
"or a renamed install silently stops being restarted". `hermes_cli/update_inventory.py`
already lists both `ai.haos.gateway-<profile>` and `ai.hermes.gateway-<profile>`.
"""

from cron.lifecycle_guard import contains_gateway_lifecycle_command as guard


def test_haos_label_built_in_an_earlier_segment_is_detected():
    """The exact shape the order-independent pass exists for, with the fork's label.

    Every case here is False before the fix: the label sits in its own `;`-segment, so only
    the order-independent pass can see it, and that pass only knew the `hermes` spelling.
    """
    for command in (
        "label=ai.haos.gateway; launchctl bootout gui/501/$label",
        "LABEL=ai.haos.gateway; launchctl kickstart -k gui/501/$LABEL",
        "SVC=ai.haos.gateway; launchctl stop system/$SVC",
        "unit=haos-gateway; launchctl stop $unit",
    ):
        assert guard(command) is True, command


def test_the_legacy_hermes_label_is_still_detected():
    """Widening to the fork's spelling must not drop the upstream one — pre-rename installs
    keep the `hermes` label and must stay guarded."""
    for command in (
        "label=ai.hermes.gateway; launchctl bootout gui/501/$label",
        "launchctl kickstart -k system/ai.hermes.gateway",
    ):
        assert guard(command) is True, command


def test_a_near_miss_label_is_not_flagged():
    """`\\b` keeps a longer word merely ENDING in `haos` out, so the wider alternation does
    not swallow unrelated labels."""
    for command in (
        "label=ai.chaos.gateway; launchctl bootout gui/501/$label",
        "label=ai.myhaos.gateway; launchctl bootout gui/501/$label",
    ):
        assert guard(command) is False, command


def test_a_lifecycle_verb_without_a_gateway_label_is_not_flagged():
    """The pass is a conjunction: a verb alone, or a label alone, is not a gateway lifecycle
    command. Guards against widening one half and silently dropping the other."""
    for command in (
        "label=ai.haos.gateway; echo $label",
        "launchctl bootout gui/501/ai.unrelated.agent",
    ):
        assert guard(command) is False, command
