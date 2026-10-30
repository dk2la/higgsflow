# higgsflow

A local client for the Higgsfield API that keeps every piece of generative work in a self-contained project.

## Language

### Work

**Project**:
A self-contained folder holding everything for one piece of work: inputs, references, prompts, generations and the final picks.
_Avoid_: workspace, folder

**Request**:
One generation call submitted to the Higgsfield API, identified by its request id and moving through statuses until it reaches a terminal one. Named after the API's own term.
_Avoid_: job (the API uses "job" for the parts inside a Request), task, generation

**Generation**:
One result file downloaded from a completed Request; a single Request may yield several Generations.
_Avoid_: output, result

**Artifact**:
An extra file that comes with a Generation, such as an archive or a 3D scene, and always travels with it.
_Avoid_: attachment, extra output

**Pick**:
A Generation the user has chosen as a final result of the Project.
_Avoid_: favorite, final, selection

**Spend**:
The total price of a Project's Requests that completed successfully; failed, rejected and canceled Requests cost nothing.
_Avoid_: cost, usage

**Budget**:
An optional spending limit of a Project that triggers a warning, never a block.
_Avoid_: limit, quota

### Files

**Input**:
A local file inside the Project passed to a Request by its path.
_Avoid_: source, asset, upload

**Reference**:
A named local file in the Project, addressed as `@name`, either supplied by the user or saved from a Generation.
_Avoid_: custom reference, character

### Catalog

**Endpoint**:
One Higgsfield API path with its own parameter schema; the atomic unit of the catalog.
_Avoid_: model, model id

**Family**:
A brand of models, such as Kling, Seedance or Wan.
_Avoid_: provider, vendor

**Model**:
A specific version within a Family, such as Kling 3.0 or Kling 3.0 Turbo.
_Avoid_: endpoint, family

**Mode**:
The kind of operation a Request performs, such as text-to-video, image-to-video, reference-to-video, edit, extend, motion or text-to-image (codes in DESIGN.md §6).
_Avoid_: type, task

**Quality tier**:
A quality variant of a Model in one Mode that the API exposes as a separate path, such as Kling 3.0 Standard, Pro and 4K.
_Avoid_: variant, version

A Model and a Mode together identify exactly one Endpoint; for a Model with quality tiers in that Mode, Model, Mode and tier do.

**Preset**:
A named, ready-made style or setup from a Higgsfield catalog (Soul styles, Genjutsu Restyle, Marketing Studio) applied to a Request.
_Avoid_: template, style

### Account

**Soul ID**:
A character identity trained on the Higgsfield side from photos and owned by the account, not by a Project; training one is not a Request.
_Avoid_: custom reference, reference, character
