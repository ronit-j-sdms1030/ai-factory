# Third-party notices

Material from other projects incorporated into this repository, and the terms it
carries. MIT and Apache-2.0 both require the copyright and permission notice to
travel with copies or substantial portions, so anything vendored is recorded
here rather than only in a dependency manifest.

Licences below were read from each project's own repository file, not taken from
the GitHub badge — several projects in this stack report a licence on their
repository page that does not match the file.

---

## Vendored source

Text copied into this repository and shipped with it.

### BMAD-METHOD

**Used for:** the eight elicitation lenses in `old/agent-service/app/elicitation.py`,
selected from the 71-method catalogue in
`skills/bmad-advanced-elicitation/assets/methods.csv` and reproduced verbatim.

**Source:** <https://github.com/bmad-code-org/BMAD-METHOD>
**Licence:** MIT

```
MIT License

Copyright (c) 2025 BMad Code, LLC

This project incorporates contributions from the open source community.

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

**Trademark — a separate matter from the licence.** BMad™, BMad Method™ and
BMad Core™ are trademarks of BMad Code, LLC. The MIT grant covers the software
only, not the brand. Its published policy permits selling products that
incorporate the software, and prohibits using the name as a product, service,
company or domain name, or presenting a product as endorsed or certified.

**How that is honoured here:** nothing in this platform is named or branded
BMad, no endorsement is claimed, and the vendored text is attributed above. The
`elicitation.py` module states the same in its docstring so the obligation
travels with the code rather than living only in this file.

### AI-DLC workflows (AWS Labs)

**Used for:** two governing rules in the intake skill file
(`old/agent-service/app/intake_skill.py`) — *"no requirement without a source"* and
*"testable or it does not exist"* — adapted from the product agent's key
principles.

**Source:** <https://github.com/awslabs/aidlc-workflows>
**Licence:** MIT-0

MIT-0 is the MIT licence with the attribution requirement removed, so nothing is
owed here. It is recorded because a reader deciding whether to change one of
those rules benefits from knowing which were borrowed and proven elsewhere
rather than invented for this project.

```
MIT No Attribution

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
the Software, and to permit persons to whom the Software is furnished to do so.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER
IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

---

## Runtime dependencies

Installed and executed, not copied into this repository. Their licences travel
with their own distributions; they are listed here because a licence-compliance
scan should find the same set the architecture commits to.

| Component | Licence | Role |
|---|---|---|
| LangGraph | MIT | Agent graph, resumable pauses |
| FastAPI | MIT | Agent service HTTP layer |
| Pydantic | MIT | Structured output schemas |
| PyMongo | Apache-2.0 | Datastore driver |
| Express | MIT | Backend HTTP layer |
| Mongoose | MIT | Backend data access |
| React | MIT | Workspace frontend |

Licence and dependency compliance is scanned in the pipeline by **Syft**
(Apache-2.0) for SBOM generation and **ScanCode** (Apache-2.0) for licence
identification, which is the mechanism behind the Scope of Work's commitment
that licence checks gate every release.

---

## Considered and deliberately not used

Recorded because the reasons are licence reasons, and a future reader should not
have to rediscover them.

| Project | Why not |
|---|---|
| **semgrep-rules** | *Semgrep Rules License v1.0* — grants use "for your own internal business purposes" and forbids making the rules "available to others as a service" |
| **opengrep-rules** | LGPL-2.1 **plus Commons Clause**, which withholds the right to "Sell", defined to include "fees for hosting or consulting/support services" |
| **Qodo Cover-Agent** | AGPL-3.0 — the network-use clause is a live exposure for a service |
| **k6** | AGPL-3.0 — same reason; Locust (MIT) is used instead |
| **PlantUML** | LGPL-3.0 strong copyleft; Mermaid (MIT) is used instead |
| **system-prompts-and-models-of-ai-tools** | GPL-3.0, and the content is extracted proprietary system prompts the publisher had no right to license |
| **dewantrie/ai-software-factory** | No licence file at all — no grant of rights |
