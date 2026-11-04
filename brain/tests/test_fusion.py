from app.domain import Hit
from app.services.retrieval import rrf_fuse


def _hit(cid: str) -> Hit:
    return Hit(chunk_id=cid, text="", section_path="", document_id="d", document_title="D", version_id="v", version_no=1)


def test_chunks_found_by_both_searches_rank_first():
    dense = [_hit("a"), _hit("b"), _hit("c")]
    keyword = [_hit("c"), _hit("d")]
    fused = rrf_fuse(dense, keyword)
    assert fused[0].chunk_id == "c"  # rank 3 + rank 1 beats rank 1 alone
    assert fused[0].dense_rank == 3 and fused[0].keyword_rank == 1
    assert {h.chunk_id for h in fused} == {"a", "b", "c", "d"}
