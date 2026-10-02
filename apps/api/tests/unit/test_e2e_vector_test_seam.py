from sqlalchemy import ARRAY, Float

from app.db.models.core import EvidenceItem
from tests.e2e.conftest import _set_test_embedding_type


def test_portable_embedding_type_resets_and_restores_cached_vector_comparator():
    original_type = EvidenceItem.__table__.c.embedding.type
    original_cosine = callable(getattr(EvidenceItem.embedding, "cosine_distance", None))
    try:
        _set_test_embedding_type(ARRAY(Float))
        assert getattr(EvidenceItem.embedding, "cosine_distance", None) is None
        assert isinstance(EvidenceItem.embedding.expression.type, ARRAY)
    finally:
        _set_test_embedding_type(original_type)
    assert callable(getattr(EvidenceItem.embedding, "cosine_distance", None)) == original_cosine
    assert EvidenceItem.embedding.expression.type is original_type
