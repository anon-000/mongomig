from __future__ import annotations

from typing import TYPE_CHECKING

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output) -> None:
    config = load_config(opts, out)
    graph = load_graph(config)
    heads = graph.heads()
    data = [
        {
            "revision": rev,
            "message": graph.scripts[rev].message,
            "path": relpath(graph.scripts[rev].path, config),
        }
        for rev in heads
    ]

    def render(con: Console) -> None:
        if not heads:
            con.print('No revisions yet. Create one with: mongomig revision -m "initial"')
            return
        for item in data:
            con.print(f"[bold]{item['revision']}[/bold] (head)  {item['message']}")
            if out.verbose:
                con.print(f"  [dim]{item['path']}[/dim]")
        if len(heads) > 1:
            con.print(
                f"\n[yellow]{len(heads)} heads:[/yellow] the history has diverged "
                "(e.g. two branches each added a revision). Merge them before upgrading."
            )

    out.result({"heads": data}, render)
