Game rules:

## The Goal

You're playing on a map of 1900s Europe. There are **7 powers**: Austria, England, France, Germany, Italy, Russia, Turkey. Everyone starts with 3-4 units.

**To win**: control **18 of the 34 supply centers** (the key cities on the map). First to 18 wins. In practice games often end in a draw if nobody dominates.

---

## How It Works

### Supply Centers

Key cities — Paris, London, Berlin, Moscow, etc. You start owning 3 or 4 (your home centers). **Each SC you own = 1 unit you're allowed to have.** Capture more → build more units. Lose some → you have to destroy units.

### One Turn = 3 Phases

**1. Spring Movement** (e.g. S1901M)
Everyone writes their orders **simultaneously and secretly**, then they're all revealed at once. No turn-by-turn — everything happens at the same time. You can order each unit to:

- `A PAR - BUR` → Army in Paris moves to Burgundy
- `A PAR H` → stays in place (Hold)
- `A MUN S A PAR - BUR` → Army in Munich **supports** Paris's move (gives it +1 strength)
- `F BRE - MAO` → Fleet in Brest moves to the Atlantic Ocean

**2. Fall Movement** (e.g. F1901M)
Same thing. But at the end of Fall, SCs change ownership — if your unit is standing on an enemy SC, you take it.

**3. Winter Adjustment** (e.g. W1901A)
Count up: more SCs than units → you **build**. Fewer SCs → you **disband**. This is the only phase where unit counts change.

---

## The Key Mechanic: Conflicts

Since everyone moves simultaneously, two units can target the same territory.

- `A PAR - BUR` and `A MUN - BUR` at the same time → **bounce**, both stay put
- But if Munich has support (`A BOH S A MUN - BUR`) → Munich has strength 2, Paris has strength 1 → Munich takes Burgundy and **Paris is bounced back**
- A unit can be **dislodged** if attacked with enough support — it must then **retreat** or **disband**

---

## The Diplomacy Part (what gives the game its name)

Before each movement phase, players can **send private messages** to each other. This is where everything happens — proposing alliances, coordinating supports, promising not to attack... or lying. There is **no mechanism that forces you to keep your promises**. Betrayal is completely legal and very common.

---

## Quick Example Between Us

You play France, I play England.

- I message you: _"I won't move into the English Channel if you keep your fleet in Brest"_
- You reply: _"Deal, want to attack Germany together?"_
- We submit our orders simultaneously
- I play `F LON - ENG` anyway → you have nothing to defend with → I take the Channel

That's exactly what the LLMs in your project will have to navigate.

---

---

### Goal

Build a complete pipeline to:Arena / LLM-based Multi-Agent System

1. Run full Diplomacy games where each power is controlled by a different LLM (via OpenRouter API).
2. Support all game phases: movement, retreats, adjustments, AND inter-power messaging (press/diplomacy).
3. Benchmark LLM behavior across multiple criteria after each game.
4. Optionally assign strategies to LLMs at game start and track adherence.
5. Eventually have an AI "admin agent" that reviews games and evolves strategies autonomously.
6. Produce clean analysis reports and visualizations.
