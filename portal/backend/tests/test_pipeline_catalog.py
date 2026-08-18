"""The pipeline picker groups models by category, so the catalog metadata has to
stay complete: every pipeline needs a real category, an explicit display slot,
and a card blurb short enough to read at a glance."""

import pytest

from app.runpod import PIPELINE_CATEGORIES, PIPELINE_ORDER, PIPELINES, ordered_pipelines

CATEGORY_KEYS = {category["key"] for category in PIPELINE_CATEGORIES}


def test_categories_are_unique_and_labelled():
    keys = [category["key"] for category in PIPELINE_CATEGORIES]
    assert len(keys) == len(set(keys))
    assert all(category["label"] for category in PIPELINE_CATEGORIES)


@pytest.mark.parametrize("key", sorted(PIPELINES))
def test_pipeline_declares_a_known_category(key):
    assert PIPELINES[key].category in CATEGORY_KEYS


@pytest.mark.parametrize("key", sorted(PIPELINES))
def test_card_copy_is_a_short_single_line(key):
    """The card shows `description`; the long form belongs in `instructions`,
    which only renders once the pipeline is selected."""
    description = PIPELINES[key].description
    assert description
    assert "\n" not in description
    assert len(description) <= 60, f"{key} card blurb is too long for the card"
    assert PIPELINES[key].instructions


def test_display_order_covers_every_pipeline_exactly_once():
    assert len(PIPELINE_ORDER) == len(set(PIPELINE_ORDER))
    assert set(PIPELINE_ORDER) == set(PIPELINES)


def test_ordered_pipelines_groups_by_category_order():
    """Rendering walks the ordered list per section, so a pipeline must never be
    dropped, and the order must not interleave categories."""
    ordered = ordered_pipelines()
    assert [p.key for p in ordered] == PIPELINE_ORDER
    assert len(ordered) == len(PIPELINES)

    category_rank = {category["key"]: i for i, category in enumerate(PIPELINE_CATEGORIES)}
    ranks = [category_rank[p.category] for p in ordered]
    assert ranks == sorted(ranks), "pipelines of one category are split across the list"


def test_unlisted_pipeline_still_renders(monkeypatch):
    """A pipeline added to PIPELINES but forgotten in PIPELINE_ORDER must still
    appear (at the end) rather than vanish from the picker."""
    monkeypatch.setattr("app.runpod.PIPELINE_ORDER", PIPELINE_ORDER[:-1])
    ordered = ordered_pipelines()
    assert len(ordered) == len(PIPELINES)
    assert ordered[-1].key == PIPELINE_ORDER[-1]


def test_boltz2_is_in_the_structure_section():
    boltz2 = PIPELINES["boltz2"]
    assert boltz2.category == "structure"
    assert boltz2.supports_sequence
    assert boltz2.endpoint_attr == "boltz2_endpoint_id"
    assert {field.name for field in boltz2.input_fields} >= {"ligand_smiles", "predict_affinity"}
