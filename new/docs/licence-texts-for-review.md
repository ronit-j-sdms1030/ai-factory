# Licence texts requiring legal review

Primary texts collected 9 September 2026 for the governed SDLC platform — not summaries.
Four questions for counsel at the end.

---

## 1. Semgrep Rules License v1.0 — the decisive one

`semgrep/semgrep-rules/LICENSE` contains only this pointer:

```
Semgrep Rules License v1.0. For more details, visit https://semgrep.dev/legal/rules-license
```

Full terms: <https://semgrep.dev/legal/rules-license>

**Semgrep and its rules were removed from the architecture on the strength of this
licence — specifically the grant being limited to "your own internal business purposes"
and the prohibition on making the rules "available to others as a service." That
decision rests on a non-lawyer's reading and should be confirmed or overturned.**

---

## 2. BMAD-METHOD — MIT with a trademark clause

```
MIT License

Copyright (c) 2025 BMad Code, LLC

This project incorporates contributions from the open source community.
See [CONTRIBUTORS.md](CONTRIBUTORS.md) for contributor attribution.

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

TRADEMARK NOTICE:
BMad™, BMad Method™, and BMad Core™ are trademarks of BMad Code, LLC, covering all
casings and variations (including BMAD, bmad, BMadMethod, BMAD-METHOD, etc.). The use of
these trademarks in this software does not grant any rights to use the trademarks
for any other purpose. See [TRADEMARK.md](TRADEMARK.md) for detailed guidelines.
```

### TRADEMARK.md

```
# Trademark Notice & Guidelines

## Trademark Ownership

The following names and logos are trademarks of BMad Code, LLC:

- **BMad** (word mark, all casings: BMad, bmad, BMAD)
- **BMad Method** (word mark, includes BMadMethod, BMAD-METHOD, and all variations)
- **BMad Core** (word mark, includes BMadCore, BMAD-CORE, and all variations)
- **BMad Code** (word mark)
- BMad Method logo and visual branding
- The "Build More, Architect Dreams" tagline

**All casings, stylings, and variations** of the above names (with or without hyphens, spaces, or specific capitalization) are covered by these trademarks.

These trademarks are protected under trademark law and are **not** licensed under the MIT License. The MIT License applies to the software code only, not to the BMad brand identity.

## What This Means

You may:

- Use the BMad software under the terms of the MIT License
- Refer to BMad to accurately describe compatibility or integration (e.g., "Compatible with BMad Method v6")
- Link to <https://github.com/bmad-code-org/BMAD-METHOD>
- Fork the software and distribute your own version under a different name

You may **not**:

- Use "BMad" or any confusingly similar variation as your product name, service name, company name, or domain name
- Present your product as officially endorsed, approved, or certified by BMad Code, LLC when it is not, without written consent from an authorized representative of BMad Code, LLC
- Use BMad logos or branding in a way that suggests your product is an official or endorsed BMad product
- Register domain names, social media handles, or trademarks that incorporate BMad branding

## Examples

| Permitted                                              | Not Permitted                                |
| ------------------------------------------------------ | -------------------------------------------- |
| "My workflow tool, compatible with BMad Method"        | "BMadFlow" or "BMad Studio"                  |
| "An alternative implementation inspired by BMad"       | "BMad Pro" or "BMad Enterprise"              |
| "My Awesome Healthcare Module (Bmad Community Module)" | "The Official BMad Core Healthcare Module"   |
| Accurately stating you use BMad as a dependency        | Implying official endorsement or partnership |

## Commercial Use

You may sell products that incorporate or work with BMad software. However:

- Your product must have its own distinct name and branding
- You must not use BMad trademarks in your marketing, domain names, or product identity
- You may truthfully describe technical compatibility (e.g., "Works with BMad Method")

## Questions?

If you have questions about trademark usage or would like to discuss official partnership or endorsement opportunities, please reach out:

- **Email**: <contact@bmadcode.com>
```

---

## 3. Open-core fences — the licence behind each

Each of these projects is permissive **outside** a fenced directory. The architecture
uses only the permissive portion.

### LiteLLM — MIT except `enterprise/`

```
The BerriAI Enterprise license (the "Enterprise License")
Copyright (c) 2024 - present Berrie AI Inc.

With regard to the BerriAI Software:

This software and associated documentation files (the "Software") may only be
used in production, if you (and any entity that you represent) have agreed to,
and are in compliance with, the BerriAI Subscription Terms of Service, available
via [call](https://enterprise.litellm.ai/demo) or email (info@berri.ai) (the "Enterprise Terms"), or other
agreement governing the use of the Software, as agreed by you and BerriAI,
and otherwise have a valid BerriAI Enterprise license for the
correct number of user seats. Subject to the foregoing sentence, you are free to
modify this Software and publish patches to the Software. You agree that BerriAI
and/or its licensors (as applicable) retain all right, title and interest in and
to all such modifications and/or patches, and all such modifications and/or
patches may only be used, copied, modified, displayed, distributed, or otherwise
exploited with a valid BerriAI Enterprise license for the  correct
number of user seats.  Notwithstanding the foregoing, you may copy and modify
the Software for development and testing purposes, without requiring a
subscription.  You agree that BerriAI and/or its licensors (as applicable) retain
all right, title and interest in and to all such modifications.  You are not
granted any other rights beyond what is expressly stated herein.  Subject to the
foregoing, it is forbidden to copy, merge, publish, distribute, sublicense,
and/or sell the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

For all third party components incorporated into the BerriAI Software, those
components are licensed under the original license provided by the owner of the
applicable component.
```

### Langfuse — MIT except `ee/`, `web/src/ee/`, `worker/src/ee/`

```
START NOTE
Langfuse is an open core project. Langfuse's core
is permissively licensed (MIT license).
Certain parts of the periphery of Langfuse are commercially
licensed and governed by this Enterprise License.
For the avoidance of doubt, this license does not apply 
to the core of Langfuse as it is defined in its license in 
the directory "/LICENSE" (as opposed to this file  "ee/LICENSE")
which can be used and run without infringing the below license
and its licensed materials.
END NOTE

START OF LICENSE
Langfuse Enterprise license (the “Enterprise License” or "EE license")

Copyright (c) 2023-2026 ClickHouse, Inc

With regard to the Langfuse Enterprise Software:
This software and associated documentation files (the "Software") may only be
used, if you (and any entity that you represent) have agreed to,
and are in compliance with, the applicable agreement governing the use of the Software, 
as agreed by you and Langfuse GmbH or ClickHouse, Inc. (including any successor or permitted assignee of either),
and otherwise have a valid Langfuse Enterprise License.

Subject to the foregoing sentence, you are free to
modify this Software and publish patches to the Software. You agree that Langfuse
and/or its licensors (as applicable) retain all right, title and interest in and
to all such modifications and/or patches, and all such modifications and/or
patches may only be used, copied, modified, displayed, distributed, or otherwise
exploited with a valid Langfuse Enterprise License. Notwithstanding the foregoing, you may copy and modify
the Software for development and testing purposes, without requiring a
subscription. You agree that Langfuse GmbH and/or its licensors (as applicable) retain
all right, title and interest in and to all such modifications. You are not
granted any other rights beyond what is expressly stated herein. Subject to the
foregoing, it is forbidden to copy, merge, publish, distribute, sublicense,
and/or sell the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
```

### Dyad — Apache-2.0 except `src/pro/`

Note this fence is **not** a standard proprietary licence — it is the Functional Source
License, which converts to Apache-2.0 on a schedule.

```
# Functional Source License, Version 1.1, ALv2 Future License

## Abbreviation

FSL-1.1-ALv2

## Notice

Copyright 2025 Dyad Tech, Inc.

## Terms and Conditions

### Licensor ("We")

The party offering the Software under these Terms and Conditions.

### The Software

The "Software" is each version of the software that we make available under
these Terms and Conditions, as indicated by our inclusion of these Terms and
Conditions with the Software.

### License Grant

Subject to your compliance with this License Grant and the Patents,
Redistribution and Trademark clauses below, we hereby grant you the right to
use, copy, modify, create derivative works, publicly perform, publicly display
and redistribute the Software for any Permitted Purpose identified below.

### Permitted Purpose

A Permitted Purpose is any purpose other than a Competing Use. A Competing Use
means making the Software available to others in a commercial product or
service that:

1. substitutes for the Software;

2. substitutes for any other product or service we offer using the Software
   that exists as of the date we make the Software available; or

3. offers the same or substantially similar functionality as the Software.

Permitted Purposes specifically include using the Software:

1. for your internal use and access;
```

---

## 4. Weak copyleft retained — the reasoning to test

| Component | Licence | Our reasoning |
|---|---|---|
| **Opengrep** | LGPL-2.1 | Invoked as a separate process, never linked into our code, so LGPL's linking obligations do not attach. |
| **OpenTofu** | MPL-2.0 | File-level copyleft. We author `.tf` configuration; we do not modify OpenTofu's own source files. |

---

## Questions for counsel

**1 · Semgrep rules — the one that changed a decision.** The licence grants use "for
your own internal business purposes" and forbids making the rules "available to others
as a service." Does deploying them inside a client's own tenant, operated by that
client, constitute their internal business use? Does the answer change if Stark operates
the platform on the client's behalf, or runs one platform across several clients?

**2 · BMAD trademark.** The MIT grant covers the code and expressly not the brand. Is
vendoring and rewriting BMAD persona files — in a product not named BMad, making no
claim of endorsement — clear of the trademark policy?

**3 · Open-core fences.** Is excluding the fenced directories at build time sufficient,
and does anything need to be demonstrable to show the fenced code is not deployed? Note
Dyad's fence is FSL-1.1, which converts to Apache-2.0 rather than remaining proprietary.

**4 · Copyleft reasoning.** Are the two justifications in section 4 sound as stated, for
a platform delivered to a client and potentially operated as a service?
