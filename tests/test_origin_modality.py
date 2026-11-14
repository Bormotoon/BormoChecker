from __future__ import annotations

from bormochecker.extract import one_origin
from bormochecker.origin import (
    from_text,
    is_episodic_observation,
    is_generic_or_deontic_statement,
    is_probabilistic_safety_opinion,
)


def test_deontic_and_generic_routing():
    deontic = "При встрече не нужно подходить близко, лучше остановиться и подождать."
    assert is_generic_or_deontic_statement(deontic)
    assert from_text(deontic) is None

    safety_op = "Здоровый зверь для человека опасности практически не представляет и, скорее всего, сам убежит."
    assert is_probabilistic_safety_opinion(safety_op)
    assert is_generic_or_deontic_statement(safety_op)
    assert from_text(safety_op) is None

    cyclic = "В конце лета у этих животных начинается гон, и они больше перемещаются в поисках пары."
    assert is_generic_or_deontic_statement(cyclic)
    assert from_text(cyclic) is None

    episodic = "На видео видно, как животное пересекло дорогу и ушло в лес."
    assert is_episodic_observation(episodic)
    assert from_text(episodic) == "unclear"

    claims = [
        {"quote": deontic, "text": deontic, "kind": "editorial", "origin": "stated"},
        {"quote": safety_op, "text": safety_op, "kind": "editorial", "origin": "stated"},
        {"quote": cyclic, "text": cyclic, "kind": "editorial", "origin": "stated"},
        {"quote": episodic, "text": episodic, "kind": "web_checkable", "origin": None},
    ]
    res = one_origin(claims, "Видео прислано читателем в редакцию.")
    assert res[0]["kind"] == "web_checkable" and res[0]["origin"] is None
    assert res[1]["kind"] == "opinion" and res[1]["origin"] is None
    assert res[2]["kind"] == "web_checkable" and res[2]["origin"] is None
    assert res[3]["kind"] == "editorial" and res[3]["origin"] == "stated"
