"""test_interaction.py — pickup/transfer/release preconditions and ownership."""

from __future__ import annotations

import pytest

from animation_lab.interaction import (
    InteractionError,
    OwnershipLedger,
    apply_interaction,
    check_preconditions,
    sort_key,
)
from animation_lab.models import Interaction, PropState, Vec2Field


def _prop(owner: str | None = None, state: str = "free",
          pos: tuple[float, float] = (0.5, 0.6)) -> PropState:
    return PropState(prop_id="orb", position=Vec2Field(x=pos[0], y=pos[1]),
                     owner_id=owner, state=state)  # type: ignore[arg-type]


def _inter(kind: str, actor: str = "a", time: float = 1.0,
           receiver: str | None = None) -> Interaction:
    return Interaction(
        id=f"i_{kind}_{actor}", kind=kind,  # type: ignore[arg-type]
        time=time, actor_id=actor, prop_id="orb", receiver_id=receiver,
        expected_prop_state_after="attached",  # type: ignore[arg-type]
    )


def test_pickup_success_and_ownership():
    inter = _inter("pickup", actor="a", time=1.0)
    res = apply_interaction(inter, _prop(), actor_position=(0.5, 0.55),
                            prop_reach=0.2)
    assert res.prop_after.owner_id == "a"
    assert res.prop_after.state == "attached"
    assert res.prop_after.attachment_point == "hand_r"


def test_pickup_out_of_reach_raises():
    with pytest.raises(InteractionError, match="reach"):
        apply_interaction(_inter("pickup"), _prop(),
                          actor_position=(0.1, 0.1), prop_reach=0.2)


def test_pickup_already_owned_raises():
    owned = _prop(owner="b", state="attached")
    with pytest.raises(InteractionError, match="owned"):
        apply_interaction(_inter("pickup", actor="a"), owned,
                          actor_position=(0.5, 0.55), prop_reach=0.2)


def test_transfer_requires_ownership():
    with pytest.raises(InteractionError, match="unowned"):
        apply_interaction(_inter("transfer", receiver="b"),
                          _prop(), actor_position=(0.5, 0.55),
                          prop_reach=0.2)
    with pytest.raises(InteractionError, match="owned by"):
        apply_interaction(_inter("transfer", actor="a", receiver="b"),
                          _prop(owner="c", state="attached"),
                          actor_position=(0.5, 0.55), prop_reach=0.2)


def test_transfer_flips_owner_preserving_position():
    owned = _prop(owner="a", state="attached")
    res = apply_interaction(_inter("transfer", actor="a", receiver="b"),
                            owned, actor_position=(0.5, 0.55), prop_reach=0.2)
    assert res.prop_after.owner_id == "b"
    assert res.prop_after.state == "transferring"
    # positional continuity: world position unchanged
    assert res.prop_after.position.as_tuple() == \
        res.prop_before.position.as_tuple()


def test_transfer_to_self_rejected():
    owned = _prop(owner="a", state="attached")
    with pytest.raises(InteractionError, match="themselves"):
        apply_interaction(_inter("transfer", actor="a", receiver="a"),
                          owned, actor_position=(0.5, 0.55), prop_reach=0.2)


def test_release_inherits_position():
    owned = _prop(owner="a", state="attached")
    res = apply_interaction(_inter("release", actor="a"), owned,
                            actor_position=(0.5, 0.55), prop_reach=0.2)
    assert res.prop_after.owner_id is None
    assert res.prop_after.state == "released"


def test_release_unowned_raises():
    with pytest.raises(InteractionError, match="unowned"):
        apply_interaction(_inter("release", actor="a"), _prop(),
                          actor_position=(0.5, 0.55), prop_reach=0.2)


def test_transfer_requires_receiver():
    with pytest.raises(ValueError):
        Interaction(
            id="i_no_receiver", kind="transfer", time=1.0, actor_id="a",
            prop_id="orb", expected_prop_state_after="transferring",
        )


def test_free_prop_cannot_have_owner():
    with pytest.raises(ValueError):
        PropState(prop_id="orb", position=Vec2Field(x=0.5, y=0.6),
                  owner_id="a", state="free")


def test_attached_prop_requires_owner():
    with pytest.raises(ValueError):
        PropState(prop_id="orb", position=Vec2Field(x=0.5, y=0.6),
                  owner_id=None, state="attached")


def test_sort_key_deterministic_ordering():
    events = [
        _inter("release", time=2.0),
        _inter("pickup", time=1.0),
        _inter("transfer", time=2.0, receiver="b"),
    ]
    ordered = sorted(events, key=sort_key)
    assert [e.kind for e in ordered] == ["pickup", "transfer", "release"]


def test_ledger_conflict_detection():
    ledger = OwnershipLedger()
    ledger.record("orb", 0.0, None, "initial")
    ledger.record("orb", 2.0, "a", "e1")
    assert ledger.conflicts() == []
    ledger.record("orb", 2.0, "b", "e2")  # same instant, different owner
    assert len(ledger.conflicts()) == 1


def test_precondition_unknown_actor():
    with pytest.raises(InteractionError, match="unknown actor"):
        check_preconditions(_inter("pickup", actor="ghost"), _prop(),
                            (0.5, 0.55), 0.2, characters={"a", "b"})
