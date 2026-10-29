import os


def runtime_of(payload, runtime=""):
    if runtime:
        return runtime
    if payload.get("han_runtime") in ("claude", "codex", "grok"):
        return payload["han_runtime"]
    if os.environ.get("GROK_HOOK_EVENT") or os.environ.get("GROK_SESSION_ID"):
        return "grok"
    if "hookEventName" in payload or "subagentType" in payload:
        return "grok"
    return "claude"


def normalize_payload(payload, runtime=""):
    result = dict(payload)
    runtime = runtime_of(payload, runtime)
    result["han_runtime"] = runtime
    if runtime == "grok":
        for source, target in (
            ("sessionId", "session_id"), ("transcriptPath", "transcript_path"),
            ("toolName", "tool_name"), ("toolInput", "tool_input"),
            ("stopHookActive", "stop_hook_active"), ("lastAssistantMessage", "last_assistant_message"),
        ):
            if target not in result and source in result:
                result[target] = result[source]
        if "hook_event_name" not in result:
            result["hook_event_name"] = "".join(part.title() for part in str(result.get("hookEventName", "")).split("_"))
        if not result.get("cwd"):
            result["cwd"] = result.get("workspaceRoot") or os.environ.get("GROK_WORKSPACE_ROOT", "")
        if not result.get("session_id"):
            result["session_id"] = os.environ.get("GROK_SESSION_ID", "")
    original_input = result.get("tool_input")
    tool_input = original_input
    if not isinstance(tool_input, dict):
        tool_input = {}
    tool_input = dict(tool_input)
    tool = result.get("tool_name")
    if runtime == "grok":
        tool = {
            "run_terminal_command": "Bash", "Shell": "Bash",
            "search_replace": "Edit", "hashline_edit": "Edit", "StrReplace": "Edit",
            "write": "Write", "EditNotebook": "NotebookEdit",
        }.get(tool, tool)
        if "file_path" not in tool_input:
            path = tool_input.get("path") or tool_input.get("target_notebook")
            if path:
                tool_input["file_path"] = path
        if "content" not in tool_input and "contents" in tool_input:
            tool_input["content"] = tool_input["contents"]
    if runtime in ("codex", "grok") and tool == "Bash":
        shell = str(tool_input.get("shell") or "")
        if runtime == "grok" and not shell:
            shell = os.environ.get("GROK_SHELL", "")
        shell = shell.lower()
        if "powershell" in shell or "pwsh" in shell or (os.name == "nt" and not shell):
            tool = "PowerShell"
    result["tool_name"] = tool
    if isinstance(original_input, str):
        result["tool_input"] = original_input
    else:
        result["tool_input"] = tool_input
    return result


def is_grok_subagent(payload):
    return payload.get("han_runtime") == "grok" and bool(payload.get("subagentType"))
