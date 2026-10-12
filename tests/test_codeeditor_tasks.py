"""
Copyright (c) Meta Platforms, Inc. and affiliates.
All rights reserved.
This source code is licensed under the license found in the
LICENSE file in the root directory of this source tree.
"""

"""
Unit tests for the Code Editor EditFileTask reward logic.
"""

from open_apps.tasks.tasks import EditFileTask
from open_apps.tasks import load_task


def make_state(files: dict[str, str]) -> dict:
    """Build a cross-app state whose ``codeeditor`` value mirrors the file
    tree returned by the ``/codeeditor_all`` endpoint.

    Args:
        files: Mapping of file ``path`` (relative to the editor root) to
            its text content.
    """
    children = []
    for path, content in files.items():
        children.append(
            {
                "type": "file",
                "name": path.split("/")[-1],
                "path": path,
                "content": content,
            }
        )
    return {
        "codeeditor": {"type": "folder", "name": "codeeditor", "children": children}
    }


SCRIPT_INITIAL = (
    "# Basic PyTorch tensor operation\n"
    "import torch\n"
    "\n"
    "x = torch.randn(2, 3, 4)\n"
)


class TestEditFileTask:
    def test_correct_file_and_contents(self):
        """Editing the right file with the required fragment earns reward."""
        initial_state = make_state({"script.py": SCRIPT_INITIAL})
        current_state = make_state(
            {"script.py": SCRIPT_INITIAL + "# Reviewed by Bob\n"}
        )
        task = EditFileTask(
            goal="Add a review comment to script.py",
            file_path="script.py",
            required_fragment="# Reviewed by Bob",
        )
        assert task.check_if_task_is_complete(initial_state, current_state)

    def test_correct_contents_in_wrong_file(self):
        """The required content in a different file does not earn reward."""
        initial_state = make_state(
            {"script.py": SCRIPT_INITIAL, "other.py": "x = 1\n"}
        )
        # The fragment ended up in other.py, not script.py.
        current_state = make_state(
            {
                "script.py": SCRIPT_INITIAL,
                "other.py": "x = 1\n# Reviewed by Bob\n",
            }
        )
        task = EditFileTask(
            goal="Add a review comment to script.py",
            file_path="script.py",
            required_fragment="# Reviewed by Bob",
        )
        assert not task.check_if_task_is_complete(initial_state, current_state)

    def test_unchanged_contents(self):
        """A file whose contents did not change earns no reward, even if the
        required fragment already happens to be present."""
        already_has_fragment = SCRIPT_INITIAL + "# Reviewed by Bob\n"
        initial_state = make_state({"script.py": already_has_fragment})
        current_state = make_state({"script.py": already_has_fragment})
        task = EditFileTask(
            goal="Add a review comment to script.py",
            file_path="script.py",
            required_fragment="# Reviewed by Bob",
        )
        assert not task.check_if_task_is_complete(initial_state, current_state)

    def test_missing_fragment_fails(self):
        """Saving the file without the required fragment earns no reward."""
        initial_state = make_state({"script.py": SCRIPT_INITIAL})
        current_state = make_state({"script.py": SCRIPT_INITIAL + "print(x)\n"})
        task = EditFileTask(
            goal="Add a review comment to script.py",
            file_path="script.py",
            required_fragment="# Reviewed by Bob",
        )
        assert not task.check_if_task_is_complete(initial_state, current_state)

    def test_expected_content_full_match(self):
        """expected_content requires a whitespace-normalized full-file match."""
        initial_state = make_state({"notes.txt": "old\n"})
        current_state = make_state({"notes.txt": "TODO: refactor\n"})
        task = EditFileTask(
            goal="Set the contents of notes.txt",
            file_path="notes.txt",
            expected_content="TODO: refactor",
        )
        assert task.check_if_task_is_complete(initial_state, current_state)

    def test_expected_content_partial_does_not_match(self):
        """A partial match must fail when expected_content is a full match."""
        initial_state = make_state({"notes.txt": "old\n"})
        current_state = make_state(
            {"notes.txt": "TODO: refactor and more stuff\n"}
        )
        task = EditFileTask(
            goal="Set the contents of notes.txt",
            file_path="notes.txt",
            expected_content="TODO: refactor",
        )
        assert not task.check_if_task_is_complete(initial_state, current_state)

    def test_create_new_file(self):
        """Creating a new file that did not exist initially earns reward."""
        initial_state = make_state({"script.py": SCRIPT_INITIAL})
        current_state = make_state(
            {"script.py": SCRIPT_INITIAL, "notes.txt": "TODO: refactor\n"}
        )
        task = EditFileTask(
            goal="Create notes.txt",
            file_path="notes.txt",
            expected_content="TODO: refactor",
        )
        assert task.check_if_task_is_complete(initial_state, current_state)

    def test_target_file_absent(self):
        """If the target file does not exist, the task is incomplete."""
        initial_state = make_state({"script.py": SCRIPT_INITIAL})
        current_state = make_state({"script.py": SCRIPT_INITIAL})
        task = EditFileTask(
            goal="Create notes.txt",
            file_path="notes.txt",
            expected_content="TODO: refactor",
        )
        assert not task.check_if_task_is_complete(initial_state, current_state)

    def test_nested_file_path(self):
        """Files nested inside folders are located by their relative path."""
        nested = {
            "codeeditor": {
                "type": "folder",
                "name": "codeeditor",
                "children": [
                    {
                        "type": "folder",
                        "name": "developing",
                        "children": [
                            {
                                "type": "file",
                                "name": "simple_python.py",
                                "path": "developing/simple_python.py",
                                "content": '# beginner\nprint("Hello, World!")\n# done\n',
                            }
                        ],
                    }
                ],
            }
        }
        initial = {
            "codeeditor": {
                "type": "folder",
                "name": "codeeditor",
                "children": [
                    {
                        "type": "folder",
                        "name": "developing",
                        "children": [
                            {
                                "type": "file",
                                "name": "simple_python.py",
                                "path": "developing/simple_python.py",
                                "content": '# beginner\nprint("Hello, World!")\n',
                            }
                        ],
                    }
                ],
            }
        }
        task = EditFileTask(
            goal="Add a comment to the nested file",
            file_path="developing/simple_python.py",
            required_fragment="# done",
        )
        assert task.check_if_task_is_complete(initial, nested)


class TestEditFileTaskInstantiation:
    def test_edit_task_instantiation(self):
        task = load_task("edit_script_add_header_comment")
        assert isinstance(task, EditFileTask)
        assert task.file_path == "script.py"
        assert task.required_fragment == "# Reviewed by Bob"

    def test_create_task_instantiation(self):
        task = load_task("create_notes_file_in_code_editor")
        assert isinstance(task, EditFileTask)
        assert task.file_path == "notes.txt"
        assert task.expected_content == "TODO: refactor"
