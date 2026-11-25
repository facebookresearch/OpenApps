import copy

import pytest

from open_apps.tasks.tasks import AppStateComparison, CompositeTask, SendMessageTask

HANDLE = "+15550100002"


def scenario(count=2):
    initial = {"todo": [], "calendar": [], "map": [], "messenger": [
        {"handle": HANDLE, "messages": [["earlier reply", HANDLE, "before"]]}
    ]}
    subtasks = [SendMessageTask(goal="send", to=HANDLE, message=f"Requested message {n}")
                for n in range(count)]
    task = CompositeTask(goal="send all messages", subtasks=subtasks)
    observed = copy.deepcopy(initial)
    for subtask in subtasks:
        observed = subtask.get_target_state(observed)
        observed["messenger"][0]["messages"].append(["automatic reply", HANDLE, "now"])
    return task, initial, observed


@pytest.mark.parametrize("count", [1, 2, 3])
def test_each_send_can_have_an_interleaved_reply(count):
    task, initial, observed = scenario(count)
    original = copy.deepcopy(observed)
    assert task.check_if_task_is_complete(initial, observed)
    assert observed == original


def test_extra_outgoing_message_is_not_ignored_as_a_reply():
    task, initial, observed = scenario()
    observed["messenger"][0]["messages"].append(["unsolicited outgoing", "you", "now"])
    assert not task.check_if_task_is_complete(initial, observed)


def test_reply_with_requested_text_does_not_replace_missing_send():
    task, initial, observed = scenario()
    messages = observed["messenger"][0]["messages"]
    del messages[-2:]  # Remove the second outgoing message and its reply.
    messages[-1][0] = "Requested message 1"
    assert not task.check_if_task_is_complete(initial, observed)


def test_extra_incoming_replies_exceed_the_send_budget():
    task, initial, observed = scenario()
    observed["messenger"][0]["messages"].append(["extra incoming", HANDLE, "now"])
    assert not task.check_if_task_is_complete(initial, observed)


def test_repeated_comparison_preserves_interleaved_reply_handling():
    task, initial, observed = scenario()
    comparison = AppStateComparison(task.get_target_state(initial), observed, reply_contacts={HANDLE: 2})
    assert comparison.compare()
    assert comparison.compare()
