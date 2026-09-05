"""CLI User Interface Styling and Banners."""

from questionary import Style
from rich.console import Console

console = Console()

ZCORE_PRIMARY = "#10b981"
ZCORE_SECONDARY = "#059669"
ZCORE_ACCENT = "#06b6d4"
ZCORE_MUTED = "#64748b"
ZCORE_TEXT = "#f8fafc"

custom_style = Style([
    ("qmark", f"fg:{ZCORE_PRIMARY} bold"),
    ("question", f"fg:{ZCORE_TEXT} bold"),
    ("answer", f"fg:{ZCORE_PRIMARY} bold"),
    ("pointer", f"fg:{ZCORE_PRIMARY} bold"),
    ("highlighted", f"fg:{ZCORE_PRIMARY} bold"),
    ("selected", f"fg:{ZCORE_PRIMARY}"),
    ("instruction", f"fg:{ZCORE_MUTED} italic"),
])


def print_banner() -> None:
    """Render the standard framework CLI banner with active versioning."""
    console.print()
    console.print(
        f"[bold {ZCORE_PRIMARY}]⚡ ZCore Framework[/bold {ZCORE_PRIMARY}] "
        f"[dim white]v0.1.0-beta.9[/dim white] "
        f"[{ZCORE_MUTED}]• Modern Modular Monolith[/{ZCORE_MUTED}]"
    )
    console.print(f" [dim {ZCORE_MUTED}]FastAPI • SQLAlchemy 2.0 • Pydantic V2[/dim {ZCORE_MUTED}]")
    console.print()


def print_step_header(title: str) -> None:
    """Print standard stylized step header box."""
    console.print(f"[dim]┌[/dim]  [bold {ZCORE_PRIMARY}]{title}[/bold {ZCORE_PRIMARY}]")
    console.print("[dim]│[/dim]")


def print_step_footer(message: str) -> None:
    """Print standard stylized step footer line."""
    console.print(f"[dim]└[/dim]  [bold {ZCORE_PRIMARY}]{message}[/bold {ZCORE_PRIMARY}]\n")


def print_cancelled() -> None:
    """Print standard stylized operation cancelled message."""
    console.print("[dim]└[/dim]  [red]Cancelled.[/red]\n")