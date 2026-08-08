from ou25_pipeline.orchestration.checkpoint import Checkpoint


def test_is_done_false_for_unknown_entity(clean_db):
    checkpoint = Checkpoint(clean_db)
    assert checkpoint.is_done("match", "mt_unknown") is False


def test_mark_done_then_is_done_true(clean_db):
    checkpoint = Checkpoint(clean_db)
    checkpoint.mark_done("match", "mt_1", competition_id="comp_1", season_id="sn_1")
    assert checkpoint.is_done("match", "mt_1") is True


def test_mark_failed_does_not_count_as_done(clean_db):
    checkpoint = Checkpoint(clean_db)
    checkpoint.mark_failed("match", "mt_2", "boom", competition_id="comp_1", season_id="sn_1")
    assert checkpoint.is_done("match", "mt_2") is False


def test_mark_failed_increments_attempts_on_repeat_failure(clean_db):
    checkpoint = Checkpoint(clean_db)
    checkpoint.mark_failed("match", "mt_3", "first failure")
    checkpoint.mark_failed("match", "mt_3", "second failure")

    from sqlalchemy import select

    from ou25_pipeline.storage.tables import extraction_state

    with clean_db.connect() as conn:
        row = conn.execute(
            select(extraction_state.c.attempts, extraction_state.c.last_error).where(
                extraction_state.c.entity_type == "match",
                extraction_state.c.entity_id == "mt_3",
            )
        ).one()
    assert row.attempts == 2
    assert row.last_error == "second failure"


def test_mark_done_overwrites_previous_failed_status(clean_db):
    checkpoint = Checkpoint(clean_db)
    checkpoint.mark_failed("match", "mt_4", "transient error")
    assert checkpoint.is_done("match", "mt_4") is False
    checkpoint.mark_done("match", "mt_4")
    assert checkpoint.is_done("match", "mt_4") is True
