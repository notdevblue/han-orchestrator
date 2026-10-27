import ast
import os
import re


STRUCTURED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
PATCH_TOOLS = ("apply_patch", "ApplyPatch")
QUESTION_NAME = re.compile(r"(?:ask|confirm)-[^/]*\.md$", re.IGNORECASE)
PATCH_TARGET = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE)
SHELL_TOKEN = re.compile(r'''(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s<>]+)|(?:\d*>>?|\*>>?)''')
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?[^\r\n]*\r?\n(.*?)\r?\n\1(?=\s|$)", re.DOTALL)
HERESTRING = re.compile(r"@['\"]\r?\n(.*?)\r?\n['\"]@", re.DOTALL)
PYTHON_NAME = re.compile(r"(?:python(?:\d+(?:\.\d+)*)?|py)(?:\.exe)?$", re.IGNORECASE)
WRITE_MODES = frozenset("wax+")
PATH_METHODS = frozenset(("write_text", "write_bytes", "touch", "unlink", "rmdir", "mkdir"))
SHELL_WRITERS = frozenset(("set-content", "add-content", "out-file", "tee-object", "sc", "ac"))
SHELL_COPY = frozenset(("cp", "copy", "copy-item", "move", "mv", "move-item", "ren", "rename-item"))
SHELL_REMOVE = frozenset(("rm", "del", "erase", "remove-item", "rmdir", "rd", "touch", "mkdir"))
BUNDLED_CLI = os.path.join(os.path.dirname(__file__), "..", "skills", "mission", "scripts", "mission_state.py")
QUESTION_ACTIONS = frozenset(("publish", "answer", "restore", "render"))


def absolute_path(path, cwd):
    return os.path.abspath(os.path.join(cwd, path.replace("\\", os.sep)))


def canonical_path(path):
    return os.path.normpath(os.path.realpath(path)).replace("\\", "/").rstrip("/").casefold()


def input_mapping(payload):
    value = payload.get("tool_input")
    return value if isinstance(value, dict) else {}


def resolved_payload(payload):
    tool_input = input_mapping(payload)
    tool_cwd = tool_input.get("cwd")
    if not isinstance(tool_cwd, str) or not tool_cwd:
        return payload
    return {
        **payload,
        "cwd": absolute_path(tool_cwd, payload.get("cwd") or os.getcwd()),
        "tool_input": {key: value for key, value in tool_input.items() if key != "cwd"},
    }


def adapter_contexts(payload, targets, guard):
    payload = resolved_payload(payload)
    runtime = payload.get("han_runtime", "claude")
    cwd = payload.get("cwd") or os.getcwd()
    starts = [cwd]
    if runtime == "claude" and os.environ.get("CLAUDE_PROJECT_DIR"):
        starts.append(os.environ["CLAUDE_PROJECT_DIR"])
    for target in targets:
        path = absolute_path(target, cwd)
        starts.extend((os.path.dirname(path), os.path.dirname(os.path.realpath(path))))
    found = {}
    pending = []
    for start in dict.fromkeys(starts):
        chain = guard.ancestors(start)
        located = []
        for folder in chain:
            for relative in guard.adapter_relatives(runtime):
                path = os.path.join(folder, *relative)
                if os.path.isfile(path):
                    located.append(guard.Located(folder, path))
                    break
            if located:
                break
        if located:
            item = located[0]
            found[(canonical_path(item.project), canonical_path(item.adapter_path))] = item
        else:
            pending.append(chain)
    errors = []
    if pending:
        try:
            registry = guard.resolve_registry(runtime)
            entries = guard.load_registry(registry.path) if registry.path else []
        except guard.GuardOff as error:
            errors.append(error)
            entries = []
        for chain in pending:
            for folder in chain:
                matches = [entry for entry in entries if any(guard.root_matches(folder, root) for root in entry["roots"])]
                if matches:
                    item = guard.Located(folder, os.path.join(registry.path, matches[0]["adapter"], "adapter.json"))
                    found[(canonical_path(item.project), canonical_path(item.adapter_path))] = item
                    break
    contexts = []
    for located in found.values():
        try:
            contexts.append((located, guard.load_adapter(located.adapter_path, runtime=runtime)))
        except guard.GuardOff as error:
            errors.append(error)
    if not contexts and errors:
        raise errors[0]
    return contexts


def protected_path(target, cwd, contexts, guard):
    path = absolute_path(target, cwd)
    candidates = {canonical_path(path), os.path.normpath(path).replace("\\", "/").rstrip("/").casefold()}
    for located, adapter in contexts:
        state = guard.state_dir_of(located.project, adapter)
        roots = {canonical_path(state), os.path.normpath(os.path.abspath(state)).replace("\\", "/").rstrip("/").casefold()}
        for candidate in candidates:
            for root in roots:
                if not candidate.startswith(root + "/"):
                    continue
                parts = candidate[len(root) + 1:].split("/")
                if len(parts) >= 2 and (parts[1] == "questions" or (len(parts) == 2 and QUESTION_NAME.fullmatch(parts[1]))):
                    return True
    return False


def protected_tree(target, cwd, contexts, guard):
    path = canonical_path(absolute_path(target, cwd))
    for located, adapter in contexts:
        state_dir = guard.state_dir_of(located.project, adapter)
        state = canonical_path(state_dir)
        ancestor = path == state or state.startswith(path + "/")
        mission_root = path.startswith(state + "/") and "/" not in path[len(state) + 1:]
        if not ancestor and not mission_root:
            continue
        if not os.path.isdir(state_dir):
            continue
        with os.scandir(state_dir) as missions:
            for mission in missions:
                if not mission.is_dir():
                    continue
                mission_path = canonical_path(mission.path)
                if path != mission_path and not mission_path.startswith(path + "/"):
                    continue
                with os.scandir(mission.path) as children:
                    if any(child.name.casefold() == "questions" or QUESTION_NAME.fullmatch(child.name) for child in children):
                        return True
    return protected_path(target, cwd, contexts, guard)


def tool_targets(payload):
    tool_input = input_mapping(payload)
    if payload.get("tool_name") in STRUCTURED_TOOLS:
        return [value for key in ("file_path", "path", "notebook_path") if isinstance(value := tool_input.get(key), str) and value]
    if payload.get("tool_name") in PATCH_TOOLS:
        if isinstance(payload.get("tool_input"), str):
            return PATCH_TARGET.findall(payload["tool_input"])
        for key in ("command", "patch", "input", "patch_text"):
            if isinstance(tool_input.get(key), str):
                return PATCH_TARGET.findall(tool_input[key])
    return []


def constant_path(node, aliases):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return aliases.get(node.id, "")
    if isinstance(node, ast.BinOp):
        left, right = constant_path(node.left, aliases), constant_path(node.right, aliases)
        if left and right and isinstance(node.op, ast.Div):
            return os.path.join(left, right)
        if left and right and isinstance(node.op, ast.Add):
            return left + right
    if isinstance(node, ast.Call):
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in ("Path", "PurePath", "PureWindowsPath", "PurePosixPath"):
            values = [constant_path(arg, aliases) for arg in node.args]
            return os.path.join(*values) if values and all(values) else ""
        if isinstance(node.func, ast.Attribute):
            base = constant_path(node.func.value, aliases)
            if base and name in ("resolve", "absolute", "expanduser"):
                return base
            if base and name == "joinpath":
                values = [constant_path(arg, aliases) for arg in node.args]
                return os.path.join(base, *values) if all(values) else ""
            if name == "join" and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "path":
                values = [constant_path(arg, aliases) for arg in node.args]
                return os.path.join(*values) if values and all(values) else ""
    return ""


def python_targets(source):
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return [], []
    aliases = {}
    targets = []
    subtrees = []
    for node in ordered_nodes(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            names = node.targets if isinstance(node, ast.Assign) else [node.target]
            for name in names:
                if isinstance(name, ast.Name):
                    aliases[name.id] = constant_path(node.value, aliases)
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        receiver = constant_path(node.func.value, aliases) if isinstance(node.func, ast.Attribute) else ""
        args = [constant_path(arg, aliases) for arg in node.args]
        keywords = {item.arg: constant_path(item.value, aliases) for item in node.keywords}
        if name in PATH_METHODS and receiver:
            targets.append(receiver)
            if name in ("unlink", "rmdir"):
                subtrees.append(receiver)
        elif name == "open":
            path = receiver or (args[0] if args else keywords.get("file", ""))
            offset = 0 if receiver else 1
            mode = args[offset] if len(args) > offset else keywords.get("mode", "r")
            if any(char in mode for char in WRITE_MODES):
                targets.append(path)
        elif name in ("rename", "replace", "move"):
            paths = ([receiver] + args[:1]) if receiver else args[:2]
            targets.extend(paths)
            subtrees.extend(paths)
        elif name in ("copy", "copy2", "copyfile", "copytree"):
            targets.extend(args[1:2])
        elif name in ("remove", "unlink", "rmdir", "rmtree", "mkdir", "makedirs"):
            targets.extend(args[:1])
            if name not in ("mkdir", "makedirs"):
                subtrees.extend(args[:1])
    return [target for target in targets if target], [target for target in subtrees if target]


def ordered_nodes(node):
    yield node
    for child in ast.iter_child_nodes(node):
        yield from ordered_nodes(child)


def positional_arguments(tokens):
    values = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
        elif token.casefold() in ("-encoding", "-value", "-filter", "-include", "-exclude", "-width"):
            skip = True
        elif not token.startswith("-"):
            values.append(token)
    return values


def changed_directory(tokens, cwd):
    if not tokens or tokens[0].casefold() not in ("cd", "chdir", "set-location", "sl"):
        return cwd
    values = [token for token in tokens[1:] if not token.startswith("-") and token.casefold() != "/d"]
    if not values or any(char in values[0] for char in ("$", "`", "*", "?")):
        return cwd
    return absolute_path(values[0], cwd)


def ordered_fragments(command, tool_name, guard):
    variables = guard.Variables(fold_case=tool_name == "PowerShell")
    return guard.expand(guard.join_continuations(command, tool_name), tool_name, variables, 0)


def preceding_directory(command, tool_name, cwd, guard):
    for fragment in ordered_fragments(command, tool_name, guard):
        tokens = [guard.unquote(token) for token in SHELL_TOKEN.findall(fragment)]
        cwd = changed_directory(tokens, cwd)
    return cwd


def python_consumer(fragment, guard):
    tokens = [guard.unquote(token) for token in SHELL_TOKEN.findall(fragment)]
    return bool(tokens and PYTHON_NAME.fullmatch(re.split(r"[\\/]", tokens[0])[-1]))


def shell_targets(command, tool_name, cwd, guard):
    targets = []
    subtrees = []

    def add_python(source, source_cwd):
        paths, trees = python_targets(source)
        targets.extend(absolute_path(path, source_cwd) for path in paths)
        subtrees.extend(absolute_path(path, source_cwd) for path in trees)

    for match in HEREDOC.finditer(command):
        prefix = command[:match.start()]
        fragments = ordered_fragments(prefix, tool_name, guard)
        if fragments and python_consumer(fragments[-1], guard):
            add_python(match.group(2), preceding_directory(prefix, tool_name, cwd, guard))
    for match in HERESTRING.finditer(command):
        suffix = command[match.end():].splitlines()[0] if command[match.end():].splitlines() else ""
        if suffix.lstrip().startswith("|") and python_consumer(suffix.lstrip()[1:].lstrip(), guard):
            add_python(match.group(1), preceding_directory(command[:match.start()], tool_name, cwd, guard))
    for fragment in ordered_fragments(command, tool_name, guard):
        raw_tokens = SHELL_TOKEN.findall(fragment)
        tokens = [guard.unquote(token) for token in raw_tokens]
        if not tokens:
            continue
        cwd = changed_directory(tokens, cwd)
        target_start, subtree_start = len(targets), len(subtrees)
        for index, token in enumerate(raw_tokens[:-1]):
            if re.fullmatch(r"(?:\d|\*)?>>?", token):
                targets.append(tokens[index + 1])
        executable = re.split(r"[\\/]", tokens[0])[-1].casefold()
        if PYTHON_NAME.fullmatch(executable) and "-c" in tokens:
            index = tokens.index("-c") + 1
            if index < len(tokens):
                add_python(tokens[index], cwd)
        if executable in SHELL_WRITERS:
            for flag in ("-path", "-literalpath", "-filepath"):
                folded = [token.casefold() for token in tokens]
                if flag in folded and folded.index(flag) + 1 < len(tokens):
                    targets.append(tokens[folded.index(flag) + 1])
                    break
            else:
                values = positional_arguments(tokens[1:])
                targets.extend(values[:1])
        elif executable in SHELL_COPY:
            folded = [token.casefold() for token in tokens]
            values = positional_arguments(tokens[1:])
            destinations = [tokens[folded.index(flag) + 1] for flag in ("-destination", "-newname", "-t", "--target-directory") if flag in folded and folded.index(flag) + 1 < len(tokens)]
            destinations = destinations or values[-1:]
            sources = [tokens[folded.index(flag) + 1] for flag in ("-path", "-literalpath") if flag in folded and folded.index(flag) + 1 < len(tokens)]
            sources = sources or [value for value in values if value not in destinations]
            for destination in destinations:
                if os.path.isdir(absolute_path(destination, cwd)):
                    targets.extend(os.path.join(destination, os.path.basename(source.replace("\\", os.sep))) for source in sources)
                else:
                    targets.append(destination)
            if executable in ("move", "mv", "move-item", "ren", "rename-item"):
                targets.extend(sources)
                subtrees.extend(sources)
        elif executable in SHELL_REMOVE:
            paths = [token for token in tokens[1:] if not token.startswith("-")]
            targets.extend(paths)
            if executable not in ("touch", "mkdir"):
                subtrees.extend(paths)
        elif executable in ("tee", "sed", "perl"):
            if executable == "tee" or any(token == "-i" or token.startswith("-i") for token in tokens[1:]):
                targets.extend(token for token in tokens[1:] if not token.startswith("-"))
        targets[target_start:] = [absolute_path(path, cwd) for path in targets[target_start:]]
        subtrees[subtree_start:] = [absolute_path(path, cwd) for path in subtrees[subtree_start:]]
    return targets, subtrees


def unbundled_question_commands(command, tool_name, cwd, guard):
    projects = []
    for fragment in ordered_fragments(command, tool_name, guard):
        tokens = [guard.unquote(token) for token in SHELL_TOKEN.findall(fragment)]
        if not tokens:
            continue
        cwd = changed_directory(tokens, cwd)
        executable = re.split(r"[\\/]", tokens[0])[-1]
        candidates = tokens[1:] if PYTHON_NAME.fullmatch(executable) else tokens
        while candidates and candidates[0].startswith("-"):
            flag = candidates.pop(0)
            if flag in ("-c", "-m"):
                candidates = []
            elif flag in ("-X", "-W") and candidates:
                candidates.pop(0)
        if not candidates or re.split(r"[\\/]", candidates[0])[-1].casefold() != "mission_state.py":
            continue
        if canonical_path(absolute_path(candidates[0], cwd)) == canonical_path(BUNDLED_CLI):
            continue
        if not any(tokens[index] == "question" and tokens[index + 1] in QUESTION_ACTIONS for index in range(len(tokens) - 1)):
            continue
        project = cwd
        if "--project" in tokens and tokens.index("--project") + 1 < len(tokens):
            project = absolute_path(tokens[tokens.index("--project") + 1], cwd)
        projects.append(os.path.join(project, "adapter-lookup"))
    return projects


def question_write_reason(payload, guard):
    payload = resolved_payload(payload)
    tool_name = payload.get("tool_name")
    targets = tool_targets(payload)
    cwd = payload.get("cwd") or os.getcwd()
    unbundled = []
    subtrees = []
    if tool_name in guard.GUARDED_TOOLS:
        command = input_mapping(payload).get("command")
        if isinstance(command, str):
            paths, subtrees = shell_targets(command, tool_name, cwd, guard)
            targets.extend(paths)
            unbundled = unbundled_question_commands(command, tool_name, cwd, guard)
    if not targets and not unbundled:
        return ""
    contexts = adapter_contexts(payload, targets + unbundled, guard)
    if unbundled and contexts:
        return "질문 쓰기는 현재 번들의 mission_state.py question publish/answer/restore/render 를 사용할 것"
    for target in targets:
        if protected_path(target, cwd, contexts, guard) or (target in subtrees and protected_tree(target, cwd, contexts, guard)):
            return f"질문 정본·생성본은 직접 쓰지 않음: {target}. 번들 mission_state.py question publish/answer/restore/render 를 사용할 것"
    return ""
