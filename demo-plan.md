# Demo Plan v0.1

## Mental model bootstrapping

1.1) Explain bootstrapping and showcase how our factory supports AI-human interaction via GitHub (human answering the socractic questions on GitHub) [Keheliya, Radin]

1.2)  Showcase the mental model itself through the explorer UI tool. Show that it captures things that are not directly obtainable from the source code itself (i.e., show value) - possibly link to 'tribal knowledge' and undocumented rules and conventions. Explain how that information is suitable for agents in the context of development work (bug fix, new feature) and code review. [Keheliya, Radin]

## Factory lines as protocols

2.1) Showcase factory lines as protocols (bug fix, feature impl, autonomous feature impl). Non-factory PRs are auto-rejected [Gustavo]

## Pre-flight 

3.1) Showcase adherence stuff. Show how deviations (e.g., under/over spec are captured). I know we have 3 adherence checks. It sufficies to show just one and simply explain the others. [Haoxiang/Kevin]

3.2) Shocase mental-model violation detection. Must be non-blocking (as human could sign off in the end and say: yes, I'm aware and I sign off on this since I think the mental model should be updated/overriden) [Keheliya, Radin]

3.3) Showcase consistency. Explain how your change can be correct, pass tests, and be perfect. Yet, it could still be the case that not all docs are not updated. [Haoxiang/Kevin]

3.4) Showcase security/guarding. This is Kevin's work where he explains what Cedar does, what Guardian does, how they differ, and how both can spot complementary problems. Need to explain that this focuses on ensuring the agent didn't think or plan anything that breaks security. Code security things (e.g., vulnerability detection, etc) is done in the multi-AI review perspective phase. [Kevin]

## Guided overview

4.1) Explain that we must move from 'red-pen marking' to something that focuses on the key points. Show how the PR is reconstructed into an overview with cohorts, risk detection, and supporting figures. Explain how our risk detection works, but that it could be done in whatever way best suits a product line. This is similar to demos we did before, just need to show the nice UI stuff here. [Kevin]

## Multi-AI review and autonomous factory

5.1) Explain that each AI reviewer does its own reviews and focuses on their specific domain. Showcase how different agents find different problems (e.g., open different issues). [Haoxiang/Kevin]

5.2) Showcase how this works in the factory model: one agent finds a problem, creates an issue, another agent triages, and another agent fixes the issue and commits in the PR. To make this simpler to showcase, remember you can do any setup you want, including removing the pre-flight entirely and even the other AI reviewers. [Haoxiang/Kevin]

## MRP

6.1) Start with a real yuanrong-datasystem PR with 400+ comments collapsed in one thread. Show how all decisions buried, too much human involvement (human as the bottleneck), etc. To know what was decided and why, you'd read all of it — so nobody does. [Here's where I had asked Haoxiang to compute a few metrics that might help show how messed up it is.] 

In contrast, show how the MRP and how all key decisions are listed in custody. Maybe have a slide button for each MRP entry that means 'me, as a human, I'm signing off on this'. I think this is the hardest one to demo, since the pipeline needs to work end to end. Ideally we want a few things there in the MRP to show. But I'm ok if you take shortcuts too. Would be nice to showcase a mental model violation and the human signing off on it. Finally, showcase how once the author signs off on everything, this becomes available for others to review. [Kevin, Haoxiang]

The keywords here are: traceability, accountability, and hand off

-> Key decisions, surfaced — author/agent choices, signed off
-> Provenance — models, human owner, spec & SMM versions
-> Overrides recorded — e.g. an intentional pre-flight deviation, with its rationale
-> Read by the right readers — the human reviewer and downstream agents


## "Factory shift"

7.1) Factories can run heavy work off-peak. Show how nightly code-cleanup procedures can be performed based on all the PRs that were merged on that day or week. [Gustavo]

## Agent honesty

8.1) [Pending] Agent honesty: demand a certificate, not a claim: a structured reasoning trace anchored to real code paths (Ugare & Chandra, 93% execution-free accuracy). Same principle, two levels — the engine checks the evidence; this structures the reasoning behind it [Gustavo]

"I want to show a sytem that checks that agents are being honest and catches them when they try to weasel their way out of work. It should be such that is easy to follow the request than to fake it (e.g., using hidden tokens and certificates of execution). Satish work fits here. Gustavo can demo this."