# Held-out spec sheets

These 30 sheets measure the result: the accuracy numbers in the project README come only from this set.

They were written before any prompt or example existed. The rule: prompts and few-shot examples are tuned on `data/dev/` only. Nobody changes a prompt, an example or a rule after looking at these sheets or at the errors on them. They are only run for the final measurement.

They use different products and invented brands from the development set, in the same format (`id`, `texto`, `estilo`, `pegadinha`, `gabarito`). `tests/test_data_files.py` checks that no product name, invented brand or text is shared between the two sets.
