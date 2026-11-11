"""state_machine.py — finite state machine for character actions.

Defines legal transitions between action states.  Illegal transitions raise
:class:`IllegalTransitionError` — the planner refuses to build impossible
motion instead of silently tolerating it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

StateName = str  # kept as plain str for serialization friendliness

STATES: tuple[str, ...] = (
    "idle", "walking", "running", "turning", "reaching", "holding",
    "speaking", "reacting", "jumping", "falling", "landing", "sitting",
    "standing",
)


class IllegalTransitionError(ValueError):
    """Raised when a state transition is not allowed by the FSM."""

    def __init__(self, from_state: str, to_state: str, reason: str = "") -> None:
        self.from_state = from_state
        self.to_state = to_state
        msg = f"illegal transition {from_state!r} -> {to_state!r}"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


#: Transition table: from -> {to -> condition description}.
#: Conditions beginning with "auto:" are unconditional segment boundaries;
#: event-driven ones name the event that enables the transition.
TRANSITIONS: dict[str, dict[str, str]] = {
    "idle": {
        "walking": "auto: movement segment starts",
        "running": "auto: fast movement segment starts",
        "turning": "auto: turn segment starts",
        "reaching": "auto: reach segment starts",
        "speaking": "event: speech segment starts",
        "sitting": "event: sit segment starts",
        "standing": "event: stand_up segment starts",
        "holding": "event: successful pickup/attach",
        "jumping": "auto: jump segment starts",
        "reacting": "event: react segment starts",
    },
    "walking": {
        "idle": "auto: movement segment ends",
        "running": "auto: speed up",
        "turning": "auto: turn segment starts",
        "reaching": "event: reach_target within arm reach",
        "jumping": "event: jump segment starts",
    },
    "running": {
        "walking": "auto: slow down",
        "idle": "auto: movement segment ends",
        "jumping": "event: jump segment starts",
        "falling": "event: lose ground contact",
    },
    "turning": {
        "idle": "auto: turn segment ends",
        "walking": "auto: walk-away segment starts",
        "reaching": "event: reach_target defined",
    },
    "reaching": {
        "holding": "event: successful pickup/attach",
        "idle": "auto: reach failed or cancelled",
        "walking": "auto: reach cancelled, walk away",
    },
    "holding": {
        "transferring": "event: transfer to receiver",
        "idle": "event: release prop",
        "walking": "event: carry prop and walk (keeps holding via action, state walks)",
        "reacting": "event: react while holding",
        "speaking": "event: speak while holding",
    },
    "speaking": {
        "idle": "auto: speech segment ends",
        "reacting": "event: react segment starts",
        "walking": "auto: walk while talking (speech ends)",
    },
    "reacting": {
        "idle": "auto: reaction ends",
        "walking": "auto: step back / walk away",
        "jumping": "event: startled jump",
    },
    "jumping": {
        "falling": "auto: apex reached (always)",
    },
    "falling": {
        "landing": "auto: ground contact (always)",
    },
    "landing": {
        "idle": "auto: landing finished",
        "standing": "auto: landing finished, standing",
        "walking": "auto: land into walk",
    },
    "sitting": {
        "idle": "event: stand_up segment starts",
        "standing": "event: stand_up segment starts",
    },
    "standing": {
        "idle": "auto: standing finished",
        "walking": "auto: movement segment starts",
        "sitting": "event: sit segment starts",
    },
}


@dataclass
class CharacterFSM:
    """FSM instance tracking one character's action state over time."""

    character_id: str
    state: StateName = "idle"
    # Parallel action overlay (e.g. state=walking, action=speaking).
    current_action: Optional[str] = None
    history: list[tuple[float, str, str]] = field(default_factory=list)

    def can_transition(self, to_state: str, event: Optional[str] = None) -> bool:
        """Whether ``to_state`` is reachable from the current state.

        Unconditional (``auto:``) transitions are always allowed.  Event-driven
        transitions require ``event`` to name the enabling event.
        """
        if to_state not in STATES:
            raise ValueError(f"unknown state {to_state!r}")
        table = TRANSITIONS.get(self.state, {})
        if to_state not in table:
            return False
        condition = table[to_state]
        if condition.startswith("auto:"):
            return True
        # Event-driven: allowed only when a matching event name is supplied.
        required = condition.split(":", 1)[1].strip()
        return bool(event) and (
            event.lower() in required.lower() or required.lower() in event.lower()
        )

    def transition(
        self, to_state: str, time: float = 0.0, event: Optional[str] = None
    ) -> None:
        """Perform a transition or raise :class:`IllegalTransitionError`."""
        if not self.can_transition(to_state, event):
            condition = TRANSITIONS.get(self.state, {}).get(to_state)
            reason = f"requires event {condition!r}" if condition else "not in table"
            raise IllegalTransitionError(self.state, to_state, reason)
        self.history.append((round(float(time), 9), self.state, to_state))
        self.state = to_state

    def require_transition(self, to_state: str, time: float = 0.0) -> None:
        """Auto transitions only: used by the simulator on segment boundaries."""
        self.transition(to_state, time, event=None)


def validate_sequence(steps: list[tuple[str, float]]) -> list[str]:
    """Validate a chronological sequence of ``(state, time)`` transitions.

    Returns a list of diagnostic messages for illegal steps (empty = valid).
    """
    problems: list[str] = []
    fsm = CharacterFSM(character_id="probe")
    last_time = -1.0
    for state, time in steps:
        if time < last_time:
            problems.append(f"time regression at {time}: {time} < {last_time}")
        last_time = time
        if not fsm.can_transition(state):
            problems.append(
                f"illegal transition {fsm.state!r} -> {state!r} at t={time}"
            )
            continue
        fsm.transition(state, time)
    return problems
