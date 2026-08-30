"""Navigation layer: browser-use Agent wiring and prompt template.

This layer owns the non-deterministic half of the pipeline (the LLM-driven
agent that reaches page state). SYS-1 introduces only ``prompt.py``
(GOAL_PROMPT verbatim); ``runner.py`` and ``controller.py`` arrive with
the layout split in Task 4.
"""
