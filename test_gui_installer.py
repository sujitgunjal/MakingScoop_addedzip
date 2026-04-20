import unittest
from types import SimpleNamespace

from myscoop.gui_installer import CLICKABLE_CONTROL_TYPES, GUIInstaller


class TestableGUIInstaller(GUIInstaller):
    def __init__(self):
        super().__init__()
        self.window_titles = {}
        self.edit_windows = set()
        self.submitted = []

    def _get_window_title(self, hwnd: int) -> str:
        return self.window_titles.get(hwnd, "")

    def _window_has_edit_control(self, hwnd: int) -> bool:
        return hwnd in self.edit_windows

    def _submit_self_extract_dialog(self, hwnd: int) -> bool:
        self.submitted.append(hwnd)
        return True


class GUIInstallerSpecialCaseTests(unittest.TestCase):
    def test_clickable_types_include_radio_and_checkbox_for_option_pages(self):
        self.assertIn("RadioButton", CLICKABLE_CONTROL_TYPES)
        self.assertIn("CheckBox", CLICKABLE_CONTROL_TYPES)

    def test_self_extract_prompt_is_not_treated_as_busy(self):
        installer = TestableGUIInstaller()
        installer.window_titles[101] = "Autodesk Self-Extract"
        installer.edit_windows.add(101)

        self.assertTrue(installer._is_self_extract_prompt(101))
        self.assertFalse(installer._detect_busy_state(101))

    def test_self_extract_prompt_is_handled_once(self):
        installer = TestableGUIInstaller()
        installer.window_titles[202] = "Autodesk Self-Extract"
        installer.edit_windows.add(202)

        self.assertTrue(installer._handle_special_installer_windows(202))
        self.assertEqual(installer.submitted, [202])
        self.assertFalse(installer._handle_special_installer_windows(202))

    def test_abb_status_labels_are_not_scored_as_buttons(self):
        installer = TestableGUIInstaller()

        self.assertEqual(installer._score_button("Download and install"), 85)
        self.assertEqual(installer._score_button("License Agreement"), -1)
        self.assertEqual(
            installer._score_button("Automation Builder Legacy Installation Manager  [2.5.9.227]"),
            -1,
        )
        self.assertEqual(
            installer._score_button("Please wait while the installer initializes ..."),
            -1,
        )

    def test_busy_text_marks_installation_page_as_progress(self):
        class BusyTextInstaller(TestableGUIInstaller):
            def _has_busy_text(self, hwnd: int) -> bool:
                return hwnd == 303

            def _detect_progress_bar(self, hwnd: int) -> bool:
                return False

        installer = BusyTextInstaller()
        installer.window_titles[303] = "ABB Automation Builder 2.9.0 Build 322 - Installation Page"

        self.assertTrue(installer._detect_busy_state(303))

    def test_selection_page_does_not_use_busy_text_detection(self):
        installer = TestableGUIInstaller()

        self.assertFalse(
            installer._should_use_busy_text(
                "ABB Automation Builder 2.9.0 Build 322 - Selection Page"
            )
        )
        self.assertTrue(
            installer._should_use_busy_text(
                "ABB Automation Builder 2.9.0 Build 322 - Installation Page"
            )
        )

    def test_uninstall_mode_can_click_remove(self):
        installer = TestableGUIInstaller()
        installer.mode = "uninstall"

        self.assertEqual(installer._score_button("Remove"), 88)
        self.assertEqual(installer._score_button("Uninstall"), 88)

    def test_license_page_fallback_accepts_first_unchecked_checkbox(self):
        installer = TestableGUIInstaller()

        class FakeRect:
            def __init__(self, top):
                self.top = top

        class FakeCheckbox:
            def __init__(self):
                self.toggled = False

            def rectangle(self):
                return FakeRect(735)

            def get_toggle_state(self):
                return 0

            def toggle(self):
                self.toggled = True

        checkbox = FakeCheckbox()
        dlg = SimpleNamespace(
            descendants=lambda control_type=None: [checkbox] if control_type == "CheckBox" else []
        )

        self.assertTrue(installer._toggle_first_unchecked_option_on_license_page(dlg, 1))
        self.assertTrue(checkbox.toggled)


if __name__ == "__main__":
    unittest.main()
