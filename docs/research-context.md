# Research Context

The broader research extension of this project is developed in the main
[StratosAPI](https://github.com/Rayou6/StratosAPI) repository. This demo
repository only contains the reduced version prepared for the course submission.
The goal here is not to publish the full research data, but to explain the idea
behind the extension and the direction in which the project can grow.

## Research Goal

The research version uses Diplomacy as a social strategy environment for LLMs.
Unlike many benchmarks where the model only answers isolated questions, a
Diplomacy game forces each model to act inside a changing multi-agent system.
Players must negotiate, coordinate, protect themselves, react to betrayals, and
choose concrete board actions at the same time.

This makes the game interesting for studying social behavior in LLMs. The same
environment can reveal how models cooperate, whether they keep promises, how
they react under pressure, and how their strategic style changes when they are
given different instructions.

## Benchmarking Idea

The main idea is to run comparable games while changing only selected variables.
For example, the setup can keep the same map, countries, prompt structure, and
model assignment, then change one strategy or one model to observe how the game
changes.

The comparison is not only about who wins. A useful benchmark can also compare:

- supply center evolution over time;
- order types, such as moves, holds, supports, and attacks;
- private messages and negotiation style;
- promise keeping, betrayal, and alliance behavior;
- invalid orders, fallbacks, latency, and token usage;
- differences between models placed in the same position.

This makes the project useful both as a game-playing experiment and as a
behavioral benchmark for LLMs in social decision-making contexts.

## Strategies And Conditions

The research version includes several strategy conditions that can be assigned
to one or more powers. Some are neutral baseline behaviors, while others push a
model toward a clearer play style.

Examples include:

- a baseline mode with no extra strategic pressure;
- a planning-oriented mode that asks the model to reason from a tactical plan;
- a supply-center pressure mode that pushes more direct board progress;
- a deception-oriented mode used to stress-test trust and alliance behavior;
- private memory experiments where selected powers remember recent events.

These strategies are not meant to prove that one prompt is universally better.
They are mainly tools for creating measurable differences between runs and for
observing how models react when their incentives or instructions change.

## Experiment Tracking

The research extension uses
[Weights & Biases](https://wandb.ai/site) as an experiment tracking tool.
Weights & Biases is commonly used in machine learning projects to store run
metadata, metrics, tables, artifacts, and visualizations in a structured way.

This is important because a full research run can produce a large amount of
data: game files, logs, model calls, messages, board states, metrics, and
analysis tables. Committing every run directly to GitHub would quickly make the
repository noisy and unnecessarily large. Instead, GitHub can stay focused on
the code, selected examples, and reproducible configurations, while detailed run
data can be tracked privately outside the repository.

No private API keys, private dashboards, or full research datasets are published
in this demo repository.

## Preliminary Direction

Early exploratory runs suggest that this environment is useful for observing
clear behavioral differences between models and strategy conditions. Some models
appear more passive or defensive, while others are more willing to coordinate,
pressure neighboring powers, or adapt after a failed plan. Private messaging also
makes the analysis richer, because the final orders can be compared with what a
model promised earlier in the phase.

These observations are still preliminary. The current goal is to build a clean
and repeatable setup first, then use it to run larger and more controlled
experiments. More detailed results can be discussed privately if someone is
interested in the research direction.

Contact: `rayan.rami@student.unisg.ch`

## Limitations

The main limitation is cost. Serious experiments with advanced models such as
Claude or ChatGPT can become expensive very quickly, because one game requires
many model calls across several powers, phases, messages, and retries. Longer
games or seven-power games multiply this cost even more.

There are also research limitations. LLM behavior can be stochastic, one game is
not enough to prove a general pattern, and fair comparison requires repeated
runs with controlled settings. For this reason, turning the project into a more
complete research benchmark would require time, infrastructure, and ideally
financial support.

## Possible Extensions

Several extensions could make the project more useful as a research tool:

- adding an external LLM judge to rate negotiation quality and strategic
  consistency after each game;
- generating automatic post-game reports;
- comparing models across standardized maps and scenarios;
- creating a leaderboard where users can submit agents or model setups;
- making the benchmark easy enough for other researchers to reproduce;
- using the system as a social-strategy benchmark for future LLMs.

In the long term, this could become more than a course project: it could be a
small benchmark for testing how LLMs behave in multi-agent social environments.

## Collaboration Interest

If a professor or researcher is interested in this direction, or knows someone
who might be, I would be very happy to discuss it. The current project provides
an initial technical setup, but stronger academic guidance and research support
would make it possible to explore the topic much more seriously.
