"""CLI entry point for eigen-neovim."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from .github_client import (
    QUERY_TEMPLATES,
    FetchState,
    GitHubClient,
    load_configs_from_disk,
    save_configs_to_disk,
)
from .output import generate_eigen_lua, generate_lazy_plugin_spec, generate_markdown_report
from .stats import StatsAggregator

# Load .env file if present
load_dotenv()

console = Console()


def get_github_token(token: str | None) -> str | None:
    """Get GitHub token from argument, GH_TOKEN, or GITHUB_TOKEN env vars."""
    if token:
        return token
    return os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")


def _parse_since(since: str) -> datetime | None:
    """Parse --since argument into a datetime.

    Supports:
    - Relative: '1y', '6m', '30d', '2w'
    - Absolute: '2024-01-01', '2024-06-15'
    """
    since = since.strip().lower()

    # Relative formats
    if since.endswith("y"):
        years = int(since[:-1])
        return datetime.now(timezone.utc) - timedelta(days=years * 365)
    if since.endswith("m"):
        months = int(since[:-1])
        return datetime.now(timezone.utc) - timedelta(days=months * 30)
    if since.endswith("w"):
        weeks = int(since[:-1])
        return datetime.now(timezone.utc) - timedelta(weeks=weeks)
    if since.endswith("d"):
        days = int(since[:-1])
        return datetime.now(timezone.utc) - timedelta(days=days)

    # Absolute date format (YYYY-MM-DD)
    try:
        return datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    return None


def _filter_configs_by_date(configs: list, since_dt: datetime) -> list:
    """Filter configs to only those with file_committed_at >= since_dt.

    Uses file_committed_at (last commit to the config file) for accurate filtering.
    Falls back to pushed_at (last repo push) if file_committed_at is not available.
    """
    filtered = []
    for config in configs:
        # Prefer file_committed_at, fall back to pushed_at
        timestamp = config.repo.file_committed_at or config.repo.pushed_at
        if not timestamp:
            # No timestamp - skip
            continue
        try:
            # Parse ISO 8601 timestamp from GitHub
            config_dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if config_dt >= since_dt:
                filtered.append(config)
        except (ValueError, AttributeError):
            # Can't parse - skip
            continue
    return filtered


@click.group()
@click.version_option()
def main():
    """Eigen-Neovim: Analyze Neovim Lua configurations at scale."""
    pass


@main.command()
@click.option(
    "--token",
    help="GitHub API token (or set GH_TOKEN/GITHUB_TOKEN in .env or environment)",
)
@click.option(
    "--query",
    default="filename:init.lua path:nvim",
    help="GitHub Code Search query (supports filename:, path:, language:, etc.)",
)
@click.option("--max-repos", default=500, help="Maximum repositories to fetch")
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("data"),
    help="Directory to save configs",
)
def fetch(token: str | None, query: str, max_repos: int, output_dir: Path):
    """Fetch Neovim configurations from GitHub."""
    token = get_github_token(token)
    if not token:
        console.print(
            "[red]Error:[/red] GitHub token required. "
            "Set GH_TOKEN in .env file, environment, or use --token"
        )
        raise SystemExit(1)

    console.print(f"[bold]Fetching up to {max_repos} configs...[/bold]")
    console.print(f"Query: [cyan]{query}[/cyan]")

    with GitHubClient(token) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching...", total=max_repos)

            def update_progress(current, total, repo):
                progress.update(
                    task,
                    completed=current,
                    total=total,
                    description=f"Fetching {repo.owner}/{repo.name}",
                )

            configs = client.fetch_configs(query, max_repos, update_progress)
            saved = list(save_configs_to_disk(configs, output_dir))

    console.print(f"\n[green]Saved {len(saved)} configs to {output_dir}[/green]")


@main.command()
@click.option(
    "--input-dir",
    type=click.Path(exists=True, path_type=Path),
    default=Path("data"),
    help="Directory containing config files",
)
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=Path("README.md"),
    help="Output markdown file",
)
@click.option(
    "--eigen-lua",
    type=click.Path(path_type=Path),
    default=Path("eigen.lua"),
    help="Output eigen.lua file",
)
@click.option(
    "--plugins-lua",
    type=click.Path(path_type=Path),
    default=None,
    help="Output plugins.lua file (lazy.nvim spec)",
)
@click.option(
    "--threshold",
    default=40.0,
    help="Minimum percentage for inclusion in eigen.lua",
)
@click.option(
    "--min-percentage",
    default=1.0,
    help="Minimum percentage for inclusion in report",
)
@click.option(
    "--plot",
    type=click.Path(path_type=Path),
    default=Path("fig.png"),
    help="Output plot file",
)
@click.option(
    "--log-scale",
    is_flag=True,
    help="Use log-log scale for plot (better for power law visualization)",
)
@click.option(
    "--since",
    type=str,
    default=None,
    help="Only include configs updated since this date (e.g., '1y', '6m', '2024-01-01')",
)
def analyze(
    input_dir: Path,
    output: Path,
    eigen_lua: Path,
    plugins_lua: Path | None,
    threshold: float,
    min_percentage: float,
    plot: Path,
    log_scale: bool,
    since: str | None,
):
    """Analyze downloaded configurations."""
    console.print(f"[bold]Analyzing configs in {input_dir}...[/bold]")

    # Parse --since filter
    since_dt = None
    if since:
        since_dt = _parse_since(since)
        if since_dt:
            console.print(f"Filtering: only configs updated after [cyan]{since_dt.date()}[/cyan]")

    aggregator = StatsAggregator()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Loading configs...", total=None)
        configs = list(load_configs_from_disk(input_dir))
        progress.update(task, description=f"Loaded {len(configs)} configs")

        # Filter by pushed_at if --since specified
        if since_dt:
            original_count = len(configs)
            configs = _filter_configs_by_date(configs, since_dt)
            skipped = original_count - len(configs)
            console.print(f"[dim]Filtered to {len(configs)} configs ({skipped} older, excluded)[/dim]")

        task2 = progress.add_task("Parsing...", total=len(configs))
        for i, config in enumerate(configs):
            aggregator.add_config(config)
            progress.update(task2, completed=i + 1)

    stats = aggregator.get_stats(min_percentage=min_percentage)

    # Display summary
    console.print("\n[bold]Analysis Summary[/bold]")
    console.print(f"Total configs analyzed: {stats.total_configs}")
    console.print(f"Skipped (not Neovim): {stats.skipped_non_neovim}")
    console.print(f"Parse errors: {stats.parse_errors}")

    # Top options table
    table = Table(title="Top 10 Options")
    table.add_column("Option", style="cyan")
    table.add_column("Usage", justify="right")
    for opt in stats.options[:10]:
        table.add_row(opt.name, f"{opt.percentage:.1f}%")
    console.print(table)

    # Top plugins table
    table = Table(title="Top 10 Plugins")
    table.add_column("Plugin", style="green")
    table.add_column("Usage", justify="right")
    for plugin in stats.plugins[:10]:
        table.add_row(plugin.name, f"{plugin.percentage:.1f}%")
    console.print(table)

    # Top colorschemes
    table = Table(title="Top 5 Colorschemes")
    table.add_column("Colorscheme", style="magenta")
    table.add_column("Usage", justify="right")
    for cs in stats.colorschemes[:5]:
        table.add_row(cs.name, f"{cs.percentage:.1f}%")
    console.print(table)

    # Generate plot with power law fit (do this first to include in report)
    power_law_fit = None
    try:
        from .plotting import generate_plot

        power_law_fit = generate_plot(stats, plot, log_scale=log_scale)
        console.print(f"\n[green]Plot saved to {plot}[/green]")
        console.print(
            f"Power law fit: y = {power_law_fit.coefficient:.1f} × x^(-{power_law_fit.exponent:.2f}), "
            f"R² = {power_law_fit.r_squared:.3f}"
        )
    except ImportError:
        console.print(
            "\n[yellow]Plot skipped: install plot dependencies with "
            "'pip install -e .[plot]'[/yellow]"
        )

    # Generate outputs
    generate_markdown_report(stats, output, power_law_fit=power_law_fit)
    console.print(f"[green]Report saved to {output}[/green]")

    generate_eigen_lua(stats, eigen_lua, threshold=threshold)
    console.print(f"[green]Eigen config saved to {eigen_lua}[/green]")

    if plugins_lua:
        generate_lazy_plugin_spec(stats, plugins_lua)
        console.print(f"[green]Plugin spec saved to {plugins_lua}[/green]")


@main.command()
@click.option(
    "--token",
    help="GitHub API token (or set GH_TOKEN/GITHUB_TOKEN in .env or environment)",
)
@click.option(
    "--query",
    default="filename:init.lua path:nvim",
    help="GitHub Code Search query (supports filename:, path:, language:, etc.)",
)
@click.option("--max-repos", default=500, help="Maximum repositories to fetch")
@click.option(
    "--output",
    type=click.Path(path_type=Path),
    default=Path("README.md"),
    help="Output markdown file",
)
@click.option(
    "--eigen-lua",
    type=click.Path(path_type=Path),
    default=Path("eigen.lua"),
    help="Output eigen.lua file",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=Path("data"),
    help="Directory to cache configs",
)
def run(
    token: str | None,
    query: str,
    max_repos: int,
    output: Path,
    eigen_lua: Path,
    cache_dir: Path,
):
    """Fetch and analyze in one step."""
    token = get_github_token(token)
    if not token:
        console.print(
            "[red]Error:[/red] GitHub token required. "
            "Set GH_TOKEN in .env file, environment, or use --token"
        )
        raise SystemExit(1)

    console.print("[bold]Eigen-Neovim Analysis[/bold]")
    console.print(f"Query: [cyan]{query}[/cyan]")
    console.print(f"Max repos: {max_repos}\n")

    aggregator = StatsAggregator()

    with GitHubClient(token) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
        ) as progress:
            task = progress.add_task("Fetching & analyzing...", total=max_repos)

            def update_progress(current, total, repo):
                progress.update(
                    task,
                    completed=current,
                    total=total,
                    description=f"{repo.owner}/{repo.name}",
                )

            configs = client.fetch_configs(query, max_repos, update_progress)
            for config in save_configs_to_disk(configs, cache_dir):
                aggregator.add_config(config)

    stats = aggregator.get_stats()

    # Display and save results
    console.print(f"\n[bold]Analyzed {stats.total_configs} configs[/bold]")
    if stats.skipped_non_neovim > 0:
        console.print(f"[dim]Skipped {stats.skipped_non_neovim} non-Neovim files[/dim]")

    table = Table(title="Top 15 Options")
    table.add_column("Option", style="cyan")
    table.add_column("Usage", justify="right")
    for opt in stats.options[:15]:
        table.add_row(opt.name, f"{opt.percentage:.1f}%")
    console.print(table)

    table = Table(title="Top 15 Plugins")
    table.add_column("Plugin", style="green")
    table.add_column("Usage", justify="right")
    for plugin in stats.plugins[:15]:
        table.add_row(plugin.name, f"{plugin.percentage:.1f}%")
    console.print(table)

    generate_markdown_report(stats, output)
    generate_eigen_lua(stats, eigen_lua)
    console.print(f"\n[green]Results saved to {output} and {eigen_lua}[/green]")


@main.command("fetch-all")
@click.option(
    "--token",
    help="GitHub API token (or set GH_TOKEN/GITHUB_TOKEN in .env or environment)",
)
@click.option(
    "--max-repos",
    default=1_000_000,
    help="Maximum repositories to fetch (default: 1 million)",
)
@click.option(
    "--output-dir",
    type=click.Path(path_type=Path),
    default=Path("data"),
    help="Directory to save configs",
)
@click.option(
    "--state-file",
    type=click.Path(path_type=Path),
    default=Path("fetch_state.json"),
    help="State file for resumption",
)
@click.option(
    "--resume/--no-resume",
    default=True,
    help="Resume from previous state if available (default: True)",
)
@click.option(
    "--reset-queries",
    is_flag=True,
    help="Reset completed queries (re-run all queries while keeping seen repos)",
)
@click.option(
    "--show-queries",
    is_flag=True,
    help="Show all query templates and exit",
)
def fetch_all(
    token: str | None,
    max_repos: int,
    output_dir: Path,
    state_file: Path,
    resume: bool,
    reset_queries: bool,
    show_queries: bool,
):
    """Fetch configs at scale with resumption support.

    Uses multiple query strategies to maximize coverage beyond GitHub's
    1000-result-per-query limit. Automatically skips already-fetched repos
    and can resume from interruptions.

    Examples:

        # Start fresh fetch
        eigen-neovim fetch-all --max-repos 10000

        # Resume interrupted fetch
        eigen-neovim fetch-all --resume

        # Start over (ignore previous state)
        eigen-neovim fetch-all --no-resume
    """
    if show_queries:
        console.print("[bold]Query templates used for fetching:[/bold]\n")
        for i, q in enumerate(QUERY_TEMPLATES, 1):
            console.print(f"  {i:2}. {q}")
        console.print(f"\n[dim]Total: {len(QUERY_TEMPLATES)} queries[/dim]")
        console.print("[dim]Each query can return up to 1000 results (GitHub limit)[/dim]")
        return

    token = get_github_token(token)
    if not token:
        console.print(
            "[red]Error:[/red] GitHub token required. "
            "Set GH_TOKEN in .env file, environment, or use --token"
        )
        raise SystemExit(1)

    # Load or create state
    state = None
    if resume and state_file.exists():
        state = FetchState.load(state_file)

        # Handle --reset-queries: keep seen_repos but restart query iteration
        if reset_queries:
            console.print(
                f"[yellow]Resetting queries (keeping {len(state.seen_repos)} seen repos)[/yellow]"
            )
            state.query_index = 0
            state.page = 1
            state.completed_queries = []
        else:
            console.print("[yellow]Resuming from previous state:[/yellow]")
            console.print(f"  - Previously fetched: {state.total_fetched}")
            console.print(f"  - Seen repos: {len(state.seen_repos)}")
            console.print(
                f"  - Completed queries: {len(state.completed_queries)}/{len(QUERY_TEMPLATES)}"
            )
            console.print(f"  - Current query index: {state.query_index}")
            console.print()

            # Check if all queries have been exhausted
            if state.query_index >= len(QUERY_TEMPLATES):
                console.print(
                    f"[green]All {len(QUERY_TEMPLATES)} queries have been completed![/green]"
                )
                console.print(f"[green]Total fetched: {state.total_fetched}[/green]")
                console.print("[dim]To start fresh, use --no-resume[/dim]")
                console.print(
                    "[dim]To re-run queries while keeping seen repos, use --reset-queries[/dim]"
                )
                return
    else:
        state = FetchState()
        if state_file.exists() and not resume:
            console.print("[yellow]Starting fresh (ignoring existing state)[/yellow]")

    # Check existing cache
    output_dir.mkdir(parents=True, exist_ok=True)
    cached_count = len(list(output_dir.glob("*.lua"))) - len(list(output_dir.glob("*.meta")))
    if cached_count > 0:
        console.print(f"[dim]Found {cached_count} cached configs in {output_dir}[/dim]")

    console.print(f"[bold]Fetching up to {max_repos:,} configs...[/bold]")
    console.print(f"[dim]Using {len(QUERY_TEMPLATES)} query strategies[/dim]")
    console.print(f"[dim]State file: {state_file}[/dim]")
    console.print(f"[dim]Output dir: {output_dir}[/dim]\n")

    def save_state(s: FetchState):
        s.save(state_file)

    saved_count = 0

    with GitHubClient(token) as client:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            console=console,
            transient=False,
        ) as progress:
            task = progress.add_task("Fetching...", total=max_repos)

            def update_progress(current, total, repo, query, cached=False):
                if repo:
                    desc = f"[{'cyan' if not cached else 'dim'}]{repo.owner}/{repo.name}[/]"
                else:
                    desc = "[dim]cached[/dim]"
                progress.update(
                    task,
                    completed=current,
                    total=total,
                    description=desc,
                )

            try:
                configs = client.fetch_configs_resumable(
                    output_dir=output_dir,
                    max_repos=max_repos,
                    state=state,
                    progress_callback=update_progress,
                    state_callback=save_state,
                )

                for config in save_configs_to_disk(configs, output_dir):
                    saved_count += 1

            except KeyboardInterrupt:
                console.print("\n[yellow]Interrupted! Saving state...[/yellow]")
                save_state(state)
                console.print(f"[green]State saved to {state_file}[/green]")
                console.print("[green]Run again with --resume to continue[/green]")
                raise SystemExit(0)

    # Final state save
    save_state(state)

    console.print(f"\n[green]Saved {saved_count} new configs to {output_dir}[/green]")
    console.print(f"[green]Total fetched: {state.total_fetched}[/green]")
    console.print(
        f"[dim]Completed queries: {len(state.completed_queries)}/{len(QUERY_TEMPLATES)}[/dim]"
    )
    console.print(f"[dim]Failed repos: {len(state.failed_repos)}[/dim]")

    # Check if we stopped early (rate limiting or other interruption)
    if state.query_index < len(QUERY_TEMPLATES):
        console.print("\n[yellow]Stopped early (likely rate limited)[/yellow]")
        console.print(
            f"[yellow]Run again with --resume to continue from query {state.query_index + 1}[/yellow]"
        )


if __name__ == "__main__":
    main()
