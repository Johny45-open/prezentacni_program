"""Three-area focus-navigation shell built from standard Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QInputMethodEvent
from PyQt6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .focus_area_manager import FocusAreaManager


SLIDES = "slides"
OBJECTS = "objects"
PROPERTIES = "properties"


@dataclass
class _LineEditState:
    cursor_position: int
    selection_start: int
    selection_length: int


class _LineEditStateAdapter:
    def capture(self, widget: QWidget) -> _LineEditState:
        editor = widget
        assert isinstance(editor, QLineEdit)
        selection_start = editor.selectionStart()
        selection_length = len(editor.selectedText()) if selection_start >= 0 else 0
        return _LineEditState(editor.cursorPosition(), selection_start, selection_length)

    def restore(self, widget: QWidget, state: Any) -> None:
        editor = widget
        if not isinstance(editor, QLineEdit) or not isinstance(state, _LineEditState):
            return
        if state.selection_start >= 0 and state.selection_length:
            start = min(state.selection_start, len(editor.text()))
            length = min(state.selection_length, len(editor.text()) - start)
            editor.setSelection(start, length)
        else:
            editor.setCursorPosition(min(state.cursor_position, len(editor.text())))


class _ImeAwareLineEdit(QLineEdit):
    compositionStateChanged = pyqtSignal(bool)

    def __init__(self, accessible_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(accessible_name)
        self._composition_active = False

    def inputMethodEvent(self, event: QInputMethodEvent) -> None:
        super().inputMethodEvent(event)
        active = bool(event.preeditString())
        if active != self._composition_active:
            self._composition_active = active
            self.setProperty("imeCompositionActive", active)
            self.compositionStateChanged.emit(active)


class MainWindow(QMainWindow):
    """Demo shell for validating keyboard focus and independent selection."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Prezentátor - ověření navigace fokusu")
        self.resize(1050, 560)
        self._building_objects = False

        central = QWidget(self)
        layout = QHBoxLayout(central)
        self.setCentralWidget(central)

        self.slides_area = self._make_area("Snímky")
        self.objects_area = self._make_area("Objekty")
        self.properties_area = self._make_area("Vlastnosti")
        layout.addWidget(self.slides_area)
        layout.addWidget(self.objects_area)
        layout.addWidget(self.properties_area)

        self.slide_list = QListWidget(self.slides_area)
        self.slide_list.setAccessibleName("Seznam snímků")
        self.slide_list.setAccessibleDescription("Výběr aktivního snímku")
        self._fill_slides()
        self.slides_area.layout().addWidget(self.slide_list)

        self.object_tree = QTreeWidget(self.objects_area)
        self.object_tree.setHeaderHidden(True)
        self.object_tree.setAccessibleName("Strom objektů")
        self.object_tree.setAccessibleDescription("Výběr objektu aktivního snímku")
        self.objects_area.layout().addWidget(self.object_tree)

        self.property_fields: dict[str, _ImeAwareLineEdit] = {}
        for field_id, label_text in (
            ("name", "Název"),
            ("text", "Text"),
            ("description", "Popis"),
        ):
            label = QLabel(label_text, self.properties_area)
            editor = _ImeAwareLineEdit(label_text, self.properties_area)
            editor.setAccessibleDescription(f"Vlastnost: {label_text.lower()}")
            label.setBuddy(editor)
            self.property_fields[field_id] = editor
            self.properties_area.layout().addWidget(label)
            self.properties_area.layout().addWidget(editor)

        self.manager = FocusAreaManager(
            self,
            navigation_guard=self._navigation_allowed,
        )
        self._register_areas()
        self._connect_ui()
        self._create_navigation_actions()
        self._rebuild_objects(1)

    def _make_area(self, title: str) -> QGroupBox:
        area = QGroupBox(title, self)
        area.setAccessibleName(title)
        area.setLayout(QVBoxLayout())
        return area

    def _fill_slides(self) -> None:
        for index in range(1, 6):
            self.slide_list.addItem(f"Snímek {index}")
        self.slide_list.setCurrentRow(0)

    def _register_areas(self) -> None:
        self.manager.register_area(SLIDES, self.slides_area, 0, "view")
        self.manager.register_target(SLIDES, "view", self.slide_list)

        self.manager.register_area(OBJECTS, self.objects_area, 1, "view")
        self.manager.register_target(OBJECTS, "view", self.object_tree)

        self.manager.register_area(
            PROPERTIES,
            self.properties_area,
            2,
            "name",
            fallback_target_ids=("text", "description"),
        )
        for field_id, editor in self.property_fields.items():
            self.manager.register_target(
                PROPERTIES,
                field_id,
                editor,
                state_adapter=_LineEditStateAdapter(),
            )

    def _connect_ui(self) -> None:
        self.slide_list.currentRowChanged.connect(self._on_slide_changed)
        self.object_tree.currentItemChanged.connect(self._on_object_changed)
        for editor in self.property_fields.values():
            editor.compositionStateChanged.connect(self._on_composition_changed)

    def _create_navigation_actions(self) -> None:
        menu = self.menuBar().addMenu("Navigace")
        self.next_area_action = QAction("Další oblast", self)
        self.next_area_action.setShortcut("F6")
        self.next_area_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.next_area_action.triggered.connect(self.manager.navigate_next)
        self.addAction(self.next_area_action)
        menu.addAction(self.next_area_action)

        self.previous_area_action = QAction("Předchozí oblast", self)
        self.previous_area_action.setShortcut("Shift+F6")
        self.previous_area_action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        self.previous_area_action.triggered.connect(self.manager.navigate_previous)
        self.addAction(self.previous_area_action)
        menu.addAction(self.previous_area_action)

    def _on_slide_changed(self, row: int) -> None:
        if row >= 0:
            self._rebuild_objects(row + 1)

    def _rebuild_objects(self, slide_number: int) -> None:
        previous_selection = self.manager.selection_token(OBJECTS) if hasattr(self, "manager") else None
        self._building_objects = True
        self.object_tree.clear()
        items: list[QTreeWidgetItem] = []
        for index in range(1, 11):
            item = QTreeWidgetItem([f"Objekt {index}, snímek {slide_number}"])
            item.setData(0, Qt.ItemDataRole.UserRole, f"slide-{slide_number}-object-{index}")
            self.object_tree.addTopLevelItem(item)
            items.append(item)
        selected = next(
            (
                item
                for item in items
                if item.data(0, Qt.ItemDataRole.UserRole) == previous_selection
            ),
            items[0] if items else None,
        )
        self.object_tree.setCurrentItem(selected)
        self._building_objects = False
        token = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        self.manager.set_selection_token(OBJECTS, token)
        self.manager.set_area_available(OBJECTS, bool(items))
        self.manager.set_area_available(PROPERTIES, selected is not None)
        self._update_properties(selected)

    def _on_object_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if self._building_objects:
            return
        token = current.data(0, Qt.ItemDataRole.UserRole) if current else None
        self.manager.set_selection_token(OBJECTS, token)
        self.manager.set_area_available(PROPERTIES, current is not None)
        self._update_properties(current)

    def _update_properties(self, item: QTreeWidgetItem | None) -> None:
        values = {
            "name": item.text(0) if item else "",
            "text": f"Ukázkový text pro {item.text(0)}" if item else "",
            "description": f"Popis {item.text(0)}" if item else "",
        }
        for field_id, editor in self.property_fields.items():
            editor.setEnabled(item is not None)
            editor.setText(values[field_id])

    def _on_composition_changed(self, _active: bool) -> None:
        focus = self.focusWidget()
        self.manager.set_navigation_blocked(
            bool(focus and focus.property("imeCompositionActive"))
        )

    def _navigation_allowed(self) -> bool:
        if QApplication.activeModalWidget() is not None:
            return False
        focus = self.focusWidget()
        return not bool(focus and focus.property("imeCompositionActive"))