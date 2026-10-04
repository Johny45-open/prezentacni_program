"""Generic keyboard-focus navigation between registered Qt widget areas."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Hashable, Protocol

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QWidget


AreaId = Hashable
TargetId = Hashable
Resolver = Callable[[], QWidget | None]
Availability = Callable[[], bool]


class FocusStateAdapter(Protocol):
    """Capture and restore widget-local focus state without domain knowledge."""

    def capture(self, widget: QWidget) -> Any: ...

    def restore(self, widget: QWidget, state: Any) -> None: ...


@dataclass
class FocusTarget:
    target_id: TargetId
    resolver: Resolver
    state_adapter: FocusStateAdapter | None = None
    available: Availability | None = None


@dataclass
class FocusArea:
    area_id: AreaId
    root: QWidget
    order: int
    default_target_id: TargetId
    fallback_target_ids: tuple[TargetId, ...] = ()
    available: bool = True
    active: bool = True
    targets: dict[TargetId, FocusTarget] = field(default_factory=dict)
    last_target_id: TargetId | None = None
    last_target_state: Any = None
    selection_token: Any = None


class FocusAreaManager(QObject):
    """Moves system focus between areas; selection tokens remain independent."""

    currentAreaChanged = pyqtSignal(object)
    availabilityChanged = pyqtSignal(object, bool)
    focusTransitionRequested = pyqtSignal(object, object)
    targetRestoreFailed = pyqtSignal(object, object)

    def __init__(
        self,
        parent: QObject | None = None,
        navigation_guard: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._areas: dict[AreaId, FocusArea] = {}
        self._navigation_blocked = False
        self._navigation_guard = navigation_guard or (lambda: True)
        self._transitioning = False
        self._pending_area: AreaId | None = None
        self._current_area: AreaId | None = None
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)

    @property
    def current_area(self) -> AreaId | None:
        return self._current_area

    def register_area(
        self,
        area_id: AreaId,
        root: QWidget,
        order: int,
        default_target_id: TargetId,
        fallback_target_ids: tuple[TargetId, ...] = (),
    ) -> None:
        if area_id in self._areas:
            raise ValueError(f"Area already registered: {area_id!r}")
        self._areas[area_id] = FocusArea(
            area_id=area_id,
            root=root,
            order=order,
            default_target_id=default_target_id,
            fallback_target_ids=fallback_target_ids,
        )

    def unregister_area(self, area_id: AreaId) -> None:
        self._areas.pop(area_id, None)
        if self._current_area == area_id:
            self._set_current_area(None)

    def register_target(
        self,
        area_id: AreaId,
        target_id: TargetId,
        target: QWidget | Resolver,
        state_adapter: FocusStateAdapter | None = None,
        available: Availability | None = None,
    ) -> None:
        area = self._require_area(area_id)
        resolver = target if callable(target) else lambda target=target: target
        if target_id in area.targets:
            raise ValueError(f"Target already registered: {target_id!r}")
        area.targets[target_id] = FocusTarget(
            target_id=target_id,
            resolver=resolver,
            state_adapter=state_adapter,
            available=available,
        )
        if not callable(target):
            target.destroyed.connect(
                lambda _obj=None, area_id=area_id, target_id=target_id: self.unregister_target(
                    area_id, target_id
                )
            )

    def unregister_target(self, area_id: AreaId, target_id: TargetId) -> None:
        area = self._areas.get(area_id)
        if area is None:
            return
        area.targets.pop(target_id, None)
        if area.last_target_id == target_id:
            area.last_target_id = None
            area.last_target_state = None

    def set_area_available(self, area_id: AreaId, available: bool) -> None:
        area = self._require_area(area_id)
        if area.available == available:
            return
        area.available = available
        self.availabilityChanged.emit(area_id, self.is_area_available(area_id))

    def set_area_active(self, area_id: AreaId, active: bool) -> None:
        area = self._require_area(area_id)
        if area.active == active:
            return
        area.active = active
        self.availabilityChanged.emit(area_id, self.is_area_available(area_id))

    def set_navigation_blocked(self, blocked: bool) -> None:
        self._navigation_blocked = blocked

    def is_area_available(self, area_id: AreaId) -> bool:
        area = self._areas.get(area_id)
        if area is None or not area.available or not area.active:
            return False
        try:
            if not area.root.isVisible() or not area.root.isEnabled():
                return False
        except RuntimeError:
            return False
        return self._resolve_area_target(area, area.last_target_id) is not None or any(
            self._resolve_area_target(area, target_id) is not None
            for target_id in self._fallback_ids(area)
        )

    def set_selection_token(self, area_id: AreaId, token: Any) -> None:
        """Store selection without changing keyboard focus."""
        self._require_area(area_id).selection_token = token

    def selection_token(self, area_id: AreaId) -> Any:
        return self._require_area(area_id).selection_token

    def next_available(self, area_id: AreaId) -> AreaId | None:
        return self._find_available(area_id, direction=1)

    def previous_available(self, area_id: AreaId) -> AreaId | None:
        return self._find_available(area_id, direction=-1)

    def navigate_next(self) -> bool:
        return self._navigate(direction=1)

    def navigate_previous(self) -> bool:
        return self._navigate(direction=-1)

    def remember_focus(self, area_id: AreaId, widget: QWidget) -> bool:
        area = self._areas.get(area_id)
        if area is None:
            return False
        match = self._target_for_widget(area, widget)
        if match is None:
            return False
        target_id, target, target_widget = match
        area.last_target_id = target_id
        if target.state_adapter is not None:
            area.last_target_state = target.state_adapter.capture(target_widget)
        return True

    def invalidate_target(self, area_id: AreaId, target_id: TargetId) -> None:
        self.unregister_target(area_id, target_id)

    def _navigate(self, direction: int) -> bool:
        if self._navigation_blocked or not self._navigation_guard():
            return False
        app = QApplication.instance()
        focus = app.focusWidget() if app is not None else None
        current = self._area_for_widget(focus) if focus is not None else None
        if current is None:
            return False

        if focus is not None:
            self.remember_focus(current.area_id, focus)
        target_area_id = self._find_available(current.area_id, direction)
        if target_area_id is None:
            return False
        area = self._areas[target_area_id]
        target_id, target, widget = self._target_to_focus(area)
        if widget is None:
            return False
        if widget is focus:
            self._set_current_area(target_area_id)
            return True

        self._pending_area = target_area_id
        self.focusTransitionRequested.emit(current.area_id, target_area_id)
        self._transitioning = True
        try:
            widget.setFocus(Qt.FocusReason.ShortcutFocusReason)
            app = QApplication.instance()
            actual_focus = app.focusWidget() if app is not None else None
            if actual_focus is not widget:
                self.targetRestoreFailed.emit(target_area_id, target_id)
                return False
            area.last_target_id = target_id
            self._set_current_area(target_area_id)
            if target.state_adapter is not None and area.last_target_state is not None:
                target.state_adapter.restore(widget, area.last_target_state)
            return True
        finally:
            self._transitioning = False
            self._pending_area = None

    def _find_available(self, area_id: AreaId, direction: int) -> AreaId | None:
        ordered = sorted(self._areas.values(), key=lambda area: area.order)
        if not ordered:
            return None
        current_index = next(
            (index for index, area in enumerate(ordered) if area.area_id == area_id),
            None,
        )
        if current_index is None:
            return None
        for offset in range(1, len(ordered) + 1):
            candidate = ordered[(current_index + direction * offset) % len(ordered)]
            if candidate.area_id != area_id and self.is_area_available(candidate.area_id):
                return candidate.area_id
        return None

    def _target_to_focus(
        self, area: FocusArea
    ) -> tuple[TargetId | None, FocusTarget | None, QWidget | None]:
        choices = (area.last_target_id, *self._fallback_ids(area))
        seen: set[TargetId] = set()
        for target_id in choices:
            if target_id is None or target_id in seen:
                continue
            seen.add(target_id)
            target = area.targets.get(target_id)
            widget = self._resolve_area_target(area, target_id)
            if target is not None and widget is not None:
                return target_id, target, widget
            if target_id == area.last_target_id:
                self.targetRestoreFailed.emit(area.area_id, target_id)
        return None, None, None

    def _fallback_ids(self, area: FocusArea) -> tuple[TargetId, ...]:
        return (area.default_target_id, *area.fallback_target_ids)

    def _resolve_area_target(self, area: FocusArea, target_id: TargetId | None) -> QWidget | None:
        if target_id is None:
            return None
        target = area.targets.get(target_id)
        if target is None:
            return None
        try:
            widget = target.resolver()
            if widget is None or not widget.isVisible() or not widget.isEnabled():
                return None
            if widget.focusPolicy() == Qt.FocusPolicy.NoFocus:
                return None
            if widget is not area.root and not area.root.isAncestorOf(widget):
                return None
            if target.available is not None and not target.available():
                return None
            return widget
        except RuntimeError:
            return None

    def _target_for_widget(
        self, area: FocusArea, widget: QWidget
    ) -> tuple[TargetId, FocusTarget, QWidget] | None:
        matches: list[tuple[int, TargetId, FocusTarget, QWidget]] = []
        for target_id, target in area.targets.items():
            target_widget = self._resolve_area_target(area, target_id)
            if target_widget is None:
                continue
            try:
                if target_widget is widget or target_widget.isAncestorOf(widget):
                    depth = 0
                    node = target_widget
                    while node is not None:
                        depth += 1
                        node = node.parentWidget()
                    matches.append((depth, target_id, target, target_widget))
            except RuntimeError:
                continue
        if not matches:
            return None
        _, target_id, target, target_widget = max(matches, key=lambda item: item[0])
        return target_id, target, target_widget

    def _area_for_widget(self, widget: QWidget | None) -> FocusArea | None:
        if widget is None:
            return None
        matches: list[tuple[int, FocusArea]] = []
        for area in self._areas.values():
            try:
                if widget is area.root or area.root.isAncestorOf(widget):
                    depth = 0
                    node = area.root
                    while node is not None:
                        depth += 1
                        node = node.parentWidget()
                    matches.append((depth, area))
            except RuntimeError:
                continue
        return max(matches, key=lambda item: item[0])[1] if matches else None

    def _on_focus_changed(self, old: QWidget | None, new: QWidget | None) -> None:
        if self._transitioning:
            return
        old_area = self._area_for_widget(old)
        if old_area is not None and old is not None:
            self.remember_focus(old_area.area_id, old)
        new_area = self._area_for_widget(new)
        if new_area is not None and new is not None:
            match = self._target_for_widget(new_area, new)
            if match is not None:
                new_area.last_target_id = match[0]
        self._set_current_area(new_area.area_id if new_area is not None else None)

    def _set_current_area(self, area_id: AreaId | None) -> None:
        if self._current_area == area_id:
            return
        self._current_area = area_id
        self.currentAreaChanged.emit(area_id)

    def _require_area(self, area_id: AreaId) -> FocusArea:
        try:
            return self._areas[area_id]
        except KeyError as error:
            raise KeyError(f"Area is not registered: {area_id!r}") from error