# Spec 015 — Review is a queue, and merges report where work landed

`review.queue` returns **every** task awaiting the operator (the cockpit previously
tracked one `review_task`, so a second finisher was unreachable), each with its
branch and **the exact merge target** — which is the parent task's branch for a
subtask, not always the project base. `podium queue` prints it. **M1**

A finished session announces itself in the feed with where approving would merge it.
A failed merge blocks the task with the conflict **and the next action** ("resolve
the conflict on task/t_x, then approve again") instead of a bare git error. **M2**
