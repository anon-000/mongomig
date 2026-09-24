from __future__ import annotations

from typing import TYPE_CHECKING, Any

from mongomig.cli.context import GlobalOptions, load_config, load_graph, relpath

if TYPE_CHECKING:
    from rich.console import Console

    from mongomig.output.console import Output


def run(opts: GlobalOptions, out: Output) -> None:
    config = load_config(opts, out)
    graph = load_graph(config)
    heads = set(graph.heads())

    data: list[dict[str, Any]] = []
    for rev in reversed(graph.topological_order()):
        script = graph.scripts[rev]
        data.append(
            {
                "revision": rev,
                "down_revisions": list(script.down_revisions),
                "message": script.message,
                "is_head": rev in heads,
                "is_base": script.is_base,
                "is_merge": script.is_merge,
                "reversible": script.reversible,
                "path": relpath(script.path, config),
            }
        )

    def render(con: Console) -> None:
        if not data:
            con.print("No revisions yet.")
            return
        for item in data:
            parents = ", ".join(item["down_revisions"]) or "<base>"
            tags = [t for t, on in (("head", item["is_head"]), ("merge", item["is_merge"])) if on]
            if not item["reversible"]:
                tags.append("irreversible")
            tag_text = f" [cyan]({', '.join(tags)})[/cyan]" if tags else ""
            con.print(f"{parents} -> [bold]{item['revision']}[/bold]{tag_text}, {item['message']}")
            if out.verbose:
                con.print(f"    [dim]{item['path']}[/dim]")

    out.result({"revisions": data}, render)
