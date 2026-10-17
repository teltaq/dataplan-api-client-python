# Teltaq Data Plane API: Python console sample

A small, menu-driven console app for trying every capability of the Teltaq
Data Plane API (`/v1`). It uses the **official OpenAI Python SDK** as its only
client:

- **OpenAI-compatible endpoints** (`/v1/models`, `/v1/chat/completions`) use
  the SDK's typed calls (`client.models.list()`,
  `client.chat.completions.create(...)`).
  - Teltaq options go in `extra_body={"teltaq": {...}}`.
  - Teltaq output is read from the extra `teltaq` field on responses and
    stream chunks.
- **Teltaq-native endpoints** (comparisons, tools, agent and workflow runs,
  documents, decisions, app and suite runs) use the SDK's generic `client.get(...)` /
  `client.post(...)`. Authentication, retries, the `{"error": {...}}`
  envelope (raised as `openai.APIStatusError`) and server-sent events all go
  through the SDK.

One file (`teltaq_console.py`), two dependencies (`openai`, `httpx`).

## Setup

You need Python 3.9 or later, and a Teltaq account on the **Pro** or **Team**
plan.

1. **Create an API key.** In Teltaq, go to **Settings → API keys** for a
   Personal key, or **Team settings → API keys** for a team key. Copy the
   secret (`tq_sk_...`); it is shown only once. A key works in the workspace
   it was created in, as you, with the scopes you gave it. Options 15–17 need
   the `apps` scope and 18–20 the `suites` scope. Keys created before those
   scopes existed don't have them, so create a new key to try them.
2. **Install:**

   ```bash
   cd samples/dataplane-python
   python -m venv .venv
   . .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure.** Either copy `.env.example` to `.env` (read automatically,
   and git-ignored) or set the variables in your shell:

   | Variable | Required | Default | Purpose |
   |---|---|---|---|
   | `TELTAQ_API_KEY` | yes | | Your `tq_sk_...` key |
   | `TELTAQ_BASE_URL` | no | `http://localhost:8000/v1` | API base URL. Production: `https://api.teltaq.com/v1` |
   | `TELTAQ_MODEL` | no | | A chat `provider_connection_id` to pre-select in pickers |
   | `TELTAQ_VERBOSE` | no | `0` | `1` starts with request/response logging on |

   ```bash
   export TELTAQ_API_KEY=tq_sk_...          # PowerShell: $env:TELTAQ_API_KEY = "tq_sk_..."
   export TELTAQ_BASE_URL=http://localhost:8000/v1
   ```

4. **Run:**

   ```bash
   python teltaq_console.py
   ```

## The menu

| # | Command | Endpoint |
|---|---|---|
| 1 | List models | `GET /v1/models`, `GET /v1/models?purpose=decision` |
| 2 | Chat with a single model, streamed or not | `POST /v1/chat/completions` |
| 3 | Chat with Auto routing (`model="auto"`) | `POST /v1/chat/completions` |
| 4 | Compare models (multi-model), streamed or not | `POST /v1/chat/comparisons` |
| 5 | List tools | `GET /v1/tools` |
| 6 | Invoke a tool directly | `POST /v1/tools/{tool_id}/invocations` |
| 7 | Chat with Teltaq tools (run server-side) | `POST /v1/chat/completions` + `teltaq.tools` |
| 8 | Run an agent, streamed or not | `POST /v1/agents/{id}/runs` |
| 9 | Run a workflow, streamed or not | `POST /v1/workflows/{id}/runs` |
| 10 | Run an agent or workflow as a chat model (`model="agent:<id>"`) | `POST /v1/chat/completions` |
| 11 | List documents | `GET /v1/documents` |
| 12 | Search documents (retrieval only) | `POST /v1/documents/search` |
| 13 | Chat with documents (RAG) | `POST /v1/chat/completions` + `teltaq.documents` |
| 14 | Ask a decision model (choice / yes_no / score) | `POST /v1/decisions` |
| 15 | List form apps: inputs, steps, tool mode, limits | `GET /v1/apps` |
| 16 | Run a form app, then wait, poll or stream it | `GET /v1/apps/{id}`, `POST /v1/apps/{id}/runs` |
| 17 | An app's runs: list, show, follow or cancel one | `GET /v1/apps/{id}/runs[/{run_id}]`, `POST .../cancel` |
| 18 | List suites: members, pinned app versions, run form, problems | `GET /v1/suites` |
| 19 | Run a suite, then wait, poll or stream it | `GET /v1/suites/{id}`, `POST /v1/suites/{id}/runs` |
| 20 | A suite's runs: list, show, follow or cancel one | `GET /v1/suites/{id}/runs[/{run_id}]`, `POST .../cancel` |
| v | Toggle request/response logging | |

Each command asks for what it needs and offers a default; press Enter to
accept it. Models, agents, workflows and tools are picked from a numbered list,
or you can paste an id. Ctrl+C cancels the current command and returns to the
menu.

### What you'll see

- **Streaming:** answer tokens print as they arrive. Teltaq events print on
  their own lines: `[tool call]`, `[tool result]`, `[routing: ...]`,
  `[sources]`, and for agent runs `>> Agent`, `[route -> ...]` and
  `[handoff ...]`. Each finishes with `finish_reason` and token usage.
- **App and suite runs** run in the background. Starting one returns the
  queued run straight away, and you choose how to follow it:
  - `wait` sends `wait: 60`: the API answers with the finished run if it
    finishes within a minute, and the console keeps polling if it doesn't;
  - `poll` long-polls `GET .../runs/{run_id}?wait=20`, printing each step's
    status as it changes;
  - `stream` sends `stream: true` and prints the named events
    (`run.created`, `run.step.updated`, `run.completed`, ...).

  Press Ctrl+C while following a run to cancel it, or leave it running and
  look it up later with option 17 or 20. Each run request carries a fresh
  `Idempotency-Key`, so a retried request returns the same run instead of
  starting another. Input fields are asked for one by one: select fields list
  their options, and a file field reads a local text file.
- **Requests and responses:** with logging on (`v`), every call prints its
  method, URL, masked key and JSON body, then its status, `x-request-id` and
  remaining rate limit.
- **Errors** print the HTTP status and the API's error type, code and message,
  plus any extras (for example `current_plan` / `required_plan` on a plan
  error) and the `x-request-id` to quote when reporting a problem:

  ```text
  HTTP 401  authentication_error / invalid_api_key
    Incorrect API key provided.
    x-request-id: req_480dda91692a428cac7b9c405aa8a8e0
  ```

## Example session

```text
$ python teltaq_console.py
Teltaq Data Plane API console  http://localhost:8000/v1
   1  List models
   ...
> 2

Chat - single model (stream or not)
   1. GPT-4o  gpt-4o  0b6c...
   2. Claude  claude-sonnet  7f1e...
Choose a chat model (number or id) [1]:
System prompt (optional):
Message [Explain what a vector database is in two sentences.]:
Stream the response? (y/n) [y]:
Assistant: A vector database stores data as numeric embeddings...
[finish_reason: stop]
[usage] {'completion_tokens': 52, 'prompt_tokens': 31, 'total_tokens': 83}
```

A suite run (option 19):

```text
> 19

Run a suite (wait / poll / stream)
   1. Help Desk  graph  runnable  9d2a...
Choose a suite (number or id) [1]:
Shared inputs
ticket (required): I was charged twice
Follow the run: wait / poll / stream [wait]: stream
[run 51c0... created]
  [triage] running
  [triage] completed
  [billing] running
  [tech] skipped
  [billing] completed
  [reply] running
  [reply] completed

Suite run (graph) completed  51c0... | usage {'prompt_tokens': 812, 'completion_tokens': 240, 'total_tokens': 1052} via API
  | triage           completed
  | billing          completed
  | tech             skipped  condition_not_met
  | reply            completed
```

## The SDK calls, at a glance

```python
from openai import OpenAI, Stream

client = OpenAI(base_url="http://localhost:8000/v1", api_key="tq_sk_...")

# OpenAI-compatible: model = a provider_connection_id, "auto", or "agent:<id>"
resp = client.chat.completions.create(
    model="<connection id>",
    messages=[{"role": "user", "content": "What is our refund policy?"}],
    extra_body={"teltaq": {"documents": "on", "tool_mode": "read_only"}},
)
print(resp.choices[0].message.content, resp.model_extra["teltaq"]["rag_sources"])

# Teltaq-native: the SDK's generic requests
comparison = client.post("/chat/comparisons", cast_to=object,
                         body={"models": ["<id A>", "<id B>"], "messages": [{"role": "user", "content": "Hi"}]})

# Named SSE events from an agent run
for event in client.post("/agents/<agent id>/runs", cast_to=object, stream=True, stream_cls=Stream[object],
                         body={"input": "Summarise this week's tickets", "stream": True}):
    print(event["type"], event.get("delta", ""))

# App runs run in the background: start one, then wait for it (long polling)
run = client.post("/apps/<app id>/runs", cast_to=object, body={"input": {"topic": "owls"}, "wait": 60},
                  options={"headers": {"Idempotency-Key": "order-42"}})
while run["status"] not in ("completed", "failed", "cancelled"):
    run = client.get(f"/apps/<app id>/runs/{run['id']}", cast_to=object, options={"params": {"wait": 30}})
print(run["output"])
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Could not reach the API` | Nothing is listening at `TELTAQ_BASE_URL`. From WSL or a container, `localhost` is not your Windows host, so use the host's address or run the sample on Windows |
| `401 invalid_api_key` | Wrong key, or a key from a different Teltaq environment or database |
| `401 api_key_revoked` / `api_key_expired` | Create a new key |
| `403 feature_locked` | Your plan doesn't include API access (Pro / Team) or the capability (`feature` says which) |
| `403 insufficient_scope` | The key lacks the endpoint's scope; create a key with it |
| `404 ..._not_found` | The id doesn't exist **in the key's workspace**. Team keys only see team resources, and personal keys only personal ones |
| `409 decision_not_configured` | No decision model in the workspace. Pick one (option 14), or set one in Settings → Model routing |
| `409 documents_unavailable` | Documents (RAG) aren't enabled for the workspace |
| `400 invalid_input` | The app or suite input doesn't fit its form; `problems` lists each field |
| `409 app_not_runnable` / `suite_not_runnable` | The app isn't a published form app, or a suite member needs attention (option 18 shows why) |
| `409 run_finished` | Cancelling a run that has already finished |
| `429 too_many_active_runs` | You already have the maximum number of app and suite runs going (shared with the Teltaq app). Wait for one, or cancel one |
| A run ends `failed` with `api_key_revoked` | The key that started it was revoked before the run (or its next suite step) started |
| `429 rate_limit_exceeded` / `insufficient_quota` | The per-key requests-per-minute limit, or the monthly message cap |
| `blocked` tool results | The tool may have side effects. Choose `tool_mode` `all`, unless the workspace requires approval for such tools, in which case the API always refuses them |

The full API reference is on the Teltaq docs site under **Docs → API**, and the
backend serves its OpenAPI document at `/v1/openapi.json`.
