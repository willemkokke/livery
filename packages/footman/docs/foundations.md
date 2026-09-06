# Foundations

Everything footman does about parallelism rests on a handful of operating
system ideas: what a process is, what the working directory and the
environment really are, how programs get spawned, and how deadlocks happen.
The Guide assumes them; this section teaches them from zero, in plain
words, each page ending in the one rule worth remembering. If you have ever
been surprised by a taught error mentioning "the parallel regime", this is
where it stops being arbitrary.

## How to read this

Each page opens with two or three **self-check questions**. If you can
answer them all, skip ahead; the pointer at the top of each page says
where. Nothing here is required reading; the pages exist so that when you
*want* the ground under a rule, it is one click away.

The pages build on each other in this order:

``` mermaid
graph TD
  P["One process, many tasks"] --> C["The working directory"]
  P --> E["The environment"]
  P --> S["Spawning programs"]
  P --> T["Threads & the GIL"]
  C --> D["Deadlocks"]
  E --> Sh["The shell"]
  Sh --> S
  E --> S
  S --> D
  T --> D
  D --> R["The four globals, two regimes"]
  C --> R
  E --> R
```

Start at [One process, many tasks](foundations-process.md): every other
page assumes only it and its ancestors in the graph.

## Where this leads

The destination is a single sentence you will be able to defend by the end:
**in a parallel run, the only non-parallel execution is the kind you
declared.** Everything else (why the working directory belongs to nobody,
why environment writes scope to a task, why a bare `input()` is an error
and a wizard is not) falls out of the pages above.
