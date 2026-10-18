#!/usr/bin/env python3
"""Teltaq Data Plane API console: try every /v1 capability from a menu.

Everything goes through the official OpenAI Python SDK:

* OpenAI-compatible endpoints use the typed SDK calls
  (``client.models.list()``, ``client.chat.completions.create(...)``), with
  Teltaq options passed in ``extra_body={"teltaq": {...}}`` and Teltaq output
  read from the response's extra ``teltaq`` field;
* Teltaq-native endpoints (comparisons, tools, agent / workflow runs,
  documents, decisions, app and suite runs) use the SDK's generic
  ``client.get`` / ``client.post``, so authentication, retries, errors and SSE
  parsing are the SDK's own.

App and suite runs are background jobs: starting one returns the queued run
at once (HTTP 202). The console then waits (``wait``), polls, or streams the
run's events until it finishes.

Configuration (environment variables):
    TELTAQ_API_KEY        required, a tq_sk_... key (Settings -> API keys)
    TELTAQ_BASE_URL       default https://api.teltaq.com/v1
    TELTAQ_MODEL          optional default chat model (provider_connection_id)
    TELTAQ_VERBOSE        "1" to start with request/response logging on

Run:  python teltaq_console.py
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional

try:
    import httpx
    import openai
    from openai import OpenAI, Stream
except ImportError:  # pragma: no cover - friendly message for a missing install
    sys.exit("The OpenAI SDK is missing: pip install -r requirements.txt")

# -- console helpers ---------------------------------------------------------

_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
if os.name == "nt" and _COLOR:
    os.system("")  # enable ANSI escape codes in the Windows console


def _load_dotenv(path: str = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")) -> None:
    """Read ``KEY=value`` lines from a ``.env`` next to this script (no
    dependency); variables already set in the environment win."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                os.environ.setdefault(key.strip(), value)


_load_dotenv()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(t: str) -> str: return _c("2", t)
def bold(t: str) -> str: return _c("1", t)
def green(t: str) -> str: return _c("32", t)
def cyan(t: str) -> str: return _c("36", t)
def yellow(t: str) -> str: return _c("33", t)
def red(t: str) -> str: return _c("31", t)


def show_json(label: str, data: Any) -> None:
    print(cyan(f"-- {label} " + "-" * max(0, 60 - len(label))))
    print(json.dumps(data, indent=2, default=str))


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def ask_yes(prompt: str, default: bool = False) -> bool:
    value = ask(f"{prompt} (y/n)", "y" if default else "n").lower()
    return value.startswith("y")


def choose(items: List[Dict[str, Any]], label: str, describe, default_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Pick one item by number (or paste an id)."""
    if not items:
        print(yellow(f"No {label} available in this workspace."))
        return None
    for i, item in enumerate(items, start=1):
        marker = " *" if default_id and item.get("id") == default_id else ""
        print(f"  {i:>2}. {describe(item)}{marker}")
    default = None
    if default_id and any(it.get("id") == default_id for it in items):
        default = str(next(i for i, it in enumerate(items, start=1) if it.get("id") == default_id))
    raw = ask(f"Choose a {label} (number or id)", default or "1")
    if raw.isdigit() and 1 <= int(raw) <= len(items):
        return items[int(raw) - 1]
    return next((it for it in items if it.get("id") == raw), {"id": raw})


# -- client ------------------------------------------------------------------


class Console:
    def __init__(self) -> None:
        api_key = os.environ.get("TELTAQ_API_KEY")
        if not api_key:
            sys.exit("Set TELTAQ_API_KEY to a Teltaq API key (Settings -> API keys).")
        self.base_url = os.environ.get("TELTAQ_BASE_URL", "https://api.teltaq.com/v1").rstrip("/")
        self.default_model = os.environ.get("TELTAQ_MODEL") or None
        self.verbose = os.environ.get("TELTAQ_VERBOSE") == "1"
        # httpx hooks print each request / response when verbose is on. The key
        # is masked; streamed bodies are printed by the commands themselves.
        http_client = httpx.Client(
            timeout=httpx.Timeout(300.0, connect=10.0),
            event_hooks={"request": [self._log_request], "response": [self._log_response]},
        )
        self.client = OpenAI(base_url=self.base_url, api_key=api_key, http_client=http_client, max_retries=1)

    # -- logging hooks --

    def _log_request(self, request: httpx.Request) -> None:
        if not self.verbose:
            return
        print(dim(f"> {request.method} {request.url}"))
        auth = request.headers.get("authorization", "")
        if auth:
            print(dim(f"  Authorization: {auth[:20]}..."))
        if request.content:
            try:
                print(dim("  " + json.dumps(json.loads(request.content), indent=2).replace("\n", "\n  ")))
            except ValueError:
                print(dim(f"  {request.content[:500]!r}"))

    def _log_response(self, response: httpx.Response) -> None:
        if not self.verbose:
            return
        headers = response.headers
        extra = [f"{h}={headers[h]}" for h in ("x-request-id", "x-ratelimit-remaining-requests") if h in headers]
        print(dim(f"< {response.status_code} {response.headers.get('content-type', '')} {' '.join(extra)}"))

    # -- small wrappers over the SDK's generic requests --

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        return self.client.get(path, cast_to=object, options={"params": params or {}})

    def post(self, path: str, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Any:
        return self.client.post(path, body=body, cast_to=object, options={"headers": headers or {}})

    def post_stream(self, path: str, body: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> Stream[Any]:
        """SSE from a Teltaq-native endpoint, parsed by the SDK (``: ping``
        keepalives are skipped, ``[DONE]`` ends it, an error frame raises)."""
        return self.client.post(path, body=body, cast_to=object, stream=True, stream_cls=Stream[object],
                                options={"headers": headers or {}})

    def get_stream(self, path: str) -> Stream[Any]:
        return self.client.get(path, cast_to=object, stream=True, stream_cls=Stream[object])

    # -- pickers --

    def pick_model(self, purpose: str = "chat", allow_auto: bool = False) -> Optional[str]:
        if purpose == "chat":
            models = [m.model_dump() for m in self.client.models.list().data]
        else:
            models = self.get("/models", {"purpose": purpose})["data"]
        if not allow_auto:
            models = [m for m in models if m["id"] != "auto"]

        def describe(m: Dict[str, Any]) -> str:
            t = m.get("teltaq") or {}
            return f"{t.get('name', m['id'])}  {dim(str(t.get('model_name') or ''))}  {dim(m['id'])}"

        picked = choose(models, f"{purpose} model", describe, self.default_model)
        return picked["id"] if picked else None

    def pick_resource(self, path: str, label: str) -> Optional[str]:
        items = self.get(path)["data"]
        picked = choose(items, label, lambda it: f"{it.get('name')}  {dim(it.get('mode', '') or '')}  {dim(it['id'])}")
        return picked["id"] if picked else None


# -- output helpers ----------------------------------------------------------


def teltaq_of(obj: Any) -> Dict[str, Any]:
    """The ``teltaq`` extension of an SDK object (an extra field)."""
    extra = getattr(obj, "model_extra", None) or {}
    return extra.get("teltaq") or {}


def print_chat_stream(stream) -> None:
    """Print a streamed chat completion: answer tokens inline, Teltaq events
    (tool calls, routing, sources) on their own lines."""
    usage = None
    for chunk in stream:
        info = teltaq_of(chunk)
        if info.get("routing"):
            r = info["routing"]
            print(dim(f"[routing: {r.get('outcome')} -> {info.get('provider_connection_id')} ({info.get('model_name')})]"))
        event = info.get("event")
        if event == "tool_call":
            print(yellow(f"\n[tool call] {info['name']}({json.dumps(info.get('arguments') or {})})"))
        elif event == "tool_result":
            print(yellow(f"[tool result] {str(info.get('result'))[:300]}"))
        elif event == "completed":
            if info.get("rag_sources"):
                print(dim("\n[sources] " + ", ".join(f"[{s['n']}] {s['filename']}" for s in info["rag_sources"])))
            if info.get("decisions"):
                print(dim(f"[decisions] {json.dumps(info['decisions'])}"))
            run = info.get("run")
            if run:
                print(dim(f"\n[run status: {run.get('status')}]"))
        for choice in chunk.choices:
            delta = choice.delta
            reasoning = (getattr(delta, "model_extra", None) or {}).get("reasoning_content")
            if reasoning:
                print(dim(reasoning), end="", flush=True)
            if delta.content:
                print(delta.content, end="", flush=True)
            if choice.finish_reason:
                print(dim(f"\n[finish_reason: {choice.finish_reason}]"))
        if chunk.usage:
            usage = chunk.usage
    if usage:
        print(dim(f"[usage] {usage.model_dump(exclude_none=True)}"))


def print_completion(resp: Any) -> None:
    message = resp.choices[0].message
    print(green(bold("Assistant: ")) + (message.content or ""))
    info = teltaq_of(resp)
    print(dim(f"[model {resp.model} | finish_reason {resp.choices[0].finish_reason} | usage {resp.usage.model_dump(exclude_none=True)}]"))
    if info.get("routing"):
        print(dim(f"[routing] {json.dumps(info['routing'])}"))
    for call in info.get("tool_calls") or []:
        flag = red(" BLOCKED") if call.get("blocked") else ""
        print(yellow(f"[tool] {call['name']}({json.dumps(call.get('arguments') or {})}){flag} -> {str(call.get('result'))[:200]}"))
    for src in info.get("rag_sources") or []:
        print(dim(f"[source {src['n']}] {src['filename']} (score {src['score']})"))


def print_run_stream(stream) -> None:
    """Print an agent / workflow run's named events."""
    current_agent = None
    for ev in stream:
        kind = ev.get("type")
        agent = ev.get("agent")
        if kind == "run.created":
            print(dim(f"[run {ev['run']['id']} created]"))
        elif kind == "run.agent.started":
            # May arrive after the agent's first tokens; only announce new agents.
            if agent != current_agent:
                current_agent = agent
                print(cyan(f"\n>> {agent}"))
        elif kind == "run.output_text.delta":
            if agent and agent != current_agent:
                current_agent = agent
                print(cyan(f"\n>> {agent}"))
            print(ev.get("delta", ""), end="", flush=True)
        elif kind == "run.reasoning.delta":
            print(dim(ev.get("delta", "")), end="", flush=True)
        elif kind == "run.tool_call":
            print(yellow(f"\n[tool call] {ev['name']}({json.dumps(ev.get('arguments') or {})})"))
        elif kind == "run.tool_result":
            flag = red(" BLOCKED") if ev.get("blocked") else ""
            print(yellow(f"[tool result]{flag} {str(ev.get('result'))[:300]}"))
        elif kind == "run.route":
            print(dim(f"\n[route -> {agent}] {ev.get('reason') or ''}"))
        elif kind == "run.handoff":
            print(dim(f"\n[handoff {ev.get('from')} -> {ev.get('to')}]"))
        elif kind == "run.parallel":
            print(dim(f"\n[parallel {agent}]"))
        elif kind == "run.agent.completed":
            print(dim(f"\n[{agent} done]"))
        elif kind in ("run.completed", "run.requires_input", "run.incomplete", "run.failed"):
            print_run(ev["run"], header=False)


def print_run(run: Dict[str, Any], header: bool = True) -> None:
    status = run["status"]
    color = green if status == "completed" else yellow if status in ("requires_input", "incomplete") else red
    print("\n" + color(bold(f"Run {status}")) + dim(f"  {run['id']} | usage {run['usage']}"))
    if header and run.get("output_text"):
        print(green(bold("Output: ")) + run["output_text"])
    if run.get("requires_input"):
        print(yellow(f"The agent asks: {run['requires_input'].get('question')}"))
    if run.get("incomplete_details"):
        print(yellow(f"Incomplete: {run['incomplete_details']}"))
    if run.get("error"):
        print(red(f"Error: {run['error']}"))
    steps = [s for s in run.get("output") or [] if s["type"] != "agent_step"]
    for step in steps:
        print(dim(f"  | {step['type']}: {json.dumps({k: v for k, v in step.items() if k != 'type' and v is not None})[:200]}"))


# -- commands ----------------------------------------------------------------


def messages_from_prompt() -> List[Dict[str, Any]]:
    system = ask("System prompt (optional)")
    user = ask("Message", "Explain what a vector database is in two sentences.")
    msgs: List[Dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    return msgs + [{"role": "user", "content": user}]


def cmd_models(app: Console) -> None:
    models = app.client.models.list()
    for m in models.data:
        info = m.model_dump().get("teltaq") or {}
        print(f"  {m.id}  {bold(info.get('name', ''))}  {dim(str(info.get('provider_type', '')))} {dim(str(info.get('model_name', '')))}")
    decision = app.get("/models", {"purpose": "decision"})["data"]
    if decision:
        print(dim("Decision models:"))
        for m in decision:
            print(f"  {m['id']}  {m['teltaq']['name']}")


def cmd_chat(app: Console, model: Optional[str] = None) -> None:
    model = model or app.pick_model()
    if not model:
        return
    messages = messages_from_prompt()
    if ask_yes("Stream the response?", True):
        stream = app.client.chat.completions.create(
            model=model, messages=messages, stream=True, stream_options={"include_usage": True},
        )
        print(green(bold("Assistant: ")), end="")
        print_chat_stream(stream)
    else:
        print_completion(app.client.chat.completions.create(model=model, messages=messages))


def cmd_auto(app: Console) -> None:
    print(dim("model='auto': the workspace's decision model picks the chat connection."))
    cmd_chat(app, model="auto")


def cmd_compare(app: Console) -> None:
    models = [m.model_dump() for m in app.client.models.list().data if m.id != "auto"]
    if len(models) < 2:
        print(yellow("A comparison needs at least two chat models in this workspace."))
        return
    for i, m in enumerate(models, start=1):
        print(f"  {i:>2}. {(m.get('teltaq') or {}).get('name')}  {dim(m['id'])}")
    raw = ask("Models to compare (comma-separated numbers)", "1,2")
    chosen = [models[int(x) - 1]["id"] for x in raw.split(",") if x.strip().isdigit()]
    names = {m["id"]: (m.get("teltaq") or {}).get("name", m["id"]) for m in models}
    body = {"models": chosen, "messages": messages_from_prompt()}
    if not ask_yes("Stream the responses?", True):
        result = app.post("/chat/comparisons", body)
        for r in result["results"]:
            print(cyan(bold(f"\n## {names.get(r['model'], r['model'])}")))
            if r.get("error"):
                print(red(f"  error: {r['error']}"))
            else:
                print("  " + r["completion"]["choices"][0]["message"]["content"])
                print(dim(f"  usage {r['completion']['usage']}"))
        print(dim(f"\n[total usage] {result['usage']}"))
        return
    current = None
    for frame in app.post_stream("/chat/comparisons", {**body, "stream": True}):
        if frame.get("object") == "chat.comparison.completed":
            print(dim(f"\n[total usage] {frame['usage']}"))
            for r in frame["teltaq"]["results"]:
                if r.get("error"):
                    print(red(f"[{names.get(r['model'], r['model'])}] error: {r['error'].get('message')}"))
            continue
        for choice in frame.get("choices") or []:
            text = (choice.get("delta") or {}).get("content")
            if text:
                if frame["model"] != current:
                    current = frame["model"]
                    print(cyan(bold(f"\n## {names.get(current, current)}: ")), end="")
                print(text, end="", flush=True)


def cmd_list_tools(app: Console) -> List[Dict[str, Any]]:
    tools = app.get("/tools")["data"]
    for t in tools:
        flag = yellow(" side effects") if t.get("side_effects") else green(" read-only")
        print(f"  {t['id']:<50} {bold(t['name'])}{flag}  {dim((t.get('description') or '')[:60])}")
    return tools


def cmd_invoke_tool(app: Console) -> None:
    tools = app.get("/tools")["data"]
    tool = choose([t for t in tools if t["type"] != "marketplace"], "tool",
                  lambda t: f"{t['name']}  {dim(t['id'])}")
    if not tool:
        return
    if tool.get("parameters"):
        print(dim(f"Parameters schema: {json.dumps(tool['parameters'])}"))
    args = json.loads(ask("Arguments (JSON)", '{"timezone": "UTC"}' if "time" in tool["id"] else "{}"))
    mode = ask("tool_mode (read_only / all / none)", "read_only")
    result = app.post(f"/tools/{tool['id']}/invocations", {"arguments": args, "tool_mode": mode})
    color = green if result["status"] == "succeeded" else yellow if result["status"] == "blocked" else red
    print(color(bold(result["status"])) + f"  {result.get('output') or result.get('error')}")


def cmd_chat_with_tools(app: Console) -> None:
    model = app.pick_model()
    if not model:
        return
    tools = app.get("/tools")["data"]
    builtin = [t["name"] for t in tools if t["type"] == "builtin"]
    dynamic = [t["id"].split(":", 1)[1] for t in tools if t["type"] == "dynamic"]
    marketplace = [t["id"].split(":", 1)[1] for t in tools if t["type"] == "marketplace"]
    print(dim(f"Offering built-ins {builtin}, {len(dynamic)} HTTP tool(s), {len(marketplace)} marketplace installation(s)."))
    mode = ask("tool_mode (read_only / all / none)", "read_only")
    user = ask("Message", "What time is it in Tokyo right now?")
    resp = app.client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user}],
        extra_body={"teltaq": {
            "tools": {"builtin": builtin, "dynamic_tool_ids": dynamic, "marketplace_installation_ids": marketplace},
            "tool_mode": mode,
        }},
    )
    print_completion(resp)


def _run(app: Console, kind: str) -> None:
    resource_id = app.pick_resource(f"/{kind}s", kind)
    if not resource_id:
        return
    user = ask("Input", "Summarise the benefits of unit testing in three bullet points.")
    body: Dict[str, Any] = {"input": user, "teltaq": {"tool_mode": ask("tool_mode", "read_only")}}
    path = f"/{kind}s/{resource_id}/runs"
    if ask_yes("Stream the run?", True):
        print_run_stream(app.post_stream(path, {**body, "stream": True}))
    else:
        print_run(app.post(path, body))


def cmd_agent(app: Console) -> None:
    _run(app, "agent")


def cmd_workflow(app: Console) -> None:
    _run(app, "workflow")


def cmd_agent_alias(app: Console) -> None:
    print(dim("Runs an agent or workflow through chat.completions with model='agent:<id>' / 'workflow:<id>'."))
    kind = ask("agent or workflow", "agent")
    resource_id = app.pick_resource(f"/{kind}s", kind)
    if not resource_id:
        return
    stream = app.client.chat.completions.create(
        model=f"{kind}:{resource_id}",
        messages=[{"role": "user", "content": ask("Message", "Hello! What can you do?")}],
        stream=True,
    )
    print(green(bold("Assistant: ")), end="")
    print_chat_stream(stream)


def cmd_documents(app: Console) -> None:
    docs = app.get("/documents")
    for d in docs["data"]:
        print(f"  {d['id']}  {bold(d['filename'])}  {dim(d['status'])}")
    print(dim(f"{docs.get('total', len(docs['data']))} document(s)"))


def cmd_search(app: Console) -> None:
    query = ask("Search query", "refund policy")
    mode = ask("search_mode (semantic / lexical / hybrid, blank = workspace default)")
    body: Dict[str, Any] = {"query": query, "top_k": 5}
    if mode:
        body["search_mode"] = mode
    result = app.post("/documents/search", body)
    print(dim(f"search_mode: {result['search_mode']}"))
    for i, hit in enumerate(result["data"], start=1):
        print(cyan(f"[{i}] {hit['filename']} #{hit['chunk_index']} (score {hit['score']})"))
        print("    " + hit["content"][:300].replace("\n", " "))


def cmd_rag_chat(app: Console) -> None:
    model = app.pick_model()
    if not model:
        return
    user = ask("Question about your documents", "What is our refund policy?")
    resp = app.client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": user}],
        extra_body={"teltaq": {"documents": "on"}},
    )
    print_completion(resp)
    if not teltaq_of(resp).get("rag_sources"):
        print(yellow("No sources were used: is Documents (RAG) enabled with ready documents in this workspace?"))


def cmd_decision(app: Console) -> None:
    model = None
    if ask_yes("Pick a decision model? (no = the workspace default)", False):
        model = app.pick_model("decision")
    kind = ask("Question type (choice / yes_no / score)", "choice")
    ticket = ask("Text to decide about", "I was charged twice for my subscription this month.")
    questions: Dict[str, Any]
    if kind == "yes_no":
        questions = {"urgent": {"type": "yes_no", "instructions": "Does this need a reply within the hour?"}}
    elif kind == "score":
        questions = {"sentiment": {"type": "score", "instructions": "How negative is the customer?",
                                   "criteria": ["frustration", "urgency"], "min": 0, "max": 1}}
    else:
        questions = {"category": {"type": "choice", "instructions": "Classify the support ticket.",
                                  "criteria": {"billing": "Payments, invoices, refunds",
                                               "bug": "Something is broken",
                                               "how_to": "A usage question"}}}
    body: Dict[str, Any] = {"state": {"ticket": ticket}, "questions": questions}
    if model:
        body["model"] = model
    result = app.post("/decisions", body)
    for key, answer in result["answers"].items():
        confidence = answer.get("confidence")
        print(green(bold(f"{key}: ")) + f"{answer['value']}  " + dim(f"confidence {confidence}"))
        if answer.get("probabilities"):
            print(dim(f"   probabilities {answer['probabilities']}"))
    print(dim(f"[model {result['model']} | {result.get('latency_ms')} ms | usage {result['usage']}]"))


# -- apps & suites (background runs) -------------------------------------------

TERMINAL = ("completed", "failed", "cancelled")
STATUS_COLOR = {"completed": green, "failed": red, "cancelled": yellow, "skipped": dim}


def status_text(status: str) -> str:
    return STATUS_COLOR.get(status, cyan)(status)


def ask_inputs(fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Prompt for each field of an ``input_schema`` (blank skips an optional one)."""
    values: Dict[str, Any] = {}
    for f in fields:
        label = f.get("label") or f["name"]
        hint = " (required)" if f.get("required") else ""
        default = f.get("default")
        if f.get("type") == "select":
            print(dim(f"  {label}: one of {', '.join(f.get('options') or [])}"))
        if f.get("type") == "file":
            path = ask(f"{label} - path to a text file{hint}")
            if path:
                with open(path, encoding="utf-8") as fh:
                    values[f["name"]] = {"name": os.path.basename(path), "content": fh.read()}
            continue
        raw = ask(f"{label}{hint}", str(default) if default is not None else None)
        if not raw:
            continue
        if f.get("type") == "number":
            try:
                values[f["name"]] = float(raw) if "." in raw else int(raw)
            except ValueError:
                values[f["name"]] = raw  # the API explains what's wrong
        else:
            values[f["name"]] = raw
    return values


def describe_fields(fields: List[Dict[str, Any]], indent: str = "    ") -> None:
    for f in fields:
        flags = ["required"] if f.get("required") else []
        if f.get("options"):
            flags.append("one of " + "/".join(f["options"]))
        if f.get("default") is not None:
            flags.append(f"default {f['default']!r}")
        print(f"{indent}{bold(f['name'])} {dim(f.get('type') or 'text')}  {dim(', '.join(flags))}")


def print_output(output: Any) -> None:
    if isinstance(output, dict) and output.get("text") and len(output) <= 3:
        print(green(bold("Output: ")) + str(output["text"]))
        if output.get("data") is not None:
            print(dim(json.dumps(output["data"], indent=2)))
    elif output is not None:
        print(green(bold("Output:")))
        print(json.dumps(output, indent=2))


def print_app_run(run: Dict[str, Any]) -> None:
    flags = dim(" via API") if run.get("via_api") else ""
    print("\n" + bold("App run ") + status_text(run["status"]) + dim(f"  {run['id']} | usage {run['usage']}") + flags)
    for step in run.get("steps") or []:
        attempt = f" attempt {step['attempt']}" if step.get("attempt", 0) > 1 else ""
        line = f"  | {step['id']:<16} {status_text(step['status'])}{dim(attempt)}"
        if step.get("error"):
            line += red(f"  {step['error']}")
        print(line)
    if run["status"] == "completed":
        print_output(run.get("output"))
    if run.get("error"):
        print(red(f"Error: {run['error'].get('code')}: {run['error'].get('message')}"))


def print_suite_run(run: Dict[str, Any]) -> None:
    flags = dim(" via API") if run.get("via_api") else ""
    print("\n" + bold(f"Suite run ({run.get('mode')}) ") + status_text(run["status"])
          + dim(f"  {run['id']} | usage {run['usage']}") + flags)
    for step in run.get("steps") or []:
        notes = []
        if step.get("attempt", 0) > 1:
            notes.append(f"attempt {step['attempt']}/{step.get('max_attempts')}")
        if step.get("handled"):
            notes.append("failure allowed")
        if step["status"] == "skipped" and step.get("error_code"):
            notes.append(step["error_code"])
        line = f"  | {step['member']:<16} {status_text(step['status'])}  {dim(', '.join(notes))}"
        if step["status"] == "failed" and step.get("error"):
            line += red(f"  {step['error']}")
        print(line)
    if run["status"] == "completed":
        for step in run.get("steps") or []:
            if step.get("output") is not None:
                print(cyan(f"\n## {step['app_name']}"))
                print_output(step["output"])
    if run.get("error"):
        print(red(f"Error: {run['error'].get('code')}: {run['error'].get('message')}"))


def step_marks(run: Dict[str, Any], key: str) -> Dict[str, tuple]:
    return {s[key]: (s["status"], s.get("attempt")) for s in run.get("steps") or []}


def follow_run(app: Console, path: str, run: Dict[str, Any], key: str,
               printer: Callable[[Dict[str, Any]], None]) -> None:
    """Poll a run (long polling with ``wait``) until it finishes, printing step
    changes. Ctrl+C offers to cancel the run (else it keeps running)."""
    seen = step_marks(run, key)
    try:
        while run["status"] not in TERMINAL:
            run = app.get(f"{path}/{run['id']}", {"wait": 20})
            for step_key, mark in step_marks(run, key).items():
                if seen.get(step_key) != mark:
                    print(dim(f"  [{step_key}] ") + status_text(mark[0]))
            seen = step_marks(run, key)
    except KeyboardInterrupt:
        if ask_yes("\nCancel the run?", False):
            run = app.post(f"{path}/{run['id']}/cancel", {})
        else:
            print(dim(f"The run keeps going: {path}/{run['id']}"))
            return
    printer(run)


def stream_run(stream, key: str, printer: Callable[[Dict[str, Any]], None]) -> None:
    for ev in stream:
        kind = ev.get("type")
        if kind == "run.created":
            print(dim(f"[run {ev['run']['id']} created]"))
        elif kind == "run.updated":
            print(dim(f"[run {ev['run']['status']}]"))
        elif kind == "run.step.updated":
            step = ev["step"]
            print(dim(f"  [{step[key]}] ") + status_text(step["status"]))
        elif kind in ("run.completed", "run.failed", "run.cancelled"):
            printer(ev["run"])


def start_background_run(app: Console, path: str, inputs: Dict[str, Any], key: str,
                         printer: Callable[[Dict[str, Any]], None]) -> None:
    mode = ask("Follow the run: wait / poll / stream", "wait")
    headers = {"Idempotency-Key": str(uuid.uuid4())}  # a retried request returns the same run
    if mode == "stream":
        stream_run(app.post_stream(path, {"input": inputs, "stream": True}, headers), key, printer)
        return
    body: Dict[str, Any] = {"input": inputs}
    if mode == "wait":
        body["wait"] = 60
    run = app.post(path, body, headers)
    print(dim(f"[run {run['id']} {run['status']}]"))
    follow_run(app, path, run, key, printer)


def pick_app(app: Console) -> Optional[Dict[str, Any]]:
    apps = app.get("/apps")["data"]
    return choose(apps, "form app", lambda a: f"{a['name']}  {dim(a['mode'] + ', tools ' + a['tool_mode'])}  {dim(a['id'])}")


def pick_suite(app: Console) -> Optional[Dict[str, Any]]:
    suites = app.get("/suites")["data"]

    def describe(s: Dict[str, Any]) -> str:
        state = green("runnable") if s.get("runnable", True) else red("needs attention")
        return f"{s['name']}  {dim(s['mode'])}  {state}  {dim(s['id'])}"

    return choose(suites, "suite", describe)


def cmd_list_apps(app: Console) -> None:
    apps = app.get("/apps")["data"]
    if not apps:
        print(yellow("No published form apps in this workspace."))
        return
    for a in apps:
        print(f"  {bold(a['name'])}  v{a['version']}  {dim(a['mode'])}  tools {a['tool_mode']}  {dim(a['id'])}")
        if a.get("description"):
            print(dim(f"    {a['description']}"))
        print(dim("    steps: " + " -> ".join(s["id"] for s in a["steps"])
                  + f" | output: {a['output_schema'].get('type')}"))
        describe_fields(a["input_schema"])
    limits = apps[0]["limits"]
    print(dim(f"Limits: {limits['max_active_runs']} active runs, run timeout {limits['run_timeout_s']} s, "
              f"wait up to {limits['max_wait_s']} s"))


def cmd_run_app(app: Console) -> None:
    picked = pick_app(app)
    if not picked:
        return
    detail = app.get(f"/apps/{picked['id']}")
    inputs = ask_inputs(detail["input_schema"])
    start_background_run(app, f"/apps/{detail['id']}/runs", inputs, "id", print_app_run)


def _manage_runs(app: Console, path: str, key: str, printer: Callable[[Dict[str, Any]], None]) -> None:
    via = ask("Show runs from: all / api", "all")
    runs = app.get(path, {"limit": 10, "via": via})
    print(dim(f"{runs['total']} run(s){', showing the newest 10' if runs['has_more'] else ''}"))
    run = choose(runs["data"], "run", lambda r: f"{status_text(r['status'])}  {dim(r['id'])}"
                 + (dim("  via API") if r.get("via_api") else ""))
    if not run:
        return
    action = ask("show / follow / cancel", "show")
    if action == "cancel":
        printer(app.post(f"{path}/{run['id']}/cancel", {}))
    elif action == "follow":
        follow_run(app, path, app.get(f"{path}/{run['id']}"), key, printer)
    else:
        printer(app.get(f"{path}/{run['id']}"))


def cmd_app_runs(app: Console) -> None:
    picked = pick_app(app)
    if picked:
        _manage_runs(app, f"/apps/{picked['id']}/runs", "id", print_app_run)


def cmd_list_suites(app: Console) -> None:
    suites = app.get("/suites")["data"]
    if not suites:
        print(yellow("No published suites with a flow in this workspace."))
        return
    for s in suites:
        state = green("runnable") if s["runnable"] else red("needs attention")
        print(f"  {bold(s['name'])}  v{s['version']}  {dim(s['mode'])}  {state}  {dim(s['id'])}")
        for m in s["members"]:
            drift = yellow(f"  republished: v{m['app_version']} -> v{m['current_version']}") if m["drift"] else ""
            print(dim(f"    {m['key']:<14} {m['app_name']}  v{m['app_version']}") + drift)
        for problem in s["needs_attention"]:
            print(red(f"    ! {problem}"))
        for group in s["input_schema"]:
            print(dim(f"    {group['title']}:"))
            describe_fields(group["fields"], indent="      ")


def cmd_run_suite(app: Console) -> None:
    picked = pick_suite(app)
    if not picked:
        return
    detail = app.get(f"/suites/{picked['id']}")
    for problem in detail["needs_attention"]:
        print(red(f"! {problem}"))
    inputs: Dict[str, Any] = {}
    for group in detail["input_schema"]:
        print(cyan(group["title"]))
        inputs.update(ask_inputs(group["fields"]))
    start_background_run(app, f"/suites/{detail['id']}/runs", inputs, "member", print_suite_run)


def cmd_suite_runs(app: Console) -> None:
    picked = pick_suite(app)
    if picked:
        _manage_runs(app, f"/suites/{picked['id']}/runs", "member", print_suite_run)


def cmd_verbose(app: Console) -> None:
    app.verbose = not app.verbose
    print(f"Request/response logging {'on' if app.verbose else 'off'}.")


MENU = [
    ("1", "List models", cmd_models),
    ("2", "Chat - single model (stream or not)", cmd_chat),
    ("3", "Chat - Auto routing (model='auto')", cmd_auto),
    ("4", "Compare models (multi-model)", cmd_compare),
    ("5", "List tools", cmd_list_tools),
    ("6", "Invoke a tool directly", cmd_invoke_tool),
    ("7", "Chat with Teltaq tools", cmd_chat_with_tools),
    ("8", "Run an agent", cmd_agent),
    ("9", "Run a workflow", cmd_workflow),
    ("10", "Agent / workflow through chat.completions", cmd_agent_alias),
    ("11", "List documents", cmd_documents),
    ("12", "Search documents (retrieval only)", cmd_search),
    ("13", "Chat with documents (RAG)", cmd_rag_chat),
    ("14", "Ask a decision model", cmd_decision),
    ("15", "List apps (inputs, steps, tool mode)", cmd_list_apps),
    ("16", "Run an app (wait / poll / stream)", cmd_run_app),
    ("17", "App runs: list / show / follow / cancel", cmd_app_runs),
    ("18", "List suites (members, pinned versions, inputs)", cmd_list_suites),
    ("19", "Run a suite (wait / poll / stream)", cmd_run_suite),
    ("20", "Suite runs: list / show / follow / cancel", cmd_suite_runs),
    ("v", "Toggle request/response logging", cmd_verbose),
]


def report_error(exc: Exception) -> None:
    if isinstance(exc, openai.APIStatusError):
        body = exc.body if isinstance(exc.body, dict) else {}
        err = body.get("error", body) if isinstance(body, dict) else {}
        print(red(bold(f"HTTP {exc.status_code}")) + red(f"  {err.get('type', '')} / {err.get('code', '')}"))
        print(red(f"  {err.get('message', exc.message)}"))
        extras = {k: v for k, v in err.items() if k not in ("message", "type", "code") and v is not None}
        if extras:
            print(red(f"  {extras}"))
        request_id = exc.response.headers.get("x-request-id") if exc.response is not None else None
        if request_id:
            print(dim(f"  x-request-id: {request_id}"))
    elif isinstance(exc, openai.APIConnectionError):
        print(red(bold("Could not reach the API.")) + red(f"  {exc}"))
        print(dim("  Check TELTAQ_BASE_URL."))
    elif isinstance(exc, openai.APIError):
        # e.g. an error frame in the middle of a stream
        print(red(bold("API error: ")) + red(str(exc)))
        if exc.body:
            print(red(f"  {exc.body}"))
    elif isinstance(exc, json.JSONDecodeError):
        print(red(f"Invalid JSON: {exc}"))
    else:
        raise exc


def main() -> None:
    app = Console()
    print(bold("Teltaq Data Plane API console") + dim(f"  {app.base_url}"))
    commands = {key: (label, fn) for key, label, fn in MENU}
    while True:
        print()
        for key, label, _ in MENU:
            print(f"  {key:>2}  {label}")
        print("   q  Quit")
        try:
            choice = input(bold("> ")).strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if choice in ("q", "quit", "exit"):
            return
        if choice not in commands:
            continue
        label, fn = commands[choice]
        print(cyan(bold(f"\n{label}")))
        try:
            fn(app)
        except KeyboardInterrupt:
            print(yellow("\n(interrupted)"))
        except Exception as exc:  # noqa: BLE001 - shown, then back to the menu
            report_error(exc)


if __name__ == "__main__":
    main()
