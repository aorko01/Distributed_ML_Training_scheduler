# workspace-stream-v1

`workspace-stream-v1` is carried in the existing six-byte `tcp-stream-v1`
record framing. It is selected only by a `HELLO` record after Gateway grant
authentication. It does not change `terminal-stream-v1`; its legacy `OPEN`,
`CLOSE`, EOF, and PTY lifecycle remain intact for connection verification and
terminal-only runtimes.

The browser sends `HELLO` with `{"protocol":"workspace-stream-v1"}` and the
runtime responds with `WORKSPACE_READY` before accepting any request. Metadata
is strict UTF-8 JSON with duplicate fields rejected and a 16 KiB maximum.
Records are at most 65,530 bytes. File data uses `FILE_CHUNK`: one byte request
ID length, ASCII request ID, four-byte big-endian sequence, then at most 32 KiB
of bytes. `FILE_END` includes the ID, byte count, and SHA-256.

`FILE_REQUEST` accepts only `list`, `stat`, `read`, `create_file`, `mkdir`,
`write`, `rename`, and `delete`. Paths are relative to the Worker-pinned
workload root. Requests and writes carry a content version where applicable.
The service allows four reads and one mutation at a time, a directory page of
200 entries, and editable UTF-8 text files up to 2 MiB. Files that are binary,
too large, symlinks, devices, FIFOs, sockets, or unsafe hard links are refused.

`PTY_OPEN`, `PTY_STDIN`, `PTY_RESIZE`, `PTY_CLOSE`, `PTY_STDOUT`, and `PTY_EXIT`
describe one default-shell PTY. Closing that PTY leaves file access available;
closing the workspace connection cancels pending file work and reaps its PTY.
`WORKSPACE_STATE`, `ERROR`, and `CANCEL` use opaque typed codes. No record
contains a container ID, host path, command, credential, or Docker API input.
