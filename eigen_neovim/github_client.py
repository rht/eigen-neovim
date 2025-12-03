"""GitHub REST API client for fetching Neovim configurations via Code Search."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


@dataclass
class RepoInfo:
    """Repository information."""

    owner: str
    name: str
    url: str
    stars: int
    default_branch: str = "main"
    pushed_at: str = ""  # ISO 8601 timestamp of last push


@dataclass
class ConfigFile:
    """A Neovim configuration file."""

    repo: RepoInfo
    path: str
    content: str


class RateLimitError(Exception):
    """Raised when GitHub rate limit is hit."""

    def __init__(self, reset_time: int):
        self.reset_time = reset_time
        super().__init__(f"Rate limit hit, resets at {reset_time}")


# Query strategies to maximize coverage (GitHub limits each query to 1000 results)
# We segment by stars, paths, and date ranges to get broader coverage
QUERY_TEMPLATES = [
    # By path variations
    "filename:init.lua path:.config/nvim",
    "filename:init.lua path:nvim",
    "filename:init.lua path:dotfiles",
    "filename:init.lua path:config",
    # By star ranges (high stars first - more likely quality configs)
    "filename:init.lua stars:>1000",
    "filename:init.lua stars:500..1000",
    "filename:init.lua stars:200..500",
    "filename:init.lua stars:100..200",
    "filename:init.lua stars:50..100",
    "filename:init.lua stars:20..50",
    "filename:init.lua stars:10..20",
    "filename:init.lua stars:5..10",
    "filename:init.lua stars:1..5",
    "filename:init.lua stars:0",
    # By creation year (to get different repos)
    "filename:init.lua created:2024-01-01..2024-12-31",
    "filename:init.lua created:2023-01-01..2023-12-31",
    "filename:init.lua created:2022-01-01..2022-12-31",
    "filename:init.lua created:2021-01-01..2021-12-31",
    "filename:init.lua created:2020-01-01..2020-12-31",
    "filename:init.lua created:2019-01-01..2019-12-31",
    "filename:init.lua created:2018-01-01..2018-12-31",
    "filename:init.lua created:2017-01-01..2017-12-31",
    "filename:init.lua created:<2017-01-01",
    # By push date (recently active)
    "filename:init.lua pushed:>2024-06-01",
    "filename:init.lua pushed:2024-01-01..2024-06-01",
    "filename:init.lua pushed:2023-06-01..2024-01-01",
    "filename:init.lua pushed:2023-01-01..2023-06-01",
    # Language filter - Lua files specifically
    "language:lua filename:init.lua",
    "language:lua filename:init.lua stars:>100",
    "language:lua filename:init.lua stars:10..100",
    "language:lua filename:init.lua stars:1..10",
    "language:lua filename:init.lua stars:0",
    "language:lua filename:init.lua created:2024-01-01..2024-12-31",
    "language:lua filename:init.lua created:2023-01-01..2023-12-31",
    "language:lua filename:init.lua created:2022-01-01..2022-12-31",
    "language:lua filename:init.lua created:2021-01-01..2021-12-31",
    "language:lua filename:init.lua created:<2021-01-01",
    "language:lua filename:init.lua pushed:>2024-01-01",
    "language:lua filename:init.lua pushed:2023-01-01..2024-01-01",
    "language:lua filename:init.lua pushed:<2023-01-01",
    # Topic-based queries - neovim
    "filename:init.lua topic:neovim",
    "filename:init.lua topic:neovim stars:>100",
    "filename:init.lua topic:neovim stars:10..100",
    "filename:init.lua topic:neovim stars:1..10",
    "filename:init.lua topic:neovim stars:0",
    "filename:init.lua topic:neovim created:>2023-01-01",
    "filename:init.lua topic:neovim created:<2023-01-01",
    # Topic-based queries - dotfiles
    "filename:init.lua topic:dotfiles",
    "filename:init.lua topic:dotfiles stars:>50",
    "filename:init.lua topic:dotfiles stars:10..50",
    "filename:init.lua topic:dotfiles stars:1..10",
    "filename:init.lua topic:dotfiles stars:0",
    "filename:init.lua topic:dotfiles created:>2023-01-01",
    "filename:init.lua topic:dotfiles created:<2023-01-01",
    # Topic-based queries - vim/nvim related
    "filename:init.lua topic:vim",
    "filename:init.lua topic:nvim",
    "filename:init.lua topic:lua",
    "filename:init.lua topic:config",
    "filename:init.lua topic:configuration",
    # Combined language + topic
    "language:lua topic:neovim",
    "language:lua topic:nvim",
    "language:lua topic:dotfiles",
    "language:lua topic:vim",
    # Additional path variations
    "filename:init.lua path:lua",
    "filename:init.lua path:neovim",
    "filename:init.lua path:.nvim",
    "filename:init.lua path:vim",
]


@dataclass
class FetchState:
    """Tracks fetch progress for resumption."""

    query_index: int = 0
    page: int = 1
    total_fetched: int = 0
    seen_repos: set[str] = field(default_factory=set)
    failed_repos: set[str] = field(default_factory=set)
    completed_queries: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "query_index": self.query_index,
            "page": self.page,
            "total_fetched": self.total_fetched,
            "seen_repos": list(self.seen_repos),
            "failed_repos": list(self.failed_repos),
            "completed_queries": self.completed_queries,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FetchState:
        return cls(
            query_index=data.get("query_index", 0),
            page=data.get("page", 1),
            total_fetched=data.get("total_fetched", 0),
            seen_repos=set(data.get("seen_repos", [])),
            failed_repos=set(data.get("failed_repos", [])),
            completed_queries=data.get("completed_queries", []),
        )

    def save(self, path: Path):
        """Save state to JSON file."""
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> FetchState:
        """Load state from JSON file."""
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        return cls()


class GitHubClient:
    """Client for GitHub REST API with Code Search."""

    CODE_SEARCH_URL = "https://api.github.com/search/code"
    REPOS_URL = "https://api.github.com/repos"

    def __init__(self, token: str):
        self.token = token
        self.client = httpx.Client(
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        self._last_request_time = 0.0
        # Code search API: 10 requests per minute (even when authenticated)
        # See: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
        self._min_request_interval = 6.0  # 10 req/min = 1 req per 6 seconds

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _rate_limit_wait(self):
        """Ensure we don't exceed rate limits."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _check_rate_limit(self, response: httpx.Response):
        """Check for rate limit errors and raise if needed."""
        if response.status_code == 403:
            reset_time = int(response.headers.get("X-RateLimit-Reset", 0))
            remaining = response.headers.get("X-RateLimit-Remaining", "?")
            if remaining == "0" or "rate limit" in response.text.lower():
                raise RateLimitError(reset_time)
        response.raise_for_status()

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(min=2, max=60),
        retry=retry_if_exception_type((httpx.HTTPStatusError, RateLimitError)),
    )
    def _code_search(self, query: str, page: int = 1, per_page: int = 100) -> dict:
        """Execute a code search query."""
        self._rate_limit_wait()
        response = self.client.get(
            self.CODE_SEARCH_URL,
            params={"q": query, "page": page, "per_page": per_page},
        )
        self._check_rate_limit(response)
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    def _get_repo_info(self, owner: str, name: str) -> dict:
        """Get repository information including stars and default branch."""
        self._rate_limit_wait()
        response = self.client.get(f"{self.REPOS_URL}/{owner}/{name}")
        self._check_rate_limit(response)
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=1, max=10),
        retry=retry_if_exception_type(httpx.HTTPStatusError),
    )
    def _get_raw_content(self, owner: str, name: str, branch: str, path: str) -> str:
        """Fetch raw file content from GitHub."""
        url = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/{path}"
        response = self.client.get(url)
        response.raise_for_status()
        return response.text

    def search_configs(
        self,
        query: str = "filename:init.lua path:nvim",
        max_repos: int = 500,
        progress_callback=None,
    ) -> Iterator[ConfigFile]:
        """
        Search for Neovim configs using GitHub Code Search API.

        Args:
            query: GitHub code search query (e.g., "filename:init.lua path:nvim")
            max_repos: Maximum number of unique repositories to fetch
            progress_callback: Optional callback(current, total, repo) for progress

        Yields:
            ConfigFile objects with repo info and file content
        """
        seen_repos: set[str] = set()
        page = 1
        fetched = 0

        while fetched < max_repos:
            try:
                data = self._code_search(query, page=page, per_page=100)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 422:
                    # Search returns 422 when no results or invalid query
                    break
                raise

            items = data.get("items", [])
            if not items:
                break

            for item in items:
                if fetched >= max_repos:
                    break

                repo_data = item.get("repository", {})
                full_name = repo_data.get("full_name", "")

                # Skip already processed repos
                if full_name in seen_repos:
                    continue
                seen_repos.add(full_name)

                parts = full_name.split("/", 1)
                if len(parts) != 2:
                    continue
                owner, name = parts

                # Get repo details (stars, default branch, pushed_at)
                try:
                    repo_info = self._get_repo_info(owner, name)
                    stars = repo_info.get("stargazers_count", 0)
                    default_branch = repo_info.get("default_branch", "main")
                    pushed_at = repo_info.get("pushed_at", "")
                except Exception:
                    stars = 0
                    default_branch = "main"
                    pushed_at = ""

                repo = RepoInfo(
                    owner=owner,
                    name=name,
                    url=repo_data.get("html_url", f"https://github.com/{full_name}"),
                    stars=stars,
                    default_branch=default_branch,
                    pushed_at=pushed_at,
                )

                if progress_callback:
                    progress_callback(fetched + 1, max_repos, repo)

                # Get file content
                file_path = item.get("path", "")
                try:
                    content = self._get_raw_content(owner, name, default_branch, file_path)
                    fetched += 1
                    yield ConfigFile(repo=repo, path=file_path, content=content)
                except Exception:
                    # Skip files we can't fetch
                    continue

            # Check if there are more pages
            total_count = data.get("total_count", 0)
            if page * 100 >= total_count or page >= 10:
                # GitHub limits code search to first 1000 results (10 pages)
                break
            page += 1

    def fetch_configs_resumable(
        self,
        output_dir: Path,
        max_repos: int = 1_000_000,
        state: FetchState | None = None,
        progress_callback=None,
        state_callback=None,
        custom_queries: list[str] | None = None,
    ) -> Iterator[ConfigFile]:
        """
        Fetch configs with resumption support and caching.

        Args:
            output_dir: Directory where configs are saved (for cache checking)
            max_repos: Maximum total repos to fetch
            state: FetchState for resumption (or None to start fresh)
            progress_callback: Optional callback(current, total, repo, query) for progress
            state_callback: Optional callback(state) called periodically to save state
            custom_queries: Optional list of queries to use instead of QUERY_TEMPLATES

        Yields:
            ConfigFile objects (only for newly fetched, not cached)
        """
        if state is None:
            state = FetchState()

        # Load existing repos from disk cache
        existing_repos = self._get_cached_repos(output_dir)
        state.seen_repos.update(existing_repos)

        queries = custom_queries or QUERY_TEMPLATES

        while state.query_index < len(queries) and state.total_fetched < max_repos:
            query = queries[state.query_index]

            if query in state.completed_queries:
                state.query_index += 1
                state.page = 1
                continue

            # Process pages for current query
            query_exhausted = False
            rate_limited = False
            while state.total_fetched < max_repos:
                try:
                    data = self._code_search(query, page=state.page, per_page=100)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 422:
                        # Invalid query or no results - query is done
                        query_exhausted = True
                        break
                    raise
                except (RateLimitError, RetryError):
                    # Rate limited (or retries exhausted) - don't mark query as completed
                    rate_limited = True
                    break

                items = data.get("items", [])
                if not items:
                    # No more results - query is truly exhausted
                    query_exhausted = True
                    break

                for item in items:
                    if state.total_fetched >= max_repos:
                        break

                    repo_data = item.get("repository", {})
                    full_name = repo_data.get("full_name", "")

                    # Skip already processed or cached repos
                    if full_name in state.seen_repos:
                        continue
                    state.seen_repos.add(full_name)

                    parts = full_name.split("/", 1)
                    if len(parts) != 2:
                        continue
                    owner, name = parts

                    # Check disk cache
                    cache_file = output_dir / f"{owner}__{name}.lua"
                    if cache_file.exists():
                        state.total_fetched += 1
                        if progress_callback:
                            progress_callback(
                                state.total_fetched, max_repos, None, query, cached=True
                            )
                        continue

                    # Get repo details
                    try:
                        repo_info_data = self._get_repo_info(owner, name)
                        stars = repo_info_data.get("stargazers_count", 0)
                        default_branch = repo_info_data.get("default_branch", "main")
                        pushed_at = repo_info_data.get("pushed_at", "")
                    except Exception:
                        stars = 0
                        default_branch = "main"
                        pushed_at = ""

                    repo = RepoInfo(
                        owner=owner,
                        name=name,
                        url=repo_data.get("html_url", f"https://github.com/{full_name}"),
                        stars=stars,
                        default_branch=default_branch,
                        pushed_at=pushed_at,
                    )

                    # Get file content
                    file_path = item.get("path", "")
                    try:
                        content = self._get_raw_content(owner, name, default_branch, file_path)
                        state.total_fetched += 1

                        if progress_callback:
                            progress_callback(
                                state.total_fetched, max_repos, repo, query, cached=False
                            )

                        # Save state periodically (every 10 repos)
                        if state_callback and state.total_fetched % 10 == 0:
                            state_callback(state)

                        yield ConfigFile(repo=repo, path=file_path, content=content)

                    except Exception:
                        state.failed_repos.add(full_name)
                        continue

                # Check pagination limits
                total_count = data.get("total_count", 0)
                if state.page * 100 >= total_count or state.page >= 10:
                    # Hit GitHub's limit or exhausted results
                    query_exhausted = True
                    break
                state.page += 1

                # Save state after each page
                if state_callback:
                    state_callback(state)

            # Only mark query as completed if truly exhausted
            if query_exhausted:
                state.completed_queries.append(query)
                state.query_index += 1
                state.page = 1

            if state_callback:
                state_callback(state)

            # If rate limited, exit entirely and let user resume later
            if rate_limited:
                return

    def _get_cached_repos(self, output_dir: Path) -> set[str]:
        """Get set of repo full names already cached on disk."""
        cached = set()
        if not output_dir.exists():
            return cached

        for filepath in output_dir.glob("*.lua"):
            if filepath.name.endswith(".meta"):
                continue
            # Filename format: owner__name.lua
            stem = filepath.stem
            if "__" in stem:
                owner, name = stem.split("__", 1)
                cached.add(f"{owner}/{name}")
        return cached

    def fetch_configs(
        self,
        query: str = "filename:init.lua path:nvim",
        max_repos: int = 500,
        progress_callback=None,
    ) -> Iterator[ConfigFile]:
        """
        Fetch Neovim configurations from GitHub.

        This is an alias for search_configs() for backwards compatibility.
        """
        yield from self.search_configs(query, max_repos, progress_callback)


def save_configs_to_disk(configs: Iterator[ConfigFile], output_dir: Path):
    """Save fetched configs to disk for caching."""
    output_dir.mkdir(parents=True, exist_ok=True)
    for config in configs:
        filename = f"{config.repo.owner}__{config.repo.name}.lua"
        filepath = output_dir / filename
        filepath.write_text(config.content, encoding="utf-8")
        # Save metadata
        meta_path = output_dir / f"{filename}.meta"
        meta_path.write_text(
            f"url={config.repo.url}\nstars={config.repo.stars}\npath={config.path}\npushed_at={config.repo.pushed_at}\n"
        )
        yield config


def load_configs_from_disk(input_dir: Path) -> Iterator[ConfigFile]:
    """Load configs from disk cache."""
    for filepath in input_dir.glob("*.lua"):
        if filepath.name.endswith(".meta"):
            continue
        meta_path = filepath.with_suffix(".lua.meta")
        content = filepath.read_text(encoding="utf-8")

        # Parse metadata if exists
        meta = {}
        if meta_path.exists():
            for line in meta_path.read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    meta[k] = v

        parts = filepath.stem.split("__", 1)
        owner = parts[0] if len(parts) > 1 else "unknown"
        name = parts[1] if len(parts) > 1 else filepath.stem

        yield ConfigFile(
            repo=RepoInfo(
                owner=owner,
                name=name,
                url=meta.get("url", ""),
                stars=int(meta.get("stars", 0)),
                pushed_at=meta.get("pushed_at", ""),
            ),
            path=meta.get("path", "init.lua"),
            content=content,
        )
