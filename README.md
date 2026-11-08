# HW5 — University IT Helpdesk

Author: Erzhan Aknur

A terminal helpdesk built with Python and LangGraph.
All helpdesk data and service statuses are fictional.
The application saves local ticket drafts and never sends them.

## Requirements

- Python 3.13.2, used for development and local tests
- Git
- An OpenAI API key for real model demonstrations

Exact package versions are listed in requirements.txt.

## Installation

Clone your public fork and open its folder:

```bash
git clone https://github.com/aknurerjankyzy14-spec/css4007-hw5.git
cd css4007-hw5
```

Replace YOUR_USERNAME with the repository owner's GitHub username.

Create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Copy .env.example to .env.

On Windows PowerShell:

```powershell
Copy-Item .env.example .env
```

On macOS/Linux:

```bash
cp .env.example .env
```

Edit .env locally:

```dotenv
OPENAI_API_KEY=YOUR_FULL_API_KEY
MODEL_NAME=gpt-4.1-mini
HELPDESK_STORAGE_DIR=local-storage
LANGSMITH_TRACING=false
```

Replace YOUR_FULL_API_KEY with your own key.
Do not commit .env or share the key.

## Part 1 — Fixed routing without a model

Run the original graph:

```bash
python part1.py
```

This runs S01–S04 and saves their traces in outputs/.

Run the graph with the clarification node:

```bash
python part1_clarify.py
```

This runs S05. A message containing both Wi-Fi and password
is routed to clarify_problem.

These commands do not require a model API key.

## Direct tool demonstrations

```bash
python demo_tools.py
```

This calls find_instructions, check_service_status and prepare_ticket
directly, without a model.

The log is outputs/direct_tools_demo.json.
Drafts are saved in outputs/tickets/.

## Part 2 — Model and tools

```bash
python part2.py --user demo-user-a --thread example-a --profile normal --log example_a.json
```

Enter messages after You:.

Example conversation:

1. Show me the Wi-Fi troubleshooting steps.
2. I enabled Wi-Fi and forgot the saved network, but Campus-Demo still will not connect. Check whether Wi-Fi is down. If it is operating, prepare a ticket about this problem.

Type exit to stop.

Available profiles:

- normal
- wifi_outage
- recovery_outage
- status_unavailable

Part 2 keeps conversation history only while the process is running.
Use a new log filename for each launch.

## Part 3 — Persistent memory

```bash
python part3.py --user demo-user-a --thread example-memory --profile normal --log memory_before.json
```

Enter:

Please remember that I prefer replies in Kazakh. My issue is Wi-Fi.
I enabled it and tried reconnecting, but it still fails.

Type exit, then start a new process:

```bash
python part3.py --user demo-user-a --thread example-memory --profile normal --log memory_after.json
```

Enter only:

Which problem and steps did we discuss?

The application restores the conversation from its checkpoint.

For the same user's new conversation, choose a new --thread value.
The language preference remains available, but the previous thread's
messages are separate.

For a different user, choose a different --user value.
Keep the same --profile when resuming a conversation.

## Storage and reset

By default, persistent memory is stored in:

- local-storage/conversations.sqlite
- local-storage/users.sqlite

HELPDESK_STORAGE_DIR changes the memory folder.

Conversation checkpoints contain messages and tool results.
User memory stores the explicitly requested reply language.

To reset all interactive memory:

1. Stop all running helpdesk processes.
2. Delete conversations.sqlite and users.sqlite from the configured folder.
3. Start the application again.

This removes saved conversations and language preferences.
Do not delete data/ or the demonstration logs.

Drafts are stored separately in outputs/tickets/.
No ticket is sent to an external service.

## Run supplied scenarios

Run all fourteen scenarios:

```bash
python run_scenarios.py --out outputs/all_scenarios_new
```

Or select scenarios:

```bash
python run_scenarios.py --only S10 S11 S12 S13 --out outputs/memory_checks_new
```

Use a new output folder for each run.

The runner reads data/scenarios.json.
It creates fresh temporary memory for each scenario and preserves it
between events within that scenario.
restart_process events use separate Python processes.
Temporary scenario databases are deleted after execution.

Each scenario produces a JSON log containing messages, execution
traces, tool results and final replies.
Ticket tool results include the local draft filename.

A completed execution is not automatically a passed scenario.
Review the actual replies and tool calls.
Measured results and limitations are documented in SUBMISSION.md.

## Offline tests

```bash
python -m pytest -q
```

Tests use temporary storage and explicitly labelled fake models.
They do not call OpenAI or require a real API key.

The tests check routing, tools, draft validation, the loop limit,
persistence after a process restart and memory isolation.

## Continuous integration

.github/workflows/tests.yml runs the offline tests on pushes
and pull requests using Python 3.13.2.

Check the repository's Actions tab for the remote result.

## Limitations

- Reference data and service statuses are fictional.
- User identifiers are not an authentication system.
- Real model demonstrations require network access and an API key.
- Model wording can still be inaccurate, including descriptions of
  the supported scope and fictional status.
- The program does not provide automatic monitoring or notifications.
- The model loop is limited to six model calls per user turn.

See SUBMISSION.md and outputs/ for actual results.