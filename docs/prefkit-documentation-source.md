---
title: "prefkit: how it works and why"
subtitle: "A teaching guide. Each step says what it does, why it exists, what it costs, and a worked example."
---

# How to read this guide

Every section follows the same four questions:

1. **What it is.** The concept, defined on first use.
2. **Why it exists.** The problem it solves. If the problem is absent, the feature is unnecessary.
3. **How it is used.** The concrete steps, with the exact command or file.
4. **Worked example and limits.** Real numbers from our runs, and what the approach does not handle.

Numbers marked *illustrative* are chosen to make the arithmetic easy. They are not measurements.

# Part 1. The problem prefkit solves

**Preference data** is a set of comparisons. Each comparison has a *prompt* (a question), two *responses* (answers), and a *label* saying which answer is better. Companies that build AI models collect millions of these from people, and they use them to teach a *reward model* (a network that scores how good an answer is) or a *judge* (a model that picks the better of two answers).

The problem is that every source stores this differently:

| Source | Field names for the two answers | Layout |
|---|---|---|
| Anthropic HH-RLHF | `chosen`, `rejected` | Two full conversations that share leading turns |
| A survey export | `response_a`, `response_b`, `winner` as `"A"` or `"B"` | One prompt per row |
| A code set | `solution_1`, `solution_2`, `preferred` as `1` or `2` | Plus unit tests |

**Why not just write one loader per dataset?** Each loader would contain its own rules for prompts, labels and errors. Two loaders would disagree about the same question, and any change would need code edits in several places. prefkit converts every dataset into one internal shape, so the rest of the system is written once.

**Why one shape, not many?** Ranking, annotation and evaluation all ask the same questions: which answer won, and for which prompt. Keeping one shape means each of those features is written once.

![Decision overview: the four things prefkit does, and where each branch leads](overview_flow.png)

*Figure 1.* Read it from the top. Each branch is one question a user brings. The grey boxes are written but not yet run, or planned. The green boxes have been run.

# Part 2. Ingest: turning a dataset into the shared shape

## 2.1 The manifest

**What it is.** A manifest is a YAML file (a plain-text format where indentation shows structure) that describes one dataset. It says where the data is and which column means what. For example:

```yaml
name: hh_helpful_local
source:
  type: local
  location: data/hh-rlhf/helpful-base/train.jsonl.gz
mapping:
  prompt_strategy: common_prefix
  response_a_field: chosen
  response_b_field: rejected
  winner_fixed: a
```

**Why it exists.** The mapping is data, not code. When a new dataset arrives, you write a 15-line file. You do not change Python. This is why the design is called modular: the mapping can change without touching the rest of the system.

**Why strict validation.** The loader rejects any key it does not recognise. Compare two outcomes for a typo:

- With lenient loading, `winner_feild: winner` is silently ignored. Every row has no winner, and the run reports success with no labels.
- With strict loading, the same typo stops the run immediately, with a message naming the key.

A small mistake that costs nothing to catch early costs a whole dataset if it is caught late.

**Why `winner_fixed` and `winner_field` are mutually exclusive.** A winner has exactly one source. If both were allowed, the file could contradict itself, and the loader would have to guess which to trust.

## 2.2 The two prompt strategies

**`field`.** The prompt sits in its own column. This is the simple case.

**`common_prefix`.** Each response is a full conversation, and both conversations start with the same turns. HH-RLHF works this way. Here is a real pattern from the data (illustrative wording):

```
chosen:   Human: How do I boil water?  Assistant: Use a kettle.
rejected: Human: How do I boil water?  Assistant: No idea.
```

**Why the shared turns become the prompt.** The label means "for this question, this answer beats that one." The question is the part the two conversations share. The part where they differ is the answer.

**How the split works.** The loader splits each conversation at its turn markers (`Human:` and `Assistant:`), walks both lists in parallel, and stops at the first turn that differs. Everything before that point is the prompt.

**Why a row is rejected in two cases.** If the two conversations are identical, there is nothing to prefer. If the first difference is a human turn rather than an assistant turn, the two answers are not comparable. Both cases are logged, not guessed at.

## 2.3 Why the label is shuffled

**The problem.** In HH-RLHF, the preferred answer is always in the `chosen` column. If the labelling interface shows the preferred answer on the left every time, people and models can learn "left wins" and score well without judging content.

**What prefkit does.** For each row, it decides from the row's own content whether to swap A and B. When it swaps them, it also flips the winner label, so the preferred answer stays preferred.

**Why the decision comes from the content, not from a random number generator.** Re-running the ingest must give the same output. Otherwise two runs cannot be compared, and a bug fix would look like a change in the data. A hash of the row is always the same for the same row, but it still spreads the swaps evenly across the dataset.

**Worked example (illustrative).** Take three rows with preferred answers "X", "Y" and "Z". The hash decides swaps of "yes, no, yes". The stored result is that X and Z are in position B and Y is in position A. In every case, the winner label points at the preferred answer.

## 2.4 Why ids are content hashes

**What a hash is.** A hash function turns any input into a short fixed-length fingerprint. SHA-256 is a common one. prefkit keeps the first 16 hexadecimal characters, which is 64 bits.

**Why the id comes from the text, not a counter.** A counter (row 1, row 2, ...) changes when the file is reordered or a row is removed. A content hash does not change, so the same response gets the same id in every run.

**Why this makes writes safe to repeat.** If a run crashes halfway and is retried, each row writes to the same id. The second write updates the existing row instead of creating a duplicate.

**How many collisions to expect.** A collision is two different responses sharing an id. With n items and 64-bit ids, the chance is about n squared divided by 2 to the power 65. For our 43,835 comparisons, that is about 43,835 squared, which is about 1.9 billion, divided by about 3.7 times 10 to the 19, so about 5 in 100 billion. This is negligible for this use.

## 2.5 Why rows are quarantined, not fatal

**The problem.** A dataset has 160,000 rows. One malformed row should not discard 159,999 good ones.

**What prefkit does.** Each row is mapped on its own. If it fails, the failure is recorded with a reason, and the run continues.

**Why there is still a limit.** A wrong mapping makes every row fail. Quarantine alone would then store nothing and report "success" with a huge quarantine count nobody reads. So two rules apply:

- **Early stop.** After 200 rows, if half or more were rejected, the run stops at once. The probable cause is a wrong mapping, so reading the other 159,800 rows wastes time.
- **Final limit.** At the end, if more than the manifest's limit (default 1%) was rejected, the whole run is rolled back.

**Why 50% for the early stop and 1% for the end.** The early stop only catches disasters. A 1% rate is normal noise in real data, so using 1% early would stop healthy runs on unlucky starts. The final check is calmer because it has seen all the rows.

## 2.6 Worked example: HH-RLHF helpful-base

| Quantity | Value | How it was obtained |
|---|---|---|
| Rows read | 43,835 | 43,774 accepted plus 61 quarantined |
| Accepted | 43,774 | Stored as comparisons |
| Quarantined | 61 (0.14%) | Below the 1% limit, so the run committed |
| Reason for all 61 | Identical chosen and rejected | Checked by eye with `scripts/show_rows.py` |

**Why the 61 are correct to exclude.** We printed rows 517, 608, 1960, 2098 and 3641. In each, the two conversations are the same. A label of "A beats B" between identical texts is noise. A reward model trained on noise learns to ignore the difference between good and bad answers.

**Why the reason text was changed.** The first version said "one transcript is a prefix of the other". That was misleading, because identical texts also match "prefix of itself". The clearer reason, "identical, so there is no preference", tells the reader what actually happened. A quarantine reason should describe the data, not the code path that caught it.

**Why rerunning is a useful test.** Running the same ingest again should give the same counts. This checks that the mapping is deterministic and that the upserts did not duplicate anything.

## 2.7 Reading the ingest flowchart

![Ingest pipeline: every path through a run](ingest_flow.png)

*Figure 2.* Follow the arrows from **Start**. Two entry points lead to one queued job, because the same work must happen whether a person clicked a button or typed a command. Each diamond is a decision with a reason:

- **Manifest invalid** and **file missing** stop at once and are not retried. Retrying cannot make a wrong file correct.
- **Quarantined** loops back to the early check. This is how the 200-row rule is applied.
- **Every 1,000 rows** is a batch boundary. Writing 1,000 rows per statement is much faster than one statement per row, and it keeps memory bounded.
- **Final check** decides between commit and rollback. Only this decision can discard the whole run.
- **Database error** is the only path that retries. A dropped connection is temporary, so trying again can succeed.

# Part 3. Jobs: running work in the background

## 3.1 Why ingest is not a normal web request

**What a request is.** A browser sends a request, and the server must send a response. Browsers and proxies commonly give up after a fixed wait, often around a minute.

**Why this matters here.** HH-RLHF takes minutes to ingest. If the button waited for the result, the connection would time out and the user would see an error even though the work continued.

**The fix.** The server accepts the request at once, records the work as a job, and returns a receipt. The browser then asks for progress.

## 3.2 HTTP 202, not 200

**What the codes mean.** 200 means "done, here is the result". 202 means "accepted, not finished, here is where to check".

**Why 202.** Returning 200 would tell the browser the work is complete. That is false, and a client that trusts the code would show success too early. 202 is the honest answer.

**Why the response includes a status URL.** The browser needs an address to ask about this specific job. Without it, the browser would have to guess.

## 3.3 What a queue is, and why there is one

**The analogy.** A queue is a post office tray. The web server drops off a job, and a worker picks it up later. Neither one has to wait for the other.

**Why separate them.** The web server must stay quick to answer requests. A worker can be slow, can run on a different machine, and can be restarted without the web server noticing.

**What prefkit uses.** Celery is the library that manages the tray and the workers. Redis is the tray itself in production. In local debugging, there is no tray: eager mode runs the task directly, so a breakpoint in the ingest code is hit in the same process.

**Why eager mode is the default.** When something goes wrong, you want to see it immediately, in one process, with a debugger attached. Running through a queue adds a second process you must attach to separately.

## 3.4 Why the job is queued after the commit

**The race.** Suppose the server starts the worker first, then saves the job row. The worker might run before the row exists, find nothing, and do nothing. The job would then sit in the queue as "queued" forever.

**What prefkit does.** It saves the row first, and tells the queue to start the job only once the save is confirmed (`transaction.on_commit`). The worker can then always find the row.

**The general lesson.** Any message that refers to data should be sent after that data is durable.

## 3.5 Why one transaction for the whole ingest

**What a transaction is.** A transaction is a group of database changes that all happen or none happen. A bank transfer is the usual example: money must leave one account and arrive in the other, or neither.

**Why prefkit uses one.** The quarantine limit is judged at the end of the run. A half-written dataset would look complete, with some rows missing and no way to tell which. Rolling back means a failed run leaves no trace.

**The cost, stated plainly.** A transaction holds its locks until it ends, so a long ingest keeps some rows locked. This is acceptable here because only the ingest writes these rows. If many writers shared the tables, the next step would be to write into a staging table and move the finished data across in one quick step.

## 3.6 Why writes are idempotent

**What idempotent means.** Doing an action twice has the same effect as doing it once.

**Why it matters.** A worker can crash after writing and before reporting success. The queue then retries the job. Without idempotent writes, the retry duplicates every row.

**How it is achieved.** Each row's id is a content hash (section 2.4), and writes are upserts: insert the row, or update it if the id exists. The retry finds the same ids and updates them.

## 3.7 Why only some errors are retried

| Error | Retried? | Why |
|---|---|---|
| Database connection dropped | Yes, up to 3 times | Temporary. Waiting and trying again can succeed. |
| Manifest invalid | No | The file is wrong. Retrying reads the same wrong file. |
| Quarantine limit exceeded | No | The data is wrong. Retrying reproduces the same result. |
| Unknown exception | No, recorded as failed | Retrying an unknown error can repeat damage. Record it, then look. |

**Why the wait grows.** Retries wait 5 seconds, then longer each time (with random jitter, a small random extra delay). If a hundred workers all retry at the same moment, they overload the database again. Spreading the retries out avoids that.

## 3.8 Why only one active job per dataset

**The problem.** Two clicks arrive at the same moment. Both check "is there an active job?", both see none, and both start one. The two jobs then race to write the same rows.

**Why a database rule and not a Python check.** A check in Python happens before the write, so the two requests can both pass it. A database constraint is enforced during the write itself, so only one insert succeeds. The second gets HTTP 409, meaning "conflict".

## 3.9 Why progress lives in the cache

**The problem.** Progress is written during the transaction. Other connections cannot see data from an uncommitted transaction (this is deliberate, so readers never see half-finished work). A progress number stored in the database would therefore show nothing until the very end.

**What prefkit does.** It stores live counts in the cache (Redis or memory), which is visible at once. The database keeps the final, authoritative counts.

## 3.10 Why the browser polls with backoff

**What polling is.** The browser asks the server "how far?" on a timer.

**The arithmetic (illustrative).** Suppose 100 dashboards each ask once per second. That is 100 requests per second for a status that changes rarely.

**What backoff does.** When nothing changes, the wait grows: 1 second, 1.5, 2.25, and so on, up to 10 seconds. A change resets it to 1 second. When the tab is hidden, the page stops asking. In this example, that cuts the load from 100 requests per second to around 10 or fewer, and to zero for hidden tabs.

**Why not push updates instead.** Server-sent events or WebSockets would be more immediate. They also need a connection kept open per viewer, which is more to run. Polling is the simpler choice until the wait becomes noticeable.

## 3.11 Why processes, not threads

**What a thread is.** A thread is one line of work inside a process, sharing its memory. Threads are cheap to start.

**The limit.** Python has a Global Interpreter Lock (GIL): one thread at a time runs Python bytecode. Mapping rows is Python code, so four threads take about as long as one.

**Why processes instead.** Each process has its own interpreter and its own lock, so four processes can use four CPU cores.

**Why not split one dataset across processes.** That would break the single transaction (section 3.5) and the global quarantine check. Parallelism comes from running several jobs, not from splitting one.

# Part 4. Compute: finding the hardware

## 4.1 What a device is

A **device** is the hardware that does the arithmetic for a model. Three kinds are supported:

| Kind | Hardware | Memory | Status |
|---|---|---|---|
| `cuda` | NVIDIA GPU | Dedicated video memory (VRAM), 8 GB on an RTX 4060 | Written, not yet run |
| `mps` | Apple Silicon | Shared with system memory | Written, not yet run |
| `cpu` | Any processor | System memory | Tested |

**Why GPUs.** A model's main work is multiplying large grids of numbers. A GPU does thousands of these in parallel. A CPU does far fewer at a time, so the same work takes much longer.

## 4.2 Why detection is a registry

**The problem.** Code that runs a model should not contain "if NVIDIA, do this; if Apple, do that". Every new machine would need edits in many files.

**What prefkit does.** Each hardware type registers a detector function with the `@backend` decorator. A decorator is a marker placed above a function that records it in a list. The caller asks `select_device()`, which tries the detectors in order of preference and returns the first working one.

**Why the order is cuda, then mps, then cpu.** Preference follows speed for model work. A CPU always works, so it is the fallback at the end.

**Why a detector must not raise on a missing driver.** A laptop without a GPU is normal, not an error. If detection raised, the whole program would fail on machines that are fine. Instead the detector returns nothing, and the next option is tried.

**Why one broken detector cannot hide the others.** Each detector is wrapped so that its own failure is logged and skipped.

## 4.3 Why 4-bit loading exists

**The arithmetic.** A model's *weights* are the learned numbers. Each weight is stored in some number of bits.

- A 3-billion-parameter model at 16 bits per weight: 3 × 10⁹ × 2 bytes = 6 GB.
- The same model at 4 bits per weight: 3 × 10⁹ × 0.5 bytes = 1.5 GB.

An 8 GB card cannot hold the 16-bit version alongside the working memory needed for batches. The 4-bit version fits with room to spare.

**The cost.** Each weight is rounded more coarsely, so answers can be slightly less accurate. This is why every judge result must be measured against human labels (Part 5), not assumed to be good.

**Why only on CUDA.** The library that performs 4-bit loading (bitsandbytes) is built for NVIDIA GPUs. On other devices the loader uses 16-bit or 32-bit weights.

## 4.4 Why one worker per GPU

**The reason.** Each worker process loads its own copy of the model into video memory. Two workers on one 8 GB card each try to hold a model, and the second fails with an out-of-memory error.

**Why `--pool=solo`.** Celery's solo pool runs one task at a time in one process. One process owns the GPU and nothing else competes for it.

## 4.5 Why a second PC can join safely

**The design.** A PC on your Tailscale network can run a worker that reads from the same queue. The queue and database stay on the control machine.

**Why not expose them to the internet.** Redis and Postgres accept commands from anyone who can reach them. Exposure means anyone can read the data or flood the queue. Bind them to the private address and require a password.

**Why pin the same git commit on both machines.** If the two machines run different code, the same manifest could produce different data on each.

# Part 5. Quality tools (planned)

These are designed. They are not built. The reasons are included so that you can judge whether to build them.

## 5.1 Why a judge at all

**The cost problem.** A human label takes time and money. A judge model can label many pairs quickly and cheaply.

**The trap.** A judge is only useful if it agrees with humans. A judge that picks at random agrees 50% of the time on two-way choices, so a judge at 50% has learned nothing.

## 5.2 Why the 200-pair trial comes first

**What statistics says.** Suppose the judge's true agreement is 65%. From 200 samples, the standard error is the square root of (0.65 × 0.35 / 200), which is about 0.034. A 95% range is about plus or minus 7 percentage points, so the measured value falls between 58% and 72%. That range clearly separates the judge from 50%.

**Why 200 is enough for a go or no-go.** The decision is "is this clearly better than chance?", not "what is its exact accuracy?". Two hundred pairs answers the first question cheaply.

**Why not 20.** With 20 pairs, the standard error is about 0.11, and a 95% range about plus or minus 22 points. Almost any result is consistent with almost any judge.

## 5.3 Why gold items

**What a gold item is.** A comparison with a known correct answer, such as a code solution where the unit tests decide which one passes.

**Why they matter.** Scoring a person or a judge against gold items measures accuracy directly. Without them, agreement with other people measures only consistency, and a group can agree on something wrong.

## 5.4 Why the game must measure accuracy

**Goodhart's law.** When a measure becomes a target, people optimise the measure rather than the thing it stood for. If points come from fast clicks, players click fast.

**The rule.** The game may reward engagement, but gold-item accuracy is tracked and reported beside it. The question the game answers is whether engagement changed the quality of labels, and that needs both numbers.

## 5.5 Why code runs in a sandbox

**The risk.** A model may write code that deletes files, opens network connections, or loops forever. Running it on the main machine trusts it completely.

**What a sandbox does.** It runs the code in an isolated environment with time limits, memory limits and no network. The worst case is then a failed test, not a damaged system.

## 5.6 Why label routing by cost

**The idea.** For each item, choose the cheapest source (human, judge, or code execution) that is accurate enough. Accuracy is measured first (5.2 and 5.3), and cost comes from the prices of each source.

**Why not always use the best source.** The best source is often the most expensive. Routing spends the expensive labels where they change the outcome.

# Part 6. The hands-on steps, with the reason for each

These steps were run on Windows PowerShell in the project folder.

| Step | Command | Why |
|---|---|---|
| 1 | `py -3.12 -m venv .venv` | Creates a private package folder for this project, so other projects' packages cannot interfere. |
| 2 | `.venv\Scripts\Activate.ps1` | Makes `python` in this terminal point at that folder. |
| 3 | `python -m pip install -r requirements-dev.txt` | Using `python -m pip` installs into the active environment. Plain `pip` can point elsewhere. |
| 4 | `copy .env.example .env` | Creates your private settings. Git ignores `.env`, so secrets stay off the repository. |
| 5 | `python manage.py migrate` | Builds the database tables. The database must have them before any data is stored. |
| 6 | `python manage.py sync_datasets` | Registers each manifest as a dataset, so the ingest can find it by name. |
| 7 | `pytest` | Runs the 32 tests. A failure here means something changed that the tests cover. |
| 8 | `hf download Anthropic/hh-rlhf --repo-type dataset --local-dir data\hh-rlhf` | Copies the dataset to disk, so the ingest runs without the network. |
| 9 | `Get-ChildItem data\hh-rlhf -Recurse -File` | Lists the actual file names, because the manifest must point at them exactly. |
| 10 | `python manage.py ingest hh_helpful_local` | Runs the ingest in the foreground, so the counts are visible at once. |
| 11 | `python scripts\show_rows.py <file> <rows>` | Lets you check quarantined rows by eye, instead of trusting the reason text. |

**Why `hf` and not `python -m huggingface_hub`.** The package has no entry point for the second form, so it fails with "cannot be directly executed". The `hf` command is the one the package provides.

**Why the "unauthenticated" warning is harmless.** It means the download is rate-limited. It does not change the files. Signing in only makes it faster.

**Why `dir /s /b` fails.** It is Windows `cmd` syntax. PowerShell's `dir` takes different parameters, so the PowerShell command above is the correct one.

# Part 7. Debugging in VS Code

**Why debug through the program at all.** A printed message shows what you expected to see. A breakpoint pauses the program and shows what is actually there.

**The configurations and why each exists:**

- **runserver (no reload).** Django's auto-reload restarts the process when a file changes, and a breakpoint is lost in the restart. `--noreload` keeps the process alive.
- **ingest demo_local.** Runs one ingest in the foreground, with no web server or queue. The smallest path through the whole system.
- **Celery worker (solo).** For tasks that run through a queue. Attach to this process when eager mode is off.
- **pytest, current file.** Runs one test file so you can step through a single failing test.

**The walkthrough.** Put a breakpoint in `ingest.py` at the command, and another at the first line of `ingest_dataset` in `tasks.py`. Press F5, then F10 to step over and F11 to step into. Watch `winner` on a swapped row: you will see the swap flip the label. Step to row `d13` and watch the quarantine branch run.

**Why the Pylance message appears.** The editor's type checker reads the Hub library's return type, which depends on the arguments. The code casts it to the type we declare. The message is a warning about static analysis, not a runtime fault.

# Part 8. Deployment on a server

**Why a separate server at all.** A development server (`runserver`) is built for one person and is slow and unsafe for the public. A production server (gunicorn) runs several copies of the app and handles many requests.

**Why nginx in front.** nginx accepts connections from the internet, handles HTTPS certificates, and forwards only the paths you choose. WordPress keeps everything else.

**Why the app listens on 127.0.0.1:8001.** That address is reachable only from the same machine. Only nginx can reach the app, so no one can bypass the HTTPS handling.

**Why `PREFKIT_URL_PREFIX`.** The app is served under `/tests/prefkit/`. Django builds links with that prefix only when told, otherwise links point to `/`, which is WordPress.

**Why `PREFKIT_BEHIND_PROXY`.** nginx receives HTTPS but forwards plain HTTP. Django would otherwise think the connection is insecure and refuse secure cookies.

**Why staff-only access.** The ingest button writes to the database and uses server time. Without a login, anyone who finds the URL can start work or read job data.

**Why the server has no GPU.** A web server should answer requests quickly. Judge models are slow and use a lot of memory, so they run on your PC and join the queue.

# Part 9. Git: what is committed and why

**What is committed.** Code, manifests, documentation and the bundled sample. These are what a reader needs to reproduce the work.

**What is ignored and why.**

| Ignored | Size or nature | Why not committed |
|---|---|---|
| `.env` | Secrets | Anyone with the repository would see the keys. |
| `.venv/` | Thousands of files | Rebuilt from `requirements.txt`. |
| `var/` | The SQLite database | Local data, rebuilt by `ingest`. |
| `data/hh-rlhf/` | About 95 MB | Downloaded again with one command. Committing it bloats history. |
| `build/` | Generated documents | Rebuilt by the build script. |

**Why check before committing.** `git status --ignored` lists what Git will skip. `git check-ignore -v` names the rule behind each entry. Together they confirm that nothing large or secret is about to be committed.

**Why line endings matter.** Windows writes CRLF line endings and Linux writes LF. Without a rule, one file can show as changed when only its line endings differ. A `.gitattributes` file containing `* text=auto eol=lf` fixes this, and it belongs in its own commit.

# Part 10. Roadmap, in the order it would be built

1. **Judge batches.** Run the local model over pairs on the device chosen in Part 4.
2. **The 200-pair trial and agreement report** (Part 5.2). This decides whether the judge is worth more work.
3. **Label routing** (Part 5.6), only if the judge passes the trial.
4. **Code sandbox** (Part 5.5), needed before any code gold items are used.
5. **Annotator game** (Part 5.4), with accuracy tracked alongside engagement.

**Why this order.** Each step depends on the one before. A router built before the agreement is measured would route labels by a guess.

# Glossary

**Broker.** The message store between the web server and workers.

**Canonical shape.** The one internal form of a prompt, two responses and a winner.

**Celery.** A library that runs tasks in background worker processes.

**CUDA.** NVIDIA's platform for running code on GPUs.

**Eager mode.** Tasks run inside the calling process, for debugging.

**Gold item.** A comparison with a known correct answer.

**GIL.** The Global Interpreter Lock: one thread runs Python bytecode at a time.

**Hash.** A fixed-length fingerprint of any input (SHA-256 here).

**Idempotent.** Doing an action twice gives the same result as once.

**Judge.** A model that picks the better of two answers.

**Manifest.** The YAML file that maps a dataset's columns to the canonical shape.

**Migration.** A generated file that tells the database which tables to create.

**MPS.** Apple's GPU interface for PyTorch.

**Quantization.** Storing model weights in fewer bits to save memory.

**Quarantine.** Setting aside a row that cannot be mapped, with its reason.

**Queue.** A list of waiting jobs, read by workers.

**Reward model.** A model trained to score answers from preference pairs.

**Transaction.** Database changes that all succeed or all fail.

**Upsert.** Insert a row, or update it if its id exists.

**VRAM.** Video memory on a GPU, where model weights live.
