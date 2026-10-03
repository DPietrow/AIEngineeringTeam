"""Eval harness: measures whether the agent team is any good, and whether a change helped.

suite.py    cases (tasks + hidden acceptance tests, seeded bad patches)
graders.py  deterministic code graders; the model never grades itself
runner.py   runs cases x trials through the real orchestrator, collects metrics
store.py    persistence (eval_runs / eval_results)
report.py   scorecard + run-to-run comparison
__main__.py CLI:  python -m agentteam.evals run|compare|list|show
"""
