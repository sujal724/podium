# Spec 013 — Navigation: drill in, and get back out

`Esc` now means **back** (session → task → board) and focuses the board;
`Ctrl+C` sends the interrupt to the live session (it previously owned `Esc`, which
collided with navigation). Selecting a task on the board pulls its **task detail**
(spec 012) and its **agent tree**, so drilling in is a real context change rather
than a highlight. **K1**
