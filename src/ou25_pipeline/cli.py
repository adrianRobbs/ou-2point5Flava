import logging
from pathlib import Path

import pandas as pd
import typer

from ou25_pipeline.api.client import StatsAPIClient
from ou25_pipeline.config import get_settings
from ou25_pipeline.export.match_csv import TIER_BY_COMPETITION, build_match_dataframe
from ou25_pipeline.export.match_csv import write_csv as write_matches_csv
from ou25_pipeline.features.team_form import build_feature_dataframe
from ou25_pipeline.features.team_form import write_csv as write_features_csv
from ou25_pipeline.logging_config import configure_logging
from ou25_pipeline.market.backtest import run_backtest
from ou25_pipeline.market.backtest import write_report as write_backtest_report
from ou25_pipeline.market.decision import classify_frame, favourite_frame
from ou25_pipeline.market.ladder import build_ladder_features, prior_match_counts
from ou25_pipeline.market.live import recommend_for_competition, recommendations_to_frame
from ou25_pipeline.modeling.dataset import chronological_split, load_features
from ou25_pipeline.modeling.evaluate import evaluate, write_report
from ou25_pipeline.modeling.train import (
    save_model,
    train_calibrated_xgboost,
    train_logistic_regression,
    train_market_anchored_xgboost,
    train_xgboost,
)
from ou25_pipeline.orchestration.catalog import list_tracked_competition_ids, set_competition_tracked
from ou25_pipeline.orchestration.coverage import find_coverage_gaps
from ou25_pipeline.orchestration.daily_sync import discover_fixtures_for_competitions, refresh_odds_for_competitions
from ou25_pipeline.orchestration.pipeline import run_backfill
from ou25_pipeline.storage.db import bootstrap_schema, get_engine
from ou25_pipeline.storage.validate import validate_backfill

app = typer.Typer(help="Over/Under 2.5 historical data extraction pipeline.")


@app.command("init-db")
def init_db() -> None:
    """Apply db/schema.sql against DATABASE_URL. Safe to re-run."""
    bootstrap_schema()
    typer.echo("Schema applied.")


@app.command()
def backfill(
    competition: str = typer.Option(
        ..., help="A `comp_...` id, or a free-text name matched via the provider's search filter."
    ),
    season: str = typer.Option(..., help="Season label or a substring/digits of it, e.g. '2024-25'."),
) -> None:
    """Backfill one competition/season: matches + all satellite data, resumable."""
    settings = get_settings()
    run_id = configure_logging(settings.log_level)
    logger = logging.getLogger(__name__)
    logger.info("Starting backfill", extra={"extra_fields": {"run_id": run_id}})

    engine = get_engine()
    with StatsAPIClient(
        base_url=settings.thestatsapi_base_url,
        api_key=settings.thestatsapi_key,
        requests_per_minute=settings.thestatsapi_rate_limit_per_min,
    ) as client:
        summary = run_backfill(client, engine, competition, season)

    typer.echo(
        f"matches_total={summary.matches_total} "
        f"processed={summary.matches_processed} "
        f"skipped_done={summary.matches_skipped_done} "
        f"failed={summary.matches_failed}"
    )
    if summary.quota_exceeded:
        typer.echo(
            "Monthly API quota exceeded — stopped early. Retrying now will fail "
            "immediately on every request; wait for the quota to reset or upgrade "
            "the plan, then re-run the same command (checkpoint will resume)."
        )
        raise typer.Exit(code=2)
    if summary.matches_failed:
        raise typer.Exit(code=1)


@app.command("sync-backfill")
def sync_backfill_cmd(
    competition: list[str] = typer.Option(
        None, help="`comp_...` ids to check. Repeatable. Omit to check every tracked competition."
    ),
    dry_run: bool = typer.Option(False, help="Only report gaps found, don't backfill them."),
) -> None:
    """The CLI equivalent of the Backfill page's per-season 'Check
    completeness' + 'Sync' buttons, without needing a paid Render Job (see
    render.yaml — the web service is on the free tier, which has no free
    option for one-off Jobs at all, so that button currently 501s).

    Finds every season already registered in our DB for each competition
    (not every season the provider has ever run — see
    `orchestration.coverage.find_coverage_gaps` for why that's a deliberate
    scope, not an oversight) whose stored match count doesn't match the
    live total, then backfills each gap with exactly the same `run_backfill`
    the `backfill` command above calls — this assembles no new backfill
    logic, only finds what needs it.

    Stops immediately on a monthly quota exceeded, same as `backfill` —
    grinding through remaining gaps after that point is pure waste.
    """
    settings = get_settings()
    engine = get_engine()
    competitions = competition or list_tracked_competition_ids(engine)

    with StatsAPIClient(
        base_url=settings.thestatsapi_base_url,
        api_key=settings.thestatsapi_key,
        requests_per_minute=settings.thestatsapi_rate_limit_per_min,
    ) as client:
        gaps = find_coverage_gaps(client, engine, competitions)

        if not gaps:
            typer.echo("No gaps found.")
            return

        typer.echo(f"Found {len(gaps)} gap(s):")
        for gap in gaps:
            typer.echo(f"  {gap.competition_id} / {gap.year}: {gap.our_count}/{gap.expected_finished} finished ({gap.status})")

        if dry_run:
            return

        any_failed = False
        for gap in gaps:
            typer.echo(f"\n=== Backfilling {gap.competition_id} / {gap.year} ===")
            summary = run_backfill(client, engine, gap.competition_id, gap.year)
            typer.echo(
                f"matches_total={summary.matches_total} processed={summary.matches_processed} "
                f"skipped_done={summary.matches_skipped_done} failed={summary.matches_failed}"
            )
            if summary.quota_exceeded:
                typer.echo(
                    "Monthly API quota exceeded — stopped early. Re-run the same command "
                    "once the quota resets; already-backfilled gaps will be skipped."
                )
                raise typer.Exit(code=2)
            any_failed = any_failed or bool(summary.matches_failed)

    if any_failed:
        raise typer.Exit(code=1)


@app.command("seed-tracked-competitions")
def seed_tracked_competitions_cmd() -> None:
    """One-off: flips `is_tracked=true` (and sets `tier`) for exactly the 13
    competitions in `TIER_BY_COMPETITION`, using `orchestration.catalog`'s
    DB-backed tracking flag instead of the hardcoded dict.

    Run this once, right after applying the `is_tracked`/`tier` schema
    change, so the switch-over from "tracked = the hardcoded dict" to
    "tracked = the DB flag" doesn't silently drop today's 13 competitions
    out of discover-fixtures/refresh-odds/sync-backfill. Requires each
    competition to already exist in `competitions` — run the Competitions
    page's catalog sync (or `backfill`, which registers on demand) first if
    any of the 13 aren't in the DB yet.
    """
    engine = get_engine()
    missing = []
    for competition_id, tier in TIER_BY_COMPETITION.items():
        try:
            set_competition_tracked(engine, competition_id, tracked=True, tier=tier)
        except ValueError:
            missing.append(competition_id)

    tracked_count = len(TIER_BY_COMPETITION) - len(missing)
    typer.echo(f"Tracked {tracked_count}/{len(TIER_BY_COMPETITION)} competitions.")
    if missing:
        typer.echo(f"Not in `competitions` yet, skipped: {', '.join(missing)}")
        typer.echo("Run the Competitions page's catalog sync, then re-run this command.")
        raise typer.Exit(code=1)


@app.command()
def validate(
    competition_id: str = typer.Option(..., help="The resolved `comp_...` id (not free-text)."),
    season_id: str = typer.Option(..., help="The resolved `sn_...` id (not free-text)."),
) -> None:
    """Post-run checks: row counts, null rates, and referential integrity."""
    engine = get_engine()
    issues = validate_backfill(engine, competition_id, season_id)
    if not issues:
        typer.echo("No issues found.")
        return
    for issue in issues:
        typer.echo(f"[{issue.check}] {issue.detail}")
    raise typer.Exit(code=1)


@app.command("export-matches")
def export_matches(
    output: Path = typer.Option(
        Path("data/exports/matches.csv"), help="CSV output path. Parent directories are created if missing."
    ),
    competition_id: list[str] = typer.Option(
        None, help="Filter to specific `comp_...` ids. Repeatable. Omit for all leagues."
    ),
    season_id: list[str] = typer.Option(
        None, help="Filter to specific `sn_...` ids. Repeatable. Omit for all seasons."
    ),
) -> None:
    """Flattens the normalized tables into one row-per-match CSV for EDA.
    Writes a `<output>.columns.md` sidecar noting which columns are
    post-match/end-of-season and unsafe as pre-match prediction features.
    """
    engine = get_engine()
    df = build_match_dataframe(
        engine,
        competition_ids=competition_id or None,
        season_ids=season_id or None,
    )
    if df.empty:
        typer.echo("No matches found for the given filters.")
        raise typer.Exit(code=1)

    notes_path = write_matches_csv(df, output)
    typer.echo(f"Wrote {len(df)} rows, {len(df.columns)} columns to {output}")
    typer.echo(f"Column notes: {notes_path}")


@app.command("export-features")
def export_features(
    output: Path = typer.Option(
        Path("data/exports/matches_features.csv"),
        help="CSV output path. Parent directories are created if missing.",
    ),
    competition_id: list[str] = typer.Option(
        None, help="Filter to specific `comp_...` ids. Repeatable. Omit for all leagues."
    ),
    season_id: list[str] = typer.Option(
        None, help="Filter to specific `sn_...` ids. Repeatable. Omit for all seasons."
    ),
) -> None:
    """Point-in-time rolling/decay team-form, market, and head-to-head
    features — one row per match, separate from `export-matches`. Writes a
    `<output>.columns.md` sidecar noting deferred feature classes and
    reliability caveats (see the module docstring in features/team_form.py).
    """
    engine = get_engine()
    df = build_feature_dataframe(
        engine,
        competition_ids=competition_id or None,
        season_ids=season_id or None,
    )
    if df.empty:
        typer.echo("No matches found for the given filters.")
        raise typer.Exit(code=1)

    notes_path = write_features_csv(df, output)
    typer.echo(f"Wrote {len(df)} rows, {len(df.columns)} columns to {output}")
    typer.echo(f"Column notes: {notes_path}")


@app.command("train-model")
def train_model(
    input: Path = typer.Option(
        Path("data/exports/matches_features.csv"), help="Path to the point-in-time features CSV."
    ),
    test_frac: float = typer.Option(
        0.2, help="Fraction of each competition's matches (chronologically last) held out for testing."
    ),
    output_dir: Path = typer.Option(
        Path("data/models"), help="Directory for saved models and the evaluation report."
    ),
) -> None:
    """Train the model variants, evaluate each against the market's own
    implied probability, and write joblib models plus a comparative markdown
    report to `output_dir`.
    """
    df = load_features(input)
    train_df, test_df = chronological_split(df, test_frac=test_frac)

    results: dict = {}
    trainers = [
        ("logistic_regression", train_logistic_regression),
        ("xgboost", train_xgboost),
        ("xgboost_calibrated", train_calibrated_xgboost),
        ("xgboost_market_anchored", train_market_anchored_xgboost),
    ]
    for name, trainer in trainers:
        pipeline = trainer(train_df)
        save_model(pipeline, output_dir / f"baseline_{name}.joblib")
        result = evaluate(pipeline, test_df)
        results[name] = result
        m, mk = result["model_metrics"], result["market_metrics"]
        typer.echo(
            f"{name}: log_loss={m['log_loss']:.4f} (market={mk['log_loss']:.4f}) "
            f"accuracy={m['accuracy']:.4f} (market={mk['accuracy']:.4f})"
        )

    report_path = output_dir / "evaluation_report.md"
    write_report(results, report_path)
    typer.echo(f"Report written to {report_path}")


@app.command("backtest-zones")
def backtest_zones(
    input: Path = typer.Option(
        Path("data/exports/matches.csv"),
        help="Raw match export — needs the full goals_<line>_*_close ladder, not just 2.5.",
    ),
    output_dir: Path = typer.Option(Path("data/decisions"), help="Directory for the report and decisions CSV."),
) -> None:
    """Fit the market's implied goal distribution, classify each match into a
    betting zone, and backtest the decisions at real closing prices.

    Writes `backtest_report.md` (zone performance with bootstrap CIs, thirds
    stability, and threshold sensitivity) plus `decisions.csv` (per-match
    calls with their frozen zone and rule_version).
    """
    df = pd.read_csv(input, parse_dates=["kickoff_utc"])
    typer.echo(f"Fitting ladder for {len(df)} matches (two-parameter fit each, this takes a minute)...")
    ladder = build_ladder_features(df)
    framed = favourite_frame(pd.concat([df, ladder], axis=1))
    framed["min_prior_matches"] = prior_match_counts(df)
    decisions = classify_frame(framed)
    combined = pd.concat([framed, decisions], axis=1)

    fitted = combined["overdispersion"].notna().sum()
    typer.echo(f"Ladder fitted for {fitted} matches ({fitted / len(df):.1%}).")

    results = run_backtest(combined)
    report_path = output_dir / "backtest_report.md"
    write_backtest_report(results, report_path)

    keep = [c for c in ["match_id", "kickoff_utc", "competition_id", "tier", "fav_side", "fav_prob",
                        "fav_odds", "dog_odds", "overdispersion", "ladder_mu", "curve_divergence",
                        "min_prior_matches", "call", "decision_zone", "bet_side", "bet_odds",
                        "edge_estimate", "skip_reason", "rule_version", "fav_won"]
            if c in combined.columns]
    decisions_path = output_dir / "decisions.csv"
    decisions_path.parent.mkdir(parents=True, exist_ok=True)
    combined[keep].to_csv(decisions_path, index=False)

    backed = (combined["call"] == "BACK_FAVOURITE").sum()
    typer.echo(f"Backing the favourite on {backed} of {len(combined)} matches ({backed / len(combined):.1%}).")
    for reason, count in combined.loc[combined["call"] != "BACK_FAVOURITE", "skip_reason"].value_counts().items():
        typer.echo(f"  skipped ({reason}): {count}")
    for zone in results["zones"]:
        roi = zone.roi
        typer.echo(
            f"{zone.name:28s} n={zone.n:5d} edge={zone.edge_pp:+5.2f}pp "
            f"ROI={roi['roi']:+.4f} CI[{roi['ci_low']:+.4f},{roi['ci_high']:+.4f}]"
        )
    typer.echo(f"Report: {report_path}")
    typer.echo(f"Decisions: {decisions_path}")


@app.command("recommend-bets")
def recommend_bets(
    competition: list[str] = typer.Option(
        None, help="`comp_...` ids to check. Repeatable. Omit to check every tracked competition."
    ),
    log: Path = typer.Option(
        Path("data/decisions/forward_log.csv"),
        help="Append-only forward-tracking log. Every run adds new rows; never overwritten or "
             "deduplicated on write, so it stays a full audit trail of what was recommended when.",
    ),
) -> None:
    """Fetch live pre-kickoff odds for upcoming matches and apply the
    validated two-component rule (see market/decision.py). This is the only
    command in the pipeline that calls the live API rather than reading
    backfilled data — everything else is a backtest; this is the real thing.

    Run this close to kickoff, not the moment a fixture is announced: the
    rule needs the full 0.5-6.5 goal-line ladder priced and mature, and an
    early market may not have it yet (see market/decision.py and
    market/live.py module docstrings for why).
    """
    settings = get_settings()
    engine = get_engine()
    competitions = competition or list_tracked_competition_ids(engine)

    all_recs = []
    with StatsAPIClient(
        base_url=settings.thestatsapi_base_url,
        api_key=settings.thestatsapi_key,
        requests_per_minute=settings.thestatsapi_rate_limit_per_min,
    ) as client:
        for competition_id in competitions:
            recs = recommend_for_competition(client, engine, competition_id)
            all_recs.extend(recs)
            if recs:
                typer.echo(f"{competition_id}: {len(recs)} upcoming priced match(es)")

    if not all_recs:
        typer.echo("No upcoming matches with odds available in any checked league.")
        raise typer.Exit(code=0)

    df = recommendations_to_frame(all_recs)
    backed = df[df["call"] == "BACK_FAVOURITE"]
    typer.echo(f"\n{len(backed)} of {len(df)} upcoming matches clear the rule:")
    for _, row in backed.iterrows():
        typer.echo(
            f"  {row['kickoff_utc']}  {row['home_team']} vs {row['away_team']}  "
            f"BACK {row['bet_side']} @ {row['bet_odds']:.2f}  [{row['decision_zone']}]"
        )

    log.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(log, mode="a", header=not log.exists(), index=False)
    typer.echo(f"\nLogged {len(df)} rows to {log}")


@app.command("discover-fixtures")
def discover_fixtures_cmd(
    competition: list[str] = typer.Option(
        None, help="`comp_...` ids to check. Repeatable. Omit to check every tracked competition."
    ),
    max_days_ahead: int | None = typer.Option(
        None, help="Only discover fixtures kicking off within this many days. Omit for no limit."
    ),
) -> None:
    """Cron-facing (or manual — see webapp/routers/sync.py's equivalent
    button, used while no cron is deployed): list upcoming fixtures and
    insert a 'pending' placeholder row in `predictions` for any not already
    known. Does not fetch odds or classify anything, that is
    `refresh-odds`'s job. Never overwrites a row a refresh pass already
    classified.

    Registers each match's competition/season/teams first if they are not
    already known — a live-discovered fixture may belong to a league
    `backfill` has never touched, and `predictions` carries the same
    foreign keys `matches` does. Cheap when already known (one SELECT per
    id, no API call); backfilling that league properly can happen later,
    since the rule engine itself uses no competition/season context at all.
    """
    settings = get_settings()
    engine = get_engine()
    competitions = competition or list_tracked_competition_ids(engine)

    with StatsAPIClient(
        base_url=settings.thestatsapi_base_url,
        api_key=settings.thestatsapi_key,
        requests_per_minute=settings.thestatsapi_rate_limit_per_min,
    ) as client:
        result = discover_fixtures_for_competitions(client, engine, competitions, max_days_ahead)

    for competition_id, inserted in result.per_competition.items():
        typer.echo(f"{competition_id}: {inserted} newly discovered")
    typer.echo(f"Discovered {result.total_discovered} new fixture(s).")


@app.command("refresh-odds")
def refresh_odds_cmd(
    competition: list[str] = typer.Option(
        None, help="`comp_...` ids to check. Repeatable. Omit to check every tracked competition."
    ),
) -> None:
    """Cron-facing (or manual — see webapp/routers/sync.py): re-fetch odds
    and reclassify every 'pending' fixture plus every previously-classified
    fixture that hasn't kicked off yet (its price may have moved). Run this
    multiple times a day, close to kickoff, since the market-shape signal
    needs a mature ladder — see market/decision.py and market/live.py
    module docstrings for why.

    Also syncs the final result for any fixture whose kickoff has already
    passed but has no recorded result yet — on this same cadence, not a
    separate live/in-play poller, since this project only ever bets
    pre-match. A match still in progress just isn't finished yet and gets
    checked again on the next scheduled run.
    """
    settings = get_settings()
    engine = get_engine()
    competitions = competition or list_tracked_competition_ids(engine)

    with StatsAPIClient(
        base_url=settings.thestatsapi_base_url,
        api_key=settings.thestatsapi_key,
        requests_per_minute=settings.thestatsapi_rate_limit_per_min,
    ) as client:
        result = refresh_odds_for_competitions(client, engine, competitions)

    for competition_id, summary in result.per_competition.items():
        typer.echo(
            f"{competition_id}: refreshed {summary['refreshed']}, {summary['backed']} clear the rule, "
            f"{summary['results_synced']}/{summary['awaiting_result']} result(s) synced"
        )
    typer.echo(f"Refreshed {result.total_refreshed} prediction(s), synced {result.total_results_synced} result(s).")


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    port: int = typer.Option(8000, help="Bind port."),
    reload: bool = typer.Option(False, help="Auto-reload on source changes (local dev only)."),
) -> None:
    """Run the webapp (API + built frontend, if `frontend/dist` exists) via
    uvicorn. Local dev parity with how the Render web service actually
    starts (see render.yaml's startCommand) — same entrypoint either way.
    """
    import uvicorn

    uvicorn.run("ou25_pipeline.webapp.main:app", host=host, port=port, reload=reload)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
