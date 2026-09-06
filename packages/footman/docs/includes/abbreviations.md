*[manifest]: The cached JSON description of your task tree; it powers completion and the chain split without importing your code.
*[manifests]: The cached JSON description of your task tree; it powers completion and the chain split without importing your code.
*[cascade]: The merged set of tasks.py (or config) files from the repo root down to your current directory; nearer files win.
*[cascades]: The merged set of tasks.py (or config) files from the repo root down to your current directory; nearer files win.
*[chain]: Several tasks on one command line; independent ones run in parallel by default.
*[chains]: Several tasks on one command line; independent ones run in parallel by default.
*[chained]: Several tasks on one command line; independent ones run in parallel by default.
*[chaining]: Several tasks on one command line; independent ones run in parallel by default.
*[taught error]: An error that names the culprit, states the expectation, and proposes the fix — footman treats errors as product surface.
*[taught errors]: An error that names the culprit, states the expectation, and proposes the fix — footman treats errors as product surface.
*[fan-out]: Running several tasks or steps concurrently, from a chain or a parallel() call inside a task body.
*[fan-outs]: Running several tasks or steps concurrently, from a chain or a parallel() call inside a task body.
*[passthrough]: Everything after -- on the command line, handed to a task verbatim via *args or passthrough().
*[stale-while-revalidate]: Serving the cached completion answer at once while a detached rebuild refreshes it for next time.
*[in-process]: Running a Python tool inside footman's own process via its console-script entry point, skipping the subprocess spawn.
*[sequential]: The run-wide mode (-s/--sequential, or sequential = true in config): no pool at all, every task one at a time, output live.
*[sequentially]: The run-wide mode (-s/--sequential, or sequential = true in config): no pool at all, every task one at a time, output live.
*[serial]: One task's lane (@task(serial=True)): the scheduler grants it the process globals, at most one holder at a time — and unlike a sequential run, the parallel pool keeps running around it.
*[context]: The per-task state behind run() — its working directory, its environment, its records; read it with Context/use_context, though most tasks never touch it directly.
*[contexts]: The per-task state behind run() — its working directory, its environment, its records; read it with Context/use_context, though most tasks never touch it directly.
*[refusal]: footman declining a command line before anything runs — exit 64, with a taught message naming the fix.
*[refusals]: footman declining a command line before anything runs — exit 64, with a taught message naming the fix.
*[envelope]: The single JSON document --json prints on stdout: schema, total_ms, and the flat items list (or a top-level error on a refusal).
*[envelopes]: The single JSON document --json prints on stdout: schema, total_ms, and the flat items list (or a top-level error on a refusal).
*[receipt]: The rendered form of one record — mark, name, command, time — proof a piece of work happened (or, under --dry-run, would have).
*[receipts]: The rendered form of one record — mark, name, command, time — proof a piece of work happened (or, under --dry-run, would have).
*[lane]: A declared resource claim (lane(), cwd_lane, console_lane, serial=): one holder at a time, granted at task boundaries by the scheduler, so claims are scheduled rather than contended for.
*[lanes]: A declared resource claim (lane(), cwd_lane, console_lane, serial=): one holder at a time, granted at task boundaries by the scheduler, so claims are scheduled rather than contended for.
*[shared]: A request answered by an execution the run already performed — the record reused, reported as shared; shared=False asks for a fresh run instead.
*[unshared]: A request that runs on its own — it reuses no earlier execution and answers no later one; asked for with shared=False on the task or the call, or inherited from an unshared caller.
