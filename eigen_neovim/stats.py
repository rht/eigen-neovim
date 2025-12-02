"""Statistics aggregation and analysis for Neovim configurations."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

import polars as pl

from .detector import is_neovim_config
from .github_client import ConfigFile
from .parser import LuaConfigParser, ParseResult


@dataclass
class OptionStat:
    """Statistics for a single vim option."""

    name: str
    count: int
    percentage: float
    values: Counter  # Distribution of values


@dataclass
class PluginStat:
    """Statistics for a plugin."""

    name: str
    count: int
    percentage: float


@dataclass
class KeymapStat:
    """Statistics for a keymap pattern."""

    lhs: str
    mode: str
    count: int
    percentage: float


@dataclass
class AggregatedStats:
    """Aggregated statistics from all parsed configs."""

    total_configs: int
    options: list[OptionStat]
    plugins: list[PluginStat]
    colorschemes: list[PluginStat]  # Reuse PluginStat structure
    keymaps: list[KeymapStat]
    leader_keys: Counter
    parse_errors: int
    skipped_non_neovim: int = 0


class StatsAggregator:
    """Aggregates statistics from multiple config files."""

    def __init__(self, skip_non_neovim: bool = True, detection_threshold: float = 0.5):
        self.parser = LuaConfigParser()
        self._skip_non_neovim = skip_non_neovim
        self._detection_threshold = detection_threshold
        self._options: Counter = Counter()
        self._option_values: dict[str, Counter] = {}
        self._plugins: Counter = Counter()
        self._colorschemes: Counter = Counter()
        self._keymaps: Counter = Counter()  # (mode, lhs) -> count
        self._leader_keys: Counter = Counter()
        self._total_configs = 0
        self._parse_errors = 0
        self._skipped_non_neovim = 0

    def add_config(self, config: ConfigFile) -> ParseResult | None:
        """Parse and add a config to the statistics.

        Returns None if the config was skipped (not a Neovim config).
        """
        # Check if this is actually a Neovim config
        if self._skip_non_neovim:
            is_nvim, confidence, _ = is_neovim_config(
                config.content, threshold=self._detection_threshold
            )
            if not is_nvim:
                self._skipped_non_neovim += 1
                return None

        result = self.parser.parse(config.content)
        self._total_configs += 1

        if result.errors:
            self._parse_errors += 1

        # Aggregate options
        for opt in result.options:
            self._options[opt.name] += 1
            if opt.name not in self._option_values:
                self._option_values[opt.name] = Counter()
            # Convert value to string for counting
            val_str = str(opt.value) if opt.value is not None else "nil"
            self._option_values[opt.name][val_str] += 1

            # Track mapleader separately
            if opt.name == "mapleader" and opt.method == "g":
                self._leader_keys[str(opt.value)] += 1

        # Aggregate plugins
        seen_plugins = set()  # Dedupe within a single config
        for plugin in result.plugins:
            if plugin.name not in seen_plugins:
                self._plugins[plugin.name] += 1
                seen_plugins.add(plugin.name)

        # Aggregate colorschemes
        for cs in result.colorschemes:
            self._colorschemes[cs.name] += 1

        # Aggregate keymaps
        for km in result.keymaps:
            mode = km.mode if isinstance(km.mode, str) else ",".join(km.mode)
            self._keymaps[(mode, km.lhs)] += 1

        return result

    def add_configs(self, configs: Iterator[ConfigFile], progress_callback=None) -> None:
        """Add multiple configs to the statistics."""
        for i, config in enumerate(configs):
            self.add_config(config)
            if progress_callback:
                progress_callback(i + 1, config)

    def get_stats(self, min_percentage: float = 1.0) -> AggregatedStats:
        """Get aggregated statistics."""
        total = self._total_configs or 1  # Avoid division by zero

        # Build option stats
        options = []
        for name, count in self._options.most_common():
            pct = count * 100.0 / total
            if pct >= min_percentage:
                options.append(
                    OptionStat(
                        name=name,
                        count=count,
                        percentage=pct,
                        values=self._option_values.get(name, Counter()),
                    )
                )

        # Build plugin stats
        plugins = []
        for name, count in self._plugins.most_common():
            pct = count * 100.0 / total
            if pct >= min_percentage:
                plugins.append(PluginStat(name=name, count=count, percentage=pct))

        # Build colorscheme stats (no min_percentage filter - colorschemes are often sparse)
        colorschemes = []
        for name, count in self._colorschemes.most_common():
            pct = count * 100.0 / total
            colorschemes.append(PluginStat(name=name, count=count, percentage=pct))

        # Build keymap stats
        keymaps = []
        for (mode, lhs), count in self._keymaps.most_common(100):
            pct = count * 100.0 / total
            if pct >= min_percentage:
                keymaps.append(KeymapStat(lhs=lhs, mode=mode, count=count, percentage=pct))

        return AggregatedStats(
            total_configs=self._total_configs,
            options=options,
            plugins=plugins,
            colorschemes=colorschemes,
            keymaps=keymaps,
            leader_keys=self._leader_keys,
            parse_errors=self._parse_errors,
            skipped_non_neovim=self._skipped_non_neovim,
        )

    def to_dataframe(self) -> dict[str, pl.DataFrame]:
        """Export statistics as Polars DataFrames."""
        total = self._total_configs or 1

        options_df = pl.DataFrame(
            {
                "option": list(self._options.keys()),
                "count": list(self._options.values()),
                "percentage": [c * 100.0 / total for c in self._options.values()],
            }
        ).sort("count", descending=True)

        plugins_df = pl.DataFrame(
            {
                "plugin": list(self._plugins.keys()),
                "count": list(self._plugins.values()),
                "percentage": [c * 100.0 / total for c in self._plugins.values()],
            }
        ).sort("count", descending=True)

        colorschemes_df = pl.DataFrame(
            {
                "colorscheme": list(self._colorschemes.keys()),
                "count": list(self._colorschemes.values()),
                "percentage": [c * 100.0 / total for c in self._colorschemes.values()],
            }
        ).sort("count", descending=True)

        return {
            "options": options_df,
            "plugins": plugins_df,
            "colorschemes": colorschemes_df,
        }
