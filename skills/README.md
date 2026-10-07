# Skills: situation-specific instructions for the live interpreter

One file per situation. The operator picks skills on the studio page (or an event file lists them under `skills`),
and their text is added to the translator's system prompt, which is cached, so a skill costs no extra latency per line.

File format: a front-matter block with `title` (shown in the UI, Korean) and `description`, then the instructions in
English for the model. Keep them short and concrete; never put examples that look like real lines (the model copies them).
