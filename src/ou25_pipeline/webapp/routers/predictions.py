"""`/api/dates`, `/api/predictions`, `/api/predictions/export` — the entire
read-side HTTP surface. All three ultimately call `webapp.service`, so the
JSON payload and the CSV export are always built from the identical query.
"""

import io
from datetime import date

import pandas as pd
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import Engine

from ou25_pipeline.storage.db import get_engine
from ou25_pipeline.webapp import service
from ou25_pipeline.webapp.schemas import DatesOut, PredictionsForDateOut

router = APIRouter(prefix="/api")


def engine_dependency() -> Engine:
    """`Depends()`-wrapped, not called inline, so tests can point every
    route at a seeded test database via `app.dependency_overrides` instead
    of the real one `get_engine()` (already `@lru_cache`'d) would return."""
    return get_engine()


@router.get("/dates", response_model=DatesOut)
def list_dates(engine: Engine = Depends(engine_dependency)) -> DatesOut:
    return DatesOut(dates=service.get_dates_with_predictions(engine))


@router.get("/predictions", response_model=PredictionsForDateOut)
def list_predictions(date: date, engine: Engine = Depends(engine_dependency)) -> PredictionsForDateOut:
    return PredictionsForDateOut(**service.get_predictions_for_date(engine, date))


# Descriptive, spreadsheet-friendly headers — deliberately not raw column
# names, per the export requirement that the CSV be easy to read in Excel/
# Sheets without a data dictionary. Order here is the order in the file.
_EXPORT_COLUMNS: dict[str, str] = {
    "kickoff_utc": "Kickoff (UTC)",
    "competition_name": "Competition",
    "home_team": "Home Team",
    "away_team": "Away Team",
    "call": "Recommendation",
    "bet_side": "Bet Side",
    "bet_odds": "Bet Odds",
    "edge_estimate": "Estimated Edge",
    "decision_zone": "Decision Zone",
    "status": "Fetch Status",
    "actual_total_goals": "Actual Total Goals",
    "outcome": "Outcome",
}


def _to_export_frame(predictions: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(predictions)
    if df.empty:
        return pd.DataFrame(columns=list(_EXPORT_COLUMNS.values()))

    df["call"] = df["call"].map({"BACK_FAVOURITE": "Back Favourite", "NO_BET": "Awaiting Update"})
    df["status"] = df["status"].map({"pending": "Awaiting Update", "updated": "Updated"})
    df["outcome"] = df["hit"].map({True: "Hit", False: "Miss"}).fillna(
        df["match_status"].apply(lambda s: "Pending" if s != "finished" else "Unknown")
    )
    return df[list(_EXPORT_COLUMNS.keys())].rename(columns=_EXPORT_COLUMNS)


@router.get("/predictions/export")
def export_predictions(date: date, engine: Engine = Depends(engine_dependency)) -> StreamingResponse:
    """CSV for exactly the date `/api/predictions` would return — built
    from the same `service.get_predictions_for_date` call, not a
    re-derived query, so the download can never disagree with the screen.
    """
    result = service.get_predictions_for_date(engine, date)
    frame = _to_export_frame(result["predictions"])

    buffer = io.StringIO()
    frame.to_csv(buffer, index=False)
    buffer.seek(0)

    filename = f"predictions_{date.isoformat()}.csv"
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
