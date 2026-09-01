"""The MCP server. Typed tools only; the agent never sees bpy.

    ./.venv/bin/python -m server.mcp_server          # stdio, for Claude Code (.mcp.json)

Every tool is a plain function in server/tools/*; this file only registers them
so the surface is visible in one place and stays small.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import Image, MCPServer   # noqa: E402

from server.tools import (asset_tools, director_tools, learning_tools,   # noqa: E402
                          model_tools, sequence_tools, spec_tools)

server = MCPServer("blendy", instructions=(
    "Blendy: direct 3D animation in Blender by editing a declarative shot spec. "
    "Loop: patch_spec -> compile_scene / render_preview -> read_image -> accept or revise. "
    "Never batch edits before looking. Landmarks are '@<asset_id>.<name>'; list_landmarks tells you "
    "the vocabulary. Errors name the entity id and stage."))

SURFACE = {
    spec_tools: ["open_spec", "read_spec", "validate_spec", "patch_spec", "acquire_write_lock",
                 "release_write_lock", "compile_scene", "render_preview", "render_final",
                 "checkpoint", "restore", "list_checkpoints"],
    asset_tools: ["search_assets", "resolve_asset", "get_asset_profile", "list_landmarks",
                  "add_sockets", "list_clips", "dialogue_status", "extract_phonemes"],
    sequence_tools: ["read_bible", "write_bible", "read_breakdown", "read_script",
                     ("validate_breakdown", "validate_breakdown_tool"), "write_breakdown",
                     "ingest_list", "read_shot", "validate_continuity", "new_shot_from_breakdown"],
    model_tools: ["list_models", "new_model", "read_model", ("validate_model", "validate_model_tool"),
                  "patch_model", "preview_model", "model_profile", "checkpoint_model", "restore_model"],
    director_tools: ["export_proxy", "list_takes", "apply_take", "promote_take", "list_presets",
                     "apply_preset", "promote_preset"],
    learning_tools: ["write_incident", "list_incidents", "triage_incident", "resolve_incident",
                     "incident_patterns", "read_skill", "propose_skill_edit", "list_proposals",
                     "evaluate_proposal", "apply_proposal", "run_evals", "add_correction",
                     "retrieve_corrections", "render_status", "render_queue_run"],
}


def register() -> list[str]:
    names = []
    for module, entries in SURFACE.items():
        for entry in entries:
            public, attr = (entry, entry) if isinstance(entry, str) else entry
            fn = getattr(module, attr)
            server.tool(name=public, description=inspect.getdoc(fn) or public)(fn)
            names.append(public)

    @server.tool(name="read_image", description="Read a preview/render image back. Look before deciding.")
    def read_image(path: str) -> Image:
        return Image(path=spec_tools.read_image_path(path))
    names.append("read_image")
    return names


TOOL_NAMES = register()


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
