# Third-party web runtime

These files are served locally by the companion web UI. They are not covered
by the project's own license; each keeps its upstream notice.

| File | Version | Bytes | SHA-256 | License |
| --- | --- | --- | --- | --- |
| `pixi.min.js` | 7.4.3 | 456150 | `712a3e1943b062a907d86c5bb56b67c7b32a3f03b3654768b00b7016d30a24c6` | MIT |
| `pixi-live2d-display-cubism4.min.js` | 0.5.0-beta (cubism4 build) | 145809 | `3167dbb6baa75762be5bd8acec8d6f1e29f4b966041e9884976b178137095438` | MIT |
| `live2dcubismcore.min.js` | Cubism Core 4 (vendor build) | 207155 | `25ae938cb4fe282ce189b357bcc97e603d1e1f7ec78bf04150d401c23cdc792f` | Live2D Proprietary Software License Agreement |
| `mesh-avatar.min.js` | 0.4.0 | 105282 | `2e8aeab7d1b9636fceb7d2bc0af97e926b4294a1cc03f581f1d1e936f7826554` | MIT (mesh-avatar + bundled fflate) |

## mesh-avatar

`mesh-avatar.min.js` is an IIFE bundle of `mesh-avatar` 0.4.0
(`packages/runtime`, MIT, Copyright (c) 2026 Yuki Shindo) with its only
dependency `fflate` 0.8.3 (MIT) inlined. It is built with esbuild from
`src/index.ts` and exposes the `MeshAvatarRuntime` global. Both MIT notices
are kept at the top of the file. The `.mavatar` archive itself is
operator-supplied and never ships with this repository.

## Live2D Cubism Core

`live2dcubismcore.min.js` is the "Redistributable Code" defined by the Live2D
Proprietary Software License Agreement. It is redistributed unmodified, with
its original header intact.

EULA §5.2 conditions this redistribution on the following, which this project
accepts:

- the application adds significant functionality of its own on top of the Core;
- distributors and end users are bound by protection at least equivalent to
  the EULA (this software is private, local-only and not distributed);
- the project indemnifies Live2D Inc. against third-party claims arising from
  this use;
- the Core is redistributed as-is, without modification.

Per EULA §5.3.2, no open-source license (including the project's own) is
applied to `live2dcubismcore.min.js`. If this project is ever published,
packaging and the top-level license text must carry the Live2D terms for this
file.

## Models

No Live2D model ships with this repository. Models are operator-supplied
files under `data/assets/<persona>/live2d/`, ignored by git, read-only to the
program.

## process-env.js

`process-env.js` is project-authored, not third-party. It only defines
`process.env.NODE_ENV = "production"` because the `pixi-live2d-display` UMD
bundle reads that flag at load time and aborts in a plain browser without it.

