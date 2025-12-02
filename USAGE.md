# Eigen-Neovim Usage Guide

Analyze Neovim Lua configurations at scale using AST-based parsing.

## Installation

```bash
pip install -e .
```

## Configuration

Create a `.env` file in the project root:

```bash
GH_TOKEN=ghp_your_github_token_here
```

Or export the environment variable:

```bash
export GH_TOKEN=ghp_your_github_token_here
```

Token is required for `fetch` and `run` commands. Get one at https://github.com/settings/tokens (no scopes needed for public repos).

## Commands

### `eigen-neovim fetch`

Download Neovim configurations from GitHub.

```bash
eigen-neovim fetch [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--token` | env | GitHub API token |
| `--query` | `filename:init.lua path:nvim` | GitHub Code Search query |
| `--max-repos` | 500 | Maximum repositories to fetch |
| `--output-dir` | `data/` | Directory to save configs |

**Examples:**

```bash
# Basic fetch
eigen-neovim fetch --max-repos 100

# Search in dotfiles repos
eigen-neovim fetch --query "filename:init.lua path:dotfiles"

# Add language filter
eigen-neovim fetch --query "filename:init.lua path:nvim language:Lua"
```

### `eigen-neovim fetch-all`

Fetch configs at scale with resumption support. Uses multiple query strategies to maximize coverage.

```bash
eigen-neovim fetch-all [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--token` | env | GitHub API token |
| `--max-repos` | 1,000,000 | Maximum repositories to fetch |
| `--output-dir` | `data/` | Directory to save configs |
| `--state-file` | `fetch_state.json` | State file for resumption |
| `--resume/--no-resume` | `--resume` | Resume from previous state |
| `--show-queries` | - | Show query templates and exit |

**Examples:**

```bash
# Start large-scale fetch
eigen-neovim fetch-all --max-repos 100000

# Resume after interruption (Ctrl+C)
eigen-neovim fetch-all

# Start fresh, ignoring previous state
eigen-neovim fetch-all --no-resume

# See all query strategies used
eigen-neovim fetch-all --show-queries
```

**Features:**
- Automatic resumption after interruption (Ctrl+C safe)
- Skips already-fetched repos (disk cache)
- Uses 27+ query strategies (by stars, dates, paths) to bypass GitHub's 1000-result limit
- Progress saved every 10 repos and after each query page

### `eigen-neovim analyze`

Analyze previously downloaded configurations.

```bash
eigen-neovim analyze [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--input-dir` | `data/` | Directory containing config files |
| `--output` | `README.md` | Output markdown report |
| `--eigen-lua` | `eigen.lua` | Output consensus config |
| `--plugins-lua` | none | Output lazy.nvim plugin spec |
| `--threshold` | 40.0 | Min % for eigen.lua inclusion |
| `--min-percentage` | 1.0 | Min % for report inclusion |

**Examples:**

```bash
# Basic analysis
eigen-neovim analyze

# Lower threshold, generate plugin spec
eigen-neovim analyze --threshold 30 --plugins-lua plugins.lua

# Analyze configs in custom directory
eigen-neovim analyze --input-dir ./my-configs
```

### `eigen-neovim run`

Fetch and analyze in one step.

```bash
eigen-neovim run [OPTIONS]
```

| Option | Default | Description |
|--------|---------|-------------|
| `--token` | env | GitHub API token |
| `--query` | `filename:init.lua path:nvim` | GitHub Code Search query |
| `--max-repos` | 500 | Maximum repositories to fetch |
| `--output` | `README.md` | Output markdown report |
| `--eigen-lua` | `eigen.lua` | Output consensus config |
| `--cache-dir` | `data/` | Directory to cache configs |

**Example:**

```bash
eigen-neovim run --max-repos 100
```

## Output Files

| File | Description |
|------|-------------|
| `README.md` | Full statistics report with ranked options, plugins, colorschemes |
| `eigen.lua` | Lua module with consensus settings (usage >= threshold) |
| `plugins.lua` | lazy.nvim compatible plugin spec (optional) |
| `data/` | Cached config files for re-analysis |

## Using eigen.lua

```lua
-- In your init.lua
require("eigen").setup()

-- Or cherry-pick settings
local eigen = require("eigen")
-- eigen.recommended_plugins contains popular plugin list
```

## What Gets Analyzed

The AST parser extracts:

- **Options**: `vim.opt.*`, `vim.o.*`, `vim.go.*`, `vim.bo.*`, `vim.wo.*`
- **Global variables**: `vim.g.*` (including `mapleader`)
- **Keymaps**: `vim.keymap.set()` calls
- **Plugins**: GitHub repo references in lazy.nvim/packer specs
- **Colorschemes**: `vim.cmd.colorscheme()` and `vim.cmd("colorscheme ...")`

## Tips

- Start with `--max-repos 100` to test quickly
- Use `analyze` to iterate on existing data without re-fetching
- Configs are cached in `data/` with `.meta` files containing repo info
- Code Search rate limits: 30 requests/minute (authenticated), throttled automatically
- GitHub limits Code Search to first 1000 results per query
