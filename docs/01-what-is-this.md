# 1. What is this?

## The problem, as a story

Imagine a big school with hundreds of doors. Every door has an alarm. When something strange happens at a door, the alarm beeps and sends a short message, like:

> "Door 7, 2:14 at night, someone used the janitor's key to switch off all the cameras."

A guard has to read that message and decide, fast:

1. **What is actually happening?** Is it the janitor, or a burglar using the janitor's key?
2. **What kind of trick is this?** Switching off cameras is a known burglar trick: "blind the guards first".
3. **What do I do now, in what order?** Lock that door, check the cameras' recordings before anyone deletes them, change the janitor's key, turn the cameras back on, and stop it happening again.

Computers in a company work the same way. The "doors" are laptops, servers, cloud accounts and factory machines. The "alarms" are **security alerts**. The "guards" are people in a **Security Operations Centre (SOC)**. They get hundreds of alerts a day, and every one needs those three answers.

## What Alert2Playbook does

You paste one alert into Alert2Playbook. A team of small AI helpers (we call them **agents**) works on it, one after another, like people on an assembly line:

1. A **guard** checks the alert for hidden tricks aimed at the AI itself.
2. A **triage** helper writes down, in plain English, what happened.
3. A **librarian** searches a big library of known attacker tricks.
4. A **matcher** picks which known tricks this alert really shows, and points to the proof in the alert.
5. A **researcher** looks up, for each trick, how experts say to stop it and how to spot it.
6. A **writer** writes a step-by-step response plan, called a **playbook**.
7. A **critic** checks the plan for mistakes and sends it back if needed.
8. Finally a **human** reads it and says "approved" or "please change this".

The answer you get has three parts:

- **The ATT&CK mapping:** which known attacker tricks this is (for example *T1490, Inhibit System Recovery*: "the attacker deleted the backups so you cannot undo the damage").
- **The playbook:** what to do, split into five phases (check, contain, clean up, recover, prevent), where every step points to the expert source it came from.
- **Hunt leads:** which logs to search to find more of the same attacker.

## Why it is built so carefully

AI can make things up. In security, a made-up answer is dangerous: a wrong step can destroy evidence or leave the attacker inside. So Alert2Playbook is built to stay honest:

- The AI may only pick attacker tricks from a real, official list. It cannot invent one.
- The plan may only cite expert advice that actually exists in that list.
- A second AI, from a different company's model family, reviews the plan.
- Normal computer code double-checks every reference the AI writes.
- A human always has the final word.

Page 6 explains each of these in detail.

## Who is it for?

- **Security teams**, to get a first draft of a response in seconds instead of half an hour.
- **Training**, for example in a cyber range (a practice battlefield for defenders), to show learners how an alert connects to known attacker behaviour and to the right defences.
- **Other AI agents**, which can call it as a tool (page 8).

## What it is not

It is not an automatic "robot defender". It never changes anything on your computers. It writes a plan for a person to check and carry out.

Next: [Words you need](02-words-you-need.md)
