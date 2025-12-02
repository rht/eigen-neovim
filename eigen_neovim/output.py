"""Output generators for eigen-neovim analysis results."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, PackageLoader

from .stats import AggregatedStats

if TYPE_CHECKING:
    from .plotting import PowerLawFit

# Initialize Jinja2 environment
_jinja_env = Environment(
    loader=PackageLoader("eigen_neovim", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _format_option_setting(opt) -> str:
    """Format an option as a vim.opt.* setting string."""
    if opt.values:
        top_value, _ = opt.values.most_common(1)[0]
        if top_value in ("True", "true"):
            return f"vim.opt.{opt.name} = true"
        elif top_value in ("False", "false"):
            return f"vim.opt.{opt.name} = false"
        elif top_value.isdigit():
            return f"vim.opt.{opt.name} = {top_value}"
        elif top_value.startswith("{") or top_value.startswith("vim."):
            return f"vim.opt.{opt.name} = {top_value}"
        else:
            return f'vim.opt.{opt.name} = "{top_value}"'
    return f"vim.opt.{opt.name} = true"


def generate_markdown_report(
    stats: AggregatedStats,
    output_path: Path,
    power_law_fit: PowerLawFit | None = None,
    query: str = "filename:init.lua path:nvim",
) -> None:
    """Generate a markdown report of the analysis using Jinja2 template."""
    template = _jinja_env.get_template("readme.md.j2")

    # Prepare options data with formatted settings
    options_data = [
        {"setting": _format_option_setting(opt), "percentage": opt.percentage}
        for opt in stats.options[:100]
    ]

    # Prepare power law fit data
    power_law_data = None
    if power_law_fit and power_law_fit.r_squared > 0:
        power_law_data = {
            "coefficient": power_law_fit.coefficient,
            "exponent": power_law_fit.exponent,
            "r_squared": power_law_fit.r_squared,
            "note": (
                "Strangely it doesn't follow the power law distribution. "
                "Likely because some settings are highly correlated with the others."
            ),
        }

    # Render template
    content = template.render(
        total_configs=stats.total_configs,
        options=options_data,
        colorschemes=stats.colorschemes[:20],
        plugins=stats.plugins[:30],
        power_law_fit=power_law_data,
        date=datetime.now().strftime("%b %d %Y"),
        query=query,
    )

    output_path.write_text(content, encoding="utf-8")


def generate_eigen_lua(
    stats: AggregatedStats, output_path: Path, threshold: float = 40.0, top_n: int = 30
) -> None:
    """Generate an eigen.lua file with the most common settings.

    Uses adaptive thresholding: if the fixed threshold yields no results,
    falls back to including top N options/plugins.
    """
    # Determine effective threshold - use adaptive if fixed threshold yields nothing
    options_above_threshold = [o for o in stats.options if o.percentage >= threshold]
    if not options_above_threshold and stats.options:
        # Fall back to top N options
        effective_threshold = stats.options[min(top_n, len(stats.options)) - 1].percentage
        threshold_note = f"Top {min(top_n, len(stats.options))} options (adaptive threshold: {effective_threshold:.1f}%+)"
    else:
        effective_threshold = threshold
        threshold_note = f"Settings appearing in {threshold:.0f}%+ of configs"

    lines = [
        "-- eigen.lua",
        "-- Community-consensus Neovim configuration",
        f"-- Based on analysis of {stats.total_configs} configurations",
        f"-- Generated: {datetime.now().strftime('%Y-%m-%d')}",
        f"-- {threshold_note}",
        "",
        "local M = {}",
        "",
        "function M.setup()",
        "  -- Leader key (set before lazy.nvim)",
    ]

    # Add leader key if available
    if stats.leader_keys:
        top_leader, _ = stats.leader_keys.most_common(1)[0]
        if top_leader == " ":
            lines.append('  vim.g.mapleader = " "')
            lines.append('  vim.g.maplocalleader = " "')
        else:
            lines.append(f'  vim.g.mapleader = "{top_leader}"')

    lines.append("")
    lines.append("  -- Options")

    # Add options that meet the threshold (or top N)
    options_added = 0
    for opt in stats.options:
        if opt.percentage >= effective_threshold or options_added < top_n:
            # Skip leader keys (handled above) and internal options
            if opt.name in (
                "mapleader",
                "maplocalleader",
                "loaded_netrw",
                "loaded_netrwPlugin",
                "base46_cache",
                "have_nerd_font",
            ):
                continue
            # Determine the most common value
            if opt.values:
                top_value, _ = opt.values.most_common(1)[0]
                # Format the value appropriately
                if top_value in ("True", "true"):
                    val = "true"
                elif top_value in ("False", "false"):
                    val = "false"
                elif top_value.isdigit():
                    val = top_value
                elif top_value.startswith("{") or top_value.startswith("vim."):
                    val = top_value  # Keep tables/vim expressions as-is
                else:
                    # Escape quotes in string values
                    val = f'"{top_value}"'
                lines.append(f"  vim.opt.{opt.name} = {val}  -- {opt.percentage:.1f}%")
                options_added += 1
        else:
            break

    lines.extend(
        [
            "end",
            "",
            "-- Popular plugins (for reference)",
            "M.recommended_plugins = {",
        ]
    )

    # Add top plugins (use adaptive threshold too)
    plugin_threshold = 1.0  # Lower default for plugins
    plugins_added = 0
    for plugin in stats.plugins[:30]:
        if plugin.percentage >= plugin_threshold or plugins_added < 20:
            lines.append(f'  "{plugin.name}",  -- {plugin.percentage:.1f}%')
            plugins_added += 1

    lines.extend(
        [
            "}",
            "",
            "-- Popular colorschemes",
            "M.colorschemes = {",
        ]
    )

    # Add top colorschemes
    for cs in stats.colorschemes[:10]:
        lines.append(f'  "{cs.name}",  -- {cs.percentage:.1f}%')

    lines.extend(
        [
            "}",
            "",
            "return M",
        ]
    )

    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_lazy_plugin_spec(stats: AggregatedStats, output_path: Path) -> None:
    """Generate a lazy.nvim plugin spec with popular plugins."""
    lines = [
        "-- Popular plugins for lazy.nvim",
        f"-- Based on analysis of {stats.total_configs} configurations",
        f"-- Generated: {datetime.now().strftime('%Y-%m-%d')}",
        "",
        "return {",
    ]

    for plugin in stats.plugins[:30]:
        if plugin.percentage >= 5.0:
            lines.append(f'  {{ "{plugin.name}" }},  -- {plugin.percentage:.1f}%')

    lines.append("}")

    output_path.write_text("\n".join(lines), encoding="utf-8")
