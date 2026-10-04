from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from presenter_focus.focus_area_manager import FocusAreaManager
from presenter_focus.main_window import MainWindow, OBJECTS, PROPERTIES, SLIDES


class _TextStateAdapter:
    def capture(self, widget: QWidget) -> int:
        return widget.cursorPosition()  # type: ignore[attr-defined]

    def restore(self, widget: QWidget, state: int) -> None:
        widget.setCursorPosition(state)  # type: ignore[attr-defined]


class FocusAreaManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def key(self, widget: QWidget, key: Qt.Key, modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
        QTest.keyClick(widget, key, modifier)
        self.app.processEvents()

    def test_f6_cycles_through_three_real_areas(self) -> None:
        self.window.slide_list.setFocus()
        self.key(self.window.slide_list, Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), self.window.object_tree)
        self.key(self.window.object_tree, Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), self.window.property_fields["name"])
        self.key(self.window.property_fields["name"], Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), self.window.slide_list)

    def test_shift_f6_moves_in_reverse_and_wraps(self) -> None:
        self.window.slide_list.setFocus()
        self.key(self.window.slide_list, Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
        self.assertIs(self.window.focusWidget(), self.window.property_fields["name"])
        self.key(self.window.property_fields["name"], Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
        self.assertIs(self.window.focusWidget(), self.window.object_tree)
        self.key(self.window.object_tree, Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
        self.assertIs(self.window.focusWidget(), self.window.slide_list)

    def test_selection_is_independent_from_focus_and_f6(self) -> None:
        self.window.slide_list.setFocus()
        self.window.object_tree.setCurrentItem(self.window.object_tree.topLevelItem(6))
        self.app.processEvents()
        token = self.window.manager.selection_token(OBJECTS)
        self.assertIs(self.window.focusWidget(), self.window.slide_list)
        self.key(self.window.slide_list, Qt.Key.Key_F6)
        self.assertEqual(self.window.manager.selection_token(OBJECTS), token)
        self.assertIs(self.window.focusWidget(), self.window.object_tree)

    def test_focus_target_and_text_cursor_are_restored(self) -> None:
        text_field = self.window.property_fields["text"]
        text_field.setFocus()
        text_field.setCursorPosition(5)
        self.key(text_field, Qt.Key.Key_F6, Qt.KeyboardModifier.ShiftModifier)
        self.assertIs(self.window.focusWidget(), self.window.object_tree)
        self.key(self.window.object_tree, Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), text_field)
        self.assertEqual(text_field.cursorPosition(), 5)

    def test_property_default_is_used_if_last_target_is_invalid(self) -> None:
        text_field = self.window.property_fields["text"]
        text_field.setFocus()
        self.window.manager.unregister_target(PROPERTIES, "text")
        self.window.object_tree.setFocus()
        self.key(self.window.object_tree, Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), self.window.property_fields["name"])

    def test_unavailable_area_is_skipped(self) -> None:
        self.window.manager.set_area_available(OBJECTS, False)
        self.window.slide_list.setFocus()
        self.key(self.window.slide_list, Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), self.window.property_fields["name"])

    def test_no_available_destination_does_not_move_focus(self) -> None:
        self.window.manager.set_area_available(OBJECTS, False)
        self.window.manager.set_area_available(PROPERTIES, False)
        self.window.slide_list.setFocus()
        self.assertFalse(self.window.manager.navigate_next())
        self.assertIs(self.window.focusWidget(), self.window.slide_list)

    def test_focus_changed_does_not_start_a_focus_loop(self) -> None:
        transitions: list[tuple[object, object]] = []
        self.window.manager.focusTransitionRequested.connect(
            lambda source, destination: transitions.append((source, destination))
        )
        self.window.slide_list.setFocus()
        self.key(self.window.slide_list, Qt.Key.Key_F6)
        self.assertEqual(len(transitions), 1)
        self.assertIs(self.window.focusWidget(), self.window.object_tree)

    def test_selection_change_does_not_move_keyboard_focus(self) -> None:
        self.window.object_tree.setFocus()
        focused = self.window.focusWidget()
        self.window.object_tree.setCurrentItem(self.window.object_tree.topLevelItem(6))
        self.app.processEvents()
        self.assertIs(self.window.focusWidget(), focused)

    def test_slide_change_rebuilds_tree_without_stealing_focus(self) -> None:
        self.window.object_tree.setFocus()
        self.window.slide_list.setCurrentRow(2)
        self.app.processEvents()
        self.assertIs(self.window.focusWidget(), self.window.object_tree)
        self.assertIn("slide-3-object-", self.window.manager.selection_token(OBJECTS))

    def test_destroyed_target_uses_registered_fallback(self) -> None:
        host = QWidget()
        host.setLayout(QVBoxLayout())
        source_root = QWidget(host)
        source_root.setLayout(QVBoxLayout())
        source = QLineEdit(source_root)
        source_root.layout().addWidget(source)
        destination_root = QWidget(host)
        destination_root.setLayout(QVBoxLayout())
        fallback = QLineEdit(destination_root)
        disappearing = QLineEdit(destination_root)
        destination_root.layout().addWidget(fallback)
        destination_root.layout().addWidget(disappearing)
        host.layout().addWidget(source_root)
        host.layout().addWidget(destination_root)
        host.show()
        manager = FocusAreaManager()
        manager.register_area("source", source_root, 0, "source")
        manager.register_target("source", "source", source)
        manager.register_area("destination", destination_root, 1, "fallback")
        manager.register_target("destination", "fallback", fallback)
        manager.register_target("destination", "target", disappearing)
        source.setFocus()
        self.app.processEvents()
        disappearing.setFocus()
        self.app.processEvents()
        source.setFocus()
        self.app.processEvents()
        disappearing.deleteLater()
        QCoreApplication.sendPostedEvents(disappearing, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertTrue(manager.navigate_next())
        self.assertIs(self.app.focusWidget(), fallback)
        host.close()

    def test_area_activation_is_independent_of_registration(self) -> None:
        self.window.manager.set_area_active(OBJECTS, False)
        self.window.slide_list.setFocus()
        self.key(self.window.slide_list, Qt.Key.Key_F6)
        self.assertIs(self.window.focusWidget(), self.window.property_fields["name"])
        self.window.manager.set_area_active(OBJECTS, True)
        self.assertTrue(self.window.manager.is_area_available(OBJECTS))


class GenericManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_token_does_not_change_focus(self) -> None:
        root = QWidget()
        root.setLayout(QVBoxLayout())
        target = QLineEdit(root)
        root.layout().addWidget(target)
        root.show()
        manager = FocusAreaManager()
        manager.register_area("generic", root, 0, "editor")
        manager.register_target("generic", "editor", target, _TextStateAdapter())
        manager.set_selection_token("generic", "opaque-item-7")
        target.setFocus()
        self.app.processEvents()
        focused = self.app.focusWidget()
        manager.set_selection_token("generic", "opaque-item-8")
        self.assertIs(self.app.focusWidget(), focused)
        self.assertEqual(manager.selection_token("generic"), "opaque-item-8")
        root.close()


if __name__ == "__main__":
    unittest.main()