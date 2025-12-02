"""Heuristic detection for Neovim configuration files."""

from __future__ import annotations

import re

# Patterns that strongly indicate Neovim config
NEOVIM_POSITIVE_PATTERNS = [
    # Core vim.* API
    r"\bvim\.opt\b",
    r"\bvim\.o\b",
    r"\bvim\.g\b",
    r"\bvim\.bo\b",
    r"\bvim\.wo\b",
    r"\bvim\.go\b",
    r"\bvim\.api\b",
    r"\bvim\.fn\b",
    r"\bvim\.cmd\b",
    r"\bvim\.keymap\b",
    r"\bvim\.lsp\b",
    r"\bvim\.treesitter\b",
    r"\bvim\.diagnostic\b",
    r"\bvim\.highlight\b",
    r"\bvim\.loop\b",
    r"\bvim\.uv\b",
    r"\bvim\.schedule\b",
    r"\bvim\.defer_fn\b",
    r"\bvim\.notify\b",
    r"\bvim\.inspect\b",
    r"\bvim\.tbl_",
    r"\bvim\.validate\b",
    r"\bvim\.env\b",
    # Plugin managers
    r'require\s*\(\s*["\']lazy["\']',
    r'require\s*\(\s*["\']packer["\']',
    r"Packer\s*{",
    r"lazy\.setup\s*\(",
    r"packer\.startup\s*\(",
    # Popular Neovim plugins
    r'require\s*\(\s*["\']lspconfig["\']',
    r'require\s*\(\s*["\']nvim-lspconfig["\']',
    r'require\s*\(\s*["\']telescope["\']',
    r'require\s*\(\s*["\']nvim-cmp["\']',
    r'require\s*\(\s*["\']cmp["\']',
    r'require\s*\(\s*["\']nvim-treesitter["\']',
    r'require\s*\(\s*["\']treesitter["\']',
    r'require\s*\(\s*["\']mason["\']',
    r'require\s*\(\s*["\']which-key["\']',
    r'require\s*\(\s*["\']neo-tree["\']',
    r'require\s*\(\s*["\']nvim-tree["\']',
    r'require\s*\(\s*["\']lualine["\']',
    r'require\s*\(\s*["\']bufferline["\']',
    r'require\s*\(\s*["\']gitsigns["\']',
    r'require\s*\(\s*["\']null-ls["\']',
    r'require\s*\(\s*["\']none-ls["\']',
    r'require\s*\(\s*["\']luasnip["\']',
    r'require\s*\(\s*["\']mini\.',
    # Neovim-specific patterns
    r"\bcolorscheme\b",
    r"\bmapleader\b",
    r"\blocalleader\b",
    r"\baugroup\b",
    r"\bautocmd\b",
    r"\bnvim_create_autocmd\b",
    r"\bnvim_set_keymap\b",
    r"\bnvim_buf_set_keymap\b",
]

# Patterns that strongly indicate NOT a Neovim config
NEOVIM_NEGATIVE_PATTERNS = [
    # AwesomeWM
    r"\bawful\.",
    r"\bwibox\.",
    r"\bbeautiful\.",
    r"\bnaughty\.",
    r"\bgears\.",
    r"\bruled\.",
    r"\bmenubar\.",
    r'require\s*\(\s*["\']awful["\']',
    r'require\s*\(\s*["\']wibox["\']',
    r'require\s*\(\s*["\']beautiful["\']',
    r'require\s*\(\s*["\']naughty["\']',
    r'require\s*\(\s*["\']gears["\']',
    # LÖVE game engine
    r"\blove\.load\b",
    r"\blove\.update\b",
    r"\blove\.draw\b",
    r"\blove\.keypressed\b",
    r"\blove\.graphics\b",
    r"\blove\.audio\b",
    r"\blove\.physics\b",
    # Lua library/module patterns (returning a module table)
    r"^return\s+\w+\s*$",  # Simple module return at end
    r"^local\s+M\s*=\s*\{\s*\}",  # Common module pattern
    # OpenResty/nginx
    r"\bngx\.",
    r"\bngx\.req\b",
    r"\bngx\.resp\b",
    # Luarocks/library patterns
    r"rockspec_format",
    r"package\.loaded",
    # Hammerspoon
    r"\bhs\.",
    r"\bhs\.hotkey\b",
    r"\bhs\.window\b",
    # Wezterm
    r"\bwezterm\.",
    r'require\s*\(\s*["\']wezterm["\']',
    # Conky
    r"\bconky\.",
    # mpv
    r"\bmp\.",
    r"\bmp\.command\b",
    r"\bmp\.observe_property\b",
]


def is_neovim_config(content: str, threshold: float = 0.5) -> tuple[bool, float, dict]:
    """
    Detect if Lua content is likely a Neovim configuration.

    Args:
        content: The Lua file content
        threshold: Minimum confidence score to consider it a Neovim config (0-1)

    Returns:
        Tuple of (is_neovim, confidence_score, details)
        - is_neovim: True if likely a Neovim config
        - confidence_score: 0.0 to 1.0
        - details: Dict with matched positive/negative patterns
    """
    if not content or not content.strip():
        return False, 0.0, {"positive": [], "negative": [], "reason": "empty"}

    positive_matches = []
    negative_matches = []

    # Check positive patterns
    for pattern in NEOVIM_POSITIVE_PATTERNS:
        if re.search(pattern, content, re.MULTILINE):
            positive_matches.append(pattern)

    # Check negative patterns
    for pattern in NEOVIM_NEGATIVE_PATTERNS:
        if re.search(pattern, content, re.MULTILINE):
            negative_matches.append(pattern)

    # Calculate score
    # Strong negative patterns are disqualifying
    if negative_matches and not positive_matches:
        return (
            False,
            0.0,
            {
                "positive": positive_matches,
                "negative": negative_matches,
                "reason": "negative_only",
            },
        )

    # Calculate weighted score
    positive_score = min(len(positive_matches) / 3.0, 1.0)  # 3+ matches = full score
    negative_penalty = min(len(negative_matches) * 0.3, 0.9)  # Each negative reduces score

    # If we have strong Neovim signals (vim.*), reduce negative impact
    has_vim_api = any("vim\\." in p for p in positive_matches)
    if has_vim_api:
        negative_penalty *= 0.3  # Reduce penalty if vim.* is present

    confidence = max(0.0, positive_score - negative_penalty)

    # Special cases
    # Pure module return without any vim stuff
    if re.search(r"^return\s+\{", content, re.MULTILINE) and not positive_matches:
        confidence = 0.1

    # Very short files are suspicious
    if len(content.strip()) < 50 and not positive_matches:
        confidence = 0.0

    is_neovim = confidence >= threshold

    return (
        is_neovim,
        confidence,
        {
            "positive": positive_matches,
            "negative": negative_matches,
            "reason": "score_based",
        },
    )
