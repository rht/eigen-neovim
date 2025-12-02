"""Tree-sitter based Lua parser for Neovim configurations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import tree_sitter_lua as tslua
from tree_sitter import Language, Node, Parser


@dataclass
class VimOption:
    """A vim.opt or vim.o setting."""

    name: str
    value: Any
    method: str  # "opt", "o", "go", "bo", "wo"


@dataclass
class Keymap:
    """A vim.keymap.set() call."""

    mode: str | list[str]
    lhs: str
    rhs: str | None
    opts: dict[str, Any] = field(default_factory=dict)


@dataclass
class PluginSpec:
    """A lazy.nvim or packer plugin specification."""

    name: str  # e.g., "nvim-telescope/telescope.nvim"
    opts: dict[str, Any] = field(default_factory=dict)


@dataclass
class Colorscheme:
    """A colorscheme setting."""

    name: str


# Known colorscheme plugin names (package name -> display name)
KNOWN_COLORSCHEMES = {
    # Popular colorschemes
    "tokyonight": "tokyonight",
    "tokyonight.nvim": "tokyonight",
    "catppuccin": "catppuccin",
    "gruvbox": "gruvbox",
    "gruvbox-material": "gruvbox-material",
    "onedark": "onedark",
    "onedarkpro": "onedark",
    "rose-pine": "rose-pine",
    "dracula": "dracula",
    "nord": "nord",
    "nightfox": "nightfox",
    "kanagawa": "kanagawa",
    "everforest": "everforest",
    "material": "material",
    "monokai": "monokai",
    "solarized": "solarized",
    "github-theme": "github",
    "vscode": "vscode",
    "one_monokai": "monokai",
    "ayu": "ayu",
    "melange": "melange",
    "oxocarbon": "oxocarbon",
    "cyberdream": "cyberdream",
    "bamboo": "bamboo",
    "lackluster": "lackluster",
    "fluoromachine": "fluoromachine",
    "moonfly": "moonfly",
    "nightfly": "nightfly",
    "sonokai": "sonokai",
    "edge": "edge",
    "aurora": "aurora",
    "palenight": "palenight",
    "onehalf": "onehalf",
    "jellybeans": "jellybeans",
    "molokai": "molokai",
    "iceberg": "iceberg",
    "tender": "tender",
    "srcery": "srcery",
    "vim-monokai-tasty": "monokai",
    "vim-one": "one",
    "papercolor": "papercolor",
    "base16": "base16",
    "doom-one": "doom-one",
    "onenord": "onenord",
    "zephyr": "zephyr",
}


@dataclass
class ParseResult:
    """Result of parsing a Lua config file."""

    options: list[VimOption] = field(default_factory=list)
    keymaps: list[Keymap] = field(default_factory=list)
    plugins: list[PluginSpec] = field(default_factory=list)
    colorschemes: list[Colorscheme] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class LuaConfigParser:
    """Parser for Neovim Lua configuration files using tree-sitter."""

    def __init__(self):
        self.language = Language(tslua.language())
        self.parser = Parser(self.language)

    def parse(self, source: str) -> ParseResult:
        """Parse a Lua configuration file."""
        result = ParseResult()
        try:
            tree = self.parser.parse(source.encode("utf-8"))
            self._walk_tree(tree.root_node, source.encode("utf-8"), result)
        except Exception as e:
            result.errors.append(str(e))
        return result

    def _get_node_text(self, node: Node, source: bytes) -> str:
        """Get the text content of a node."""
        return source[node.start_byte : node.end_byte].decode("utf-8")

    def _walk_tree(self, node: Node, source: bytes, result: ParseResult):
        """Walk the AST and extract configuration elements."""
        # Handle assignment statements: vim.opt.X = Y
        if node.type == "assignment_statement":
            self._handle_assignment(node, source, result)

        # Handle function calls: vim.keymap.set(...), require(...), etc.
        elif node.type == "function_call":
            self._handle_function_call(node, source, result)

        # Handle variable declarations: local X = ...
        elif node.type == "variable_declaration":
            self._handle_variable_declaration(node, source, result)

        # Recurse into children
        for child in node.children:
            self._walk_tree(child, source, result)

    def _handle_assignment(self, node: Node, source: bytes, result: ParseResult):
        """Handle assignment statements like vim.opt.number = true."""
        var_list = node.child_by_field_name("variable") or (
            node.children[0] if node.children else None
        )
        expr_list = node.child_by_field_name("value") or (
            node.children[-1] if len(node.children) > 1 else None
        )

        if not var_list or not expr_list:
            return

        var_text = self._get_node_text(var_list, source)
        val_text = self._get_node_text(expr_list, source)

        # Check for vim.opt.X = Y, vim.o.X = Y, vim.g.X = Y patterns
        for method in ("opt", "o", "go", "bo", "wo", "g"):
            prefix = f"vim.{method}."
            if var_text.startswith(prefix):
                option_name = var_text[len(prefix) :]
                # Handle bracket notation: vim.opt["number"]
                if "[" in option_name:
                    option_name = option_name.split("[")[0]
                result.options.append(
                    VimOption(
                        name=option_name,
                        value=self._parse_value(val_text),
                        method=method,
                    )
                )
                return

        # Check for vim.opt["X"] = Y pattern
        if "vim.opt[" in var_text or "vim.o[" in var_text:
            method = "opt" if "vim.opt[" in var_text else "o"
            # Extract the option name from bracket notation
            start = var_text.find("[")
            end = var_text.find("]")
            if start != -1 and end != -1:
                option_name = var_text[start + 1 : end].strip("\"'")
                result.options.append(
                    VimOption(
                        name=option_name,
                        value=self._parse_value(val_text),
                        method=method,
                    )
                )

    def _handle_function_call(self, node: Node, source: bytes, result: ParseResult):
        """Handle function calls like vim.keymap.set(), vim.cmd.colorscheme()."""
        call_text = self._get_node_text(node, source)

        # vim.keymap.set(mode, lhs, rhs, opts)
        if "vim.keymap.set" in call_text:
            self._extract_keymap(node, source, result)

        # vim.cmd.colorscheme("name") or vim.cmd("colorscheme name")
        elif "colorscheme" in call_text.lower():
            self._extract_colorscheme(call_text, result)

        # require("colorscheme").setup() or require("colorscheme").load()
        elif "require" in call_text:
            # Check for known colorscheme requires
            self._extract_colorscheme_from_require(call_text, result)
            # Also check for plugin manager setup
            if "lazy" in call_text or "packer" in call_text:
                self._extract_plugins(node, source, result)

    def _handle_variable_declaration(self, node: Node, source: bytes, result: ParseResult):
        """Handle variable declarations that might contain plugin specs."""
        text = self._get_node_text(node, source)
        # Look for plugin table patterns
        if "{" in text and ("/" in text or "nvim" in text.lower()):
            self._extract_plugin_table(node, source, result)

    def _extract_keymap(self, node: Node, source: bytes, result: ParseResult):
        """Extract keymap from vim.keymap.set() call."""
        # Find the arguments
        args_node = None
        for child in node.children:
            if child.type == "arguments":
                args_node = child
                break

        if not args_node:
            return

        args = [c for c in args_node.children if c.type not in ("(", ")", ",", "comment")]

        if len(args) >= 2:
            mode = self._parse_value(self._get_node_text(args[0], source))
            lhs = self._parse_value(self._get_node_text(args[1], source))
            rhs = self._parse_value(self._get_node_text(args[2], source)) if len(args) > 2 else None
            opts = {}
            if len(args) > 3:
                opts_text = self._get_node_text(args[3], source)
                if opts_text.startswith("{"):
                    opts = self._parse_table(opts_text)

            result.keymaps.append(Keymap(mode=mode, lhs=lhs, rhs=rhs, opts=opts))

    def _extract_colorscheme(self, call_text: str, result: ParseResult):
        """Extract colorscheme from vim.cmd call."""
        # Try to find the colorscheme name
        # vim.cmd.colorscheme("tokyonight")
        # vim.cmd.colorscheme "tokyonight"  (Lua call syntax)
        # vim.cmd("colorscheme tokyonight")
        # vim.cmd([[colorscheme tokyonight]])
        # vim.cmd "colorscheme tokyonight"
        import re

        patterns = [
            # vim.cmd.colorscheme("name") or vim.cmd.colorscheme "name"
            r'\.colorscheme\s*\(?\s*["\']([^"\']+)["\']',
            # vim.cmd("colorscheme name") or vim.cmd "colorscheme name"
            r'cmd\s*\(?\s*["\']colorscheme\s+([^"\']+)["\']',
            # vim.cmd([[colorscheme name]])
            r"cmd\s*\(?\s*\[\[colorscheme\s+([^\]]+)\]\]",
            # General colorscheme pattern as fallback
            r"colorscheme\s+([a-zA-Z0-9_-]+)",
        ]
        # Common false positives to filter out
        false_positives = {
            "vim",
            "cmd",
            "colorscheme",
            "that",
            "the",
            "a",
            "an",
            "my",
            "your",
            "this",
            "new",
            "old",
            "default",
            "custom",
            "config",
        }
        for pattern in patterns:
            match = re.search(pattern, call_text, re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                # Clean up any trailing characters
                name = name.split()[0] if name else name
                if name and name.lower() not in false_positives and len(name) > 1:
                    result.colorschemes.append(Colorscheme(name=name))
                    return

    def _extract_colorscheme_from_require(self, call_text: str, result: ParseResult):
        """Extract colorscheme from require() calls for known colorscheme plugins.

        Matches patterns like:
        - require("tokyonight").setup()
        - require("catppuccin").load()
        - require('gruvbox').setup({})
        """
        import re

        # Extract the module name from require("name") or require('name')
        match = re.search(r'require\s*\(\s*["\']([^"\']+)["\']', call_text)
        if not match:
            return

        module_name = match.group(1).strip()

        # Check if it's a known colorscheme
        if module_name in KNOWN_COLORSCHEMES:
            # Only count if it looks like it's being used (setup, load, or standalone)
            if ".setup" in call_text or ".load" in call_text or call_text.strip().endswith(")"):
                colorscheme_name = KNOWN_COLORSCHEMES[module_name]
                result.colorschemes.append(Colorscheme(name=colorscheme_name))

    def _extract_plugins(self, node: Node, source: bytes, result: ParseResult):
        """Extract plugin specifications from lazy.nvim or packer setup."""
        text = self._get_node_text(node, source)
        self._extract_plugin_names(text, result)

    def _extract_plugin_table(self, node: Node, source: bytes, result: ParseResult):
        """Extract plugins from a table definition."""
        text = self._get_node_text(node, source)
        self._extract_plugin_names(text, result)

    def _extract_plugin_names(self, text: str, result: ParseResult):
        """Extract plugin names from text containing plugin specs."""
        import re

        # Match patterns like "user/repo" or 'user/repo'
        pattern = r'["\']([a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+)["\']'
        matches = re.findall(pattern, text)
        for match in matches:
            # Filter out things that don't look like GitHub repos
            if "/" in match and not match.startswith("http"):
                result.plugins.append(PluginSpec(name=match))

    def _parse_value(self, text: str) -> Any:
        """Parse a Lua value into a Python value."""
        text = text.strip()
        if text == "true":
            return True
        if text == "false":
            return False
        if text == "nil":
            return None
        if text.startswith('"') or text.startswith("'"):
            return text[1:-1]
        if text.startswith("[[") and text.endswith("]]"):
            return text[2:-2]
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            pass
        return text

    def _parse_table(self, text: str) -> dict:
        """Simple table parsing for opts tables."""
        result = {}
        # Very basic parsing - just extract key = value pairs
        import re

        pairs = re.findall(r"(\w+)\s*=\s*([^,}]+)", text)
        for key, value in pairs:
            result[key] = self._parse_value(value.strip())
        return result
