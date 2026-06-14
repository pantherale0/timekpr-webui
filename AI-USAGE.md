# AI Development & Multi-Model Validation Policy
This document provides complete transparency regarding how Artificial Intelligence (AI) is used to design, build, and independently audit Guardian. 

Rather than relying blindly on a single AI assistant or masking AI code as purely human effort, this project employs a strict **"Human Architect + Multi-Model Zero-Trust Validation Pipeline"** to ensure code safety, correctness, and structural integrity.

---

## 1. The Multi-Model Pipeline & WorkflowOur development pipeline moves through three completely isolated stages to entirely eliminate AI confirmation bias:


[ Human Architecture & Security Design ]
│
▼
[ Stage 1: Build (Antigravity via ag-kit) ] ──► Raw Code Output
│
▼
[ Stage 2: Blind Audit (Cursor / Claude) ] ──► Zero-Context Review
│
▼
[ Stage 3: Human Verification ] ──► Local Testing & Final Gate


### 🧠 Stage 1: Design & Build (Human + Antigravity)
* **Human Role:** I (pantherale0) defines 100% of the software architecture, component designs, database schemas, API contracts, and threat boundaries. No AI tool makes core logic or architectural decisions.
* **AI Build Engine:** Code production, boilerplate generation, and syntax implementations are handled by **Antigravity**, leveraging the specialized agentic frameworks of [ag-kit](https://github.com/vudovn/ag-kit).

### 🔍 Stage 2: The Blind Review (Cursor)
* To prevent an AI model from simply "agreeing with its own output", the raw code generated in Stage 1 is moved into a completely separate sandbox using **Cursor** where it is then reviewed by a set of independant models.
* **Zero Context:** The secondary models are given **zero background information** on what the original prompts or requirements were. It evaluates the code purely on its immediate syntax, structural efficiency, and logic. It is instructed to actively break the code and spot subtle bugs, security vulnerabilities, or hallucinations.

### 🧪 Stage 3: Human Gatekeeper & Testing
* I (pantherale0) will review the independent feedback from the Stage 2 audit.
* I will perform local execution, manual logic verifications, and strict functional unit tests. I remain the sole final gate before any commit hits the repository.

---

## 2. Security Model & Vulnerability Isolation
Because security is a foundational pillar of this project, I explicitly use competitor AI tools against one another to validate my threat models:
1. **Bias Mitigation:** Standard LLMs suffer from confirmation bias if asked to review their own work. By separating the *Builder* tool from the *Auditor* tool, we replicate an external, adversarial review.
2. **Cryptographic Integrity:** All cryptographic choices and authentication boundaries are manually designed by me, then dual-checked by both AI systems from opposing perspectives.
3. **No Automated Merges:** No AI tool has the authority to write code directly to the repository or auto-approve a pull request. 

---

## 3. Tool Matrix
* **Primary Builder Engine:** Antigravity (powered by `ag-kit` workflows)
* **Independent Reviewer Engine:** Cursor
* **Testing & Compilation Suite:** Executed locally by me.

---

## 4. Guidelines for Contributors

I welcome community contributions! To maintain my security standards, outside contributors are asked to follow a transparent process:

* **Disclose Workflow:** If you use AI to draft a Pull Request, please state which models you used in your PR description.
* **Test Locally:** Do not submit raw, unverified AI output. You must locally execute and test your code.
* **Expect Blind Review:** Your submissions will be put through the exact same strict, zero-context review pipeline before being merged into the codebase.

---

## Questions or Public Audits?
I highly encourage public security audits of my codebase. If you spot any anomalies or edge cases that missed my dual-AI and human pipeline, please open an Issue or a Security Advisory immediately.

*This document was last updated on: 2026-06-14*
